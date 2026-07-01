"""MQTT bus for the MES engine.

Responsibilities:
  - subscribe to DairyWorks/# Status topics, keep a latest-value tag cache
  - publish commands to units (Start/Stop/Fault/Inject/...)
  - publish engine events (Order/HU/Sample/OEE) on DairyWorks/Plant/MES/...

Degrades gracefully: if paho is missing or the broker is absent, every call is a
no-op (with the tag cache still usable), so the engine runs fully offline.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Optional

log = logging.getLogger("mes.bus")

try:
    import paho.mqtt.client as mqtt

    _HAVE_PAHO = True
except Exception:  # pragma: no cover - env without paho
    mqtt = None
    _HAVE_PAHO = False


class MesBus:
    """Thin MQTT client wrapper; safe to use with no broker present."""

    def __init__(
        self,
        model,
        host: str = "monstermq",
        port: int = 1883,
        client_id: str = "mes-engine",
        namespace_root: Optional[str] = None,
    ):
        self.model = model
        self.host = host
        self.port = port
        self.client_id = client_id
        self.namespace_root = namespace_root or (
            model.namespace_root if model else "DairyWorks/Plant"
        )
        self.connected = False
        self._tags: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.client = None

    # ---------------------------------------------------------------- lifecycle

    def start(self, wait_connected_s: float = 3.0) -> bool:
        """Attempt to connect. Returns True if connected, False if degraded."""
        if not _HAVE_PAHO:
            log.warning("paho-mqtt not installed — bus running in offline (no-op) mode")
            return False
        try:
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=self.client_id,
                clean_session=True,
            )
            self.client.on_connect = self._on_connect
            self.client.on_message = self._on_message
            self.client.reconnect_delay_set(min_delay=1, max_delay=30)
            self.client.connect_async(self.host, self.port, keepalive=30)
            self.client.loop_start()

            evt = threading.Event()
            self._connected_evt = evt
            connected = evt.wait(timeout=wait_connected_s)
            if not connected:
                log.warning(
                    "MQTT broker %s:%s not reachable in %.1fs — offline mode "
                    "(engine still runs, publishes are no-ops until reconnect)",
                    self.host, self.port, wait_connected_s,
                )
            return connected
        except Exception as e:
            log.warning("MQTT start failed (%s) — offline mode", e)
            self.client = None
            return False

    def stop(self) -> None:
        if self.client is not None:
            try:
                self.client.loop_stop()
                self.client.disconnect()
            except Exception:
                pass

    # ---------------------------------------------------------------- callbacks

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected = True
            sub = f"{self.namespace_root}/#"
            client.subscribe(sub, qos=0)
            log.info("MES bus connected, subscribed to %s", sub)
            evt = getattr(self, "_connected_evt", None)
            if evt is not None:
                evt.set()
        else:
            log.error("MES bus connect failed rc=%s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        with self._lock:
            self._tags[msg.topic] = payload

    # ---------------------------------------------------------------- publish

    def _publish(self, topic: str, payload: str, retain: bool = False, qos: int = 0) -> bool:
        if self.client is None or not self.connected:
            log.debug("publish skipped (offline): %s = %s", topic, payload)
            return False
        try:
            self.client.publish(topic, payload, qos=qos, retain=retain)
            return True
        except Exception as e:
            log.debug("publish error %s: %s", topic, e)
            return False

    def command(self, equipment: str, cmd: str, payload: Any = "1") -> bool:
        """Publish a command to a unit's Command/{cmd} topic (payload as string)."""
        topic = self.model.command_topic(equipment, cmd)
        if isinstance(payload, (dict, list)):
            payload_s = json.dumps(payload, separators=(",", ":"))
        elif isinstance(payload, bool):
            payload_s = "1" if payload else "0"
        else:
            payload_s = str(payload)
        ok = self._publish(topic, payload_s, qos=1)
        log.debug("CMD %s %s=%s (sent=%s)", equipment, cmd, payload_s, ok)
        return ok

    def emit_event(self, kind: str, body: dict) -> bool:
        """Publish an engine event on DairyWorks/Plant/MES/{kind}."""
        topic = f"{self.namespace_root}/MES/{kind}"
        return self._publish(topic, json.dumps(body, separators=(",", ":"), default=str))

    # ---------------------------------------------------------------- tag cache

    def latest(self, topic: str) -> Any:
        with self._lock:
            return self._tags.get(topic)

    def latest_tag(self, equipment: str, tag: str) -> Any:
        return self.latest(self.model.status_topic(equipment, tag))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._tags)
