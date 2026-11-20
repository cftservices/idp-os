# -*- coding: utf-8 -*-
"""Storingen inschieten op lijn Vla-B, vanaf een REST-aanroep.

Het convergentiepunt. Vier ingangen komen hier samen:

    UI-paneel      -> Next.js BFF -> deze module
    REST           -> deze module
    scenario-runner-> REST -> deze module
    MQTT           -> rechtstreeks naar de machine, langs deze module heen

Die laatste is met opzet niet via hier: een MQTT-client hoort te werken zonder
dat de MES-laag draait. Maar alle vier landen op DEZELFDE FaultInjector in de
machine, dus een storing die via MQTT is ingeschoten is hier zichtbaar en
andersom.

## Welk transport per machine

    OPC UA / OPC-DA   methode-aanroep op het machine-object
    al het andere     MQTT Command-topic

Elke parkmachine luistert op zijn Command-topic, ongeacht protocol: park_runner
houdt die MQTT-verbinding altijd open, ook als het oppervlak Modbus of SQL is.
Toch krijgt OPC UA de voorkeur waar hij bestaat, want een methode-aanroep werkt
ook als de BROKER plat ligt. Bij een demo die over storingen gaat, wil je niet
dat je storingsknop afhangt van het onderdeel dat misschien stuk is.

Offline-veilig, net als opcua_control: zonder machine geeft hij
`{"connected": false}` terug en gooit hij niets. Een MES-laag die crasht omdat
een simulator er niet is, is erger dan geen knop.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

log = logging.getLogger("vla.park_control")

MODEL_DIR = os.environ.get("PARK_MODEL_DIR", "/model")
CANONICAL_ROOT = os.environ.get("CANONICAL_ROOT", "DairyWorks/Vla-B")
RAW_ROOT = os.environ.get("PARK_RAW_ROOT", "raw/vla-park")
PRESENCE_STALE_S = float(os.environ.get("PARK_PRESENCE_STALE_S", "30") or 30)


class ParkPresence:
    """Levert een parkmachine data? Gemeten op de raw-topics.

    Zonder dit lijkt een storing op een machine die niet draait gewoon actief:
    MQTT is fire-and-forget, dus het bericht gaat weg en niemand zegt dat er
    niemand luistert. Raw (`raw/vla-park/{machine}/...`) en niet de canonieke
    boom, want die valt ook stil als alleen de modellaag uit staat.

    Drie standen per machine: True (data binnen `stale_s`), False (we luisteren
    en er kwam niets) en None (we luisteren niet, dus we weten het niet). Alleen
    False weigert; onbekend is niet hetzelfde als uit.
    """

    def __init__(self, stale_s: float = PRESENCE_STALE_S,
                 clock: Callable[[], float] = time.monotonic):
        self.stale_s = stale_s
        self.clock = clock
        self.listening = False
        self._seen: dict[str, float] = {}
        self._lock = threading.Lock()
        self._client = None

    def on_topic(self, topic: str) -> None:
        parts = topic.split("/")
        root = RAW_ROOT.split("/")
        if len(parts) <= len(root) or parts[:len(root)] != root:
            return
        with self._lock:
            self._seen[parts[len(root)]] = self.clock()

    def age(self, equipment_id: str) -> Optional[float]:
        with self._lock:
            t = self._seen.get(equipment_id)
        return None if t is None else round(self.clock() - t, 1)

    def online(self, equipment_id: str) -> Optional[bool]:
        if not self.listening:
            return None
        age = self.age(equipment_id)
        return age is not None and age <= self.stale_s

    def start(self, host: str, port: int = 1883) -> bool:
        """Luisteren op RAW_ROOT/#. Offline-veilig: zonder broker blijft het None."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:  # pragma: no cover
            return False

        def on_connect(client, _u, _f, rc, _p=None):
            if rc == 0:
                client.subscribe(RAW_ROOT + "/#", qos=0)
                self.listening = True

        def on_disconnect(*_a, **_k):
            self.listening = False

        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                            client_id="vla-batch-engine-park-presence")
            c.on_connect = on_connect
            c.on_disconnect = on_disconnect
            c.on_message = lambda _c, _u, msg: self.on_topic(msg.topic)
            c.reconnect_delay_set(min_delay=1, max_delay=30)
            c.connect_async(host, port, keepalive=30)
            c.loop_start()
            self._client = c
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("park-presence niet gestart (%s); aanwezigheid onbekend", e)
            return False

try:
    from asyncua import Client
    HAVE_ASYNCUA = True
except ImportError:  # pragma: no cover
    Client = None
    HAVE_ASYNCUA = False


class ParkControl:
    """Storingscatalogus plus de route naar elke machine."""

    def __init__(self, model_dir: Optional[str] = None, mqtt_publish=None,
                 connect_timeout_s: float = 3.0, retries: int = 1,
                 presence: Optional[ParkPresence] = None):
        self.model_dir = model_dir or MODEL_DIR
        self.mqtt_publish = mqtt_publish
        self.presence = presence
        self.connect_timeout_s = connect_timeout_s
        self.retries = retries
        self._lock = threading.Lock()
        self.machines: dict = {}
        self.load_error: Optional[str] = None
        self._load()

    # ------------------------------------------------------------------ model

    def _load(self):
        """Catalogus + endpoints uit de gegenereerde artefacten.

        park-faults.json is gegenereerd uit het FAULTS-attribuut van de
        physics-klassen, dus de catalogus kan geen storing claimen die de
        fysica niet implementeert.
        """
        try:
            with io.open(os.path.join(self.model_dir, "park-faults.json"),
                         encoding="utf-8") as fh:
                faults = json.load(fh)["machines"]
        except Exception as e:  # noqa: BLE001
            self.load_error = "park-faults.json onleesbaar: %s" % e
            log.warning(self.load_error)
            return

        areas, endpoints = {}, {}
        try:
            with io.open(os.path.join(self.model_dir, "isa95-vla.json"),
                         encoding="utf-8") as fh:
                model = json.load(fh)
            for site in model["enterprise"]["sites"]:
                for line in site["lines"]:
                    if line["id"] != "LINE-VLA-B":
                        continue
                    for area in line["areas"]:
                        for wc in area["work_centers"]:
                            eq = wc["equipment_id"]
                            areas[eq] = area["name"]
                            p = wc.get("park") or {}
                            if p.get("protocol") in ("opc-ua", "opc-da"):
                                endpoints[eq] = {
                                    "url": p.get("opcua_endpoint"),
                                    "ns": int(p.get("opcua_namespace_index", 2)),
                                }
        except Exception as e:  # noqa: BLE001
            log.warning("isa95-vla.json onleesbaar (%s); alleen MQTT-route", e)

        for eq, spec in faults.items():
            self.machines[eq] = {
                "equipment_id": eq,
                "area": areas.get(eq, "Unknown"),
                "physics_type": spec.get("physics_type"),
                "vendor": spec.get("vendor"),
                "faults": list(spec.get("faults") or []),
                "opcua": endpoints.get(eq),
                "transport": "opcua" if eq in endpoints else "mqtt",
                "active": {},
            }
        log.info("park-control: %d machines, %d storingen",
                 len(self.machines),
                 sum(len(m["faults"]) for m in self.machines.values()))

    # -------------------------------------------------------------- catalogus

    def _online(self, equipment_id: str) -> Optional[bool]:
        return self.presence.online(equipment_id) if self.presence else None

    def catalogue(self) -> dict:
        with self._lock:
            machines = [
                {k: v for k, v in m.items() if k != "active"} | {
                    "active_faults": dict(m["active"]),
                    "online": self._online(m["equipment_id"]),
                    "last_seen_s": (self.presence.age(m["equipment_id"])
                                    if self.presence else None)}
                for m in sorted(self.machines.values(),
                                key=lambda x: x["equipment_id"])
            ]
        states = [m["online"] for m in machines]
        return {
            "machines": machines,
            "total_faults": sum(len(m["faults"]) for m in machines),
            "load_error": self.load_error,
            # None = we don't listen, so we don't know; False = nothing arrives
            "park_live": None if not states or None in states else any(states),
        }

    # ---------------------------------------------------------------- verzenden

    async def _opcua_call(self, spec: dict, method: str, *args: Any) -> dict:
        url = spec["url"]
        last = None
        for attempt in range(self.retries + 1):
            client = Client(url=url, timeout=self.connect_timeout_s)
            try:
                await asyncio.wait_for(client.connect(),
                                       timeout=self.connect_timeout_s)
                try:
                    node = client.get_node("ns=%d;s=%s" % (spec["ns"], method))
                    obj = client.get_node("ns=%d;s=%s" % (spec["ns"], spec["unit"]))
                    rc = await obj.call_method(node, *args)
                    rc = int(rc) if rc is not None else 0
                    return {"connected": True, "transport": "opcua",
                            "rc": rc, "accepted": rc == 0}
                finally:
                    try:
                        await client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
            except Exception as e:  # noqa: BLE001
                last = e
                if attempt < self.retries:
                    await asyncio.sleep(0.4 * (attempt + 1))
        return {"connected": False, "transport": "opcua", "rc": None,
                "accepted": False, "error": str(last)}

    def _send(self, machine: dict, method: str, payload: dict, *args: Any) -> dict:
        """OPC UA waar het kan, anders MQTT. Nooit een exception naar boven."""
        spec = machine.get("opcua")
        if spec and spec.get("url") and HAVE_ASYNCUA:
            try:
                s = dict(spec, unit=machine["equipment_id"])
                return asyncio.run(self._opcua_call(s, method, *args))
            except Exception as e:  # noqa: BLE001
                log.warning("opc-ua-aanroep %s op %s mislukt (%s), val terug op MQTT",
                            method, machine["equipment_id"], e)

        if self.mqtt_publish is None:
            return {"connected": False, "transport": "none", "accepted": False,
                    "error": "geen MQTT-publisher en geen bereikbare OPC-UA-machine"}
        topic = "%s/%s/%s/Command/Fault/%s" % (
            CANONICAL_ROOT, machine["area"], machine["equipment_id"],
            "Inject" if method == "InjectFault" else "Clear")
        try:
            self.mqtt_publish(topic, json.dumps(payload))
            # MQTT is fire-and-forget: 'accepted' zegt hier dat het BERICHT weg
            # is, niet dat de machine hem heeft verwerkt. Dat verschil hoort
            # zichtbaar te zijn en niet weggepoetst.
            return {"connected": True, "transport": "mqtt", "topic": topic,
                    "accepted": True, "note": "fire-and-forget, geen bevestiging"}
        except Exception as e:  # noqa: BLE001
            return {"connected": False, "transport": "mqtt", "accepted": False,
                    "error": str(e)}

    # -------------------------------------------------------------------- api

    def inject(self, equipment_id: str, fault_id: str, magnitude: float = 1.0) -> dict:
        m = self.machines.get(equipment_id)
        if m is None:
            return {"ok": False, "error": "onbekende machine %r" % equipment_id,
                    "known": sorted(self.machines)}
        if fault_id not in m["faults"]:
            # Weigeren, niet doorsturen. De catalogus komt uit de fysica; een
            # storing die er niet in staat bestaat niet, en hem toch versturen
            # levert een knop op die niets doet.
            return {"ok": False, "error": "machine %s kent storing %r niet"
                                          % (equipment_id, fault_id),
                    "known": m["faults"]}
        if self._online(equipment_id) is False:
            # Weigeren, niet versturen: over MQTT zou het bericht gewoon weggaan en
            # stond de knop daarna op actief, terwijl er geen machine is die luistert.
            return {"ok": False, "reason": "machine_offline",
                    "equipment_id": equipment_id, "fault": fault_id,
                    "error": "machine %s levert geen data (draait het park?)"
                             % equipment_id}
        magnitude = max(0.0, min(float(magnitude), 1.0))
        res = self._send(m, "InjectFault",
                         {"fault": fault_id, "magnitude": magnitude},
                         fault_id, magnitude)
        if res.get("accepted"):
            with self._lock:
                m["active"][fault_id] = magnitude
        return {"ok": bool(res.get("accepted")), "equipment_id": equipment_id,
                "fault": fault_id, "magnitude": magnitude, **res}

    def clear(self, equipment_id: str, fault_id: Optional[str] = None) -> dict:
        m = self.machines.get(equipment_id)
        if m is None:
            return {"ok": False, "error": "onbekende machine %r" % equipment_id}
        res = self._send(m, "ClearFault", {"fault": fault_id or ""},
                         fault_id or "")
        if res.get("accepted"):
            with self._lock:
                if fault_id:
                    m["active"].pop(fault_id, None)
                else:
                    m["active"].clear()
        return {"ok": bool(res.get("accepted")), "equipment_id": equipment_id,
                "fault": fault_id, **res}

    def clear_all(self) -> dict:
        """Alles uit. De knop die je na een demo wilt hebben."""
        out = [self.clear(eq) for eq in sorted(self.machines)]
        return {"ok": all(r.get("ok") for r in out) if out else True,
                "cleared": len(out), "results": out}
