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
                 DEMO_AUTOPILOT_LOGISTICS=0 switches off the simulated
                 shipments/forklifts/tanker (GET /api/v1/logistics)
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
from vla.equipment import CipRequired, EquipmentMonitor
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
from vla.logistics_sim import LogisticsSim
from vla.trace import order_trace
from vla import forklift_trace
from vla.preparation import PrepManager
from vla import datalayer as DL, dataops as DO, mcp as MCP, rca as RCA
from vla.cmms import TECHNICIANS, CmmsConnector, reliability as cmms_reliability
from vla.losses import losses_per_equipment

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
               "scan": None, "handling": None, "equipment": None,
               # per productielijn (L1 chocolade, L2 smaken)
               "runners": {}, "buses": {}, "controls": {}, "autopilots": {},
               "prep": None}
API = "/api/v1"


class CreateBatch(BaseModel):
    recipe_id: str
    planned_L: float | None = None


class CreatePrep(BaseModel):
    recipe_id: str
    order_id: str | None = None
    operator_id: str | None = None


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


def _runner(line: str | None = None) -> BatchRunner:
    runner = (STATE.get("runners") or {}).get(line or M.DEFAULT_LINE) \
        or STATE.get("runner")
    if runner is None:
        raise HTTPException(503, "engine not initialized")
    return runner


def _runner_for_recipe(recipe_id: str) -> BatchRunner:
    return _runner(M.line_of_recipe(recipe_id))


def _runner_for_batch(batch_id: str) -> BatchRunner:
    db = STATE.get("db")
    doc = db.dw_batches.find_one({"batch_id": batch_id}) if db is not None else None
    return _runner(M.line_of_doc(doc))


def _check_line(line: str | None) -> str | None:
    if line is not None and line not in M.LINES:
        raise HTTPException(400, f"unknown line {line!r} (allowed: {', '.join(M.LINES)})")
    return line


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
    # Eén bus en één OPC-UA-pad per productielijn. L1 houdt client-id en root
    # van vroeger, zodat historian, Grafana en archief niets merken.
    buses: dict = {}
    controls: dict = {}
    for lid, ln in M.LINES.items():
        b = VlaBus(
            host=os.environ.get("MQTT_HOST", "monstermq"),
            port=int(os.environ.get("MQTT_PORT", 1883)),
            client_id="vla-batch-engine" if lid == M.DEFAULT_LINE
            else f"vla-batch-engine-{lid.lower()}",
            uns_root=ln.uns_root,
        )
        try:
            b.start(wait_connected_s=float(os.environ.get("MQTT_WAIT_S", 3.0)))
        except Exception as e:
            log.warning("bus %s start failed (%s) — continuing offline", lid, e)
        buses[lid] = b
        # PRIMARY control path: direct OPC-UA to the factory (offline-safe no-op).
        controls[lid] = OpcuaControl(url=os.environ.get(ln.opcua_env, ln.opcua_default))
    bus = buses[M.DEFAULT_LINE]
    control = controls[M.DEFAULT_LINE]
    # Lijn Vla-B. Offline-veilig: zonder park-model blijft de catalogus leeg en
    # geven de routes een nette 503 in plaats van een stacktrace.
    park = ParkControl(
        model_dir=os.environ.get("PARK_MODEL_DIR", "/model"),
        mqtt_publish=(lambda t, p: bus.client.publish(t, p, qos=0))
        if getattr(bus, "client", None) else None)
    seed_recipes(db)
    # Track & trace reads dw_batch_events by batch, order, pallet and truck;
    # without indexes each trace is a full scan over ~50k events.
    try:
        for keys in (["batch_id"], ["order_id"], ["payload.hu_id"],
                     ["event_type", "payload.forklift_id"]):
            db.dw_batch_events.create_index([(k, 1) for k in keys])
    except Exception as e:  # pragma: no cover - offline / mock db
        log.warning("index creation skipped (%s)", e)
    orders = OrderManager(db, bus)
    equipment = EquipmentMonitor(db, bus, buses=buses)
    runners = {lid: BatchRunner(db, buses[lid], control=controls[lid], orders=orders,
                                equipment=equipment, line=lid)
               for lid in M.LINES}
    runner = runners[M.DEFAULT_LINE]
    prep = PrepManager(db, bus, events=runner._event)
    STATE.update({"db": db, "bus": bus, "control": control, "park": park,
                  "orders": orders, "runners": runners, "buses": buses,
                  "controls": controls, "prep": prep,
                  "runner": runner, "scan": ScanFlow(db, runner, orders),
                  "handling": HandlingUnitManager(db), "equipment": equipment})
    # Onderhoud aan de datalaag: werkorders uit storingen en CBM-alerts.
    breakdowns = os.environ.get("DEMO_BREAKDOWNS", "0").strip().lower() \
        in ("1", "true", "yes", "on")
    cmms = CmmsConnector(
        db, equipment, buses=buses, events=runner._event,
        auto_breakdowns=breakdowns,
        mean_breakdown_interval_s=float(os.environ.get(
            "DEMO_BREAKDOWN_INTERVAL_S", "2700")))
    if os.environ.get("CMMS_ENABLED", "1").strip().lower() in ("1", "true", "yes", "on"):
        cmms.start(poll_s=float(os.environ.get("CMMS_POLL_S", "3")))
        STATE["cmms"] = cmms
    STATE["mcp"] = _build_mcp()
    if os.environ.get("DEMO_AUTOPILOT", "0").strip().lower() in ("1", "true", "yes", "on"):
        try:
            warehouse = None
            if os.environ.get("DEMO_AUTOPILOT_WAREHOUSE", "1").strip().lower() \
                    in ("1", "true", "yes", "on"):
                warehouse = WarehouseSim(db, STATE["handling"], AUTOPILOT_OPERATOR)
                if os.environ.get("DEMO_AUTOPILOT_LOGISTICS", "1").strip().lower() \
                        in ("1", "true", "yes", "on"):
                    warehouse.logistics = LogisticsSim(db, AUTOPILOT_OPERATOR,
                                                       events=runner._event)
                    STATE["logistics"] = warehouse.logistics
            use_prep = os.environ.get("DEMO_AUTOPILOT_PREP", "1").strip().lower() \
                in ("1", "true", "yes", "on")
            gap = float(os.environ.get("DEMO_AUTOPILOT_GAP_S", "10"))
            keep = float(os.environ.get("DEMO_AUTOPILOT_KEEP_HOURS", "24"))
            pilots = {}
            for lid in M.LINES:
                primary = lid == M.DEFAULT_LINE
                pilots[lid] = Autopilot(
                    db, runners[lid], equipment=equipment, orders=orders,
                    bus=buses[lid], line=lid,
                    recipe_id=os.environ.get("DEMO_AUTOPILOT_RECIPE", "chocolate-vla-1L")
                    if primary else None,
                    gap_s=gap, keep_hours=keep,
                    warehouse=warehouse if primary else None,
                    prep=prep if use_prep else None, primary=primary)
            for p_ in pilots.values():
                p_.start()
            STATE["autopilots"] = pilots
            STATE["autopilot"] = pilots[M.DEFAULT_LINE]
        except ValueError as e:
            log.error("autopilot not started: %s", e)
    log.info("batch-engine ready (db=%s, mqtt=%s, opcua=%s)",
             db.backend, bus.connected, control.url)


@app.on_event("shutdown")
def _shutdown() -> None:
    if STATE.get("cmms") is not None:
        STATE["cmms"].stop()
    for pilot in (STATE.get("autopilots") or {}).values():
        pilot.stop()
    if STATE.get("autopilot") is not None:
        STATE["autopilot"].stop()
    for b in (STATE.get("buses") or {}).values() or [STATE.get("bus")]:
        if b is not None:
            b.stop()


# ------------------------------------------------------------------ endpoints

@app.get(f"{API}/health")
def health():
    return {"status": "ok"}


@app.get(f"{API}/autopilot")
def autopilot_status():
    """Demo-autopilot status: off, or what it last did."""
    pilot = STATE.get("autopilot")
    if pilot is None:
        return {"enabled": False}
    out = dict(pilot.status)
    out["lines"] = {lid: dict(p.status)
                    for lid, p in (STATE.get("autopilots") or {}).items()}
    return out


@app.get(f"{API}/lines")
def list_lines():
    """De productielijnen: hal, recepten, equipment en wat de autopilot doet."""
    pilots = STATE.get("autopilots") or {}
    out = []
    for lid, ln in M.LINES.items():
        st = pilots[lid].status if lid in pilots else None
        out.append({"line_id": lid, "name": ln.name, "hall": ln.hall,
                    "uns_root": ln.uns_root,
                    "recipes": [{"recipe_id": r,
                                 "product_name": M.get_recipe(r).product_name,
                                 "fg_material_id": M.fg_of_recipe(r),
                                 "flavour": M.flavour_of(r),
                                 "rank": M.RECIPE_RANK.get(r, 0)}
                                for r in ln.recipes],
                    "equipment": dict(ln.equipment),
                    "autopilot": {"recipe_id": st.get("recipe_id"),
                                  "last_action": st.get("last_action"),
                                  "changeover_cips": st.get("changeover_cips")}
                    if st else None})
    return out


@app.get(f"{API}/prep-orders")
def list_prep_orders(line: str | None = Query(default=None),
                     state: str | None = Query(default=None),
                     limit: int = Query(default=100)):
    """Voorbereiding: afweeg- en premixsets per batch (beide lijnen)."""
    prep = STATE.get("prep")
    if prep is None:
        raise HTTPException(503, "engine not initialized")
    return {"sets": prep.list(line=_check_line(line), state=state, limit=limit),
            "stations": prep.stations()}


@app.post(f"{API}/prep-orders")
def create_prep_order(body: CreatePrep):
    prep = STATE.get("prep")
    if prep is None:
        raise HTTPException(503, "engine not initialized")
    try:
        return prep.plan(M.line_of_recipe(body.recipe_id), body.recipe_id,
                         order_id=body.order_id, operator_id=body.operator_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get(f"{API}/logistics")
def logistics_overview():
    """Shipments, forklifts and inbound milk for the 3D twin (source: demo-sim).

    Only with the autopilot warehouse flow on; otherwise an empty, labelled
    answer so the twin can say "no logistics data" instead of failing."""
    sim = STATE.get("logistics")
    if sim is None:
        return {"source": None, "shipments": [], "forklifts": [], "inbound": None}
    return sim.snapshot()


def _db():
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    return db


@app.get(f"{API}/forklifts/{{forklift_id}}/history")
def forklift_history(forklift_id: str, limit: int = Query(default=50, ge=0, le=500)):
    """Which orders (and pallets) this forklift handled, newest first."""
    db = _db()
    sim = STATE.get("logistics")
    known = {f.id for f in sim.forklifts.values()} if sim is not None else set()
    if known and forklift_id not in known:
        raise HTTPException(404, f"unknown forklift: {forklift_id}")
    return forklift_trace.forklift_history(db, forklift_id, limit)


@app.get(f"{API}/tags")
def get_tags(verbose: int = Query(default=0)):
    """Laatste UNS-snapshot.

    Met ?verbose=1 komt per topic {value, ts, unit, quality, retained, age_s}.
    De platte vorm gooit precies weg wat nodig is om een VEROUDERDE waarde te
    herkennen, en stale is een eerste-klas toestand op de operatorschermen."""
    buses = list((STATE.get("buses") or {}).values()) or [STATE.get("bus")]
    if buses[0] is None:
        raise HTTPException(503, "engine not initialized")
    out: dict = {}
    for b in buses:
        out.update(b.snapshot_verbose() if verbose else b.snapshot())
    return out


@app.get(f"{API}/equipment")
def equipment_snapshot(line: str | None = Query(default=None)):
    eq = STATE.get("equipment")
    if eq is None:
        raise HTTPException(503, "engine not initialized")
    return eq.snapshot(line=_check_line(line))


@app.get(f"{API}/oee")
def equipment_oee(line: str | None = Query(default=None)):
    """PR-21: per-equipment OEE-light (availability x performance x quality)."""
    eq = STATE.get("equipment")
    if eq is None:
        raise HTTPException(503, "engine not initialized")
    return eq.oee(line=_check_line(line))


@app.get(f"{API}/equipment/health")
def equipment_health(line: str | None = Query(default=None)):
    """PR-32: equipment snapshot extended with heat-up trend + open CBM alerts."""
    eq = STATE.get("equipment")
    if eq is None:
        raise HTTPException(503, "engine not initialized")
    return eq.health(line=_check_line(line))


@app.post(f"{API}/equipment/{{equipment_id}}/cip")
def equipment_cip(equipment_id: str, body: CipRequest):
    """PR-29: CIP cleaning action — resets fouling counter, clears Dirty,
    resolves open fouling alerts."""
    if equipment_id not in M.ALL_EQUIPMENT_IDS:
        raise HTTPException(404, f"unknown equipment {equipment_id!r}")
    eq = STATE.get("equipment")
    if eq is None:
        raise HTTPException(503, "engine not initialized")
    try:
        return eq.perform_cip(equipment_id, operator_id=body.operator_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


# ------------------------------------------------------------------ datalaag
# Grip op je fabriek via de datalaag: wat zit erin, hoe goed is het, wat kost
# het, en wat doet onderhoud ermee. Alles hieronder leest; de enige schrijvende
# routes zijn demo-knoppen (storing, sensoruitval) die de video nodig heeft.

def _extra() -> dict:
    return {"logistics": STATE.get("logistics") is not None,
            "cmms": STATE.get("cmms") is not None,
            "agent": STATE.get("mcp") is not None}


def _buses() -> dict:
    buses = STATE.get("buses") or {}
    if not buses:
        raise HTTPException(503, "engine not initialized")
    return buses


@app.get(f"{API}/datalayer/map")
def datalayer_map():
    """Bronnen -> datalaag -> opslag en afnemers, met live status per schakel."""
    return DL.layer_map(_buses(), STATE.get("db"), _extra())


@app.get(f"{API}/datalayer/context/{{equipment_id}}")
def datalayer_context(equipment_id: str):
    """Eén machine zoals de datalaag hem kent: ISA-95-pad, topics met kwaliteit
    en de ruwe OPC-UA-bron erachter, plus open alerts en werkorders."""
    if equipment_id not in M.ALL_EQUIPMENT_IDS:
        raise HTTPException(404, f"unknown equipment {equipment_id!r}")
    return DL.context(equipment_id, _buses(), STATE.get("db"),
                      STATE.get("equipment"), STATE.get("cmms"))


@app.get(f"{API}/datalayer/quality")
def datalayer_quality():
    return DL.quality(_buses())


@app.post(f"{API}/datalayer/dropout/{{equipment_id}}")
def datalayer_dropout(equipment_id: str, seconds: float = Query(default=120.0)):
    """Demo: simuleer sensoruitval. De sensorwaarden van deze machine melden
    BAD tot de tijd om is; de fabriek zelf merkt er niets van."""
    try:
        return DL.set_dropout(equipment_id, seconds)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.delete(f"{API}/datalayer/dropout/{{equipment_id}}")
def datalayer_dropout_clear(equipment_id: str):
    return DL.clear_dropout(None if equipment_id == "all" else equipment_id)


def _cmms():
    c = STATE.get("cmms")
    if c is None:
        raise HTTPException(503, "cmms not enabled")
    return c


@app.get(f"{API}/cmms/work-orders")
def cmms_work_orders(line: str | None = Query(default=None),
                     status: str | None = Query(default=None),
                     equipment_id: str | None = Query(default=None),
                     open_only: bool = Query(default=False),
                     limit: int = Query(default=100, ge=1, le=1000)):
    c = _cmms()
    return {"adapter": c.adapter.name, "status": c.status,
            "technicians": TECHNICIANS,
            "work_orders": c.list(line=_check_line(line), status=status,
                                  equipment_id=equipment_id, open_only=open_only,
                                  limit=limit)}


class BreakdownRequest(BaseModel):
    cause: str | None = None
    repair_s: float | None = None


@app.post(f"{API}/equipment/{{equipment_id}}/breakdown")
def equipment_breakdown(equipment_id: str, body: BreakdownRequest | None = None):
    """Demo: een storing. De machine gaat op Down, er ontstaat een werkorder,
    en pas de gereedmelding geeft hem weer vrij."""
    if equipment_id not in M.ALL_EQUIPMENT_IDS:
        raise HTTPException(404, f"unknown equipment {equipment_id!r}")
    body = body or BreakdownRequest()
    repair = None if body.repair_s is None else max(30.0, min(body.repair_s, 1800.0))
    return _cmms().report_breakdown(equipment_id, cause=body.cause, repair_s=repair)


@app.get(f"{API}/reliability")
def get_reliability(line: str | None = Query(default=None),
                    hours: float = Query(default=24.0, gt=0, le=24 * 31)):
    """ISO 22400-tijden (APT/ADET/idle), beschikbaarheid, MTBF en MTTR per machine."""
    cost = K.load_cost_model() or {}
    return cmms_reliability(_db(), STATE.get("equipment"), line=_check_line(line),
                            hours=hours,
                            cost_per_downtime_hour=cost.get("cost_per_downtime_hour"))


@app.get(f"{API}/losses/equipment")
def get_losses_equipment(line: str | None = Query(default=None),
                         hours: float = Query(default=24.0, gt=0, le=24 * 31)):
    """Verlies in euro's per machine, met dezelfde kostenparameters als de KPI's."""
    start, end = DL.recent_window(hours)
    return losses_per_equipment(_db(), STATE.get("equipment"), start, end,
                                K.load_cost_model(), line=_check_line(line))


@app.get(f"{API}/alarms/{{alarm_id}}/cause")
def get_alarm_cause(alarm_id: str, ai: int = Query(default=0)):
    """Van alarm naar oorzaak: gecorreleerde signalen uit de datalaag en een
    afgeleide keten. `?ai=1` laat het lokale model de keten samenvatten."""
    out = RCA.cause(_db(), alarm_id, cmms=STATE.get("cmms"),
                    cost_model=K.load_cost_model(), ai=bool(ai),
                    ollama_url=os.environ.get("OLLAMA_URL"),
                    ai_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"))
    if out is None:
        raise HTTPException(404, f"unknown alarm {alarm_id!r}")
    return out


def _build_mcp() -> MCP.ToolRegistry:
    reg = MCP.ToolRegistry()
    eq_schema = {"type": "object", "properties": {
        "equipment_id": {"type": "string", "enum": list(M.ALL_EQUIPMENT_IDS)}},
        "required": ["equipment_id"]}
    hours_schema = {"type": "object", "properties": {
        "line": {"type": "string", "enum": list(M.LINES)},
        "hours": {"type": "number", "default": 24}}}

    def _ctx(a):
        if a.get("equipment_id") not in M.ALL_EQUIPMENT_IDS:
            raise ValueError("onbekende equipment_id")
        return DL.context(a["equipment_id"], STATE.get("buses") or {},
                          STATE.get("db"), STATE.get("equipment"), STATE.get("cmms"))

    def _losses(a):
        start, end = DL.recent_window(float(a.get("hours") or 24))
        return losses_per_equipment(STATE["db"], STATE.get("equipment"), start, end,
                                    K.load_cost_model(), line=a.get("line"))

    def _wos(a):
        c = STATE.get("cmms")
        return {"work_orders": c.list(line=a.get("line"),
                                      equipment_id=a.get("equipment_id"),
                                      open_only=bool(a.get("open_only")), limit=50)
                if c else []}

    def _rel(a):
        cost = K.load_cost_model() or {}
        return cmms_reliability(STATE["db"], STATE.get("equipment"),
                                line=a.get("line"), hours=float(a.get("hours") or 24),
                                cost_per_downtime_hour=cost.get("cost_per_downtime_hour"))

    reg.register("get_context", "Context van één machine: ISA-95-pad, live topics "
                 "met kwaliteit, ruwe OPC-UA-bron, open alerts en werkorders.",
                 eq_schema, _ctx)
    reg.register("get_losses", "Verlies in euro's per machine over de laatste uren.",
                 hours_schema, _losses)
    reg.register("list_work_orders", "Werkorders uit het onderhoudssysteem.",
                 {"type": "object", "properties": {
                     "line": {"type": "string", "enum": list(M.LINES)},
                     "equipment_id": {"type": "string"},
                     "open_only": {"type": "boolean"}}}, _wos)
    reg.register("get_quality", "Datakwaliteit per machine: GOOD, STALE of BAD.",
                 {"type": "object", "properties": {}},
                 lambda a: DL.quality(STATE.get("buses") or {}))
    reg.register("get_reliability", "MTBF, MTTR, beschikbaarheid en stilstand per machine.",
                 hours_schema, _rel)
    reg.register("get_datalayer_map", "Welke bronnen en afnemers aan de datalaag hangen.",
                 {"type": "object", "properties": {}},
                 lambda a: DL.layer_map(STATE.get("buses") or {}, STATE.get("db"),
                                        _extra()))
    return reg


@app.post(f"{API}/mcp")
def mcp_endpoint(payload: dict | list = Body(...)):
    """Read-only MCP (JSON-RPC 2.0). Een agent leest de fabriek, schrijft nooit."""
    reg = STATE.get("mcp") or _build_mcp()
    out = MCP.handle(payload, reg)
    if out is None:
        return Response(status_code=202)
    return JSONResponse(out)


@app.get(f"{API}/system/resources")
def system_resources():
    """Wat de stack op de VPS gebruikt (host-meting, alleen-lezen gemount)."""
    return DO.resources()


@app.get(f"{API}/dataops")
def get_dataops():
    return DO.dataops(DL.quality(_buses()), _buses(), DL.active_dropouts())


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
        is_fg = M.is_finished_good(mid)
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
def list_batches(line: str | None = Query(default=None)):
    return _runner().list_batches(line=_check_line(line))


@app.post(f"{API}/batches")
def create_batch(body: CreateBatch):
    runner = _runner_for_recipe(body.recipe_id)
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
    runner = _runner_for_batch(batch_id)
    if runner.get_batch(batch_id) is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    batch = runner.start_batch(batch_id)
    return {"batch_id": batch_id, "state": batch["state"]}


@app.post(f"{API}/batches/{{batch_id}}/ack-verdict")
def ack_verdict(batch_id: str, body: AckRequest):
    """Acknowledge a batch verdict (idempotent). Batch must be COMPLETE with verdict."""
    runner = _runner_for_batch(batch_id)
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


@app.get(f"{API}/orders/{{order_id}}/trace")
def get_order_trace(order_id: str):
    """Order -> batches -> pallets -> trucks -> customer, for the trace view."""
    now = datetime.now(timezone.utc)
    sim = STATE.get("logistics")
    live = sim.shipments(now) if sim is not None else []
    out = order_trace(_orders().db, order_id, now, live)
    if out is None:
        raise HTTPException(404, f"order {order_id} not found")
    return out


@app.post(f"{API}/orders/{{order_id}}/batches")
def create_order_batch(order_id: str, body: CreateOrderBatch):
    order = _orders().get_order(order_id)
    if order is None:
        raise HTTPException(404, f"order {order_id} not found")
    runner = _runner_for_recipe(order["recipe_id"])
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
    runner = _runner_for_batch(body.batch_id)
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
def line_live(line: str = Query(default=M.DEFAULT_LINE)):
    """De L1-payload: fase, doseringen, vulling en kwaliteit, kant-en-klaar.
    Alles wat de oude client zelf afleidde (lijnsnelheid, packs-target,
    totaalcharge, pallets) komt hiervandaan."""
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    return L.live(db, K.load_factory_model(), line=_check_line(line))


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
    runner = _runner_for_batch(batch_id)
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
    p = body.params or {}
    cmd = body.cmd.lower()
    # Welke lijn: expliciet (params.line), via het recept, of via het equipment.
    if p.get("line"):
        line = _check_line(str(p["line"]))
    elif cmd == "start" and p.get("recipe_id"):
        line = M.line_of_recipe(str(p["recipe_id"]))
    elif body.equipment_id in M.ALL_EQUIPMENT_IDS:
        line = M.line_of_equipment(body.equipment_id)
    else:
        line = M.DEFAULT_LINE
    control = (STATE.get("controls") or {}).get(line) or STATE.get("control")
    bus = (STATE.get("buses") or {}).get(line) or STATE.get("bus")
    if control is None:
        raise HTTPException(503, "engine not initialized")

    if cmd == "start":
        recipe_id = str(p.get("recipe_id") or M.get_line(line).recipes[0])
        result = control.start_batch(recipe_id)
        if bus is not None:
            bus.start_batch(recipe_id)
    elif cmd == "stop":
        runner = _runner(line)
        active = next((b for b in runner.list_batches(line=line)
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
            "line": line, "cmd": cmd, "opcua": result}


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


@app.get(f"{API}/hu/{{hu_id}}/history")
def hu_history(hu_id: str):
    """Every event of one pallet: wrap, putaway (by which forklift), load, ship."""
    out = forklift_trace.hu_history(_db(), hu_id)
    if out is None:
        raise HTTPException(404, f"unknown pallet: {hu_id}")
    return out


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
