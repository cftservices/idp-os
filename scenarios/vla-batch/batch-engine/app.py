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
  DEMO_AUTOPILOT default 0 -> 1 keeps batches flowing unattended
                 (see vla/autopilot.py for its DEMO_AUTOPILOT_* settings;
                 DEMO_AUTOPILOT_WAREHOUSE=0 switches off the pallet flow
                 of vla/warehouse.py)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from vla import alarms as A, inventory, kpi as K, line as L, model as M
from vla.autopilot import OPERATOR as AUTOPILOT_OPERATOR, Autopilot
from vla.batches import BatchRunner
from vla.bus import VlaBus
from vla.db import get_db, seed_recipes
from vla.equipment import EQUIPMENT_IDS, CipRequired, EquipmentMonitor
from vla.handling import HandlingUnitManager
from vla.opcua_control import OpcuaControl
from vla.park_control import ParkControl
from vla.orders import OrderManager
from vla.period_reports import (assemble_equipment_report,
                                assemble_period_report, render_equipment_pdf,
                                render_period_pdf)
from vla.report import render_json, render_pdf
from vla.scan import ScanFlow, ScanRejected
from vla.warehouse import WarehouseSim

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


class ShelveRequest(BaseModel):
    # Reden en einddatum zijn verplicht: een parkering zonder beide laat een
    # alarm stil verdwijnen.
    reason: str
    until: str
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


class GoodsReceipt(BaseModel):
    qty: float
    lot_no: str | None = None
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
    except CipRequired as e:
        raise HTTPException(400, e.detail)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _refused(e: ValueError) -> HTTPException:
    """400 for a refused write. Gate errors (CipRequired) carry a structured
    detail with the reason + the action that clears the block; every other
    ValueError stays a plain-string detail."""
    return HTTPException(400, getattr(e, "detail", str(e)))


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
    # Lijn Vla-B. Offline-veilig: zonder park-model blijft de catalogus leeg en
    # geven de routes een nette 503 in plaats van een stacktrace.
    park = ParkControl(
        model_dir=os.environ.get("PARK_MODEL_DIR", "/model"),
        mqtt_publish=(lambda t, p: bus.client.publish(t, p, qos=0))
        if getattr(bus, "client", None) else None)
    seed_recipes(db)
    orders = OrderManager(db, bus)
    equipment = EquipmentMonitor(db, bus)
    runner = BatchRunner(db, bus, control=control, orders=orders, equipment=equipment)
    STATE.update({"db": db, "bus": bus, "control": control, "park": park,
                  "orders": orders,
                  "runner": runner, "scan": ScanFlow(db, runner, orders),
                  "handling": HandlingUnitManager(db), "equipment": equipment})
    if os.environ.get("DEMO_AUTOPILOT", "0").strip().lower() in ("1", "true", "yes", "on"):
        try:
            warehouse = None
            if os.environ.get("DEMO_AUTOPILOT_WAREHOUSE", "1").strip().lower() \
                    in ("1", "true", "yes", "on"):
                warehouse = WarehouseSim(db, STATE["handling"], AUTOPILOT_OPERATOR)
            pilot = Autopilot(
                db, runner, equipment=equipment, orders=orders, bus=bus,
                recipe_id=os.environ.get("DEMO_AUTOPILOT_RECIPE", "chocolate-vla-1L"),
                gap_s=float(os.environ.get("DEMO_AUTOPILOT_GAP_S", "10")),
                keep_hours=float(os.environ.get("DEMO_AUTOPILOT_KEEP_HOURS", "24")),
                warehouse=warehouse)
            pilot.start()
            STATE["autopilot"] = pilot
        except ValueError as e:
            log.error("autopilot not started: %s", e)
    log.info("batch-engine ready (db=%s, mqtt=%s, opcua=%s)",
             db.backend, bus.connected, control.url)


@app.on_event("shutdown")
def _shutdown() -> None:
    pilot = STATE.get("autopilot")
    if pilot is not None:
        pilot.stop()
    bus = STATE.get("bus")
    if bus is not None:
        bus.stop()


# ------------------------------------------------------------------ endpoints

@app.get(f"{API}/health")
def health():
    return {"status": "ok"}


@app.get(f"{API}/autopilot")
def autopilot_status():
    """Demo-autopilot status: off, or what it last did."""
    pilot = STATE.get("autopilot")
    return dict(pilot.status) if pilot is not None else {"enabled": False}


@app.get(f"{API}/tags")
def get_tags(verbose: int = Query(default=0)):
    """Laatste UNS-snapshot.

    Met ?verbose=1 komt per topic {value, ts, unit, quality, retained, age_s}.
    De platte vorm gooit precies weg wat nodig is om een VEROUDERDE waarde te
    herkennen, en stale is een eerste-klas toestand op de operatorschermen."""
    bus = STATE.get("bus")
    if bus is None:
        raise HTTPException(503, "engine not initialized")
    return bus.snapshot_verbose() if verbose else bus.snapshot()


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
    try:
        return eq.perform_cip(equipment_id, operator_id=body.operator_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get(f"{API}/materials")
def list_materials():
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    return db.dw_materials.find({})


@app.post(f"{API}/materials/{{material_id}}/receipt")
def material_receipt(material_id: str, body: GoodsReceipt):
    """Goods receipt — book delivered raw material into stock.

    Consumption is the only stock movement the demo had, so ingredients went
    negative once more was dosed than the seed held. This is the way back up.
    """
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    try:
        after = inventory.receive(db, _runner()._event, material_id,
                                  body.qty, body.lot_no, body.operator_id)
    except ValueError as e:
        raise _refused(e)
    return {"material_id": material_id, "received": body.qty, "stock_qty": after}


@app.get(f"{API}/inventory")
def inventory_overview(order_id: str | None = Query(default=None)):
    # NOTE: not named `inventory` — that would shadow the `vla.inventory` module
    # import at module level and break material_receipt() above.
    """Stock + consumption + production per material.

    Aggregated from dw_doses (what went in) and dw_production (what came out),
    joined onto the dw_materials master for stock_qty / reorder_level. With
    ?order_id= the consumption is limited to that order's batches.
    """
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")

    scope: set | None = None
    if order_id:
        scope = {b["batch_id"] for b in db.dw_batches.find({"order_id": order_id})}

    consumed: dict[str, float] = {}
    dose_batches: dict[str, set] = {}
    for d in db.dw_doses.find({}):
        if scope is not None and d.get("batch_id") not in scope:
            continue
        mid = d.get("material_id")
        consumed[mid] = consumed.get(mid, 0.0) + float(d.get("qty_actual") or 0)
        dose_batches.setdefault(mid, set()).add(d.get("batch_id"))

    produced_packs = 0
    produced_L = 0.0
    for p in db.dw_production.find({}):
        if scope is not None and p.get("batch_id") not in scope:
            continue
        produced_packs += p.get("packs") or 0
        produced_L += (p.get("packs") or 0) * float(p.get("pack_size_L") or 1)

    rows = []
    for m in db.dw_materials.find({}):
        mid = m.get("material_id")
        is_fg = mid == M.FINISHED_GOOD_ID
        stock = float(m.get("stock_qty") or 0)
        reorder = float(m.get("reorder_level") or 0)
        rows.append({
            "material_id": mid,
            "name": m.get("name"),
            "uom": m.get("uom"),
            "category": m.get("category"),
            "stock_qty": stock,
            "reorder_level": reorder,
            "below_reorder": bool(reorder) and stock < reorder,
            "stock_pct": round(100.0 * stock / reorder, 1) if reorder else None,
            "is_finished_good": is_fg,
            "consumed_total": round(consumed.get(mid, 0.0), 3),
            "batches_count": len(dose_batches.get(mid, ())),
            "produced_total": produced_packs if is_fg else 0,
            "produced_L": round(produced_L, 1) if is_fg else 0.0,
        })
    rows.sort(key=lambda r: (r["is_finished_good"], -r["consumed_total"]))
    return {"order_id": order_id, "materials": rows,
            "produced_packs": produced_packs, "produced_L": round(produced_L, 1)}


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
        raise _refused(e)
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
    # full detail: order + progress + its batches + ingredient roll-up, so the
    # dashboard's order panel needs exactly one request
    detail = _orders().order_detail(order_id)
    if detail is None:
        raise HTTPException(404, f"order {order_id} not found")
    return detail


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
        raise _refused(e)
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


@app.get(f"{API}/alarms")
def list_alarms(since: str | None = Query(default=None),
                priority: str | None = Query(default=None),
                state: str | None = Query(default=None),
                limit: int = Query(default=200)):
    """Alarmen lezen. Bestond niet: er was alleen een ack-route, waardoor een
    alarmscherm onmogelijk was."""
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    return A.list_alarms(db, since=since, priority=priority, state=state, limit=limit)


@app.post(f"{API}/alarms/{{alarm_id}}/shelve")
def shelve_alarm(alarm_id: str, body: ShelveRequest):
    """Parkeer een alarm tot een tijdstip, met verplichte reden. Zonder beide is
    het een alarm dat stil verdwijnt, en dat is precies wat alarmmanagement moet
    voorkomen."""
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    try:
        return A.shelve(db, alarm_id, body.reason, body.until, body.operator_id)
    except ValueError as e:
        raise HTTPException(404 if "unknown" in str(e) else 400, str(e))


@app.get(f"{API}/line/live")
def line_live():
    """De L1-payload: fase, doseringen, vulling en kwaliteit, kant-en-klaar.
    Alles wat de oude client zelf afleidde (lijnsnelheid, packs-target,
    totaalcharge, pallets) komt hiervandaan."""
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    return L.live(db, K.load_factory_model())


@app.get(f"{API}/kpi/summary")
def get_kpi_summary(window: str = Query(default="week"),
                    compare: bool = Query(default=True)):
    """PR-36/38: de KPI-set over een venster, met norm-status, delta tegen het
    vorige venster en het verliesblok. Scherm 11 en 12 lezen hieruit.

    Bewust een `window` in plaats van `days`: een dienst is niet in dagen uit
    te drukken en week-op-week is geen 7 dagen vanaf nu. De respons stuurt
    `from`, `to` en de tijdzone mee, zodat het PDF hetzelfde venster kan
    aantonen als het scherm."""
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    try:
        return JSONResponse(K.summary(db, window=window, compare=compare))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get(f"{API}/report/period")
def get_period_report(days: int = Query(default=7),
                      window: str | None = Query(default=None),
                      format: str = Query(default="json")):
    """PR-22: plant-wide management report.

    Two ways to pick the period, and `window` wins: `window=shift|day|week|month`
    uses exactly the same bounds as GET /kpi/summary, `days=N` is the older
    rolling window that the report centre still offers.

    The window parameter exists because the management screen let you pick a
    period and then asked for `days=7` regardless — see assemble_period_report.

    NOTE: this literal route MUST stay ahead of GET /report/{batch_id}
    below, otherwise FastAPI matches "period" as a batch_id path param."""
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    try:
        rep = assemble_period_report(db, days=days, window=window)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if format == "pdf":
        pdf = render_period_pdf(rep)
        media = "application/pdf" if pdf[:4] == b"%PDF" else "text/plain"
        return Response(content=pdf, media_type=media, headers={
            "Content-Disposition": 'inline; filename="period-report.pdf"',
        })
    return JSONResponse(rep)


@app.get(f"{API}/report/equipment/{{equipment_id}}")
def get_equipment_report(equipment_id: str, days: int = Query(default=30),
                         format: str = Query(default="json")):
    """PR-33: per-equipment maintenance report over the last `days` days.
    NOTE: this literal-prefix route MUST stay ahead of GET /report/{batch_id}
    below, otherwise FastAPI matches "equipment" as a batch_id path param."""
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    try:
        rep = assemble_equipment_report(db, equipment_id, days)
    except ValueError as e:
        raise HTTPException(404, str(e))
    if format == "pdf":
        pdf = render_equipment_pdf(rep)
        media = "application/pdf" if pdf[:4] == b"%PDF" else "text/plain"
        return Response(content=pdf, media_type=media, headers={
            "Content-Disposition":
                f'inline; filename="equipment-report-{equipment_id}.pdf"',
        })
    return JSONResponse(rep)


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


# ── STORINGEN, lijn Vla-B ────────────────────────────────────────────────────
#
# Het convergentiepunt van drie van de vier ingangen: het UI-paneel en de
# scenario-runner komen hier binnen, en MQTT gaat er met opzet omheen omdat een
# MQTT-client moet werken zonder dat deze laag draait. Alle vier landen op
# dezelfde FaultInjector in de machine.

def _park():
    p = STATE.get("park")
    if p is None:
        raise HTTPException(status_code=503, detail={
            "message": "park-control niet beschikbaar (draait het park?)",
            "reason": "park_unavailable"})
    return p


@app.get(f"{API}/park/faults")
def park_faults():
    """De storingscatalogus plus wat er nu actief staat.

    De catalogus is gegenereerd uit het FAULTS-attribuut van de physics-klassen,
    dus hij kan geen storing tonen die de fysica niet implementeert. Een knop
    die niets doet is erger dan geen knop.
    """
    return _park().catalogue()


@app.post(f"{API}/park/{{equipment_id}}/fault")
def park_inject(equipment_id: str, body: dict = Body(...)):
    fault_id = str(body.get("fault") or body.get("fault_id") or "").strip()
    if not fault_id:
        raise HTTPException(status_code=400, detail={
            "message": "veld 'fault' ontbreekt", "reason": "missing_fault"})
    try:
        magnitude = float(body.get("magnitude", 1.0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail={
            "message": "magnitude moet een getal tussen 0 en 1 zijn",
            "reason": "bad_magnitude"})
    res = _park().inject(equipment_id, fault_id, magnitude)
    if not res.get("ok") and res.get("error"):
        raise HTTPException(status_code=400, detail=res)
    return res


@app.delete(f"{API}/park/{{equipment_id}}/fault/{{fault_id}}")
def park_clear(equipment_id: str, fault_id: str):
    return _park().clear(equipment_id, None if fault_id in ("all", "*") else fault_id)


@app.post(f"{API}/park/clear-all")
def park_clear_all():
    """Alles uit. De knop die je na een demo wilt hebben."""
    return _park().clear_all()
