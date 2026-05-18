"""IoT-publisher — directe MQTT publisher voor CIP + sensor data.

Bedoeld om workshop 1 CONNECT te tonen dat sommige bronnen geen OPC-UA
nodig hebben: een IoT-device of een ESP32 met MQTT-firmware kan direct
naar de broker publiseren. Geen tussenlaag.

Publisher schrijft naar MonsterMQ op `monstermq:1883` (intern docker network)
of `mqtt.techflow24.com:1884` (public). Topic-tree volgt DairyPlant
ISA-95 namespace.
"""
import os
import random
import time

import paho.mqtt.publish as publish


MQTT_HOST = os.getenv("MQTT_HOST", "monstermq")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
PUBLISH_INTERVAL_SEC = float(os.getenv("PUBLISH_INTERVAL_SEC", "1.0"))


# CIP-cyclus simulator — 4 fases die circulair lopen
# Phase 1: pre-rinse (koud water, ~20°C, lage conductivity)
# Phase 2: caustic wash (~85°C, hoge conductivity door NaOH)
# Phase 3: intermediate rinse (~40°C, dalende conductivity)
# Phase 4: final rinse (koud water, weer ~20°C, lage conductivity)
CIP_PHASE_DURATION_SEC = 60  # elke fase 60s = totale cyclus 4 min
CIP_PHASES = {
    1: {"name": "pre-rinse",       "temp": 20.0, "cond": 0.3, "duration": CIP_PHASE_DURATION_SEC},
    2: {"name": "caustic-wash",    "temp": 85.0, "cond": 12.0, "duration": CIP_PHASE_DURATION_SEC},
    3: {"name": "intermediate",    "temp": 40.0, "cond": 4.0, "duration": CIP_PHASE_DURATION_SEC},
    4: {"name": "final-rinse",     "temp": 20.0, "cond": 0.5, "duration": CIP_PHASE_DURATION_SEC},
}


def current_cip_phase(elapsed_sec: int) -> int:
    cycle_pos = elapsed_sec % (4 * CIP_PHASE_DURATION_SEC)
    return cycle_pos // CIP_PHASE_DURATION_SEC + 1


def publish_one(topic: str, value):
    publish.single(
        topic=topic,
        payload=str(value),
        hostname=MQTT_HOST,
        port=MQTT_PORT,
        qos=0,
        retain=False,
    )


def main():
    start = time.time()
    print(f"iot-publisher → {MQTT_HOST}:{MQTT_PORT} every {PUBLISH_INTERVAL_SEC}s")
    while True:
        elapsed = int(time.time() - start)

        # CIP-loop tags
        phase = current_cip_phase(elapsed)
        spec = CIP_PHASES[phase]
        publish_one("DairyPlant/CIP/Loop1/phase", phase)
        publish_one(
            "DairyPlant/CIP/Loop1/temp_C",
            round(spec["temp"] + random.gauss(0, 0.3), 2),
        )
        publish_one(
            "DairyPlant/CIP/Loop1/conductivity_mS",
            round(spec["cond"] + random.gauss(0, 0.1), 3),
        )

        # Ambient sensoren — losse IoT devices die direct in MQTT publishen
        publish_one(
            "DairyPlant/Sensors/AmbientTemp/value",
            round(20.0 + random.gauss(0, 0.3), 2),
        )
        publish_one(
            "DairyPlant/Sensors/AmbientHumidity/value",
            round(60.0 + random.gauss(0, 1.5), 1),
        )

        time.sleep(PUBLISH_INTERVAL_SEC)


if __name__ == "__main__":
    main()
