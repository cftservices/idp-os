"""Voorbereiding: afwegen en premixen van de droge grondstoffen per batch.

Eén voorbereidingsruimte bedient beide lijnen (datalayer-patroon
"voorbereidingsorder"). Per batch wordt een set klaargezet: suiker, zetmeel en
de smaakstof worden afgewogen op een weegstation en in een premixtank
opgelost. Melk komt rechtstreeks uit de silo en hoort niet bij de set.

Toestanden van een set (dw_prep_orders):

    gepland -> afwegen -> premix -> gereed -> verbruikt

Elke lijn heeft een eigen weegstation en premixtank in dezelfde ruimte, zodat
een storing op de ene lijn de andere niet ophoudt. Een station verwerkt één
set tegelijk; de volgende wacht in `gepland`.

De autopilot start een batch alleen als er een `gereed`-set voor die lijn en
dat recept ligt, en houdt er een paar vooruit. Handmatige batches via de API
worden bewust niet tegengehouden: de voorbereiding is een planningshulp, geen
veiligheidspoort.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from vla import model as M

log = logging.getLogger("vla.preparation")

GEPLAND, AFWEGEN, PREMIX, GEREED, VERBRUIKT = (
    "gepland", "afwegen", "premix", "gereed", "verbruikt")
STATES = (GEPLAND, AFWEGEN, PREMIX, GEREED, VERBRUIKT)
OPEN_STATES = (GEPLAND, AFWEGEN, PREMIX, GEREED)

# Demo-tempo: een batch duurt ~2 min, dus een set is ruim op tijd klaar.
WEIGH_S = 25.0
PREMIX_S = 30.0
AHEAD = 2

PREP_ROOT = "DairyWorks/Prep"
STATIONS = {
    "L1": {"weigh": "weigh-station-01", "premix": "premix-tank-01"},
    "L2": {"weigh": "weigh-station-02", "premix": "premix-tank-02"},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def components(recipe_id: str) -> list[dict]:
    """De droge doseringen van een recept: alles behalve melk."""
    recipe = M.get_recipe(recipe_id)
    if recipe is None:
        return []
    return [{"material_id": d.material_id, "qty_kg": d.qty_target,
             "uom": d.uom} for d in recipe.doses if d.material_id != "milk"]


class PrepManager:
    def __init__(self, db, bus=None, events=None):
        self.db = db
        self.bus = bus
        self.events = events
        self._lock = threading.Lock()

    # --------------------------------------------------------------- helpers

    def _next_id(self, now: datetime) -> str:
        day = now.strftime("%y%m%d")
        n = sum(1 for d in self.db.dw_prep_orders.find({})
                if str(d.get("prep_id", "")).startswith(f"VB-{day}-"))
        return f"VB-{day}-{n + 1:03d}"

    def _set_state(self, doc: dict, state: str, now: datetime, **extra) -> dict:
        hist = list(doc.get("history") or []) + [{"state": state, "ts": _iso(now)}]
        upd = {"state": state, "state_changed_at": _iso(now), "history": hist}
        upd.update(extra)
        self.db.dw_prep_orders.update_one({"prep_id": doc["prep_id"]}, {"$set": upd})
        doc = {**doc, **upd}
        self._publish(doc)
        return doc

    def _publish(self, doc: dict) -> None:
        client = getattr(self.bus, "client", None) if self.bus else None
        if client is None:
            return
        station = doc.get("premix_tank") if doc["state"] in (PREMIX, GEREED) \
            else doc.get("weigh_station")
        payload = json.dumps({"value": doc["state"], "prep_id": doc["prep_id"],
                              "recipe_id": doc["recipe_id"], "line": doc["line"],
                              "ts": doc.get("state_changed_at")})
        try:
            client.publish(f"{PREP_ROOT}/{station}/Status/state", payload, qos=0,
                           retain=True)
        except Exception:  # offline-safe
            pass

    @staticmethod
    def _age(doc: dict, now: datetime) -> float:
        try:
            return (now - datetime.fromisoformat(doc["state_changed_at"])).total_seconds()
        except (KeyError, ValueError, TypeError):
            return 1e9

    # ------------------------------------------------------------------ API

    def plan(self, line: str, recipe_id: str, order_id: Optional[str] = None,
             operator_id: Optional[str] = None, now: Optional[datetime] = None) -> dict:
        if M.get_recipe(recipe_id) is None:
            raise ValueError(f"onbekend recept {recipe_id!r}")
        if recipe_id not in M.get_line(line).recipes:
            raise ValueError(f"recept {recipe_id} hoort niet bij lijn {line}")
        now = now or _now()
        st = STATIONS.get(line, STATIONS["L1"])
        with self._lock:
            doc = {"prep_id": self._next_id(now), "line": line,
                   "recipe_id": recipe_id, "order_id": order_id,
                   "weigh_station": st["weigh"], "premix_tank": st["premix"],
                   "components": components(recipe_id), "state": GEPLAND,
                   "created_at": _iso(now), "state_changed_at": _iso(now),
                   "history": [{"state": GEPLAND, "ts": _iso(now)}],
                   "batch_id": None, "created_by": operator_id}
            self.db.dw_prep_orders.insert_one(doc)
        self._publish(doc)
        return doc

    def tick(self, now: Optional[datetime] = None) -> list[str]:
        """Zet sets een stap verder. Geeft de overgangen terug (voor logging)."""
        now = now or _now()
        moved: list[str] = []
        docs = sorted(self.db.dw_prep_orders.find({}), key=lambda d: d.get("created_at", ""))
        busy_weigh = {d["weigh_station"] for d in docs if d.get("state") == AFWEGEN}
        busy_premix = {d["premix_tank"] for d in docs if d.get("state") == PREMIX}
        for d in docs:
            state = d.get("state")
            if state == PREMIX and self._age(d, now) >= PREMIX_S:
                self._set_state(d, GEREED, now)
                busy_premix.discard(d["premix_tank"])
                moved.append(f"{d['prep_id']} gereed")
        for d in docs:
            if d.get("state") == AFWEGEN and self._age(d, now) >= WEIGH_S \
                    and d["premix_tank"] not in busy_premix:
                self._set_state(d, PREMIX, now)
                busy_weigh.discard(d["weigh_station"])
                busy_premix.add(d["premix_tank"])
                moved.append(f"{d['prep_id']} premix")
        for d in docs:
            if d.get("state") == GEPLAND and d["weigh_station"] not in busy_weigh:
                self._set_state(d, AFWEGEN, now)
                busy_weigh.add(d["weigh_station"])
                moved.append(f"{d['prep_id']} afwegen")
        return moved

    def ensure_ahead(self, line: str, recipe_id: str, order_id: Optional[str] = None,
                     n: int = AHEAD, operator_id: Optional[str] = None) -> int:
        """Houd n open sets klaar voor dit recept op deze lijn. Nog niet
        begonnen sets van een ander recept op die lijn vervallen (de
        productwissel maakte ze overbodig); al afgewogen sets blijven liggen
        voor de volgende ronde van het productwiel."""
        for d in self.db.dw_prep_orders.find({"line": line, "state": GEPLAND}):
            if d.get("recipe_id") != recipe_id:
                self.db.dw_prep_orders.delete_many({"prep_id": d["prep_id"]})
        have = sum(1 for d in self.db.dw_prep_orders.find({"line": line,
                                                           "recipe_id": recipe_id})
                   if d.get("state") in OPEN_STATES)
        made = 0
        while have + made < n:
            self.plan(line, recipe_id, order_id=order_id, operator_id=operator_id)
            made += 1
        return made

    def ready(self, line: str, recipe_id: str) -> Optional[dict]:
        docs = self.db.dw_prep_orders.find({"line": line, "recipe_id": recipe_id,
                                            "state": GEREED})
        return min(docs, key=lambda d: d.get("created_at", "")) if docs else None

    def consume(self, prep_id: str, batch_id: str, order_id: Optional[str] = None,
                now: Optional[datetime] = None) -> Optional[dict]:
        doc = self.db.dw_prep_orders.find_one({"prep_id": prep_id})
        if doc is None:
            return None
        extra = {"batch_id": batch_id}
        if order_id:
            extra["order_id"] = order_id
        return self._set_state(doc, VERBRUIKT, now or _now(), **extra)

    def get(self, prep_id: str) -> Optional[dict]:
        return self.db.dw_prep_orders.find_one({"prep_id": prep_id})

    def for_batch(self, batch_id: str) -> Optional[dict]:
        return self.db.dw_prep_orders.find_one({"batch_id": batch_id})

    def list(self, line: Optional[str] = None, state: Optional[str] = None,
             limit: int = 100) -> list[dict]:
        q: dict = {}
        if line:
            q["line"] = line
        if state:
            q["state"] = state
        docs = sorted(self.db.dw_prep_orders.find(q),
                      key=lambda d: d.get("created_at", ""), reverse=True)
        return docs[:limit]

    def stations(self) -> list[dict]:
        docs = self.db.dw_prep_orders.find({})
        out = []
        for line, st in STATIONS.items():
            for kind, sid, active in (("weigh", st["weigh"], AFWEGEN),
                                      ("premix", st["premix"], PREMIX)):
                cur = next((d for d in docs if d.get("state") == active and
                            d.get("weigh_station" if kind == "weigh" else "premix_tank") == sid),
                           None)
                out.append({"station_id": sid, "kind": kind, "line": line,
                            "state": "bezig" if cur else "vrij",
                            "prep_id": cur["prep_id"] if cur else None,
                            "recipe_id": cur["recipe_id"] if cur else None})
        return out

    def prune(self, keep_hours: float) -> int:
        cutoff = _iso(_now() - timedelta(hours=keep_hours))
        old = [d for d in self.db.dw_prep_orders.find({"state": VERBRUIKT})
               if (d.get("state_changed_at") or "") < cutoff]
        for d in old:
            self.db.dw_prep_orders.delete_many({"prep_id": d["prep_id"]})
        return len(old)
