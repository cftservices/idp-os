"""SQL-oppervlak: de machine logt naar zijn eigen database.

Zo werkt een legacy historian-export in de praktijk. De machine schrijft zijn
waarden naar een tabel in zijn eigen database, en daar staan ze. Netjes,
queryable, en volstrekt onzichtbaar voor de rest van de fabriek zolang niemand
hem aansluit.

De vorm van de tabel is met opzet die van een echte export en niet die van een
modern schema:

    tag       TEXT               VLA.CPK01.GLUE_TEMP_C
    val       DOUBLE PRECISION   het getal, of NULL bij tekst
    valstr    TEXT               tekst, of NULL bij een getal
    q         TEXT               'Good' / 'Suspect' / 'Bad'
    ts_local  TEXT               lokale wandkloktijd, ZONDER zone

Die laatste twee zijn waar het om gaat. Kwaliteit als tekst en tijd als lokale
string zonder zone: allebei prima leesbaar voor een mens, allebei onbruikbaar
voor een machine zonder dat iemand van buiten vertelt hoe je ze moet lezen.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("packml-sim.sql")

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

# De tabel groeit onbeperkt als niemand hem opruimt, en dat is precies wat er in
# de praktijk gebeurt. Hier houden we hem klein: een demo hoeft geen jaren
# historie te dragen, en een volgelopen disk op de VPS legt de hele stack om.
TRIM = """
DELETE FROM tag_history
 WHERE id < (SELECT COALESCE(MAX(id), 0) - %s FROM tag_history)
"""


class SqlSurface:
    """Schrijft de native waarden naar een Postgres-tabel."""

    def __init__(self, cfg):
        sql = cfg.get("sql") or {}
        self.unit_id = cfg.get("unit_id") or cfg.get("equipment") or "unit"
        self.host = sql.get("host", "vla-park-db")
        self.port = int(sql.get("port", 5432))
        self.database = sql.get("database", "vendor_e")
        self.table = sql.get("table", "tag_history")
        self.user = sql.get("user", "vendor_e")
        self.password = sql.get("password", "vendor_e")
        self.keep_rows = int(sql.get("keep_rows", 20000))
        self._conn = None
        self._lock = threading.Lock()
        self._writes = 0

    def _connect(self):
        import psycopg
        dsn = ("host=%s port=%d dbname=%s user=%s password=%s"
               % (self.host, self.port, self.database, self.user, self.password))
        conn = psycopg.connect(dsn, autocommit=True, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute(DDL)
        log.info("%s: verbonden met Postgres %s/%s", self.unit_id,
                 self.host, self.database)
        return conn

    def start(self):
        try:
            self._conn = self._connect()
        except Exception as e:  # noqa: BLE001
            # Niet fataal: de database kan later opkomen. Wel luid, want een
            # machine die stil niets logt lijkt op een machine die stilstaat.
            log.warning("%s: nog geen Postgres (%s), probeer het later opnieuw",
                        self.unit_id, e)

    def stop(self):
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None

    def write(self, rows):
        """rows = [(tag, waarde, kwaliteitstekst, lokale-tijd-string)]."""
        if not rows:
            return 0
        with self._lock:
            if self._conn is None:
                try:
                    self._conn = self._connect()
                except Exception as e:  # noqa: BLE001
                    log.debug("%s: Postgres nog niet bereikbaar (%s)", self.unit_id, e)
                    return 0
            try:
                with self._conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO tag_history (tag, val, valstr, q, ts_local) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        [(t, (None if isinstance(v, str) else float(v)),
                          (v if isinstance(v, str) else None), q, ts)
                         for t, v, q, ts in rows])
                    self._writes += len(rows)
                    if self._writes >= 2000:
                        self._writes = 0
                        cur.execute(TRIM % "%s", (self.keep_rows,))
                return len(rows)
            except Exception as e:  # noqa: BLE001
                log.warning("%s: schrijven mislukt (%s), verbinding wordt herzet",
                            self.unit_id, e)
                try:
                    self._conn.close()
                except Exception:  # noqa: BLE001
                    pass
                self._conn = None
                return 0
