# -*- coding: utf-8 -*-
"""vla-park-gateway: de silo zonder technisch excuus.

Twee machines loggen hun data naar een Postgres-tabel. Geen protocolgat, geen
verouderd apparaat, geen DCOM-ellende. Een moderne database met een prima
query-interface, en hij is onzichtbaar voor de fabriek omdat niemand hem heeft
aangesloten.

Dat is de reden dat dit eiland het zwaarst telt in de demo. Bij OPC-DA kun je
nog naar het protocol wijzen. Hier kan dat niet: de data staat er gewoon, netjes,
en er is nooit iemand langsgekomen om hem op te halen.

MonsterMQ heeft geen SQL-BRONconnector (`jdbcLogger` en verwanten zijn sinks),
dus deze gateway pollt de tabel en publiceert op DEZELFDE raw-root als alle
andere transporten. De conditioner weet daardoor niet dat dit uit een database
kwam, en dat hoort ook zo.

Waarom Postgres en niet SQL Server. Het teruggedraaide werk gebruikte SQL Server
met pymssql. SQL Server Express in Docker eist ~2 GB RAM als ondergrens en die
hebben we niet op een 8 GB VPS naast de rest. Postgres doet hetzelfde werk in
~60 MB. Het punt van dit eiland is niet het merk van de database.
"""

from __future__ import annotations

import io
import json
import logging
import os
import time

log = logging.getLogger("park-gateway")

MODEL_DIR = os.environ.get("MODEL_DIR", "/model")
RAW_ROOT = os.environ.get("RAW_ROOT", "raw/vla-park")

DDL = """
CREATE TABLE IF NOT EXISTS tag_history (
    id        BIGSERIAL PRIMARY KEY,
    tag       TEXT              NOT NULL,
    val       DOUBLE PRECISION,
    valstr    TEXT,
    q         TEXT              NOT NULL DEFAULT 'Good',
    ts_local  TEXT              NOT NULL
);
CREATE INDEX IF NOT EXISTS tag_history_tag_id ON tag_history (tag, id DESC);
"""


def load_rules():
    with io.open(os.path.join(MODEL_DIR, "park-conditioning.json"),
                 encoding="utf-8") as fh:
        rules = json.load(fh)["rules"]
    return [r for r in rules if r.get("protocol") == "sql"]


def connect():
    import psycopg
    dsn = os.environ.get("PG_DSN") or (
        "host=%s port=%s dbname=%s user=%s password=%s" % (
            os.environ.get("PG_HOST", "vla-park-db"),
            os.environ.get("PG_PORT", "5432"),
            os.environ.get("PG_DB", "vendor_e"),
            os.environ.get("PG_USER", "vendor_e"),
            os.environ.get("PG_PASSWORD", "vendor_e")))
    return psycopg.connect(dsn, autocommit=True)


def poll_latest(conn, tags):
    """De laatste rij per tag.

    DISTINCT ON is Postgres-specifiek en precies wat je hier wilt: één query
    voor alle tags in plaats van N queries. Bij een historian-tabel die alleen
    maar groeit is dat het verschil tussen een gateway die het bijhoudt en een
    die achterloopt.
    """
    sql = """
        SELECT DISTINCT ON (tag) tag, val, valstr, q, ts_local
          FROM tag_history
         WHERE tag = ANY(%s)
      ORDER BY tag, id DESC
    """
    with conn.cursor() as cur:
        cur.execute(sql, (list(tags),))
        return cur.fetchall()


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s", datefmt="%H:%M:%S")

    import paho.mqtt.client as mqtt

    rules = load_rules()
    by_tag = {r["native_name"]: r for r in rules}
    machines = sorted({r["source_system"] for r in rules})
    log.info("%d sql-tags over %s", len(rules), machines)
    if not rules:
        log.warning("geen sql-machines in het model; niets te doen")

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="vla-park-gateway")
    user = os.environ.get("MQTT_USERNAME")
    if user:
        client.username_pw_set(user, os.environ.get("MQTT_PASSWORD") or None)
    client.connect(os.environ.get("MQTT_HOST", "monstermq"),
                   int(os.environ.get("MQTT_PORT", 1883)), 60)
    client.loop_start()

    conn = None
    interval = float(os.environ.get("POLL_INTERVAL_S", "2.0"))
    hb = "DairyWorks/Vla-B/DataQuality/connector-gateway/Status/last_poll_ok"

    try:
        while True:
            started = time.time()
            sent = 0
            try:
                if conn is None:
                    conn = connect()
                    with conn.cursor() as cur:
                        cur.execute(DDL)
                    log.info("verbonden met Postgres")
                for tag, val, valstr, q, ts_local in poll_latest(conn, by_tag):
                    r = by_tag.get(tag)
                    if r is None:
                        continue
                    # De payload draagt de kolommen zoals ze in de tabel staan.
                    # Kwaliteit is hier een TEKST ("Good"/"Suspect"/"Bad") en de
                    # tijd is lokale wandkloktijd zonder zone; de conditioner
                    # weet daar raad mee, deze gateway hoeft dat niet te weten.
                    body = {"value": valstr if val is None else val,
                            "quality": q, "ts": ts_local}
                    client.publish("%s/%s/%s" % (RAW_ROOT, r["source_system"], tag),
                                   json.dumps(body), qos=0)
                    sent += 1
            except Exception as e:  # noqa: BLE001
                log.warning("pollronde mislukt: %s", e)
                try:
                    if conn is not None:
                        conn.close()
                except Exception:  # noqa: BLE001
                    pass
                conn = None

            # Hartslag: een gateway die dood gaat levert stilte op, en stilte
            # lijkt op een machine die niets doet.
            client.publish(hb, json.dumps({
                "value": sent, "unit": "", "quality": "GOOD",
                "tags": len(by_tag), "machines": machines}), qos=0)
            time.sleep(max(0.1, interval - (time.time() - started)))
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        if conn is not None:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
