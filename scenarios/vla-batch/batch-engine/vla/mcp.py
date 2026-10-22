"""Read-only MCP-ingang op de datalaag.

Een AI-agent (Copilot, OpenCode, Claude) vraagt de fabriek via het Model
Context Protocol: JSON-RPC 2.0 over één HTTP-POST. Alleen `initialize`,
`tools/list`, `tools/call` en `ping`; elke tool leest en geen enkele schrijft.
Dat is geen afspraak maar de constructie: er is simpelweg geen schrijvende tool
geregistreerd, en de registry weigert er een aan te nemen.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

log = logging.getLogger("vla.mcp")

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "dairyworks-datalaag", "version": "0.1.0"}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, dict] = {}

    def register(self, name: str, description: str, schema: dict,
                 fn: Callable[[dict], Any], read_only: bool = True) -> None:
        if not read_only:
            raise ValueError("deze MCP-ingang is read-only; schrijvende tools zijn niet toegestaan")
        self._tools[name] = {"name": name, "description": description,
                             "inputSchema": schema, "fn": fn}

    def list(self) -> list[dict]:
        return [{"name": t["name"], "description": t["description"],
                 "inputSchema": t["inputSchema"],
                 "annotations": {"readOnlyHint": True, "destructiveHint": False}}
                for t in self._tools.values()]

    def call(self, name: str, args: dict) -> Any:
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name]["fn"](args or {})


def _ok(rid, result) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def handle(req: Any, registry: ToolRegistry) -> Any:
    if isinstance(req, list):
        out = [r for r in (handle(x, registry) for x in req) if r is not None]
        return out or None
    if not isinstance(req, dict) or req.get("jsonrpc") != "2.0" or "method" not in req:
        return _err(req.get("id") if isinstance(req, dict) else None, -32600,
                    "ongeldig JSON-RPC-verzoek")
    rid = req.get("id")
    method = req["method"]
    params = req.get("params") or {}
    if rid is None:  # notificatie, zoals notifications/initialized
        return None
    if method == "initialize":
        return _ok(rid, {"protocolVersion": PROTOCOL_VERSION,
                         "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": SERVER_INFO,
                         "instructions": "Alleen-lezen toegang tot de datalaag van "
                                         "DairyWorks: context per machine, verliezen "
                                         "in euro's, werkorders, datakwaliteit en "
                                         "betrouwbaarheid."})
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": registry.list()})
    if method == "tools/call":
        name = params.get("name")
        try:
            data = registry.call(name, params.get("arguments") or {})
        except KeyError:
            return _err(rid, -32602, f"onbekende tool: {name}")
        except ValueError as e:
            return _ok(rid, {"isError": True,
                             "content": [{"type": "text", "text": str(e)}]})
        except Exception as e:  # pragma: no cover
            log.exception("mcp tool %s faalde", name)
            return _ok(rid, {"isError": True,
                             "content": [{"type": "text", "text": f"fout: {e}"}]})
        return _ok(rid, {"content": [{"type": "text",
                                      "text": json.dumps(data, ensure_ascii=False,
                                                         default=str)}],
                         "structuredContent": data if isinstance(data, dict)
                         else {"items": data},
                         "isError": False})
    return _err(rid, -32601, f"methode niet ondersteund: {method}")
