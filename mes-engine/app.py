"""mes-engine FastAPI app.

Startup: load the ISA-95 factory model, connect DB (Mongo or in-memory) + MQTT
bus (degrades gracefully with no broker), expose order/OEE/sample/EBR endpoints
and admin command/fault endpoints.

Env:
  FACTORY_MODEL  (optional) path to isa95-dairyworks.json
  MONGO_URL      (optional) -> Mongo backend, else in-memory
  MQTT_HOST      default monstermq
  MQTT_PORT      default 1883
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from mes.bus import MesBus
from mes.db import get_db
from mes.ebr import assemble_ebr, render_html
from mes.model import load_model
from mes.orders import OrderRunner

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mes.app")

app = FastAPI(title="DairyWorks MES Engine", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- shared state, wired at startup ---
STATE: dict = {"model": None, "db": None, "bus": None, "runner": None}


class CreateOrder(BaseModel):
    recipe_id: str
    planned_qty: float


class AdminCommand(BaseModel):
    equipment: str
    command: str
    payload: str = "1"


class AdminFault(BaseModel):
    equipment: str
    fault: str
    magnitude: float = 0.4


def _runner() -> OrderRunner:
    runner = STATE.get("runner")
    if runner is None:
        raise HTTPException(503, "engine not initialized")
    return runner


@app.on_event("startup")
def _startup() -> None:
    model = load_model()
    db = get_db()
    bus = MesBus(
        model,
        host=os.environ.get("MQTT_HOST", "monstermq"),
        port=int(os.environ.get("MQTT_PORT", 1883)),
    )
    # Non-blocking; offline-safe. Do not fail startup if broker is absent.
    try:
        bus.start(wait_connected_s=float(os.environ.get("MQTT_WAIT_S", 3.0)))
    except Exception as e:
        log.warning("bus start failed (%s) — continuing offline", e)
    STATE.update({
        "model": model,
        "db": db,
        "bus": bus,
        "runner": OrderRunner(model, db, bus),
    })
    log.info("mes-engine ready (db=%s, mqtt=%s)", db.backend, bus.connected)


@app.on_event("shutdown")
def _shutdown() -> None:
    bus = STATE.get("bus")
    if bus is not None:
        bus.stop()


# ------------------------------------------------------------------ endpoints

@app.get("/health")
def health():
    db = STATE.get("db")
    bus = STATE.get("bus")
    model = STATE.get("model")
    return {
        "status": "ok" if STATE.get("runner") else "starting",
        "db_backend": db.backend if db else None,
        "mqtt_connected": bool(bus and bus.connected),
        "recipes": list(model.recipes().keys()) if model else [],
        "units": list(model.units().keys()) if model else [],
    }


@app.get("/orders")
def list_orders():
    return _runner().list_orders()


@app.get("/orders/{order_id}")
def get_order(order_id: str):
    bundle = _runner().get_order(order_id)
    if bundle is None:
        raise HTTPException(404, f"order {order_id} not found")
    return bundle


@app.post("/orders")
def create_order(body: CreateOrder):
    runner = _runner()
    try:
        runner.create_order(body.recipe_id, body.planned_qty)
    except ValueError as e:
        raise HTTPException(400, str(e))
    order = runner.list_orders()[-1]
    # auto-run
    bundle = runner.run_order(order["order_id"])
    return bundle


@app.post("/orders/{order_id}/start")
def start_order(order_id: str):
    runner = _runner()
    if runner.get_order(order_id) is None:
        raise HTTPException(404, f"order {order_id} not found")
    return runner.run_order(order_id)


@app.get("/tags")
def live_tags():
    """Latest sim Status values from the MQTT tag cache (for the factory view)."""
    bus = STATE.get("bus")
    if bus is None:
        raise HTTPException(503, "engine not initialized")
    return {"connected": bool(bus.connected), "tags": bus.snapshot()}


@app.get("/oee")
def list_oee():
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    return db.dw_oee.find({})


@app.get("/samples")
def list_samples(order_id: str | None = Query(default=None)):
    db = STATE.get("db")
    if db is None:
        raise HTTPException(503, "engine not initialized")
    query = {"order_id": order_id} if order_id else {}
    return db.dw_samples.find(query)


@app.get("/ebr/{order_id}")
def get_ebr(order_id: str, fmt: str = Query(default="html")):
    runner = _runner()
    bundle = runner.get_order(order_id)
    if bundle is None:
        raise HTTPException(404, f"order {order_id} not found")
    ebr = assemble_ebr(bundle, STATE.get("model"))
    if fmt == "json":
        return JSONResponse(ebr)
    return HTMLResponse(render_html(ebr))


@app.post("/admin/command")
def admin_command(body: AdminCommand):
    bus = STATE.get("bus")
    if bus is None:
        raise HTTPException(503, "engine not initialized")
    sent = bus.command(body.equipment, body.command, body.payload)
    return {"equipment": body.equipment, "command": body.command,
            "payload": body.payload, "sent": sent}


@app.post("/admin/fault")
def admin_fault(body: AdminFault):
    bus = STATE.get("bus")
    if bus is None:
        raise HTTPException(503, "engine not initialized")
    sent = bus.command(body.equipment, "Fault/Inject",
                       {"fault": body.fault, "magnitude": body.magnitude})
    return {"equipment": body.equipment, "fault": body.fault,
            "magnitude": body.magnitude, "sent": sent}
