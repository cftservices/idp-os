import os

# Keep app startup fast + fully offline in tests: no MQTT wait, no Mongo.
os.environ.setdefault("MQTT_WAIT_S", "0")
os.environ.pop("MONGO_URL", None)
