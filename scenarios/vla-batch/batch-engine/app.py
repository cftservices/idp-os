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

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from vla import model as M
from vla.batches import BatchRunner
from vla.bus import VlaBus
from vla.db import get_db, seed_recipes
from vla.opcua_control import OpcuaControl
from vla.report import render_json, render_pdf

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("vla.app")

app = FastAPI(title="DairyWorks Vla Batch Engine", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATE: dict = {"db": None, "bus": None, "control": None, "runner": None}
API = "/api/v1"

# admin/command -> OPC-UA method mapping (§OPC-UA methods). Setpoint targets are
# passed through to SetSetpoint as-is when they match the contract target-strings.
_COMMAND_ALIASES = {
    "start": "StartBatch", "startbatch": "StartBatch",
    "stop": "Stop",
    "sample": "TakeSample", "takesample": "TakeSample",
    "fault": "InjectFault", "injectfault": "InjectFault",
    "clear": "ClearFault", "clearfault": "ClearFault",
    "setsetpoint": "SetSetpoint",
}


class CreateBatch(BaseModel):
    recipe_id: str
    planned_L: float | None = None


class TakeSample(BaseModel):
    batch_id: str
    sample_type: str = "adhoc"


class AdminCommand(BaseModel):
    target: str            # "batch" | equipment_id
    command: str           # StartBatch|Stop|SetSetpoint|InjectFault|ClearFault|TakeSample|<tag>
    value: float | str | None = None


def _runner() -> BatchRunner:
    runner = STATE.get("runner")
    if runner is None:
        raise HTTPException(503, "engine not initialized")
    return runner


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
    STATE.update({"db": db, "bus": bus, "control": control,
                  "runner": BatchRunner(db, bus, control=control)})
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
    return {"batch_id": batch["batch_id"], "state": batch["state"]}


@app.get(f"{API}/batches/{{batch_id}}")
def get_batch(batch_id: str):
    batch = _runner().get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    return batch


@app.post(f"{API}/batches/{{batch_id}}/start")
def start_batch(batch_id: str):
    runner = _runner()
    if runner.get_batch(batch_id) is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    batch = runner.start_batch(batch_id)
    return {"batch_id": batch_id, "state": batch["state"]}


@app.get(f"{API}/samples")
def list_samples(batch_id: str | None = Query(default=None)):
    return _runner().get_samples(batch_id)


@app.post(f"{API}/samples")
def take_sample(body: TakeSample):
    runner = _runner()
    if runner.get_batch(body.batch_id) is None:
        raise HTTPException(404, f"batch {body.batch_id} not found")
    return runner.take_sample(body.batch_id, body.sample_type)


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
    """Route a control action to the factory. PRIMARY path = direct OPC-UA
    method on the Batch object; MQTT Command publish stays as secondary fallback.

    `target` = "batch" | equipment_id. `command` maps to an OPC-UA method:
      start->StartBatch, stop->Stop, sample->TakeSample, fault->InjectFault,
      clear->ClearFault. A setpoint target-string (cook.setpoint_C, dose.milk,
      receiving.fat, ...) or command 'SetSetpoint' -> SetSetpoint(target, value).
    """
    control = STATE.get("control")
    bus = STATE.get("bus")
    if control is None:
        raise HTTPException(503, "engine not initialized")

    raw = str(body.command)
    method = _COMMAND_ALIASES.get(raw.lower(), raw)

    # A raw setpoint target-string (e.g. "cook.setpoint_C", "dose.milk") is a
    # SetSetpoint shorthand.
    from vla.opcua_control import SETPOINT_TARGETS
    setpoint_target = None
    if method == "SetSetpoint":
        setpoint_target = str(body.value) if isinstance(body.value, str) else None
    elif raw in SETPOINT_TARGETS:
        method, setpoint_target = "SetSetpoint", raw

    if method == "StartBatch":
        result = control.start_batch(
            str(body.value or M.RECIPE_CHOCOLATE_VLA_1L.recipe_id))
        if bus is not None:
            bus.start_batch(str(body.value or M.RECIPE_CHOCOLATE_VLA_1L.recipe_id))
    elif method == "Stop":
        result = control.stop()
        if bus is not None:
            bus.stop_batch()
    elif method == "TakeSample":
        result = control.take_sample(str(body.value or "adhoc"))
        if bus is not None:
            bus.take_sample(str(body.value or "adhoc"))
    elif method == "InjectFault":
        result = control.inject_fault(str(body.value or "cook_undertemp"), 0.5)
        if bus is not None:
            bus.inject_fault(str(body.value or "cook_undertemp"), 0.5)
    elif method == "ClearFault":
        result = control.clear_fault()
        if bus is not None:
            bus.clear_fault()
    elif method == "SetSetpoint":
        if setpoint_target is None:
            raise HTTPException(400, "SetSetpoint needs a target (e.g. cook.setpoint_C)")
        # value carries the numeric setpoint; when the target came via `command`,
        # the numeric value is in body.value.
        try:
            sp_value = float(body.value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise HTTPException(400, "SetSetpoint needs a numeric value")
        result = control.set_setpoint(setpoint_target, sp_value)
        if bus is not None:
            bus.set_setpoint(setpoint_target, sp_value)
    else:
        # unknown command: fall back to a raw MQTT Command publish (secondary)
        sent = bus.command(body.target, raw, value=body.value) if bus else False
        return {"accepted": True, "sent": bool(sent), "path": "mqtt-fallback",
                "target": body.target, "command": raw}

    return {"accepted": True, "path": "opcua", "target": body.target,
            "command": method, "opcua": result}
