"""DairyPlant process model: continuous flow + a 3-scenario failure engine.

Pure Python, no dependencies. Mirrors the equipment in the existing OPC-UA
dairy-sim (Receiving/Tank01, Separator, Pasteurizer, Homogenizer, Bottler) and
layers three independently triggerable demo scenarios on top:

  cooling   tank refrigeration weakens -> in_temp_C creeps up -> predictive alarm
  leak      hidden leak -> only visible by comparing inflow/outflow/level (mass balance)
  fault     a machine trips to ERROR -> event frame + line cascade (slide 14)

Status codes: 0 = Stopped, 1 = Running, 2 = Error.  See SPEC.md.
"""

from __future__ import annotations

STOPPED, RUNNING, ERROR = 0, 1, 2

# --- nominal setpoints -----------------------------------------------------
TANK_TEMP_NOMINAL = 5.0          # C, cold store
TANK_TEMP_LIMIT = 6.0            # C, cold-chain spec ceiling
TANK_TEMP_WARN = 5.5             # C, predictive warning level
NOMINAL_FLOW = 1000.0            # L/min through the line
SEP_RPM = 6000.0
FAT_PCT = 3.5
HTST_SETPOINT = 72.0             # C
HTST_DIVERT_BELOW = 71.5         # C, divert valve trips below this
HOMOG_BAR = 180.0
BOTTLE_ML = 1000.0
BOTTLES_PER_MIN = 120.0

# --- scenario rates (fast-forward knobs so the arc plays on camera) --------
COOL_RATE = 1.0                  # cooling_health decay multiplier
LEAK_L_MIN = 80.0                # injected leak size when active
BALANCE_TOLERANCE = 25.0         # L/min mass-balance error that trips the leak alarm


class Equipment:
    def __init__(self, name: str):
        self.name = name
        self.status = RUNNING
        self.running_hours = 0.0

    def tick_hours(self, dt_s: float):
        if self.status == RUNNING:
            self.running_hours += dt_s / 3600.0


class Tank(Equipment):
    def __init__(self):
        super().__init__("Tank01")
        self.in_temp_c = TANK_TEMP_NOMINAL
        self.flow_in = NOMINAL_FLOW
        self.flow_out = NOMINAL_FLOW
        self.level_pct = 70.0
        self.cooling_health_pct = 100.0


class Separator(Equipment):
    def __init__(self):
        super().__init__("Separator")
        self.rpm = SEP_RPM
        self.fat_pct = FAT_PCT


class Pasteurizer(Equipment):
    def __init__(self):
        super().__init__("Pasteurizer")
        self.htst_temp_c = HTST_SETPOINT
        self.hold_sec = 15
        self.divert_valve = False
        self.diverted_l = 0.0


class Homogenizer(Equipment):
    def __init__(self):
        super().__init__("Homogenizer")
        self.pressure_bar = HOMOG_BAR


class Bottler(Equipment):
    def __init__(self):
        super().__init__("Bottler")
        self.bottles_per_min = BOTTLES_PER_MIN
        self.fill_volume_ml = BOTTLE_ML
        self.reject_count = 0
        self.bottles_total = 0
        self._partial = 0.0


class Plant:
    """Continuous milk line + scenario engine. One tick advances dt_s seconds."""

    EQUIP_ORDER = ["Tank01", "Separator", "Pasteurizer", "Homogenizer", "Bottler"]

    def __init__(self, cool_rate: float = COOL_RATE):
        self.tank = Tank()
        self.separator = Separator()
        self.pasteurizer = Pasteurizer()
        self.homogenizer = Homogenizer()
        self.bottler = Bottler()
        self.equipment = {
            "Tank01": self.tank, "Separator": self.separator,
            "Pasteurizer": self.pasteurizer, "Homogenizer": self.homogenizer,
            "Bottler": self.bottler,
        }
        self.master_running = True
        self.sim_time_s = 0.0
        self.cool_rate = cool_rate

        # scenario flags
        self.scn_cooling = False
        self.scn_leak = False
        self.leak_l_min = 0.0
        self.faulted_asset: str | None = None

        # detection / alarms
        self.alarms: dict[str, dict] = {}      # keyed by alarm id
        self.events: list[dict] = []           # event-frame log (most recent last)
        self._open_event: dict | None = None
        self.balance_error = 0.0

    # ---- control ----------------------------------------------------------
    def start(self):
        self.master_running = True
        for e in self.equipment.values():
            if e.status != ERROR:
                e.status = RUNNING

    def stop(self):
        self.master_running = False
        for e in self.equipment.values():
            if e.status != ERROR:
                e.status = STOPPED

    # ---- scenario triggers ------------------------------------------------
    def trigger(self, scenario: str, asset: str | None = None):
        if scenario == "cooling":
            self.scn_cooling = True
        elif scenario == "leak":
            self.scn_leak = True
            self.leak_l_min = LEAK_L_MIN
        elif scenario == "fault":
            target = asset or "Separator"
            if target in self.equipment:
                self.faulted_asset = target
                self.equipment[target].status = ERROR
                self._open_event = {
                    "asset": target, "type": "Machine fault",
                    "start_s": round(self.sim_time_s, 1), "end_s": None,
                    "duration_s": None, "acknowledged": False,
                }

    def clear(self, scenario: str):
        if scenario == "cooling":
            self.scn_cooling = False
            self.tank.cooling_health_pct = 100.0
            self.alarms.pop("cooling", None)
        elif scenario == "leak":
            self.scn_leak = False
            self.leak_l_min = 0.0
            self.alarms.pop("leak", None)
        elif scenario == "fault":
            if self.faulted_asset:
                self.equipment[self.faulted_asset].status = RUNNING
            self.faulted_asset = None
            if self._open_event:
                self._open_event["end_s"] = round(self.sim_time_s, 1)
                self._open_event["duration_s"] = round(
                    self._open_event["end_s"] - self._open_event["start_s"], 1)
                self.events.append(self._open_event)
                self.events = self.events[-30:]
                self._open_event = None
            self.alarms.pop("fault", None)

    def heal_all(self):
        for s in ("cooling", "leak", "fault"):
            self.clear(s)
        self.pasteurizer.divert_valve = False

    def acknowledge_events(self):
        for ev in self.events:
            ev["acknowledged"] = True

    # ---- simulation -------------------------------------------------------
    def tick(self, dt_s: float):
        self.sim_time_s += dt_s
        for e in self.equipment.values():
            e.tick_hours(dt_s)

        self._scenario_cooling(dt_s)
        self._line_flow(dt_s)
        self._scenario_leak()
        self._pasteurizer_logic()
        self._production(dt_s)
        self._fault_cascade()
        self._detect()

    def _scenario_cooling(self, dt_s: float):
        t = self.tank
        if self.scn_cooling and t.status != ERROR:
            t.cooling_health_pct = max(0.0, t.cooling_health_pct - dt_s * 1.2 * self.cool_rate)
        # temp rises as cooling fails: nominal at 100% health, climbs as it drops
        deficit = (100.0 - t.cooling_health_pct) / 100.0
        target = TANK_TEMP_NOMINAL + deficit * 3.5   # up to ~8.5 C when cooling fully gone
        t.in_temp_c += (target - t.in_temp_c) * min(1.0, dt_s * 0.4)

    def _line_flow(self, dt_s: float):
        t = self.tank
        running = self.master_running and t.status != ERROR
        t.flow_in = NOMINAL_FLOW if running else 0.0
        # flow_out is what actually leaves toward the process (less the leak)
        nominal_out = NOMINAL_FLOW if running else 0.0
        t.flow_out = max(0.0, nominal_out - (self.leak_l_min if self.scn_leak else 0.0))

    def _scenario_leak(self):
        # nothing extra to compute here; detection handled in _detect via mass balance
        pass

    def _pasteurizer_logic(self):
        p = self.pasteurizer
        if p.status == ERROR:
            p.htst_temp_c += (20.0 - p.htst_temp_c) * 0.2   # cools toward ambient when down
        else:
            # holds setpoint with small noise (no fouling scenario in this build)
            target = HTST_SETPOINT
            p.htst_temp_c += (target - p.htst_temp_c) * 0.5
        p.divert_valve = p.htst_temp_c < HTST_DIVERT_BELOW

    def _production(self, dt_s: float):
        b = self.bottler
        upstream_ok = (
            self.master_running
            and self.tank.status != ERROR
            and self.separator.status != ERROR
            and self.pasteurizer.status != ERROR
            and self.homogenizer.status != ERROR
            and not self.pasteurizer.divert_valve
        )
        if b.status == ERROR or not upstream_ok:
            b.bottles_per_min = 0.0
        else:
            b.bottles_per_min = BOTTLES_PER_MIN
            b._partial += (b.bottles_per_min / 60.0) * dt_s
            while b._partial >= 1.0:
                b._partial -= 1.0
                b.bottles_total += 1
        if self.pasteurizer.divert_valve:
            self.pasteurizer.diverted_l += (NOMINAL_FLOW / 60.0) * dt_s

    def _fault_cascade(self):
        # crude but legible cascade: a faulted asset backs up the tank level and
        # starves the separator rpm / homogenizer pressure downstream of it.
        if not self.faulted_asset:
            # relax back toward nominal
            self.tank.level_pct += (70.0 - self.tank.level_pct) * 0.05
            self.separator.rpm += (SEP_RPM - self.separator.rpm) * 0.2
            self.homogenizer.pressure_bar += (HOMOG_BAR - self.homogenizer.pressure_bar) * 0.2
            return
        idx = self.EQUIP_ORDER.index(self.faulted_asset)
        # upstream backs up -> tank level rises
        self.tank.level_pct = min(100.0, self.tank.level_pct + 0.6)
        # downstream starves
        if idx <= self.EQUIP_ORDER.index("Separator"):
            self.separator.rpm = max(0.0, self.separator.rpm - 200.0)
        if idx <= self.EQUIP_ORDER.index("Homogenizer"):
            self.homogenizer.pressure_bar = max(0.0, self.homogenizer.pressure_bar - 8.0)

    def _detect(self):
        # Cooling: predictive alarm before the 6 C breach
        t = self.tank
        if t.in_temp_c > TANK_TEMP_WARN:
            sev = "critical" if t.in_temp_c > TANK_TEMP_LIMIT else "warning"
            self.alarms["cooling"] = {
                "id": "cooling", "severity": sev, "asset": "Tank01",
                "message": (
                    f"Tank temperature {t.in_temp_c:.1f} C "
                    + ("ABOVE the 6.0 C cold-chain limit." if sev == "critical"
                       else "rising toward the 6.0 C limit. Service tank cooling.")),
            }
        else:
            self.alarms.pop("cooling", None)

        # Leak: mass balance. Single tags look fine; the comparison reveals it.
        # expected_out == flow_in (steady level). balance_error = in - out.
        self.balance_error = abs(t.flow_in - t.flow_out)
        if self.balance_error > BALANCE_TOLERANCE:
            self.alarms["leak"] = {
                "id": "leak", "severity": "critical", "asset": "Line",
                "message": (
                    f"Leak detected by mass balance: in {t.flow_in:.0f} L/min vs "
                    f"out {t.flow_out:.0f} L/min = {self.balance_error:.0f} L/min unaccounted. "
                    "Invisible in any single tag."),
            }
        else:
            self.alarms.pop("leak", None)

        # Fault: surface the open event frame as an alarm
        if self.faulted_asset:
            self.alarms["fault"] = {
                "id": "fault", "severity": "critical", "asset": self.faulted_asset,
                "message": (
                    f"{self.faulted_asset} in fault (ERROR). Line cascade: upstream backing up, "
                    "downstream starving. Event frame open."),
            }
        else:
            self.alarms.pop("fault", None)

        # Divert as its own alarm if active
        if self.pasteurizer.divert_valve:
            self.alarms["divert"] = {
                "id": "divert", "severity": "critical", "asset": "Pasteurizer",
                "message": "Divert valve OPEN: under-pasteurised milk diverted from the bottler.",
            }
        else:
            self.alarms.pop("divert", None)
