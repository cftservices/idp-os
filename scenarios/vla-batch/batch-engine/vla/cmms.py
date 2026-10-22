"""Onderhoud (CMMS) aangesloten op de datalaag.

Adapterpatroon, net als de bronkoppelingen: de connector kent alleen een
`CmmsAdapter`-interface. De eerste adapter is een demo-CMMS met synthetische
DairyWorks-data in een eigen collectie (`dw_work_orders`); een echt CMMS wordt
een tweede adapter, zonder dat de rest van de engine verandert.

De lus die dit sluit:

    storing / CBM-alert  ->  werkorder (equipment-id via de ISA-95-mapping)
                         ->  toegewezen -> in uitvoering -> gereed
                         ->  status terug als context op de datalaag
                         ->  machine weer beschikbaar, MTTR uit gereedmelding

Een storing zet de machine op Down (zie EquipmentMonitor.set_down); de
gereedmelding van de werkorder is het enige dat hem weer vrijgeeft. Een CBM-
werkorder is preventief: hij zet niets stil en is gereed zodra de CIP het
alert oplost.
"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Protocol

from . import model as M

log = logging.getLogger("vla.cmms")

STATUSES = ("reported", "assigned", "in_progress", "done")
OPEN = ("reported", "assigned", "in_progress")

TECHNICIANS = [
    {"id": "MT-01", "name": "Monteur A", "skill": "mechanisch"},
    {"id": "MT-02", "name": "Monteur B", "skill": "elektrisch"},
    {"id": "MT-03", "name": "Monteur C", "skill": "proces"},
]

# Storingsmodi per rol: (oorzaak, vakgebied, reparatietijd in seconden min-max)
FAILURE_MODES = {
    "receiving": [("Niveaumeting geeft geen signaal", "elektrisch", (150, 300)),
                  ("Toevoerklep sluit niet", "mechanisch", (180, 360))],
    "process": [("Roerwerkmotor thermisch beveiligd", "elektrisch", (180, 420)),
                ("Doseerklep lekt", "mechanisch", (150, 300))],
    "cook": [("Stoomregelklep hangt", "mechanisch", (240, 480)),
             ("Temperatuurvoeler defect", "elektrisch", (150, 300))],
    "cooler": [("Koelwaterpomp uitgevallen", "mechanisch", (210, 420)),
               ("Flowmeter koelwater wijkt af", "proces", (150, 270))],
    "filler": [("Vulkop lekt", "mechanisch", (180, 360)),
               ("Sealer haalt temperatuur niet", "elektrisch", (150, 330))],
}

ASSIGN_AFTER_S = 20.0
START_AFTER_S = 25.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


def _parse(ts) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None


def role_of(equipment_id: str) -> Optional[str]:
    for ln in M.LINES.values():
        for role, eq in ln.equipment.items():
            if eq == equipment_id:
                return role
    return None


class CmmsAdapter(Protocol):
    name: str

    def create(self, wo: dict) -> dict: ...
    def update(self, wo_id: str, fields: dict) -> None: ...
    def get(self, wo_id: str) -> Optional[dict]: ...
    def all(self) -> list[dict]: ...


class DemoCmmsAdapter:
    """Synthetisch CMMS in MongoDB. Vervang door een echte adapter (REST naar
    het CMMS van de klant) zonder de connector te wijzigen."""

    name = "demo-cmms"

    def __init__(self, db):
        self.db = db

    def create(self, wo: dict) -> dict:
        self.db.dw_work_orders.insert_one(dict(wo))
        return wo

    def update(self, wo_id: str, fields: dict) -> None:
        self.db.dw_work_orders.update_one({"wo_id": wo_id}, {"$set": fields})

    def get(self, wo_id: str) -> Optional[dict]:
        return self.db.dw_work_orders.find_one({"wo_id": wo_id})

    def all(self) -> list[dict]:
        return self.db.dw_work_orders.find({})


class CmmsConnector:
    def __init__(self, db, equipment, buses: Optional[dict] = None,
                 adapter: Optional[CmmsAdapter] = None,
                 rng: Optional[random.Random] = None,
                 auto_breakdowns: bool = False,
                 mean_breakdown_interval_s: float = 2700.0,
                 events=None):
        self.db = db
        self.equipment = equipment
        self.buses = dict(buses or {})
        self.adapter = adapter or DemoCmmsAdapter(db)
        self.rng = rng or random.Random()
        self.auto_breakdowns = auto_breakdowns
        self.mean_breakdown_interval_s = float(mean_breakdown_interval_s)
        self.events = events
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_tick: Optional[float] = None
        self.status = {"adapter": self.adapter.name, "auto_breakdowns": auto_breakdowns,
                       "last_tick": None, "created": 0, "completed": 0,
                       "last_error": None}

    # ------------------------------------------------------------ lifecycle

    def start(self, poll_s: float = 3.0) -> None:
        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as e:  # thread must not die silently
                    log.exception("cmms tick failed")
                    self.status["last_error"] = str(e)
                self._stop.wait(poll_s)
        self._thread = threading.Thread(target=loop, name="vla-cmms", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------ datalaag

    def _publish(self, wo: dict) -> None:
        eq = wo["equipment_id"]
        bus = self.buses.get(M.line_of_equipment(eq))
        if bus is None:
            return
        area = M.area_of(eq)
        open_ = wo["status"] in OPEN
        bus.publish_json(f"{area}/{eq}/Status/work_order", {
            "value": wo["status"] if open_ else "none",
            "wo_id": wo["wo_id"], "kind": wo["kind"], "priority": wo["priority"],
            "technician": (wo.get("technician") or {}).get("name"),
            "source": self.adapter.name, "quality": "GOOD", "ts": _iso()})
        maint = "none"
        if open_:
            maint = "repair" if wo["kind"] == "breakdown" else "planned"
        bus.publish_json(f"{area}/{eq}/Status/maintenance_state",
                         {"value": maint, "quality": "GOOD", "ts": _iso()})

    def _event(self, wo: dict, event_type: str) -> None:
        if self.events is None:
            return
        try:
            self.events(wo.get("batch_id") or "", event_type, {
                "wo_id": wo["wo_id"], "equipment_id": wo["equipment_id"],
                "kind": wo["kind"], "status": wo["status"]})
        except Exception:  # pragma: no cover
            log.debug("cmms event skipped", exc_info=True)

    # ------------------------------------------------------------ create

    def create(self, equipment_id: str, kind: str, cause: str,
               priority: str = "P2", skill: Optional[str] = None,
               repair_s: Optional[float] = None,
               source_ref: Optional[str] = None) -> dict:
        if equipment_id not in M.ALL_EQUIPMENT_IDS:
            raise ValueError(f"unknown equipment: {equipment_id}")
        wo = {
            "wo_id": f"WO-{uuid.uuid4().hex[:6].upper()}",
            "equipment_id": equipment_id,
            "line": M.line_of_equipment(equipment_id),
            "area": M.area_of(equipment_id),
            "isa95_path": f"{M.get_line(M.line_of_equipment(equipment_id)).uns_root}"
                          f"/{M.area_of(equipment_id)}/{equipment_id}",
            "kind": kind,
            "cause": cause,
            "priority": priority,
            "skill": skill,
            "status": "reported",
            "reported_at": _iso(),
            "assigned_at": None, "started_at": None, "done_at": None,
            "technician": None,
            "repair_s": repair_s,
            "source_ref": source_ref,
            "adapter": self.adapter.name,
            "history": [{"status": "reported", "ts": _iso()}],
        }
        self.adapter.create(wo)
        self.status["created"] += 1
        self._publish(wo)
        self._event(wo, "work_order_created")
        log.info("werkorder %s: %s %s (%s)", wo["wo_id"], kind, equipment_id, cause)
        return wo

    def report_breakdown(self, equipment_id: str, cause: Optional[str] = None,
                         repair_s: Optional[float] = None) -> dict:
        """Storing: machine Down en een werkorder. Eén open storing per machine."""
        existing = [w for w in self.list(equipment_id=equipment_id, open_only=True)
                    if w["kind"] == "breakdown"]
        if existing:
            return existing[0]
        role = role_of(equipment_id) or "process"
        mode = self.rng.choice(FAILURE_MODES.get(role, FAILURE_MODES["process"]))
        cause = cause or mode[0]
        if repair_s is None:
            lo, hi = mode[2]
            repair_s = float(self.rng.randint(lo, hi))
        self.equipment.set_down(equipment_id, cause)
        wo = self.create(equipment_id, "breakdown", cause, priority="P1",
                         skill=mode[1], repair_s=repair_s)
        self.equipment.attach_work_order(equipment_id, wo["wo_id"])
        return wo

    # ------------------------------------------------------------ read

    def list(self, line: Optional[str] = None, status: Optional[str] = None,
             equipment_id: Optional[str] = None, open_only: bool = False,
             limit: int = 200) -> list[dict]:
        rows = self.adapter.all()
        out = []
        for r in rows:
            if line and r.get("line") != line:
                continue
            if status and r.get("status") != status:
                continue
            if equipment_id and r.get("equipment_id") != equipment_id:
                continue
            if open_only and r.get("status") not in OPEN:
                continue
            out.append(r)
        out.sort(key=lambda r: r.get("reported_at") or "", reverse=True)
        return out[:limit]

    # ------------------------------------------------------------ advance

    def _transition(self, wo: dict, status: str, **fields) -> dict:
        ts = _iso()
        hist = list(wo.get("history") or []) + [{"status": status, "ts": ts}]
        key = {"assigned": "assigned_at", "in_progress": "started_at",
               "done": "done_at"}.get(status)
        upd = {"status": status, "history": hist, **fields}
        if key:
            upd[key] = ts
        self.adapter.update(wo["wo_id"], upd)
        wo = dict(wo, **upd)
        self._publish(wo)
        self._event(wo, f"work_order_{status}")
        return wo

    def _technician(self, skill: Optional[str]) -> dict:
        busy = {(w.get("technician") or {}).get("id")
                for w in self.list(open_only=True) if w.get("technician")}
        pool = [t for t in TECHNICIANS if t["skill"] == skill] or TECHNICIANS
        free = [t for t in pool if t["id"] not in busy]
        return dict((free or pool)[0])

    def tick(self, now: Optional[datetime] = None) -> list[str]:
        now = now or _now()
        actions: list[str] = []
        self.status["last_tick"] = _iso(now)

        # 1. CBM-alerts zonder werkorder krijgen er één (preventief, P3).
        open_alerts = {a["alert_id"]: a for a in self.equipment.open_alerts()}
        cbm_wos = {w.get("source_ref"): w for w in self.adapter.all()
                   if w.get("kind") == "cbm"}
        for aid, alert in open_alerts.items():
            if aid not in cbm_wos:
                self.create(alert["equipment_id"], "cbm",
                            "Vervuiling: opwarmtijd boven CBM-grens, CIP plannen",
                            priority="P3", skill="proces", source_ref=aid)
                actions.append(f"cbm {alert['equipment_id']}")

        for wo in self.list(open_only=True, limit=10_000):
            reported = _parse(wo.get("reported_at")) or now
            if wo["kind"] == "cbm":
                # Gereed zodra de CIP het alert oplost: de status komt terug uit
                # de fabriek, niet uit een knop in het CMMS.
                if wo.get("source_ref") not in open_alerts:
                    self._transition(wo, "done", completed_by="CIP")
                    self.status["completed"] += 1
                    actions.append(f"done {wo['wo_id']}")
                elif wo["status"] == "reported" and \
                        (now - reported).total_seconds() >= ASSIGN_AFTER_S:
                    self._transition(wo, "assigned",
                                     technician=self._technician(wo.get("skill")))
                continue
            if wo["status"] == "reported" and \
                    (now - reported).total_seconds() >= ASSIGN_AFTER_S:
                self._transition(wo, "assigned",
                                 technician=self._technician(wo.get("skill")))
                actions.append(f"assigned {wo['wo_id']}")
            elif wo["status"] == "assigned":
                assigned = _parse(wo.get("assigned_at")) or now
                if (now - assigned).total_seconds() >= START_AFTER_S:
                    self._transition(wo, "in_progress")
                    actions.append(f"started {wo['wo_id']}")
            elif wo["status"] == "in_progress":
                started = _parse(wo.get("started_at")) or now
                if (now - started).total_seconds() >= float(wo.get("repair_s") or 300):
                    self._transition(wo, "done", completed_by=(
                        wo.get("technician") or {}).get("name"))
                    self.equipment.clear_down(wo["equipment_id"])
                    self.status["completed"] += 1
                    actions.append(f"done {wo['wo_id']}")

        # 2. Demo: af en toe een storing, zodat stilstand en MTTR echt bestaan.
        if self.auto_breakdowns:
            mono = time.monotonic()
            dt = 0.0 if self._last_tick is None else mono - self._last_tick
            self._last_tick = mono
            # kans per tick ~ dt / gemiddelde interval, per lijn
            for lid in M.LINES:
                if self.equipment.line_down(lid):
                    continue
                if dt > 0 and self.rng.random() < dt / self.mean_breakdown_interval_s:
                    eq = self.rng.choice(M.LINES[lid].equipment_ids)
                    self.report_breakdown(eq)
                    actions.append(f"breakdown {eq}")
        return actions


# ------------------------------------------------------------------ betrouwbaarheid

def reliability(db, equipment, line: Optional[str] = None,
                hours: float = 24.0, cost_per_downtime_hour: Optional[float] = None,
                now: Optional[datetime] = None) -> dict:
    """ISO 22400-tijden, MTBF en MTTR per machine over de laatste `hours`.

    POT  planned operation time   = het venster
    APT  actual production time   = Running/Dirty
    ADET actual unit delay time   = Down/Error (stilstand)
    AUIT idle time                = Idle/Allocated
    MTTR = gemiddelde (gereed - gemeld) van afgeronde storingswerkorders
    MTBF = APT / aantal storingen (None zonder storing: niet oneindig)
    """
    now = now or _now()
    start = now - timedelta(hours=float(hours))
    ids = M.LINES[line].equipment_ids if line in M.LINES else M.ALL_EQUIPMENT_IDS
    wos = DemoCmmsAdapter(db).all()
    out = []
    for eq in ids:
        rows = sorted(db.dw_equipment_state.find({"equipment_id": eq}),
                      key=lambda r: r.get("ts") or "")
        apt = adet = idle = 0.0
        for i, row in enumerate(rows):
            t0 = _parse(row.get("ts"))
            if t0 is None:
                continue
            t1 = _parse(rows[i + 1].get("ts")) if i + 1 < len(rows) else now
            lo, hi = max(t0, start), min(t1 or now, now)
            dur = max(0.0, (hi - lo).total_seconds())
            st = row.get("state")
            if st in M.PRODUCTIVE_STATES:
                apt += dur
            elif st in M.NEUTRAL_STATES:
                idle += dur
            else:
                adet += dur
        fails = [w for w in wos if w.get("equipment_id") == eq
                 and w.get("kind") == "breakdown"
                 and (_parse(w.get("reported_at")) or now) >= start]
        done = [w for w in fails if w.get("status") == "done"]
        repairs = [((_parse(w["done_at"]) or now) - (_parse(w["reported_at"]) or now))
                   .total_seconds() for w in done if w.get("done_at")]
        mttr_min = round(sum(repairs) / len(repairs) / 60.0, 1) if repairs else None
        mtbf_h = round(apt / 3600.0 / len(fails), 2) if fails else None
        denom = apt + adet
        availability = round(apt / denom, 4) if denom > 0 else None
        euro = round(adet / 3600.0 * cost_per_downtime_hour, 2) \
            if cost_per_downtime_hour is not None else None
        out.append({
            "equipment_id": eq, "line": M.line_of_equipment(eq),
            "pot_h": round(float(hours), 2),
            "apt_h": round(apt / 3600.0, 3),
            "adet_h": round(adet / 3600.0, 3),
            "idle_h": round(idle / 3600.0, 3),
            "availability": availability,
            "failures": len(fails),
            "open_failures": len(fails) - len(done),
            "mtbf_h": mtbf_h,
            "mttr_min": mttr_min,
            "downtime_eur": euro,
            "down": equipment.down_info(eq) if equipment is not None else None,
        })
    return {"window_h": hours, "from": _iso(start), "to": _iso(now),
            "equipment": out}
