"""Direct OPC-UA control path to the vla-factory (write/command side).

Since MonsterMQ has a native OPC-UA client, the standalone MQTT connector is now
an optional fallback (default OFF). So MQTT Command-topics may have no consumer.
The CONTROL path (write/command) therefore goes DIRECTLY to the factory's OPC-UA
server, decoupled from the ingest choice. Telemetry READING stays over MQTT/UNS
(the bus tag-cache) — only writing/commanding lives here.

Calls the 6 methods on the line-level Batch object
`ns=2;s=DairyWorks.Vla.Batch` (§OPC-UA methods):
  StartBatch(recipeId:str) -> Int32
  Stop() -> Int32
  SetSetpoint(target:str, value:float) -> Int32
  TakeSample(sampleType:str) -> Int32
  InjectFault(faultId:str, magnitude:float) -> Int32
  ClearFault() -> Int32
Return 0 = OK, >0 = geweigerd.

Robust + offline-safe:
  * connect-per-call with a short timeout + a few retries with backoff;
  * if the factory is unreachable, log a warning and return a
    {"connected": False, ...} status (NO exception) so the engine — and the
    offline selftest — keep working with no factory present.

FastAPI is sync, so each call bridges to async via asyncio.run() on a fresh
short-lived event loop (simplest robust option; the factory calls are rare
control actions, not a hot path).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

log = logging.getLogger("vla.opcua_control")

try:
    from asyncua import Client, ua  # type: ignore

    _HAVE_ASYNCUA = True
except Exception:  # pragma: no cover - env without asyncua
    Client = None  # type: ignore
    ua = None  # type: ignore
    _HAVE_ASYNCUA = False

DEFAULT_OPCUA_URL = "opc.tcp://vla-factory:4840/DairyWorks"

# Node-id of the line-level Batch object that owns the methods (§OPC-UA, ns=2).
BATCH_NODE_ID = "ns=2;s=DairyWorks.Vla.Batch"

# Valid SetSetpoint target strings (§OPC-UA methods) — used for validation only.
SETPOINT_TARGETS = {
    "cook.setpoint_C", "cook.hold_sec", "cooler.target_C", "mixing.agitator_rpm",
    "dose.milk", "dose.sugar", "dose.starch", "dose.cocoa", "receiving.fat",
}


class OpcuaControl:
    """Direct OPC-UA method caller for factory control (offline-safe)."""

    def __init__(
        self,
        url: Optional[str] = None,
        connect_timeout_s: float = 3.0,
        retries: int = 2,
        backoff_s: float = 0.5,
    ):
        self.url = url or os.environ.get("OPCUA_URL", DEFAULT_OPCUA_URL)
        self.connect_timeout_s = connect_timeout_s
        self.retries = max(0, int(retries))
        self.backoff_s = backoff_s
        self.available = _HAVE_ASYNCUA

    # ---------------------------------------------------------------- internals

    async def _call_async(self, method: str, *args: Any) -> dict:
        """Connect, call `method` on the Batch object, disconnect. Async core."""
        last_err: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            client = Client(url=self.url, timeout=self.connect_timeout_s)
            try:
                await asyncio.wait_for(client.connect(), timeout=self.connect_timeout_s)
                try:
                    node = client.get_node(BATCH_NODE_ID)
                    # call_method accepts the browsename/qualified method name.
                    result = await node.call_method(f"2:{method}", *args)
                    rc = int(result) if result is not None else 0
                    return {"connected": True, "method": method, "rc": rc,
                            "accepted": rc == 0, "url": self.url}
                finally:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
            except Exception as e:  # connect/call failure -> retry/backoff
                last_err = e
                if attempt < self.retries:
                    await asyncio.sleep(self.backoff_s * (attempt + 1))
        log.warning("OPC-UA control unreachable at %s (%s: %s) — offline no-op",
                    self.url, method, last_err)
        return {"connected": False, "method": method, "rc": None,
                "accepted": False, "url": self.url, "error": str(last_err)}

    def _call(self, method: str, *args: Any) -> dict:
        """Sync bridge: run the async call on a fresh event loop. Offline-safe."""
        if not _HAVE_ASYNCUA:
            log.warning("asyncua not installed — OPC-UA control is a no-op")
            return {"connected": False, "method": method, "rc": None,
                    "accepted": False, "url": self.url, "error": "asyncua missing"}
        try:
            return asyncio.run(self._call_async(method, *args))
        except RuntimeError:
            # e.g. called from within a running loop — use a private loop.
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self._call_async(method, *args))
            finally:
                loop.close()
        except Exception as e:  # last-resort guard: never raise from control
            log.warning("OPC-UA control call failed (%s) — offline no-op", e)
            return {"connected": False, "method": method, "rc": None,
                    "accepted": False, "url": self.url, "error": str(e)}

    # ---------------------------------------------------------------- methods

    def start_batch(self, recipe_id: str) -> dict:
        return self._call("StartBatch", str(recipe_id))

    def stop(self) -> dict:
        return self._call("Stop")

    def set_setpoint(self, target: str, value: float) -> dict:
        if target not in SETPOINT_TARGETS:
            log.warning("unknown SetSetpoint target %r (allowed: %s)",
                        target, sorted(SETPOINT_TARGETS))
        return self._call("SetSetpoint", str(target), float(value))

    def take_sample(self, sample_type: str) -> dict:
        return self._call("TakeSample", str(sample_type))

    def inject_fault(self, fault_id: str, magnitude: float) -> dict:
        return self._call("InjectFault", str(fault_id), float(magnitude))

    def clear_fault(self) -> dict:
        return self._call("ClearFault")
