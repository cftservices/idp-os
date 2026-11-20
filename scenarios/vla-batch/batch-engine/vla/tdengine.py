"""Minimal TDengine REST client (POST /rest/sql), standard library only.

Same endpoint and credentials as the bridge and Grafana use (TD_URL, TD_USER, TD_PASS). The
engine only writes KPI snapshots with it; it never reads telemetry this way.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from typing import Optional


class TdError(RuntimeError):
    pass


class TdClient:
    def __init__(self, url: Optional[str] = None, user: Optional[str] = None,
                 password: Optional[str] = None, db: Optional[str] = None, timeout: float = 20.0):
        self.url = (url or os.environ.get("TD_URL", "http://vla-tdengine:6041")).rstrip("/")
        self.db = db or os.environ.get("TD_DB", "idp")
        user = user or os.environ.get("TD_USER", "root")
        password = password if password is not None else os.environ.get("TD_PASS", "taosdata")
        self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.timeout = timeout

    def sql(self, statement: str) -> dict:
        req = urllib.request.Request(f"{self.url}/rest/sql", data=statement.encode("utf-8"),
                                     method="POST",
                                     headers={"Authorization": self._auth,
                                              "Content-Type": "text/plain"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            body = json.load(r)
        if body.get("code", 0) != 0:
            raise TdError(f"TDengine {body.get('code')}: {body.get('desc')}")
        return body


def quote(s: str) -> str:
    """A TDengine string literal. Tag values here are our own ids and units, no user input."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def number(v) -> str:
    if v is None:
        return "NULL"
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        return "NULL"
    return repr(round(v, 6))
