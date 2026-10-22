"""Groep E, F en G van het 30-signalen-sjabloon: tellers, CBM en het alarmwoord.

Eén implementatie voor alle machines in het park. Elke physics-module modelleert
zijn eigen proces; wat ze allemaal delen is een motor die stroom trekt, een lager
dat warm wordt, een teller die oploopt en een alarmwoord dat bits zet.

Waarom dit bestaat. Zonder groep F zijn twaalf machines alleen maar meer getallen.
Met groep F krijgt elke storing een zichtbaar mechanisch gevolg:

    f8   vervuiling/verstopping  -> stroom omhoog, trilling omhoog, rendement omlaag
    f13  motorslip               -> stroom omhoog, snelheid omlaag
    f2   sensordrift             -> langzaam weglopende meting (in de physics-module)

en levert energy_kWh gedeeld door units_total de energie per pak op, wat in
zuivel de overtuigendste kosten-KPI is die er is.

De trillingsschaal volgt ISO 10816: 2,8 mm/s goed, 7,1 acceptabel, 11,2 alarm.
Lagertemperatuur is een eerste-orde naijling op de stroom en loopt dus ACHTER op
de trilling. Dat is geen modelleerfout maar het punt: trilling waarschuwt eerder
dan temperatuur, en daarom hangt condition-based maintenance aan trilling.
"""

from __future__ import annotations

import math
import random

# Bits in alarm_word. Zelfde volgorde als in factory-model/signal-template.json;
# lopen die uiteen, dan leest een dashboard bit 4 als bit 5 en klopt er niets meer.
BIT_PV_LOW = 0
BIT_PV_HIGH = 1
BIT_DRIVE_FAULT = 2
BIT_SENSOR_FAULT = 3
BIT_CIP_DUE = 4
BIT_INTERLOCK = 5
BIT_QUALITY_BAD = 6
BIT_STALE_INPUT = 7


class HealthModel:
    """Mechanische gezondheid en tellers voor één machine."""

    def __init__(self, config, state_machine, fault_injector):
        self.cfg = config or {}
        self.sm = state_machine
        self.faults = fault_injector

        self.nominal_current_a = float(self.cfg.get("nominal_current_a", 25.0))
        self.design_speed = float(self.cfg.get("design_speed", 120.0)) or 120.0
        self.mass_per_unit_kg = float(self.cfg.get("mass_per_unit_kg", 1.03))
        self.cip_cycle_limit = int(self.cfg.get("cip_cycle_limit", 4))
        self.service_interval_h = float(self.cfg.get("service_interval_h", 2000.0))
        self.voltage = float(self.cfg.get("voltage", 400.0))
        self.power_factor = float(self.cfg.get("power_factor", 0.86))

        # Toestand
        self.units_total = 0
        self.reject_total = 0
        self.mass_total_kg = 0.0
        self.runtime_hours = 0.0
        self.motor_current_a = 0.0
        self.vibration_mm_s = 0.4
        self.bearing_temp_c = 20.0
        self.energy_kwh = 0.0
        self.cycles_since_cip = 0
        self.hours_since_service = 0.0

        # Groep D. Bewust hier en niet in de physics-modules: een regelklep en
        # een frequentieregelaar zijn generiek mechanisch, net als de motorstroom.
        # Twaalf physics-modules elk hun eigen klepstand laten uitvinden levert
        # twaalf keer dezelfde code op, en elf kansen om het net anders te doen.
        self.valve_pos_pct = 0.0
        self.drive_out_pct = 0.0

        self._unit_frac = 0.0
        self._wear = 0.0          # lagerslijtage, loopt langzaam op met draaiuren
        self._ambient_c = 20.0
        self._alarm_word = 0
        self._ext_bits = 0        # bits die de physics-module zelf zet

    # ------------------------------------------------------------------ input

    def set_external_bits(self, bits: int) -> None:
        """Laat een physics-module eigen alarmbits zetten (bv. divert actief)."""
        self._ext_bits = int(bits) & 0xFF

    def note_cycle(self) -> None:
        """Eén productiecyclus (batch, CIP-interval) afgerond."""
        self.cycles_since_cip += 1

    def do_cip(self) -> None:
        self.cycles_since_cip = 0

    def do_service(self) -> None:
        self.hours_since_service = 0.0
        self._wear = 0.0

    # ------------------------------------------------------------------- step

    def step(self, dt: float) -> None:
        if dt <= 0:
            return
        sm = self.sm
        running = sm.is_running()
        speed = float(getattr(sm, "cur_mach_speed", 0.0))
        load = max(0.0, min(speed / self.design_speed, 1.5))

        f8 = self.faults.magnitude("f8") if self.faults.is_active("f8") else 0.0
        f13 = self.faults.magnitude("f13") if self.faults.is_active("f13") else 0.0
        f12 = self.faults.magnitude("f12") if self.faults.is_active("f12") else 0.0

        if running:
            self.runtime_hours += dt / 3600.0
            self.hours_since_service += dt / 3600.0
            self._wear = min(1.0, self._wear + dt / 3600.0 / max(self.service_interval_h, 1.0))

            # Tellers. Snelheid is eenheden per minuut.
            self._unit_frac += speed * dt / 60.0
            whole = int(self._unit_frac)
            if whole:
                self._unit_frac -= whole
                self.units_total += whole
                self.mass_total_kg += whole * self.mass_per_unit_kg
                # Afkeur loopt op met vervuiling: een slechter draaiende machine
                # levert meer uitval. Basis ~0,4%, tot ~4% bij volle f8.
                p = 0.004 + 0.036 * f8
                self.reject_total += sum(1 for _ in range(whole) if random.random() < p)

            # Motorstroom: nullast + belasting, plus de storingseffecten.
            # f8 laat de motor harder werken tegen weerstand in, f13 laat hem
            # slippen zodat hij stroom trekt zonder snelheid te maken.
            base = self.nominal_current_a * (0.35 + 0.65 * load)
            target = base * (1.0 + 0.45 * f8 + 0.30 * f13)
            self.motor_current_a += (target - self.motor_current_a) * min(1.5 * dt, 1.0)
            self.motor_current_a = max(0.0, self.motor_current_a + random.gauss(0, 0.05))

            # Trilling: basis + slijtage + storing. Onbalans door aanslag (f8)
            # is de grootste bijdrage, precies zoals bij een vervuilde rotor.
            vib = (0.4 + 1.2 * load) * (1.0 + 0.8 * self._wear) + 5.5 * f8 + 2.0 * f13
            self.vibration_mm_s += (vib - self.vibration_mm_s) * min(0.8 * dt, 1.0)
            self.vibration_mm_s = max(0.0, self.vibration_mm_s + random.gauss(0, 0.02))

            self.energy_kwh += (math.sqrt(3) * self.voltage * self.motor_current_a
                                * self.power_factor / 1000.0) * (dt / 3600.0)

            # Regelklep: opent verder naarmate het proces meer moeite kost.
            # Vervuiling (f8) vraagt meer energie voor hetzelfde resultaat en
            # een zwakke klep (f12) loopt tegen zijn eindstand aan. Een klep die
            # tegen de 100% aan hangt is in de praktijk het eerste signaal dat
            # een warmtewisselaar toe zit; eerder dan de temperatuur wegloopt.
            v_target = min(100.0, (25.0 + 55.0 * load) * (1.0 + 0.55 * f8 + 0.40 * f12))
            self.valve_pos_pct += (v_target - self.valve_pos_pct) * min(0.6 * dt, 1.0)

            # Frequentieregelaar: stuurt naar de gevraagde snelheid. Bij slip
            # stuurt hij harder dan de machine daadwerkelijk loopt.
            d_target = min(100.0, 100.0 * load * (1.0 + 0.35 * f13))
            self.drive_out_pct += (d_target - self.drive_out_pct) * min(1.0 * dt, 1.0)
        else:
            self.motor_current_a += (0.0 - self.motor_current_a) * min(2.0 * dt, 1.0)
            self.vibration_mm_s += (0.15 - self.vibration_mm_s) * min(0.5 * dt, 1.0)
            self.valve_pos_pct += (0.0 - self.valve_pos_pct) * min(1.0 * dt, 1.0)
            self.drive_out_pct += (0.0 - self.drive_out_pct) * min(1.5 * dt, 1.0)

        # Lagertemperatuur volgt de stroom met een trage naijling, en loopt dus
        # achter op de trilling. Dat is de hele reden dat CBM aan trilling hangt.
        eq = self._ambient_c + 1.15 * self.motor_current_a * (1.0 + 0.5 * self._wear)
        self.bearing_temp_c += (eq - self.bearing_temp_c) * min(0.02 * dt, 1.0)

        # Alarmwoord.
        w = self._ext_bits
        if self.vibration_mm_s > 11.2:
            w |= 1 << BIT_DRIVE_FAULT
        if self.cycles_since_cip >= self.cip_cycle_limit:
            w |= 1 << BIT_CIP_DUE
        if f12 > 0.3:
            w |= 1 << BIT_PV_LOW
        if self.bearing_temp_c > 95.0:
            w |= 1 << BIT_PV_HIGH
        if self.valve_pos_pct > 97.0:
            # Klep tegen de eindstand: de regeling heeft geen ruimte meer.
            w |= 1 << BIT_INTERLOCK
        if self.faults.is_active("f1") or self.faults.is_active("f2"):
            w |= 1 << BIT_SENSOR_FAULT
        self._alarm_word = w & 0xFF

    # ------------------------------------------------------------------- read

    def read(self) -> dict:
        return {
            "units_total": int(self.units_total),
            "reject_total": int(self.reject_total),
            "mass_total_kg": round(self.mass_total_kg, 1),
            "valve_pos_pct": round(self.valve_pos_pct, 1),
            "drive_out_pct": round(self.drive_out_pct, 1),
            "runtime_hours": round(self.runtime_hours, 3),
            "motor_current_A": round(self.motor_current_a, 2),
            "vibration_mm_s": round(self.vibration_mm_s, 2),
            "bearing_temp_C": round(self.bearing_temp_c, 1),
            "energy_kWh": round(self.energy_kwh, 3),
            "cycles_since_cip": int(self.cycles_since_cip),
            "hours_since_service": round(self.hours_since_service, 2),
            "alarm_word": int(self._alarm_word),
        }
