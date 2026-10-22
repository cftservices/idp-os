"""MonsterMQ -> TDengine bridge (open-source PoC, GEEN taosX/Enterprise).

Bewijst het kernpunt uit de research: je hebt de Enterprise-connectoren
(taosX MQTT/OPC-UA) NIET nodig om TDengine als Store-laag te gebruiken.
Deze ~150 regels Python doen wat AVEVA/TDengine achter een paywall zetten:

    MonsterMQ (MQTT broker, gratis OPC-UA/MQTT)
        |  subscribe idp/# + DairyPlant/# + bakery-works-utrecht/#
        v
    bridge.py  (paho-mqtt -> InfluxDB line protocol)
        |  POST http://tdengine:6041/influxdb/v1/write
        v
    TDengine TSDB-OSS (schemaless: 1 super-table, 1 sub-table per topic)

Schemaless schrijven via taosAdapter betekent: geen CREATE TABLE, geen
kolom-management. TDengine leidt het schema af uit de line-protocol tags/fields
en maakt automatisch een super-table `telemetry` met een sub-table per signaal.

Env vars (defaults werken binnen het idp docker-network):
    MQTT_HOST       monstermq
    MQTT_PORT       1883
    MQTT_TOPICS     idp/#,DairyPlant/#,bakery-works-utrecht/#   (comma-separated)
    TD_URL          http://tdengine:6041
    TD_DB           idp
    TD_USER         root
    TD_PASS         taosdata
    FLUSH_SECONDS   1.0
    FLUSH_LINES     200
"""
import json
import os
import threading
import time

import paho.mqtt.client as mqtt
import requests

MQTT_HOST = os.getenv("MQTT_HOST", "monstermq")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPICS = os.getenv("MQTT_TOPICS", "idp/#,DairyPlant/#,bakery-works-utrecht/#").split(",")

TD_URL = os.getenv("TD_URL", "http://tdengine:6041").rstrip("/")
TD_DB = os.getenv("TD_DB", "idp")
TD_USER = os.getenv("TD_USER", "root")
TD_PASS = os.getenv("TD_PASS", "taosdata")
TD_AUTH = (TD_USER, TD_PASS)

# legacy = topic+src (bestaand gedrag, NIET wijzigen voor draaiende bridges).
# uns    = site/line/area/machine/tagname als losse tags, zodat je per machine
#          kunt queryen. Alleen voor nieuwe databases; een bestaande
#          super-tabel kun je niet zomaar van tag-set wisselen.
TAG_SCHEME = os.getenv("TAG_SCHEME", "legacy").strip().lower()

FLUSH_SECONDS = float(os.getenv("FLUSH_SECONDS", "1.0"))
FLUSH_LINES = int(os.getenv("FLUSH_LINES", "200"))

# ── line-protocol buffer (thread-safe) ──────────────────────────────────────
_buffer: list[str] = []
_lock = threading.Lock()


def _escape_tag(value: str) -> str:
    """Tag keys/values escapen: komma, spatie, '=' moeten geescaped (ILP-regel)."""
    return value.replace("\\", "\\\\").replace(",", "\\,").replace(" ", "\\ ").replace("=", "\\=")


def _escape_str_field(value: str) -> str:
    """String field value: dubbele quotes + backslash escapen."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _num_field(v) -> str:
    """bool/int/float -> ILP numeriek 'value=<double>'."""
    if isinstance(v, bool):
        return f"value={1 if v else 0}"
    return f"value={float(v)}"


def _coerce_field(payload: str) -> str:
    """MQTT-payload -> ILP field. Numeriek -> double; bool -> 0/1; rest -> string.

    UNS-payloads zijn JSON-objecten ({"value":60.0,"timestamp":..,"status":0}).
    Die pakken we uit naar het numerieke `value`-veld zodat ANOMALY_WINDOW /
    forecasting (TDgpt) en Grafana-trends erop kunnen werken. Alleen als de
    payload noch een kaal getal noch JSON-met-numerieke-value is, valt het terug
    op het string-veld `valuestr` (recept-namen, fase-strings, e.d.).
    """
    p = payload.strip()
    low = p.lower()
    if low in ("true", "false"):
        return f"value={1 if low == 'true' else 0}"
    try:
        return f"value={float(p)}"  # kaal getal -> double
    except ValueError:
        pass
    # UNS JSON {"value": <num>, ...} -> numeriek value uitpakken
    if p[:1] in ("{", "["):
        try:
            obj = json.loads(p)
        except (ValueError, TypeError):
            obj = None
        if isinstance(obj, dict) and "value" in obj:
            v = obj["value"]
            if isinstance(v, bool) or isinstance(v, (int, float)):
                return _num_field(v)
            # value is een string (recept/fase) -> string-veld
            return f'valuestr="{_escape_str_field(str(v))}"'
    # niet-numeriek / onbekend -> string-veld
    return f'valuestr="{_escape_str_field(p)}"'


def _uns_tags(topic: str) -> str:
    """UNS-topic -> losse, indexeerbare tags.

    DairyWorks/Vla-B/Cook/pasteuriser-01/Status/temp_out_C
      -> site=DairyWorks, line=Vla-B, area=Cook,
         machine=pasteuriser-01, tagname=temp_out_C

    Zonder deze tags kun je in TDengine alleen per machine filteren met een LIKE
    op de volledige topic-string. Dat werkt wel, maar het is traag en het maakt
    elke query afhankelijk van de exacte topic-opbouw. Met losse tags is het:

        select last(value) from idp_park.telemetry
         where machine = 'pasteuriser-01' group by tagname;

    TDengine indexeert tags, dus dit is de goedkope weg. Kwaliteit staat er MET
    OPZET niet bij: tags bepalen de sub-tabel, dus een tag die per bericht kan
    wisselen zou bij elke kwaliteitswissel een nieuwe sub-tabel aanmaken.
    """
    parts = topic.split("/")
    site = parts[0] if len(parts) > 0 else ""
    line = parts[1] if len(parts) > 1 else ""
    area = parts[2] if len(parts) > 2 else ""
    machine = parts[3] if len(parts) > 3 else ""
    tagname = parts[-1] if len(parts) > 4 else ""
    return (f"site={_escape_tag(site)},line={_escape_tag(line)},"
            f"area={_escape_tag(area)},machine={_escape_tag(machine)},"
            f"tagname={_escape_tag(tagname)}")


def to_line(topic: str, payload: str, ts_ms: int) -> str:
    """Bouw 1 InfluxDB-line-protocol regel.

    super-table = telemetry
    field       = value (double) of valuestr (string)

    Twee tag-schema's, via TAG_SCHEME:

      legacy  topic (volledig) + src (eerste segment). De STANDAARD, en wat de
              bestaande bridges van de monoliet en de bakkerij gebruiken. Niet
              wijzigen: de tag-set bepaalt de sub-tabel-structuur van een
              bestaande super-tabel, en de Grafana-dashboards hangen eraan.
      uns     site/line/area/machine/tagname als losse tags, zodat je per
              machine kunt queryen zonder LIKE. Voor lijn Vla-B.
    """
    if TAG_SCHEME == "uns":
        tags = _uns_tags(topic) + f",topic={_escape_tag(topic)}"
    else:
        src = topic.split("/", 1)[0]
        tags = f"topic={_escape_tag(topic)},src={_escape_tag(src)}"
    field = _coerce_field(payload)
    return f"telemetry,{tags} {field} {ts_ms}"


def flush() -> None:
    global _buffer
    with _lock:
        if not _buffer:
            return
        lines, _buffer = _buffer, []
    body = "\n".join(lines)
    try:
        r = requests.post(
            f"{TD_URL}/influxdb/v1/write",
            params={"db": TD_DB, "precision": "ms"},
            data=body.encode("utf-8"),
            auth=TD_AUTH,
            timeout=10,
        )
        if r.status_code >= 300:
            print(f"[bridge] write {r.status_code}: {r.text[:200]}", flush=True)
        else:
            print(f"[bridge] wrote {len(lines)} points", flush=True)
    except requests.RequestException as exc:
        print(f"[bridge] write failed: {exc}", flush=True)


def flush_loop() -> None:
    while True:
        time.sleep(FLUSH_SECONDS)
        flush()


# ── TDengine bootstrap ──────────────────────────────────────────────────────
def ensure_database() -> None:
    """Wacht tot taosAdapter er is en maak de database (PRECISION ms)."""
    sql = f"CREATE DATABASE IF NOT EXISTS {TD_DB} PRECISION 'ms'"
    for attempt in range(1, 31):
        try:
            r = requests.post(f"{TD_URL}/rest/sql", data=sql.encode("utf-8"), auth=TD_AUTH, timeout=5)
            if r.status_code < 300:
                print(f"[bridge] database '{TD_DB}' ready", flush=True)
                return
            print(f"[bridge] create-db {r.status_code}: {r.text[:160]}", flush=True)
        except requests.RequestException as exc:
            print(f"[bridge] waiting for TDengine ({attempt}/30): {exc}", flush=True)
        time.sleep(2)
    raise SystemExit("[bridge] TDengine not reachable after 60s")


# ── MQTT callbacks (paho 2.x VERSION2 API) ──────────────────────────────────
def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code != 0:
        print(f"[bridge] MQTT connect failed: {reason_code}", flush=True)
        return
    for topic in MQTT_TOPICS:
        client.subscribe(topic.strip(), qos=0)
    print(f"[bridge] subscribed: {', '.join(t.strip() for t in MQTT_TOPICS)}", flush=True)


def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode("utf-8", errors="replace")
    except Exception:
        return
    line = to_line(msg.topic, payload, int(time.time() * 1000))
    with _lock:
        _buffer.append(line)
        full = len(_buffer) >= FLUSH_LINES
    if full:
        flush()


def main() -> None:
    ensure_database()
    threading.Thread(target=flush_loop, daemon=True).start()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = on_connect
    client.on_message = on_message
    print(f"[bridge] connecting to MQTT {MQTT_HOST}:{MQTT_PORT}", flush=True)
    client.connect(MQTT_HOST, MQTT_PORT, keepalive=30)
    client.loop_forever()


if __name__ == "__main__":
    main()
