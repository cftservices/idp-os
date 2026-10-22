# -*- coding: utf-8 -*-
"""vla-park-conditioner: Connect -> Condition -> Model, als losse service.

Abonneert op `raw/vla-park/#`, repareert elk punt volgens
factory-model/park-conditioning.json, en publiceert het canoniek op
`DairyWorks/Vla-B/{Area}/{Equipment}/Status/{tag}`.

Protocol-agnostisch met opzet. Deze service weet NIET of een waarde via OPC-UA,
OPC-DA, Modbus, MQTT, REST of SQL is binnengekomen: alles landt op dezelfde
raw-root en wordt met dezelfde regels behandeld. Zou hij dat wel weten, dan
kreeg je zes takken code die elk net iets anders doen, en dan zit het verschil
straks in de data.

De demo-schakelaar. `POST /api/v1/model-layer {"enabled": false}` laat de raw
gewoon doorstromen en legt de canonieke UNS stil. Grafana en de UI lopen leeg
terwijl de fabriek gewoon draait. Dat is in tien seconden het hele argument.
Bewust een vlag en geen container-stop: opstarttijd verpest het moment.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import os
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import paho.mqtt.client as mqtt

from condition import condition, suppress, is_stale, Refused
import crosscheck as xc

log = logging.getLogger("park-conditioner")

MODEL_DIR = os.environ.get(
    "MODEL_DIR", "/model")
CANONICAL_ROOT = os.environ.get("CANONICAL_ROOT", "DairyWorks/Vla-B")
RAW_ROOT = os.environ.get("RAW_ROOT", "raw/vla-park")
DQ_ROOT = "%s/DataQuality" % CANONICAL_ROOT


class Conditioner:
    def __init__(self, rules, checks):
        self.by_raw = {r["raw_topic"]: r for r in rules}
        self.checks = checks
        self.model_layer = True
        self.counts = Counter()
        self.last_seen = {}
        self.last_pub = {}
        self.quality_cache = {}
        self.unmapped_seen = set()
        self._lock = threading.Lock()
        self.started = dt.datetime.now(dt.timezone.utc)

    # ------------------------------------------------------------- topics

    def subscriptions(self):
        subs = {"%s/#" % RAW_ROOT}
        for c in self.checks:
            for t in c.topics():
                if not t.startswith(RAW_ROOT):
                    subs.add(t)
        return sorted(subs)

    # ------------------------------------------------------------ inkomend

    def handle(self, topic, payload, now=None):
        """[(topic, payload-str)] om te publiceren."""
        now = now or dt.datetime.now(dt.timezone.utc)
        with self._lock:
            self.counts["msgs_in"] += 1

        # Kwaliteits-companion: geen eigen bericht, maar de kwaliteit van een
        # ander punt. Onthouden en verder niets publiceren.
        for suffix in (".Q",):
            if topic.endswith(suffix):
                base = topic[: -len(suffix)]
                with self._lock:
                    self.quality_cache[base] = _scalar(payload)
                    self.counts["quality_updates"] += 1
                return []

        # Een cross-check mag ook op canonieke topics van de MONOLIET luisteren.
        out = []
        if not topic.startswith(RAW_ROOT):
            d = _decode(payload)
            for c in self.checks:
                if c.observe(topic, d.get("value"), d.get("quality")):
                    out.extend(self._emit_checks(now))
            return out

        rule = self.by_raw.get(topic)
        if rule is None:
            with self._lock:
                self.counts["unmapped"] += 1
                if topic not in self.unmapped_seen:
                    self.unmapped_seen.add(topic)
                    log.warning("ongemapt raw-topic %s (nog %d andere)",
                                topic, len(self.unmapped_seen) - 1)
            return []

        d = _decode(payload)
        raw_value = d.get("value", d.get("v"))
        raw_ts = d.get("ts", d.get("timestamp"))
        quality_raw = d.get("status", d.get("quality"))
        if quality_raw is None:
            quality_raw = self.quality_cache.get(topic)

        try:
            msg = condition(rule, raw_value, quality_raw=quality_raw,
                            raw_ts=raw_ts, received_at=now)
        except Refused as e:
            # Een gat, geen nul. Geteld en gelogd, maar er gaat NIETS uit.
            with self._lock:
                self.counts["refused"] += 1
            log.debug("geweigerd %s: %s", topic, e.reason)
            return []

        with self._lock:
            self.last_seen[topic] = now
            prev = self.last_pub.get(topic)
            if suppress(rule, prev, msg):
                self.counts["suppressed"] += 1
                return []
            self.last_pub[topic] = msg
            self.counts["msgs_out"] += 1

        if not self.model_layer:
            # De modellaag staat uit. Raw stroomt door, canoniek valt stil.
            with self._lock:
                self.counts["model_layer_off"] += 1
            return []

        out.append((rule["canonical_topic"], json.dumps(msg, ensure_ascii=False)))
        for c in self.checks:
            if c.observe(rule["canonical_topic"], msg["value"], msg["quality"]):
                out.extend(self._emit_checks(now))
        return out

    def _emit_checks(self, now):
        out = []
        for c in self.checks:
            for topic, body in c.evaluate(now):
                out.append((topic, json.dumps(body, ensure_ascii=False)))
        return out

    # -------------------------------------------------------------- status

    def stale_topics(self, now=None):
        now = now or dt.datetime.now(dt.timezone.utc)
        with self._lock:
            return [t for t, r in self.by_raw.items()
                    if is_stale(r, self.last_seen.get(t), now)]

    def status(self):
        stale = self.stale_topics()
        with self._lock:
            c = dict(self.counts)
        return {
            "service": "vla-park-conditioner",
            "model_layer": self.model_layer,
            "uptime_s": int((dt.datetime.now(dt.timezone.utc) - self.started).total_seconds()),
            "rules": len(self.by_raw),
            "msgs_in": c.get("msgs_in", 0),
            "msgs_out": c.get("msgs_out", 0),
            "suppressed": c.get("suppressed", 0),
            "refused": c.get("refused", 0),
            "unmapped": c.get("unmapped", 0),
            "unmapped_topics": sorted(self.unmapped_seen)[:20],
            "quality_updates": c.get("quality_updates", 0),
            "stale": len(stale),
            "stale_topics": stale[:20],
            "checks": [c_.id for c_ in self.checks],
            "gates": {
                "unmapped_must_be_zero": c.get("unmapped", 0) == 0,
                "note": ("unmapped != 0 betekent dat een machine iets publiceert "
                         "wat het model niet kent. Dat is een deploy-gate en geen "
                         "waarschuwing: ongemodelleerde data hoort nergens te landen."),
            },
        }


def _decode(payload):
    s = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else str(payload)
    s = s.strip()
    if s[:1] == "{":
        try:
            d = json.loads(s)
            if isinstance(d, dict):
                return d
        except ValueError:
            pass
    return {"value": s}


def _scalar(payload):
    d = _decode(payload)
    v = d.get("value", d.get("v"))
    return v


# ------------------------------------------------------------------- http

def make_http(cond):
    class H(BaseHTTPRequestHandler):
        def _send(self, code, body):
            b = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):  # noqa: N802
            if self.path.rstrip("/") in ("/api/v1/status", "/status"):
                return self._send(200, cond.status())
            if self.path.rstrip("/") in ("/health", "/api/v1/health"):
                return self._send(200, {"status": "ok"})
            return self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/api/v1/model-layer":
                return self._send(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "geen geldige json"})
            cond.model_layer = bool(body.get("enabled", True))
            log.warning("modellaag %s", "AAN" if cond.model_layer else "UIT")
            return self._send(200, {
                "model_layer": cond.model_layer,
                "note": ("Raw blijft stromen; de canonieke UNS ligt stil. "
                         "Grafana en de UI lopen leeg terwijl de fabriek draait."
                         if not cond.model_layer else "Canonieke UNS is weer live."),
            })

        def log_message(self, *_a):
            pass

    return H


# ------------------------------------------------------------------- main

def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s", datefmt="%H:%M:%S")

    path = os.path.join(MODEL_DIR, "park-conditioning.json")
    with io.open(path, encoding="utf-8") as fh:
        rules = json.load(fh)["rules"]
    checks = xc.build(xc.DEFAULT_CHECKS, DQ_ROOT)
    cond = Conditioner(rules, checks)
    log.info("%d conditioning-regels, %d cross-checks", len(rules), len(checks))

    port = int(os.environ.get("HTTP_PORT", 8080))
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_http(cond))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("status op :%d/api/v1/status", port)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                         client_id="vla-park-conditioner")
    user = os.environ.get("MQTT_USERNAME")
    if user:
        client.username_pw_set(user, os.environ.get("MQTT_PASSWORD") or None)

    def on_connect(c, _u, _f, rc, _p=None):
        for t in cond.subscriptions():
            c.subscribe(t, qos=0)
            log.info("geabonneerd op %s", t)

    def on_message(c, _u, msg):
        try:
            for topic, payload in cond.handle(msg.topic, msg.payload):
                c.publish(topic, payload, qos=0)
        except Exception:  # noqa: BLE001
            log.exception("fout bij %s", msg.topic)

    client.on_connect = on_connect
    client.on_message = on_message
    client.connect(os.environ.get("MQTT_HOST", "monstermq"),
                   int(os.environ.get("MQTT_PORT", 1883)), 60)
    client.loop_start()

    # Stale-melding. Stilte is geen nul, dus hij hoort actief gerapporteerd te
    # worden en niet pas op te vallen als iemand een grafiek opent.
    try:
        while True:
            time.sleep(15)
            st = cond.status()
            client.publish("%s/conditioner-01/Status/msgs_in" % DQ_ROOT,
                           json.dumps({"value": st["msgs_in"], "unit": "",
                                       "quality": "GOOD"}), qos=0)
            for key in ("msgs_out", "suppressed", "refused", "unmapped", "stale"):
                client.publish("%s/conditioner-01/Status/%s" % (DQ_ROOT, key),
                               json.dumps({"value": st[key], "unit": "",
                                           "quality": "GOOD"}), qos=0)
            if st["unmapped"]:
                log.warning("%d ongemapte berichten, %d unieke topics",
                            st["unmapped"], len(st["unmapped_topics"]))
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        srv.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
