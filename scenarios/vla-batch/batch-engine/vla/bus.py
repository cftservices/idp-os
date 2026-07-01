"""MQTT bus for the Vla batch-engine (data-layer / UNS consumer + commander).

Responsibilities:
  - subscribe to DairyWorks/Vla/# and keep a latest-value snapshot cache (GET /tags)
  - publish Command messages to equipment / line-level Batch (StartBatch, Stop,
    SetSetpoint, InjectFault, ClearFault, TakeSample) as JSON payloads

Degrades gracefully: if paho is missing or the broker is absent, publishes are
no-ops and the snapshot cache stays usable, so the engine runs fully offline.

Contract: §UNS topics (payload {"value","unit","ts","quality"}); commands
{"value","ts","source"} or method-args JSON.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from . import model as M

log = logging.getLogger("vla.bus")

try:
    import paho.mqtt.client as mqtt

    _HAVE_PAHO = True
except Exception:  # pragma: no cover - env without paho
    mqtt = None
    _HAVE_PAHO = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VlaBus:
    """Thin MQTT client wrapper; safe to use with no broker present."""

    def __init__(
        self,
        host: str = "monstermq",
        port: int = 1883,
        client_id: str = "vla-batch-engine",
        uns_root: str = M.UNS_ROOT,
    ):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.uns_root = uns_root
        self.connected = False
        self._tags: dict[str, Any] = {}
        self._lock = threading.Lock()
        self.client = None
        self._connected_evt: Optional[threading.Event] = None

    # ---------------------------------------------------------------- lifecycle

    def start(self, wait_connected_s: float = 3.0) -> bool:
        """Attempt to connect. Returns True if connected, False if degraded."""
        if not _HAVE_PAHO:
            log.warning("paho-mqtt not installed — bus running offline (no-op)")
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
                    "MQTT broker %s:%s not reachable in %.1fs — offline mode",
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
            sub = f"{self.uns_root}/#"
            client.subscribe(sub, qos=0)
            log.info("vla bus connected, subscribed to %s", sub)
            if self._connected_evt is not None:
                self._connected_evt.set()
        else:
            log.error("vla bus connect failed rc=%s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        with self._lock:
            self._tags[msg.topic] = payload

    # ---------------------------------------------------------------- publish

    def _publish(self, topic: str, payload: str, retain: bool = False, qos: int = 1) -> bool:
        if self.client is None or not self.connected:
            log.debug("publish skipped (offline): %s = %s", topic, payload)
            return False
        try:
            self.client.publish(topic, payload, qos=qos, retain=retain)
            return True
        except Exception as e:
            log.debug("publish error %s: %s", topic, e)
            return False

    def command(self, equipment: str, cmd: str, value: Any = None,
                extra: Optional[dict] = None) -> bool:
        """Publish a Command message to the equipment/line Command/{cmd} topic.

        Payload = {"value":.., "ts":.., "source":.., **extra}. Method-style
        commands (StartBatch/InjectFault/...) put their args in `extra`.
        """
        topic = M.command_topic(equipment, cmd)
        body: dict[str, Any] = {"ts": _now_iso(), "source": "batch-engine"}
        if value is not None:
            body["value"] = value
        if extra:
            body.update(extra)
        payload = json.dumps(body, separators=(",", ":"), default=str)
        ok = self._publish(topic, payload, qos=1)
        log.debug("CMD %s %s=%s (sent=%s)", equipment, cmd, payload, ok)
        return ok

    def start_batch(self, recipe_id: str) -> bool:
        """Line-level StartBatch(recipeId)."""
        return self.command("Batch", "StartBatch", extra={"recipeId": recipe_id})

    def set_setpoint(self, target: str, value: float) -> bool:
        """Line-level SetSetpoint(target, value)."""
        return self.command("Batch", "SetSetpoint",
                            extra={"target": target, "value": value})

    def inject_fault(self, fault_id: str, magnitude: float) -> bool:
        return self.command("Batch", "InjectFault",
                            extra={"faultId": fault_id, "magnitude": magnitude})

    def clear_fault(self) -> bool:
        return self.command("Batch", "ClearFault")

    def take_sample(self, sample_type: str) -> bool:
        return self.command("Batch", "TakeSample",
                            extra={"sampleType": sample_type})

    def stop_batch(self) -> bool:
        return self.command("Batch", "Stop")

    # ---------------------------------------------------------------- tag cache

    def latest_raw(self, topic: str) -> Any:
        with self._lock:
            return self._tags.get(topic)

    def latest_value(self, equipment: str, tag: str) -> Any:
        """Return the scalar `value` from a Status topic's JSON payload."""
        raw = self.latest_raw(M.status_topic(equipment, tag))
        if raw is None:
            return None
        try:
            obj = json.loads(raw)
            return obj.get("value", raw) if isinstance(obj, dict) else raw
        except (ValueError, TypeError):
            return raw

    def snapshot(self) -> dict[str, Any]:
        """dict topic -> value (scalar where payload is the contract JSON)."""
        out: dict[str, Any] = {}
        with self._lock:
            items = list(self._tags.items())
        for topic, raw in items:
            try:
                obj = json.loads(raw)
                out[topic] = obj.get("value", raw) if isinstance(obj, dict) else raw
            except (ValueError, TypeError):
                out[topic] = raw
        return out
