# -*- coding: utf-8 -*-
"""Storingsscenario's die vanzelf lopen.

Een demo waarin iemand op knoppen moet drukken vertelt geen verhaal. Dit
draait een scenario af op de maat van de FABRIEK: bij elke batch die op de
monoliet afrondt, gaat de vervuiling van de warmtewisselaar een stap omhoog.
Zes batches later staat de vla buiten spec en is dat aantoonbaar geen toeval.

Ze lopen op batch-overgangen en niet op een wandklok. Een scenario dat op
minuten tikt loopt uit de pas met een fabriek die je hebt versneld of stilgezet,
en dan gebeurt de storing op het verkeerde moment in je verhaal.

De cursor staat in MongoDB. Een herstart hervat waar hij was in plaats van vanaf
stap 1, en dat is geen netheid: opnieuw afspelen levert een ANDERE
vervuilingscurve op dan die op je slide staat, want de fysica bouwt op.

Storingen gaan via de REST-laag van de batch-engine, niet rechtstreeks naar de
machines. Zo komen het UI-paneel en dit script op hetzelfde punt uit en zie je
in de catalogus wat een scenario heeft aangezet.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time

import yaml

log = logging.getLogger("park-scenario")

SCEN_DIR = os.environ.get("SCENARIO_DIR", "/scenarios")
ENGINE = os.environ.get("BATCH_ENGINE_URL", "http://vla-batch-engine:8000")
API = ENGINE.rstrip("/") + "/api/v1"


# ------------------------------------------------------------------ scenario

class Scenario:
    def __init__(self, spec, path=""):
        self.path = path
        self.id = spec["id"]
        self.title = spec.get("title", self.id)
        trig = spec.get("trigger") or {}
        self.trigger_topic = trig.get("topic",
                                      "DairyWorks/Vla/Batch/Status/state")
        self.trigger_to = trig.get("to", "COMPLETE")
        self.steps = sorted(spec.get("steps") or [],
                            key=lambda s: int(s.get("at_trigger", 0)))
        self.loop = bool(spec.get("loop", False))
        self.clear_on_finish = bool(spec.get("clear_on_finish", True))
        if not self.steps:
            raise ValueError("%s heeft geen stappen" % self.id)

    @property
    def last_trigger(self):
        return int(self.steps[-1].get("at_trigger", 0))

    def steps_at(self, n):
        return [s for s in self.steps if int(s.get("at_trigger", 0)) == n]

    def validate(self, catalogue):
        """Elke stap moet een storing noemen die de machine ECHT kent.

        De catalogus komt uit het FAULTS-attribuut van de physics-klassen. Een
        scenario dat een niet-bestaande storing aanroept faalt anders pas
        halverwege je demo, en dan sta je te improviseren.
        """
        known = {m["equipment_id"]: set(m["faults"])
                 for m in catalogue.get("machines", [])}
        problems = []
        for s in self.steps:
            eq, f = s.get("machine"), s.get("fault")
            if eq not in known:
                problems.append("%s: onbekende machine %r" % (self.id, eq))
            elif f not in known[eq]:
                problems.append("%s: %s kent storing %r niet (wel: %s)"
                                % (self.id, eq, f, sorted(known[eq])))
            m = s.get("magnitude", 1.0)
            if not isinstance(m, (int, float)) or not 0.0 <= float(m) <= 1.0:
                problems.append("%s: magnitude %r moet tussen 0 en 1 liggen"
                                % (self.id, m))
        return problems


def load_scenarios(directory=None):
    directory = directory or SCEN_DIR
    out = []
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith((".yml", ".yaml")):
            continue
        p = os.path.join(directory, name)
        with io.open(p, encoding="utf-8") as fh:
            out.append(Scenario(yaml.safe_load(fh), p))
    return out


# -------------------------------------------------------------------- cursor

class Cursor:
    """Waar een scenario is gebleven. Mongo als het kan, anders in-memory."""

    def __init__(self, db=None):
        self.db = db
        self._mem = {}

    def get(self, scenario_id):
        if self.db is not None:
            try:
                d = self.db.dw_park_scenarios.find_one({"_id": scenario_id})
                if d:
                    return int(d.get("cursor", 0))
            except Exception as e:  # noqa: BLE001
                log.debug("cursor lezen mislukt (%s), val terug op geheugen", e)
        return int(self._mem.get(scenario_id, 0))

    def set(self, scenario_id, value, extra=None):
        self._mem[scenario_id] = int(value)
        if self.db is None:
            return
        try:
            doc = {"cursor": int(value), "updated_at": time.time()}
            doc.update(extra or {})
            self.db.dw_park_scenarios.update_one(
                {"_id": scenario_id}, {"$set": doc}, upsert=True)
        except Exception as e:  # noqa: BLE001
            log.debug("cursor schrijven mislukt (%s)", e)


# --------------------------------------------------------------------- engine

def _post(path, body=None):
    import urllib.error
    import urllib.request
    req = urllib.request.Request(
        API + path, method="POST",
        data=json.dumps(body or {}).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "error": "http %s" % e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _get(path):
    import urllib.request
    try:
        with urllib.request.urlopen(API + path, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log.debug("GET %s mislukt: %s", path, e)
        return {}


def apply_step(step):
    eq = step["machine"]
    return _post("/park/%s/fault" % eq,
                 {"fault": step["fault"],
                  "magnitude": float(step.get("magnitude", 1.0))})


class Runner:
    """Voert scenario's uit op batch-overgangen."""

    def __init__(self, scenarios, cursor, post=apply_step, clear=None):
        self.scenarios = scenarios
        self.cursor = cursor
        self.post = post
        self.clear = clear or (lambda: _post("/park/clear-all"))

    def on_trigger(self, topic, value):
        """Eén batchovergang. Geeft terug wat er is toegepast."""
        applied = []
        for sc in self.scenarios:
            if topic != sc.trigger_topic or str(value) != sc.trigger_to:
                continue
            n = self.cursor.get(sc.id) + 1
            if n > sc.last_trigger:
                if sc.loop:
                    if sc.clear_on_finish:
                        self.clear()
                    n = 1
                    log.info("%s: rondje af, opnieuw vanaf stap 1", sc.id)
                else:
                    continue
            for step in sc.steps_at(n):
                res = self.post(step)
                applied.append({"scenario": sc.id, "trigger": n,
                                "step": step, "result": res})
                log.info("%s trigger %d: %s %s @ %.2f -> %s", sc.id, n,
                         step["machine"], step["fault"],
                         float(step.get("magnitude", 1.0)),
                         "ok" if res.get("ok") else res.get("error"))
            self.cursor.set(sc.id, n, {"title": sc.title,
                                       "last_step_at": time.time()})
        return applied


# ----------------------------------------------------------------------- main

def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s", datefmt="%H:%M:%S")

    import paho.mqtt.client as mqtt

    scenarios = load_scenarios()
    if not scenarios:
        log.warning("geen scenario's in %s; niets te doen", SCEN_DIR)
    for sc in scenarios:
        log.info("scenario %s: %d stappen tot trigger %d%s",
                 sc.id, len(sc.steps), sc.last_trigger,
                 ", herhalend" if sc.loop else "")

    # Valideren tegen de ECHTE catalogus voordat er iets draait.
    cat = _get("/park/faults")
    if cat:
        problems = [p for sc in scenarios for p in sc.validate(cat)]
        for p in problems:
            log.error("scenario ongeldig: %s", p)
        if problems:
            log.error("%d fout(en); scenario's worden NIET uitgevoerd", len(problems))
            scenarios = []

    db = None
    try:
        from pymongo import MongoClient
        url = os.environ.get("MONGO_URL")
        if url:
            db = MongoClient(url, serverSelectionTimeoutMS=2000)[
                os.environ.get("MONGO_DB", "idp")]
            db.list_collection_names()
            log.info("cursor in MongoDB")
    except Exception as e:  # noqa: BLE001
        log.warning("geen Mongo (%s); cursor alleen in geheugen, een herstart "
                    "begint dan opnieuw", e)
        db = None

    runner = Runner(scenarios, Cursor(db))
    topics = sorted({sc.trigger_topic for sc in scenarios}) or \
        ["DairyWorks/Vla/Batch/Status/state"]
    last = {}

    def on_connect(c, *_a):
        for t in topics:
            c.subscribe(t, qos=0)
            log.info("volgt %s", t)

    def on_message(c, _u, msg):
        raw = msg.payload.decode("utf-8", "replace").strip()
        try:
            val = json.loads(raw).get("value") if raw[:1] == "{" else raw
        except ValueError:
            val = raw
        # Alleen op de OVERGANG reageren, niet op elke herhaling van dezelfde
        # toestand: anders vuurt een retained of herhaald bericht het scenario
        # meerdere keren af en klopt de curve niet meer.
        if last.get(msg.topic) == val:
            return
        last[msg.topic] = val
        runner.on_trigger(msg.topic, val)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="vla-park-scenario")
    user = os.environ.get("MQTT_USERNAME")
    if user:
        client.username_pw_set(user, os.environ.get("MQTT_PASSWORD") or None)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(os.environ.get("MQTT_HOST", "monstermq"),
                   int(os.environ.get("MQTT_PORT", 1883)), 60)
    client.loop_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
