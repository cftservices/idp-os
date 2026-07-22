"""batch-engine FastAPI app — MES-laag voor de Vla Batch v2 demo.

Startup: connect DB (Mongo or in-memory) + MQTT bus (offline-safe), seed the
recipe, expose the batch/sample/report/admin endpoints under base /api/v1.

Env:
  MONGO_URL   (optional) -> Mongo backend, else in-memory
  MONGO_DB    default idp
  MQTT_HOST   default monstermq
  MQTT_PORT   default 1883
  MQTT_WAIT_S default 3.0
  AUTO_START  default 1 -> POST /batches auto-starts the batch
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from vla import model as M
from vla.batches import BatchRunner
from vla.bus import VlaBus
from vla.db import get_db, seed_recipes
from vla.equipment import EQUIPMENT_IDS, EquipmentMonitor
from vla.handling import HandlingUnitManager
from vla.opcua_control import OpcuaControl
from vla.orders import OrderManager
from vla.report import render_json, render_pdf
from vla.scan import ScanFlow, ScanRejected

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vla.app")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()

app = FastAPI(title="DairyWorks Vla Batch Engine", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE: dict = {"db": None, "bus": None, "control": None, "orders": None, "runner": None,
               "scan": None, "handling": None, "equipment": None}
API = "/api/v1"


class CreateBatch(BaseModel):
    recipe_id: str
    planned_L: float | None = None


class TakeSample(BaseModel):
    batch_id: str
    sample_type: str = "viscosity"
    operator_id: str | None = None


class AdminCommand(BaseModel):
    """05-Backend §4.3 contract: {equipment_id, cmd, params}."""
    equipment_id: str                    # "Batch" | equipment_id
    cmd: str                             # start|stop|sample|fault|clear|setpoint
    params: dict | None = None


class CreateOrder(BaseModel):
    recipe_id: str
    target_qty_L: float
    due_date: str | None = None


class CreateOrderBatch(BaseModel):
    planned_L: float | None = None
    operator_id: str | None = None


class ScanOrder(BaseModel):
    code: str
    operator_id: str | None = None


class ScanLabel(BaseModel):
    batch_id: str
    material_id: str
    lot_no: str
    operator_id: str | None = None


class CreateHu(BaseModel):
    batch_id: str
    packs_count: int
    operator_id: str | None = None


class HuAction(BaseModel):
    operator_id: str | None = None


class AckRequest(BaseModel):
    operator_id: str | None = None


class ScanWeigh(BaseModel):
    batch_id: str
    material_id: str
    qty_kg: float | None = None
    lot_no: str | None = None
    source_equipment: str = "scale-01"
    operator_id: str | None = None
    total: bool = False


class ScanReport(BaseModel):
    batch_id: str
    operator_id: str | None = None


class BookProduction(BaseModel):
    batch_id: str
    packs: int
    operator_id: str | None = None


class CipRequest(BaseModel):
    operator_id: str | None = None


def _runner() -> BatchRunner:
    runner = STATE.get("runner")
    if runner is None:
        raise HTTPException(503, "engine not initialized")
    return runner


def _orders() -> "OrderManager":
    om = STATE.get("orders")
    if om is None:
        raise HTTPException(503, "engine not initialized")
    return om


def _scan() -> "ScanFlow":
    s = STATE.get("scan")
    if s is None:
        raise HTTPException(503, "engine not initialized")
    return s


def _handling() -> "HandlingUnitManager":
    h = STATE.get("handling")
    if h is None:
        raise HTTPException(503, "engine not initialized")
    return h


def _scan_call(fn, *args, **kw):
    try:
        return fn(*args, **kw)
    except ScanRejected as e:
        code = 404 if e.reason == "unknown" else 409
        raise HTTPException(code, {"message": str(e), "reason": e.reason})
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.on_event("startup")
def _startup() -> None:
    db = get_db()
    bus = VlaBus(
        host=os.environ.get("MQTT_HOST", "monstermq"),
        port=int(os.environ.get("MQTT_PORT", 1883)),
    )
    try:
        bus.start(wait_connected_s=float(os.environ.get("MQTT_WAIT_S", 3.0)))
    except Exception as e:
        log.warning("bus start failed (%s) — continuing offline", e)
    # PRIMARY control path: direct OPC-UA to the factory (offline-safe no-op).
    control = OpcuaControl()
    seed_recipes(db)
    orders = OrderManager(db, bus)
    equipment = EquipmentMonitor(db, bus)
    runner = BatchRunner(db, bus, control=control, orders=orders, equipment=equipment)
    STATE.update({"db": db, "bus": bus, "control": control, "orders": orders,
                  "runner": runner, "scan": ScanFlow(db, runner, orders),
                  "handling": HandlingUnitManager(db), "equipment": equipment})
    log.info("batch-engine ready (db=%s, mqtt=%s, opcua=%s)",
             db.backend, bus.connected, control.url)


@app.on_event("shutdown")
def _shutdown() -> None:
    bus = STATE.get("bus")
    if bus is not None:
        bus.stop()


# ------------------------------------------------------------------ endpoints

@app.get(f"{API}/health")
def health():
    return {"status": "ok"}


@app.get(f"{API}/tags")
def live_tags():
    """Snapshot of latest UNS values (dict topic -> value) for the dashboard."""
    bus = STATE.get("bus")
    if bus is None:
        raise HTTPException(503, "engine not initialized")
    return bus.snapshot()


@app.get(f"{API}/equipment")
def equipment_snapshot():
    eq = STATE.get("equipment")
    if eq is None:
        raise HTTPException(503, "engine not initialized")
    return eq.snapshot()


@app.get(f"{API}/oee")
def equipment_oee():
    """PR-21: per-equipment OEE-light (availability x performance x quality)."""
    eq = STATE.get("equipment")
    if eq is None:
        raise HTTPException(503, "engine not initialized")
    return eq.oee()


@app.get(f"{API}/equipment/health")
def equipment_health():
    """PR-32: equipment snapshot extended with heat-up trend + open CBM alerts."""
    eq = STATE.get("equipment")
    if eq is None:
        raise HTTPException(503, "engine not initialized")
    return eq.health()


@app.post(f"{API}/equipment/{{equipment_id}}/cip")
def equipment_cip(equipment_id: str, body: CipRequest):
    """PR-29: CIP cleaning action — resets fouling counter, clears Dirty,
    resolves open fouling alerts."""
    if equipment_id not in EQUIPMENT_IDS:
        raise HTTPException(404, f"unknown equipment {equipment_id!r}")
    eq = STATE.get("equipment")
    if eq is None:
        raise HTTPException(503, "engine not initialized")
    return eq.perform_cip(equipment_id, operator_id=body.operator_id)


@app.get(f"{API}/materials")
def list_materials():
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    return db.dw_materials.find({})


@app.get(f"{API}/batches")
def list_batches():
    return _runner().list_batches()


@app.post(f"{API}/batches")
def create_batch(body: CreateBatch):
    runner = _runner()
    auto = os.environ.get("AUTO_START", "1") not in ("0", "false", "False")
    try:
        batch = runner.create_batch(body.recipe_id, body.planned_L, auto_start=auto)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"batch_id": batch["batch_id"], "state": batch["state"],
            "order_id": batch.get("order_id"),
            "dose_setpoints": {d["material_id"]: d["qty_target"]
                               for d in batch["doses"]}}


@app.get(f"{API}/batches/{{batch_id}}")
def get_batch(batch_id: str):
    batch = _runner().get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    batch["telemetry_summary"] = {
        "peak_cook_temp_C": batch.get("peak_cook_temp_C"),
        "hold_elapsed_sec": batch.get("hold_elapsed_sec"),
        "end_viscosity_cP": batch.get("end_viscosity_cP"),
        "packs_total": batch.get("packs_total", 0),
        "reject_count": batch.get("reject_count", 0),
    }
    return batch


@app.post(f"{API}/batches/{{batch_id}}/start")
def start_batch(batch_id: str):
    runner = _runner()
    if runner.get_batch(batch_id) is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    batch = runner.start_batch(batch_id)
    return {"batch_id": batch_id, "state": batch["state"]}


@app.post(f"{API}/batches/{{batch_id}}/ack-verdict")
def ack_verdict(batch_id: str, body: AckRequest):
    """Acknowledge a batch verdict (idempotent). Batch must be COMPLETE with verdict."""
    runner = _runner()
    try:
        batch = runner.ack_verdict(batch_id, operator_id=body.operator_id)
    except ValueError as e:
        code = 404 if "unknown" in str(e) else 409
        raise HTTPException(code, str(e))
    return batch


@app.post(f"{API}/orders")
def create_order(body: CreateOrder):
    try:
        return _orders().create_order(body.recipe_id, body.target_qty_L, body.due_date)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get(f"{API}/orders")
def list_orders():
    return _orders().list_orders()


@app.get(f"{API}/orders/{{order_id}}")
def get_order(order_id: str):
    order = _orders().get_order(order_id)
    if order is None:
        raise HTTPException(404, f"order {order_id} not found")
    return {**order, "progress": _orders().order_progress(order_id)}


@app.post(f"{API}/orders/{{order_id}}/batches")
def create_order_batch(order_id: str, body: CreateOrderBatch):
    runner = _runner()
    order = _orders().get_order(order_id)
    if order is None:
        raise HTTPException(404, f"order {order_id} not found")
    auto = os.environ.get("AUTO_START", "1") not in ("0", "false", "False")
    try:
        batch = runner.create_batch(order["recipe_id"], body.planned_L,
                                    auto_start=auto, order_id=order_id,
                                    operator_id=body.operator_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"batch_id": batch["batch_id"], "state": batch["state"],
            "order_id": order_id,
            "dose_setpoints": {d["material_id"]: d["qty_target"]
                               for d in batch["doses"]}}


@app.post(f"{API}/orders/{{order_id}}/close")
def close_order(order_id: str):
    try:
        return _orders().close_order(order_id)
    except ValueError as e:
        code = 404 if "unknown order" in str(e) else 409
        raise HTTPException(code, str(e))


@app.post(f"{API}/scan/order")
def scan_order(body: ScanOrder):
    return _scan_call(_scan().scan_order, body.code, body.operator_id)


@app.post(f"{API}/scan/label")
def scan_label(body: ScanLabel):
    return _scan_call(_scan().scan_label, body.batch_id, body.material_id,
                      body.lot_no, body.operator_id)


@app.post(f"{API}/scan/weigh")
def scan_weigh(body: ScanWeigh):
    return _scan_call(_scan().weigh, body.batch_id, body.material_id,
                      qty_kg=body.qty_kg, lot_no=body.lot_no,
                      source_equipment=body.source_equipment,
                      operator_id=body.operator_id, total=body.total)


@app.post(f"{API}/scan/report")
def scan_report(body: ScanReport):
    return _scan_call(_scan().scan_report, body.batch_id, body.operator_id)


@app.post(f"{API}/production")
def book_production(body: BookProduction):
    return _scan_call(_scan().book_production, body.batch_id, body.packs,
                      body.operator_id)


@app.post(f"{API}/samples/{{sample_id}}/reprint-label")
def reprint_sample_label(sample_id: str):
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    row = db.dw_samples.find_one({"sample_id": sample_id})
    if row is None:
        raise HTTPException(404, f"sample {sample_id} not found")
    db.dw_samples.update_one({"sample_id": sample_id},
                             {"$set": {"label_printed": True}})
    db.dw_batch_events.insert_one({
        "batch_id": row["batch_id"], "event_type": "sample_label_printed",
        "payload": {"sample_id": sample_id, "reprint": True},
        "ts": _iso()})
    return {"ok": True, "sample_id": sample_id}


@app.get(f"{API}/samples")
def list_samples(batch_id: str | None = Query(default=None)):
    return _runner().get_samples(batch_id)


@app.post(f"{API}/samples")
def take_sample(body: TakeSample):
    runner = _runner()
    if runner.get_batch(body.batch_id) is None:
        raise HTTPException(404, f"batch {body.batch_id} not found")
    try:
        return runner.take_sample(body.batch_id, body.sample_type, body.operator_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post(f"{API}/alarms/{{alarm_id}}/ack")
def ack_alarm(alarm_id: str, body: AckRequest):
    """Acknowledge an alarm by alarm_id."""
    runner = _runner()
    try:
        alarm = runner.ack_alarm(alarm_id, operator_id=body.operator_id)
    except ValueError as e:
        code = 404 if "unknown" in str(e) else 409
        raise HTTPException(code, str(e))
    return alarm


@app.get(f"{API}/report/{{batch_id}}")
def get_report(batch_id: str, format: str = Query(default="json")):
    runner = _runner()
    batch = runner.get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    if format == "pdf":
        pdf = render_pdf(batch)
        media = "application/pdf" if pdf[:4] == b"%PDF" else "text/plain"
        return Response(content=pdf, media_type=media, headers={
            "Content-Disposition": f'inline; filename="batch-{batch_id}.pdf"',
        })
    return JSONResponse(render_json(batch))


@app.post(f"{API}/admin/command")
def admin_command(body: AdminCommand):
    """Route a control action to the factory (PRIMARY = direct OPC-UA method;
    MQTT Command publish secondary). Contract 05-Backend §4.3."""
    control = STATE.get("control")
    bus = STATE.get("bus")
    if control is None:
        raise HTTPException(503, "engine not initialized")
    p = body.params or {}
    cmd = body.cmd.lower()

    if cmd == "start":
        recipe_id = str(p.get("recipe_id") or M.RECIPE_CHOCOLATE_VLA_1L.recipe_id)
        result = control.start_batch(recipe_id)
        if bus is not None:
            bus.start_batch(recipe_id)
    elif cmd == "stop":
        runner = _runner()
        active = next((b for b in runner.list_batches()
                       if b["state"] in ("DOSING", "COOKING", "COOLING", "FILLING")),
                      None)
        if active is not None:
            booked = runner.db.dw_production.count_documents(
                {"batch_id": active["batch_id"]})
            if booked == 0:
                raise HTTPException(
                    409, "stop refused: no production booked for active batch "
                         f"{active['batch_id']} (PR-34 stop rule)")
        result = control.stop()
        if bus is not None:
            bus.stop_batch()
    elif cmd == "sample":
        stype = str(p.get("sample_type") or "viscosity")
        if stype not in M.SAMPLE_TYPES:
            raise HTTPException(400, f"unknown sample_type {stype!r}")
        result = control.take_sample(stype)
        if bus is not None:
            bus.take_sample(stype)
    elif cmd == "fault":
        fid = str(p.get("fault_id") or "cook_undertemp")
        mag = float(p.get("magnitude", 0.5) or 0.5)
        result = control.inject_fault(fid, mag)
        if bus is not None:
            bus.inject_fault(fid, mag)
    elif cmd == "clear":
        result = control.clear_fault()
        if bus is not None:
            bus.clear_fault()
    elif cmd == "setpoint":
        target = p.get("target")
        from vla.opcua_control import SETPOINT_TARGETS
        if target not in SETPOINT_TARGETS:
            raise HTTPException(400, f"unknown setpoint target {target!r}")
        try:
            value = float(p.get("value"))
        except (TypeError, ValueError):
            raise HTTPException(400, "setpoint needs a numeric params.value")
        result = control.set_setpoint(target, value)
        if bus is not None:
            bus.set_setpoint(target, value)
    else:
        raise HTTPException(400, f"unknown cmd {body.cmd!r} "
                                 "(allowed: start|stop|sample|fault|clear|setpoint)")

    return {"accepted": True, "path": "opcua", "equipment_id": body.equipment_id,
            "cmd": cmd, "opcua": result}


@app.post(f"{API}/hu")
def create_hu(body: CreateHu):
    return _scan_call(_handling().create_hu, body.batch_id, body.packs_count,
                      body.operator_id)


@app.post(f"{API}/hu/{{hu_id}}/putaway")
def putaway_hu(hu_id: str, body: HuAction):
    return _scan_call(_handling().putaway, hu_id, body.operator_id)


@app.post(f"{API}/hu/{{hu_id}}/ship")
def ship_hu(hu_id: str, body: HuAction):
    return _scan_call(_handling().ship, hu_id, body.operator_id)


@app.get(f"{API}/hu")
def list_hus(batch_id: str | None = Query(default=None)):
    return _handling().list_hus(batch_id)
