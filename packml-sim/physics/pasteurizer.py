"""HTST pasteurizer — safety-critical thermal unit.

Continuous heat-treatment. PackML state controls steam valve + flow.
Holding-tube hold time is fixed by physical geometry.

Safety logic (PMO 21 CFR 1240 — High Temperature Short Time):
    If HTST temperature < hold_min_c, divert valve trips immediately
    and the SM auto-Aborts (regulatory requirement).

Faults:
    f1   sensor bias  (temp reads high → false safety)
    f12  steam valve  (temp can't reach setpoint)
    f8   regen plate fouling (heat-exchange efficiency drops)
"""

from __future__ import annotations

import random

from packml import PackMLState

from .base import PhysicsBase, PhysicsRegistry


@PhysicsRegistry.register("pasteurizer")
class Pasteurizer(PhysicsBase):
    #: Wat deze module ECHT implementeert. tools/gen-park.py leest dit uit om
    #: park-faults.json te vullen, zodat de storingscatalogus geen storing kan
    #: claimen die de fysica niet kent. Een knop die niets doet is erger dan
    #: geen knop, en in een demo merkt het publiek dat als eerste.
    FAULTS = {
        "f1": "sensorbias, de meting leest te hoog en suggereert valse veiligheid",
        "f8": "vervuiling van de regeneratieplaten, warmteoverdracht loopt terug",
        "f12": "stoomklep haalt het setpoint niet meer",
    }

    def __init__(self, config, state_machine, fault_injector):
        super().__init__(config, state_machine, fault_injector)
        self.setpoint_c = float(config.get("setpoint_c", 72.0))
        self.hold_min_c = float(config.get("hold_min_c", 71.5))
        self.hold_sec = int(config.get("hold_sec", 15))
        self.regen_eff = float(config.get("regen_eff", 0.85))
        self.htst_temp_c = 25.0
        self.divert = False
        self.flow_l_min = 0.0
        self._auto_aborted = False
        self._reached_temp = False

        # Uitgebreide procesvariabelen voor het park (lijn Vla-B). Achter een
        # vlag, want zonder vlag moet deze module byte-voor-byte publiceren wat
        # de bestaande DairyPlant- en bakkerij-scenario's al jaren zien.
        self.extended_pvs = bool(config.get("extended_pvs", False))
        self.temp_in_c = 8.0
        self.hold_temp_c = 25.0
        self.balance_level_pct = 62.0
        self.flow_sp = float(config.get("flow_sp", 800.0))
        self.throughput_ref_L = float(config.get("throughput_ref_L", 5000.0))

    def step(self, dt):
        sm = self.sm
        if sm.is_running():
            steam_target = self.setpoint_c
            if self.faults.is_active("f12"):
                steam_target *= (1.0 - 0.04 * self.faults.magnitude("f12"))
            if self.faults.is_active("f8"):
                # Regen plate fouling — slower approach
                tau_factor = 1.0 / max(self.regen_eff - 0.4 * self.faults.magnitude("f8"), 0.1)
            else:
                tau_factor = 1.0 / max(self.regen_eff, 0.1)
            if self.extended_pvs:
                # Warmtebalans in plaats van alleen een tragere aanloop.
                #
                # Q = U * A * dT. Vervuiling verlaagt U, dus de haalbare
                # uittredetemperatuur daalt zodra de stoomklep geen marge meer
                # heeft. Tot die drempel merk je vervuiling ALLEEN aan energie
                # en klepstand, niet aan de temperatuur; dat is precies waarom
                # condition-based maintenance eerder waarschuwt dan een
                # temperatuuralarm, en waarom een fabriek die alleen op
                # temperatuur bewaakt de vervuiling pas ziet als het te laat is.
                f8m = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
                headroom = max(0.0, f8m - 0.35)
                steam_target = min(steam_target, self.setpoint_c - headroom * 28.0)
            alpha = min(0.15 / tau_factor * dt, 1.0)
            self.htst_temp_c += (steam_target - self.htst_temp_c) * alpha
            self.htst_temp_c += random.gauss(0, 0.08)
            self.flow_l_min = sm.cur_mach_speed * 8.0  # 120 → 960 L/min nominal
        else:
            self.htst_temp_c += (25.0 - self.htst_temp_c) * 0.005 * dt
            self.flow_l_min = max(0.0, self.flow_l_min - 30.0 * dt)

        if self.extended_pvs:
            self._step_extended(dt)

        # Safety: divert if temp below hold_min while producing
        reading = self.htst_temp_c
        if self.faults.is_active("f1"):
            reading += 1.5 * self.faults.magnitude("f1")
        if sm.state == PackMLState.EXECUTE and reading < self.hold_min_c:
            self.divert = True
            # Tijdens het OPWARMEN divert een echte HTST wel, maar aborteert hij
            # niet: forward flow begint pas na come-up, en divert is dan normaal
            # bedrijf en geen storing. Alleen als de temperatuur ooit gehaald was
            # en daarna wegzakt, is het een echte veiligheidstrip.
            #
            # Alleen in parkmodus, want de bestaande DairyPlant- en bakkerij-
            # scenario's draaien al jaren op het oude gedrag en dat mag deze
            # uitbreiding niet veranderen.
            trip = self._reached_temp if self.extended_pvs else True
            if trip and not self._auto_aborted:
                sm.command("abort")
                self._auto_aborted = True
        else:
            self.divert = False
            if sm.state == PackMLState.EXECUTE and reading >= self.hold_min_c:
                self._reached_temp = True
            if sm.state == PackMLState.IDLE:
                self._auto_aborted = False
                self._reached_temp = False

    def _step_extended(self, dt):
        """Drie extra procesvariabelen, alleen voor het park (lijn Vla-B).

        Achter `extended_pvs`, want deze keys erbij zetten zou veranderen wat de
        bestaande DairyPlant- en bakkerij-scenario's publiceren, en die worden
        gearchiveerd. Een sim uitbreiden mag nooit een bestaande UNS wijzigen.
        """
        sm = self.sm
        running = sm.is_running()

        # Inlaattemperatuur: koude melk, iets voorverwarmd door regeneratie.
        # Vervuiling (f8) haalt juist die regeneratie onderuit, dus de inlaat
        # komt kouder aan en de stoomklep moet harder werken. Dat is precies
        # waarom vervuiling zich als energieverbruik laat zien voordat het
        # zich als temperatuurafwijking laat zien.
        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
        regen_now = max(self.regen_eff - 0.4 * f8, 0.05)
        target_in = 8.0 + (self.htst_temp_c - 8.0) * regen_now * 0.55 if running else 8.0
        self.temp_in_c += (target_in - self.temp_in_c) * min(0.08 * dt, 1.0)
        self.temp_in_c += random.gauss(0, 0.03)

        # Holdbuis: uittrede ligt altijd iets onder de HTST-temperatuur door
        # warmteverlies over de buis. Dit is de tag die OF RECORD is voor het
        # pasteurisatiedossier, en de a-kant van XC-COOK-TEMP.
        loss = 0.35 + 0.9 * f8
        target_hold = (self.htst_temp_c - loss) if running else 25.0
        self.hold_temp_c += (target_hold - self.hold_temp_c) * min(0.12 * dt, 1.0)
        self.hold_temp_c += random.gauss(0, 0.05)

        # Balanstank: loopt leeg naarmate de procestank leeg raakt. throughput_ref_L
        # wordt door de follower gevoed met het niveau van de monoliet-procestank,
        # dus een lege tank betekent hier ook echt geen doorzet.
        want = 62.0
        if running:
            want = max(5.0, min(95.0, self.throughput_ref_L / 80.0))
        self.balance_level_pct += (want - self.balance_level_pct) * min(0.05 * dt, 1.0)

    def read(self):
        reading = self.htst_temp_c
        if self.faults.is_active("f1"):
            reading += 1.5 * self.faults.magnitude("f1")
        base = {
            "HTST_temp_C": round(reading, 2),
            "hold_sec": self.hold_sec,
            "divert_valve_status": self.divert,
            "flow_L_min": round(self.flow_l_min, 1),
            "regen_efficiency_pct": round(self.regen_eff * 100.0, 1),
        }
        if not self.extended_pvs:
            return base
        base.update({
            "temp_in_c": round(self.temp_in_c, 2),
            "hold_temp_c": round(self.hold_temp_c, 2),
            "balance_level_pct": round(self.balance_level_pct, 1),
        })
        return base
