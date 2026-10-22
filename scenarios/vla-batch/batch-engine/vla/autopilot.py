"""Demo-autopilot: keeps MES batches flowing without an operator.

The factory chains its own batches (AUTOSTART_RECIPE), but those never reach
the MES layer: no batch record, no doses, no verdict, no EBR. The screens then
show the last manual batch forever. The autopilot closes that gap by doing
what an operator would do, through the same runner calls as the API:

  1. wait until no MES batch is running and the factory reports COMPLETE/IDLE;
  2. run CIP on cook-unit-01 when the PR-29 gate says Dirty;
  3. book the batch on the current customer order (next customer once the
     order is in full);
  4. create + start the batch (live-follow does the rest).

It never bypasses a gate: CIP goes through perform_cip, the batch through
create_batch. The factory waits AUTOSTART_DELAY_S after COMPLETE before it
self-chains, so the autopilot wins the race; when the engine is down the
factory keeps producing on its own.

Retention: an unattended line makes ~700 batches a day, and list_batches and
running_hours read everything. Autopilot batches (and only those) older than
keep_hours are pruned, plus old equipment-state history.

Lines: one Autopilot per production line (L1 chocolade, L2 smaken). Each
follows only its own batches and its own factory (via the line's bus), keeps
its own customer order (dw_autopilot key "order" for L1, "order-L2" ...) and
runs a product wheel light -> dark, so a changeover only needs CIP when the
wheel wraps round (strawberry -> vanilla). When a PrepManager is given, a
batch only starts once the preparation room has a ready set for it.
The primary autopilot also drives the warehouse, the preparation tick and
retention, so those run once.

Env (read in app.py):
  DEMO_AUTOPILOT             1 -> enabled (default off)
  DEMO_AUTOPILOT_RECIPE      default chocolate-vla-1L
  DEMO_AUTOPILOT_GAP_S       seconds between batches (default 10)
  DEMO_AUTOPILOT_KEEP_HOURS  retention of autopilot batches (default 24)
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from vla import model as M
from vla.equipment import EQUIPMENT_IDS, CipRequired
from vla.logistics_sim import CUSTOMERS

log = logging.getLogger("vla.autopilot")

OPERATOR = "autopilot"
RUNNING = (M.DOSING, M.COOKING, M.COOLING, M.FILLING)
# Customer orders: size in batches (cycled) and delivery deadline. At ~2 min
# a batch an order of 12 takes ~25 min, well inside the lead time.
ORDER_BATCHES = (6, 8, 10, 12)
ORDER_LEAD_HOURS = 2.0
# A running batch nobody follows any more (engine restart) is dropped after
# this, instead of staying COOKING/COOLING in every list until keep_hours.
ORPHAN_PRUNE_S = 3600.0
# collections whose rows belong to one batch (pruned with the batch)
BATCH_COLLECTIONS = ("dw_doses", "dw_samples", "dw_batch_events", "dw_alarms",
                     "dw_handling_units", "dw_production", "dw_shipments")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# A batch is followed by a thread in the process that started it. A running
# batch from before this process has no follower any more: it is orphaned.
PROCESS_STARTED = _now()


def _followed(batch_id: str) -> bool:
    name = f"vla-follow-{batch_id}"
    return any(t.name == name and t.is_alive() for t in threading.enumerate())

class Autopilot:
    def __init__(self, db, runner, equipment=None, orders=None, bus=None,
                 recipe_id: Optional[str] = "chocolate-vla-1L", gap_s: float = 10.0,
                 keep_hours: float = 24.0, poll_s: float = 3.0,
                 prune_every_s: float = 3600.0, warehouse=None,
                 line: Optional[str] = None, recipes: Optional[list] = None,
                 prep=None, primary: bool = True):
        line_id = line or getattr(getattr(runner, "line", None), "line_id", None) \
            or (M.line_of_recipe(recipe_id) if recipe_id else M.DEFAULT_LINE)
        self.line = M.get_line(line_id)
        if recipes:
            wheel = list(recipes)
        elif recipe_id and len(self.line.recipes) <= 1:
            wheel = [recipe_id]
        else:
            # product wheel light -> dark: CIP only when it wraps round
            wheel = sorted(self.line.recipes, key=lambda r: M.RECIPE_RANK.get(r, 0))
        for rid in wheel:
            if M.get_recipe(rid) is None:
                raise ValueError(f"unknown recipe_id {rid!r}")
        recipe_id = wheel[0]
        self.wheel = wheel
        self.prep = prep
        self.primary = primary
        self.order_key = "order" if self.line.line_id == M.DEFAULT_LINE \
            else f"order-{self.line.line_id}"
        self.db = db
        self.runner = runner
        self.equipment = equipment
        self.orders = orders
        self.bus = bus
        self.recipe_id = recipe_id
        self.gap_s = float(gap_s)
        self.keep_hours = float(keep_hours)
        self.poll_s = float(poll_s)
        self.prune_every_s = float(prune_every_s)
        self.warehouse = warehouse
        self._idle_since: Optional[float] = None
        self._last_prune: Optional[float] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.status: dict = {"enabled": True, "line": self.line.line_id,
                             "recipe_id": recipe_id, "wheel": list(wheel),
                             "gap_s": self.gap_s, "keep_hours": self.keep_hours,
                             "last_action": None, "last_action_at": None,
                             "last_batch_id": None, "batches_started": 0,
                             "cip_runs": 0, "changeover_cips": 0,
                             "prep": prep is not None, "last_prep_id": None,
                             "last_error": None,
                             "warehouse": warehouse is not None,
                             "warehouse_last": None}

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        name = "vla-autopilot" if self.line.line_id == M.DEFAULT_LINE \
            else f"vla-autopilot-{self.line.line_id}"
        self._thread = threading.Thread(target=self._loop, name=name, daemon=True)
        self._thread.start()
        log.info("autopilot %s on (wheel=%s gap=%ss keep=%sh)",
                 self.line.line_id, self.wheel, self.gap_s, self.keep_hours)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as e:  # thread must not die silently
                log.exception("autopilot step failed")
                self.status["last_error"] = str(e)
            self._stop.wait(self.poll_s)

    # ----------------------------------------------------------------- step

    def _record(self, action: str) -> str:
        if action != self.status["last_action"]:
            log.info("autopilot %s: %s", self.line.line_id, action)
        self.status["last_action"] = action
        self.status["last_action_at"] = _now().isoformat()
        return action

    def _factory_state(self) -> Optional[str]:
        if self.bus is None:
            return None
        raw = self.bus.latest_value("Batch", "state")
        return None if raw is None else str(raw)

    def _is_active(self, batch: dict) -> bool:
        """Running, and not orphaned. A batch whose live-follow thread died
        (engine restart mid-batch) stays DOSING..FILLING forever. It stops
        blocking the line at once if it predates this process and nobody
        follows it, and in any case past the follow timeout."""
        if batch.get("state") not in RUNNING:
            return False
        started = batch.get("started_at")
        if not started:
            return True
        try:
            started_at = datetime.fromisoformat(started)
        except ValueError:
            return True
        if started_at < PROCESS_STARTED and not _followed(batch.get("batch_id", "")):
            return False
        age = (_now() - started_at).total_seconds()
        limit = getattr(self.runner, "FOLLOW_TIMEOUT_S", 900.0) + 60.0
        return age < limit

    def step(self, now: Optional[float] = None) -> str:
        now = time.monotonic() if now is None else now
        if self.primary and (self._last_prune is None
                             or now - self._last_prune >= self.prune_every_s):
            self._last_prune = now
            self.prune()

        if self.prep is not None and self.primary:
            try:
                self.prep.tick()
            except Exception as e:
                log.exception("autopilot: preparation tick failed")
                self.status["last_error"] = f"prep: {e}"

        if self.warehouse is not None:
            # independent of the line: pallets keep moving while a batch runs
            try:
                moved = self.warehouse.step()
                if moved:
                    self.status["warehouse_last"] = moved[-1]
            except Exception as e:
                log.exception("autopilot: warehouse step failed")
                self.status["last_error"] = f"warehouse: {e}"

        live = self.bus is not None
        if live and not getattr(self.bus, "connected", False):
            return self._record("wait_bus")

        if any(self._is_active(b) for b in self.db.dw_batches.find({})
               if M.line_of_doc(b) == self.line.line_id):
            self._idle_since = None
            return self._record("wait_batch")

        if live and self._factory_state() in RUNNING:
            # factory runs its own self-chained batch; wait for COMPLETE
            self._idle_since = None
            return self._record("wait_factory")

        if self._idle_since is None:
            self._idle_since = now
        if now - self._idle_since < self.gap_s:
            return self._record("wait_gap")

        order_id, recipe_id = self._current_order()
        self.recipe_id = recipe_id
        self.status["recipe_id"] = recipe_id

        prep_set = None
        if self.prep is not None:
            self.prep.ensure_ahead(self.line.line_id, recipe_id, order_id=order_id,
                                   operator_id=OPERATOR)
            prep_set = self.prep.ready(self.line.line_id, recipe_id)
            if prep_set is None:
                return self._record("wait_prep")

        cook = self.line.eq("cook")
        if self.equipment is not None and self.equipment.is_dirty(cook):
            try:
                self.equipment.perform_cip(cook, operator_id=OPERATOR)
            except ValueError as e:
                self.status["last_error"] = str(e)
                return self._record("cip_refused")
            self.status["cip_runs"] += 1
            return self._record("cip")

        if self.equipment is not None and \
                self.equipment.needs_changeover_cip(cook, recipe_id):
            try:
                self.equipment.perform_cip(cook, operator_id=OPERATOR,
                                           reason="productwissel")
            except ValueError as e:
                self.status["last_error"] = str(e)
                return self._record("cip_refused")
            self.status["cip_runs"] += 1
            self.status["changeover_cips"] += 1
            return self._record(f"cip productwissel -> {recipe_id}")

        try:
            batch = self.runner.create_batch(
                recipe_id, auto_start=True, order_id=order_id, operator_id=OPERATOR,
                prep_set_id=prep_set["prep_id"] if prep_set else None)
        except CipRequired:
            return self._record("cip_required")
        except ValueError as e:
            self.status["last_error"] = str(e)
            return self._record("start_refused")
        self._idle_since = None
        if prep_set is not None:
            self.prep.consume(prep_set["prep_id"], batch["batch_id"], order_id=order_id)
            self.status["last_prep_id"] = prep_set["prep_id"]
        self.status["last_batch_id"] = batch["batch_id"]
        self.status["batches_started"] += 1
        return self._record(f"started {batch['batch_id']}")

    # ---------------------------------------------------------------- order

    def _current_order(self) -> tuple[Optional[str], str]:
        """The customer order the next batch is for.

        A customer asks for N batches' worth of product with a delivery
        deadline. The autopilot keeps batching on it until it is in full
        (produced >= target), closes it then -- on time unless the line was
        slow -- and takes the next customer. This replaced one standing order
        per day without a customer: its 600 x basis_L target was never reached
        (a batch fills 4,980 packs from 5,000 L) and it was closed after its
        own deadline, so OTIF could only ever be 0 %."""
        if self.orders is None:
            return None, self.wheel[0]
        st = self.db.dw_autopilot.find_one({"key": self.order_key}) or {}
        order = self.orders.get_order(st["order_id"]) if st.get("order_id") else None
        if order is not None and order["status"] != M.ORDER_DONE:
            prog = self.orders.order_progress(order["order_id"])
            # an order without a customer is a legacy standing day order whose
            # target is never reached: close it so customer orders can start
            if order.get("customer") and order.get("recipe_id") in self.wheel and \
                    prog["produced_L"] < float(order.get("target_qty_L") or 0):
                return order["order_id"], order["recipe_id"]
            try:
                self.orders.close_order(order["order_id"])
            except ValueError as e:
                log.warning("autopilot: closing %s failed: %s", order["order_id"], e)
                if order.get("recipe_id") in self.wheel:
                    return order["order_id"], order["recipe_id"]
        seq = int(st.get("seq") or 0)
        customer = CUSTOMERS[seq % len(CUSTOMERS)]
        n_batches = ORDER_BATCHES[seq % len(ORDER_BATCHES)]
        recipe_id = self.wheel[seq % len(self.wheel)]
        recipe = M.get_recipe(recipe_id)
        # ISO with offset, like UI-created orders: a bare date compares as
        # offset-naive and broke /kpi/summary (OTIF) with a TypeError.
        due = (_now() + timedelta(hours=ORDER_LEAD_HOURS)).isoformat()
        new = self.orders.create_order(recipe_id, recipe.basis_L * n_batches,
                                       due_date=due, customer=customer)
        self.db.dw_autopilot.update_one(
            {"key": self.order_key},
            {"$set": {"order_id": new["order_id"], "seq": seq + 1}}, upsert=True)
        self.db.dw_orders.update_one({"order_id": new["order_id"]},
                                     {"$set": {"created_by": OPERATOR,
                                               "line": self.line.line_id}})
        return new["order_id"], recipe_id

    # ------------------------------------------------------------ retention

    def prune(self) -> int:
        """Drop autopilot batches older than keep_hours (and their rows), the
        DONE autopilot orders left empty, and old equipment-state history.
        Manual batches are never touched. Returns the number of batches."""
        cutoff = (_now() - timedelta(hours=self.keep_hours)).isoformat()
        orphan_cutoff = (_now() - timedelta(seconds=ORPHAN_PRUNE_S)).isoformat()
        n = 0
        for b in self.db.dw_batches.find({"created_by": OPERATOR}):
            if self._is_active(b):
                continue
            orphan = b.get("state") in RUNNING and \
                (b.get("started_at") or b.get("created_at") or "") < orphan_cutoff
            if not orphan and (b.get("created_at") or "") >= cutoff:
                continue
            for coll in BATCH_COLLECTIONS:
                self.db.collection(coll).delete_many({"batch_id": b["batch_id"]})
            self.db.dw_batches.delete_many({"batch_id": b["batch_id"]})
            n += 1
        for o in self.db.dw_orders.find({"created_by": OPERATOR}):
            if o.get("status") == M.ORDER_DONE and \
                    self.db.dw_batches.count_documents({"order_id": o["order_id"]}) == 0:
                self.db.dw_orders.delete_many({"order_id": o["order_id"]})
                self.db.dw_batch_events.delete_many({"order_id": o["order_id"]})
        for eq in EQUIPMENT_IDS:
            self.db.dw_equipment_state.delete_older_than("ts", cutoff,
                                                         {"equipment_id": eq})
        self.db.dw_batch_events.delete_older_than(
            "ts", cutoff, {"batch_id": None, "event_type": "cip_performed"})
        if self.prep is not None:
            self.prep.prune(self.keep_hours)
        if n:
            log.info("autopilot: pruned %d batches older than %s", n, cutoff)
        return n
