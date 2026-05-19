"""MQTT publisher + UNS topic builder.

Topic convention (bakery-works-utrecht scenario):

    {site}/{line}/{area}/{equipment}/Status/{tag}
    {site}/{line}/{area}/{equipment}/Command/{cmd}

  site      = bakery-works-utrecht
  line      = line-a | line-b | shared | enterprise
  area      = mixing | bulk-ferm | forming | proofing | baking | cooling | packaging | cip
  equipment = mixer-01 | tunnel-oven-01 | wrapper-01 | ...
  tag       = StateCurrent | temperature | dough-temp | output-rate | ...

Examples:
    bakery-works-utrecht/line-a/mixing/mixer-01/Status/StateCurrentStr      "Execute"
    bakery-works-utrecht/line-a/baking/tunnel-oven-01/zone-3/temperature   198.4
    bakery-works-utrecht/line-a/mixing/mixer-01/Command/Start              1
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

import paho.mqtt.client as mqtt


log = logging.getLogger(__name__)


@dataclass
class TopicBuilder:
    """Builds UNS-compliant topic strings for one physics unit."""

    site: str
    line: str
    area: str
    equipment: str

    @property
    def base(self) -> str:
        return f"{self.site}/{self.line}/{self.area}/{self.equipment}"

    def status(self, tag: str) -> str:
        return f"{self.base}/Status/{tag}"

    def command_filter(self) -> str:
        return f"{self.base}/Command/#"

    def parse_command(self, topic: str) -> Optional[str]:
        """Extract the command tail from a topic, or None if not a command."""
        prefix = f"{self.base}/Command/"
        if topic.startswith(prefix):
            return topic[len(prefix):]
        return None


class MQTTPublisher:
    """Thin wrapper around paho-mqtt with auto-reconnect + JSON payload helper.

    Subscribes to `{base}/Command/#` and routes incoming commands to
    `on_command(cmd, payload_str)`. Threadsafe — paho runs its own loop.
    """

    def __init__(
        self,
        host: str,
        port: int,
        client_id: str,
        topic_builder: TopicBuilder,
        username: Optional[str] = None,
        password: Optional[str] = None,
        on_command: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.client_id = client_id
        self.topics = topic_builder
        self._on_command = on_command
        self._connected = threading.Event()

        self.client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            clean_session=True,
        )
        if username:
            self.client.username_pw_set(username, password or "")
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(min_delay=1, max_delay=30)

    # ------------------------------------------------------------------ lifecycle

    def start(self, wait_connected_s: float = 10.0) -> None:
        log.info("Connecting to MQTT %s:%s as %s", self.host, self.port, self.client_id)
        self.client.connect_async(self.host, self.port, keepalive=30)
        self.client.loop_start()
        if not self._connected.wait(timeout=wait_connected_s):
            log.warning("MQTT not connected after %.1fs — continuing in background", wait_connected_s)

    def stop(self) -> None:
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    # ------------------------------------------------------------------- publish

    def publish(self, tag: str, value, retain: bool = False, qos: int = 0) -> None:
        topic = self.topics.status(tag)
        payload = self._serialize(value)
        self.client.publish(topic, payload, qos=qos, retain=retain)

    def publish_many(self, values: dict, retain: bool = False, qos: int = 0) -> None:
        for tag, value in values.items():
            self.publish(tag, value, retain=retain, qos=qos)

    @staticmethod
    def _serialize(value) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return f"{value:.4f}" if isinstance(value, float) else str(value)
        if isinstance(value, (dict, list)):
            return json.dumps(value, separators=(",", ":"))
        return str(value)

    # ------------------------------------------------------------------ callbacks

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            log.info("MQTT connected: %s", self.client_id)
            self._connected.set()
            sub = self.topics.command_filter()
            client.subscribe(sub, qos=1)
            log.info("Subscribed to %s", sub)
        else:
            log.error("MQTT connect failed: rc=%s", reason_code)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        log.warning("MQTT disconnected: rc=%s — paho will auto-reconnect", reason_code)
        self._connected.clear()

    def _on_message(self, client, userdata, msg):
        if self._on_command is None:
            return
        cmd = self.topics.parse_command(msg.topic)
        if cmd is None:
            return
        try:
            payload = msg.payload.decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        try:
            self._on_command(cmd, payload)
        except Exception:
            log.exception("on_command handler raised for %s", msg.topic)
