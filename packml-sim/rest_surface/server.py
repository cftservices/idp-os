"""REST-oppervlak voor een parkmachine.

Een OEM-machinegateway met een eigen HTTP-API. Vorm bewust gelijk aan de
bestaande `ip21-stub`: `GET /tags/{naam}/current`.

Wat dit protocol laat zien is niet het transport maar de GEVOLGEN ervan:

  - REST kent geen push. Iemand moet pollen, en dus is de frequentie een keuze
    van de datalaag en niet van de machine. Mist de poller een piek, dan heeft
    die piek nooit bestaan voor de rest van de wereld.
  - Het tijdstempel in de payload is de tijd waarop de MACHINE hem opschreef,
    in epoch-milliseconden, en zegt niets over wanneer jij hem ophaalde.
  - Er zit GEEN kwaliteit in. De conditioner concludeert daarom UNCERTAIN, en
    niet GOOD-bij-gebrek-aan-beter.

Waarden gaan als string de deur uit, inclusief af en toe een "N/A", want zo doet
vendor-d dat. Die worden verderop geweigerd en nooit naar nul geforceerd.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger("packml-sim.rest")


class RestSurface:
    """Serveert de laatste native waarden over HTTP."""

    def __init__(self, cfg):
        rest = cfg.get("rest") or {}
        self.port = int(rest.get("port", 8000))
        self.unit_id = cfg.get("unit_id") or cfg.get("equipment") or "unit"
        self.by_native = {s["native_name"]: s for s in (cfg.get("signals") or [])}
        self._values = {}
        self._lock = threading.Lock()
        self._srv = None
        self._thread = None

    def publish(self, native_name, value, timestamp=None):
        with self._lock:
            self._values[native_name] = {"value": value, "ts": timestamp}

    def publish_many(self, pairs, timestamp=None):
        with self._lock:
            for name, value in pairs:
                self._values[name] = {"value": value, "ts": timestamp}

    def snapshot(self):
        with self._lock:
            return dict(self._values)

    def start(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def _send(self, code, body):
                b = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):  # noqa: N802
                path = self.path.split("?")[0].rstrip("/")
                if path in ("/health", ""):
                    return self._send(200, {"status": "ok", "unit": outer.unit_id})
                if path == "/tags":
                    return self._send(200, {"tags": sorted(outer.by_native)})
                if path.startswith("/tags/") and path.endswith("/current"):
                    name = path[len("/tags/"):-len("/current")]
                    with outer._lock:  # noqa: SLF001
                        rec = outer._values.get(name)  # noqa: SLF001
                    if rec is None:
                        # Nog nooit gepubliceerd. 404, en met opzet GEEN nul:
                        # een tag zonder waarde is een gat en geen meting.
                        return self._send(404, {"error": "no value yet", "tag": name})
                    body = {"tag": name, "value": rec["value"]}
                    if rec["ts"] is not None:
                        body["ts"] = rec["ts"]
                    return self._send(200, body)
                return self._send(404, {"error": "not found"})

            def log_message(self, *_a):
                pass

        self._srv = ThreadingHTTPServer(("0.0.0.0", self.port), H)
        self._thread = threading.Thread(target=self._srv.serve_forever,
                                        name="rest-%s" % self.unit_id, daemon=True)
        self._thread.start()
        log.info("REST op :%d, %d tags", self.port, len(self.by_native))

    def stop(self):
        if self._srv is not None:
            self._srv.shutdown()
        if self._thread:
            self._thread.join(timeout=5.0)
