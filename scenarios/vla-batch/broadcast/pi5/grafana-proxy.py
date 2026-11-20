#!/usr/bin/env python3
"""Grafana for the livestream on the Pi, with a login the browser never sees.

Listens on 127.0.0.1:3002 and forwards to Grafana on 127.0.0.1:3001 (the ssh tunnel to the VPS).
The proxy logs in itself as a Viewer (GRAFANA_USER / GRAFANA_PASS from ~/.broadcast.env) and adds
the session cookie to every request. Why not a public dashboard: the TDengine plugin queries from
the browser via /api/datasources/proxy, and that route needs a login (401 on 20-11-2026).

- On a 401 it logs in again once and retries.
- When Grafana rotates the session (Set-Cookie grafana_session) it keeps the new cookie.
- The Origin header is dropped: Grafana's CSRF check compares it with the Host it sees, and the
  browser's localhost:3002 never matches.
- Only for the kiosk browser on the Pi: binds to 127.0.0.1, no TLS, no websockets (Grafana Live
  falls back to polling).

Started by stream.sh; stops with the stream.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN = ("127.0.0.1", int(os.environ.get("GRAFANA_PROXY_PORT", "3002")))
UPSTREAM = ("127.0.0.1", 3001)
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "cookie", "origin", "content-length"}
SESSION = re.compile(r"grafana_session=([^;]+)")

_cookie = ""
_lock = threading.Lock()


def log(msg: str) -> None:
    print(f"grafana-proxy: {msg}", file=sys.stderr, flush=True)


def login() -> bool:
    global _cookie
    body = json.dumps({"user": os.environ["GRAFANA_USER"], "password": os.environ["GRAFANA_PASS"]})
    conn = http.client.HTTPConnection(*UPSTREAM, timeout=15)
    try:
        conn.request("POST", "/grafana/login", body=body, headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        r.read()
        for v in r.headers.get_all("Set-Cookie") or []:
            m = SESSION.search(v)
            if m and m.group(1):
                with _lock:
                    _cookie = m.group(1)
                log(f"ingelogd ({r.status})")
                return True
        log(f"inloggen mislukt ({r.status})")
        return False
    finally:
        conn.close()


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # stil; fouten gaan via log()
        pass

    def _forward(self, body: bytes | None, retry: bool = True) -> None:
        global _cookie
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        with _lock:
            cookie = _cookie
        if cookie:
            headers["Cookie"] = f"grafana_session={cookie}"
        conn = http.client.HTTPConnection(*UPSTREAM, timeout=60)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            r = conn.getresponse()
            data = r.read()
        except OSError as e:
            self.send_error(502, f"Grafana niet bereikbaar: {e}")
            return
        finally:
            conn.close()
        # Niet ingelogd: een API geeft 401, een pagina stuurt met 302 door naar /login.
        naar_login = r.status in (301, 302, 303, 307) and "/login" in (r.headers.get("Location") or "")
        if (r.status == 401 or naar_login) and retry and not self.path.startswith("/grafana/login"):
            try:
                ok = login()
            except OSError:
                ok = False
            if ok:
                self._forward(body, retry=False)
                return
        for v in r.headers.get_all("Set-Cookie") or []:
            m = SESSION.search(v)
            if m and m.group(1):
                with _lock:
                    _cookie = m.group(1)
        self.send_response(r.status)
        for k, v in r.headers.items():
            if k.lower() not in HOP and k.lower() != "set-cookie":
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _body(self) -> bytes | None:
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else None

    def do_GET(self) -> None:
        self._forward(None)

    def do_HEAD(self) -> None:
        self._forward(None)

    def do_POST(self) -> None:
        self._forward(self._body())

    def do_PUT(self) -> None:
        self._forward(self._body())


def main() -> int:
    if not os.environ.get("GRAFANA_USER") or not os.environ.get("GRAFANA_PASS"):
        log("GRAFANA_USER/GRAFANA_PASS ontbreken; ik stop")
        return 2
    # Mag mislukken: de tunnel komt tegelijk op. De eerste 401 logt alsnog in. Een uitzondering hier
    # zou de proxy laten stoppen, en daarmee (wait -n in stream.sh) de hele stream.
    def eerste_login() -> None:
        for _ in range(60):  # tot 5 minuten, elke 5 s
            try:
                if login():
                    return
            except OSError as e:
                log(f"inloggen nog niet gelukt ({e})")
            time.sleep(5)

    threading.Thread(target=eerste_login, daemon=True).start()
    srv = ThreadingHTTPServer(LISTEN, Proxy)
    log(f"luistert op http://{LISTEN[0]}:{LISTEN[1]} -> http://{UPSTREAM[0]}:{UPSTREAM[1]}")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
