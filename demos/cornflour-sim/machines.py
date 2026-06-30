"""Corn-flour mill process model: 4 machines + batch state machine + grinder wear.

Pure Python, no dependencies. The class list here IS the asset model the workshop
turns into ISA-95 in Step 3 (Model). See SPEC.md.

Status codes: 0 = Stopped, 1 = Running, 2 = Error.
"""

from __future__ import annotations

STOPPED, RUNNING, ERROR = 0, 1, 2

# --- batch / process constants (tuned so a batch flows through in well under a
#     minute of real time, and the grinder visibly wears over a few batches) ---
RAW_PER_BATCH_KG = 100.0          # one batch = 100 kg raw corn
WASHER_LOSS = 0.02                # debris/water removed
DRYER_LOSS = 0.05                 # moisture driven off
GRINDER_LOSS = 0.10               # ~10% grounds (bran/waste)
BAG_SIZE_KG = 10.0

# nominal throughput per machine, kg per simulated second (when running + fed)
WASHER_RATE = 4.0
DRYER_RATE = 3.0
GRINDER_NOMINAL_RATE = 3.0
BAGFILLER_RATE = 5.0

DRYER_TARGET_C = 80.0
GRINDER_NOMINAL_RPM = 1450.0

# grinder wear: blade wear (%) gained per hour of grinding, before WEAR_RATE accel
WEAR_PCT_PER_HOUR = 1.5
# performance falls k% for every 1% of blade wear
WEAR_PERF_K = 1.0
SPEC_THRESHOLD_PCT = 80.0         # below this, flour drifts out of spec (Solve trigger)


class Machine:
    """Common base: capacity, start/stop, status, running hours, level."""

    def __init__(self, name: str, capacity_kg: float):
        self.name = name
        self.capacity_kg = capacity_kg
        self.status = STOPPED
        self.running_hours = 0.0
        self.level_kg = 0.0

    def start(self):
        if self.status != ERROR:
            self.status = RUNNING

    def stop(self):
        if self.status != ERROR:
            self.status = STOPPED

    def tick_hours(self, dt_s: float):
        if self.status == RUNNING:
            self.running_hours += dt_s / 3600.0


class Washer(Machine):
    def __init__(self):
        super().__init__("Washer", 60.0)


class Dryer(Machine):
    def __init__(self):
        super().__init__("Dryer", 30.0)
        self.temperature_c = 20.0

    def update_temp(self, dt_s: float):
        # ramp toward target when running, cool down when stopped
        target = DRYER_TARGET_C if self.status == RUNNING else 20.0
        self.temperature_c += (target - self.temperature_c) * min(1.0, dt_s * 0.5)


class Grinder(Machine):
    def __init__(self):
        super().__init__("Grinder", 30.0)
        self.speed_rpm = 0.0
        self.blade_wear_pct = 0.0
        self.performance_pct = 100.0
        self.throughput_kgph = GRINDER_NOMINAL_RATE * 3600.0

    def update_wear(self, dt_s: float, wear_rate: float):
        if self.status == RUNNING:
            self.speed_rpm = GRINDER_NOMINAL_RPM
            self.blade_wear_pct += (dt_s / 3600.0) * WEAR_PCT_PER_HOUR * wear_rate
            self.blade_wear_pct = min(100.0, self.blade_wear_pct)
        else:
            self.speed_rpm = 0.0
        self.performance_pct = max(0.0, 100.0 - WEAR_PERF_K * self.blade_wear_pct)
        self.throughput_kgph = GRINDER_NOMINAL_RATE * (self.performance_pct / 100.0) * 3600.0

    @property
    def current_rate(self) -> float:
        return GRINDER_NOMINAL_RATE * (self.performance_pct / 100.0)

    @property
    def out_of_spec(self) -> bool:
        return self.performance_pct < SPEC_THRESHOLD_PCT


class BagFiller(Machine):
    def __init__(self):
        super().__init__("BagFiller", 120.0)
        self.bags_filled = 0          # lifetime
        self.bags_this_batch = 0


# --- batch lifecycle ---
IDLE, BATCH_RUNNING, COMPLETE = "IDLE", "RUNNING", "COMPLETE"


class Factory:
    """Staged pipeline of the 4 machines + per-batch lifecycle + grinder wear.

    The model is intentionally simple: each running, fed machine moves material
    downstream at its rate, applying its loss factor. The grinder's rate scales
    with blade performance, so as blades wear the whole line slows and, past the
    spec threshold, the flour drifts out of spec. That crossing is the Solve test.
    """

    def __init__(self, wear_rate: float = 1.0):
        self.washer = Washer()
        self.dryer = Dryer()
        self.grinder = Grinder()
        self.bagfiller = BagFiller()
        self.machines = [self.washer, self.dryer, self.grinder, self.bagfiller]

        self.wear_rate = wear_rate
        self.master_running = False
        self.state = IDLE

        # material buffers between stages (kg)
        self.raw_buffer = 0.0         # raw corn waiting for washer
        self.washed_buffer = 0.0      # between washer and dryer
        self.dried_buffer = 0.0       # between dryer and grinder
        self.flour_buffer = 0.0       # between grinder and bagfiller

        self.batch_id = 0
        self.batch_start_s = 0.0
        self.batch_raw_in = 0.0
        self.batch_flour_out = 0.0
        self.batch_bags = 0
        self._grinder_perf_samples: list[float] = []

        self.sim_time_s = 0.0
        self.batches: list[dict] = []         # completed batch records (most recent last)
        self.maintenance_alarm: dict | None = None

    # -- control --
    def start_factory(self):
        self.master_running = True
        for m in self.machines:
            m.start()
        if self.state in (IDLE, COMPLETE):
            self._begin_batch()

    def stop_factory(self):
        self.master_running = False
        for m in self.machines:
            m.stop()

    def reset_blades(self):
        self.grinder.blade_wear_pct = 0.0
        self.grinder.running_hours = 0.0
        self.maintenance_alarm = None

    def _begin_batch(self):
        self.batch_id += 1
        self.state = BATCH_RUNNING
        self.batch_start_s = self.sim_time_s
        self.raw_buffer = RAW_PER_BATCH_KG
        self.batch_raw_in = RAW_PER_BATCH_KG
        self.batch_flour_out = 0.0
        self.batch_bags = 0
        self._grinder_perf_samples = []

    # -- simulation step --
    def tick(self, dt_s: float):
        self.sim_time_s += dt_s
        for m in self.machines:
            m.tick_hours(dt_s)
        self.dryer.update_temp(dt_s)
        self.grinder.update_wear(dt_s, self.wear_rate)

        if self.master_running and self.state == BATCH_RUNNING:
            self._flow(dt_s)
            self._check_batch_complete()

        self._check_maintenance()

    def _flow(self, dt_s: float):
        # Washer: raw -> washed
        moved = min(self.raw_buffer, WASHER_RATE * dt_s)
        self.raw_buffer -= moved
        self.washed_buffer += moved * (1 - WASHER_LOSS)
        self.washer.level_kg = self.raw_buffer

        # Dryer: washed -> dried
        moved = min(self.washed_buffer, DRYER_RATE * dt_s)
        self.washed_buffer -= moved
        self.dried_buffer += moved * (1 - DRYER_LOSS)
        self.dryer.level_kg = self.washed_buffer

        # Grinder: dried -> flour (rate scales with blade performance)
        moved = min(self.dried_buffer, self.grinder.current_rate * dt_s)
        self.dried_buffer -= moved
        self.flour_buffer += moved * (1 - GRINDER_LOSS)
        self.grinder.level_kg = self.dried_buffer
        if moved > 0:
            self._grinder_perf_samples.append(self.grinder.performance_pct)

        # BagFiller: flour -> bags
        moved = min(self.flour_buffer, BAGFILLER_RATE * dt_s)
        self.flour_buffer += 0  # noop, clarity
        # accumulate into bag buffer
        self.bagfiller.level_kg += moved
        self.flour_buffer -= moved
        self.batch_flour_out += moved
        while self.bagfiller.level_kg >= BAG_SIZE_KG:
            self.bagfiller.level_kg -= BAG_SIZE_KG
            self.bagfiller.bags_filled += 1
            self.batch_bags += 1

    def _check_batch_complete(self):
        empty = (
            self.raw_buffer < 0.01
            and self.washed_buffer < 0.01
            and self.dried_buffer < 0.01
            and self.flour_buffer < 0.01
            and self.bagfiller.level_kg < BAG_SIZE_KG
        )
        if empty:
            avg_perf = (
                sum(self._grinder_perf_samples) / len(self._grinder_perf_samples)
                if self._grinder_perf_samples else self.grinder.performance_pct
            )
            record = {
                "batch_id": self.batch_id,
                "start_s": round(self.batch_start_s, 1),
                "end_s": round(self.sim_time_s, 1),
                "duration_s": round(self.sim_time_s - self.batch_start_s, 1),
                "raw_kg_in": round(self.batch_raw_in, 1),
                "flour_kg_out": round(self.batch_flour_out, 1),
                "bags_out": self.batch_bags,
                "avg_grinder_performance_pct": round(avg_perf, 1),
                "out_of_spec": avg_perf < SPEC_THRESHOLD_PCT,
            }
            self.batches.append(record)
            self.batches = self.batches[-20:]
            self.state = COMPLETE
            if self.master_running:
                self._begin_batch()      # continuous production for the demo

    def _check_maintenance(self):
        g = self.grinder
        if g.performance_pct < SPEC_THRESHOLD_PCT and self.maintenance_alarm is None:
            self.maintenance_alarm = {
                "asset": "Grinder",
                "severity": "warning",
                "message": (
                    "Grinder blades approaching end of life. Performance "
                    f"{g.performance_pct:.0f}% is below the {SPEC_THRESHOLD_PCT:.0f}% "
                    "spec line. Schedule a blade change before output drifts out of spec."
                ),
                "blade_wear_pct": round(g.blade_wear_pct, 1),
                "performance_pct": round(g.performance_pct, 1),
                "raised_at_s": round(self.sim_time_s, 1),
            }
