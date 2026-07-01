"""vla-factory — the DairyWorks chocolate-vla line as ONE OPC-UA server.

This is the "black box with SCADA buttons": a single asyncua Server that exposes the
whole ISA-95 line (Areas Receiving/Mixing/Cook/Cooling/Filling + a line-level Batch
object) over OPC-UA, with the internal batch-physics from physics.py running behind it.

  READ   : status tags are written every ~0.5s from VlaProcess.read() / .batch_status()
  WRITE  : writable setpoints push into the process; the Batch object exposes the
           StartBatch/Stop/SetSetpoint/TakeSample/InjectFault/ClearFault methods.

Contract (vla-build-contract.md §OPC-UA, §ISA-95):
  Endpoint   : opc.tcp://0.0.0.0:4840/DairyWorks
  Namespace  : urn:dairyworks  -> ns=2
  Node-id    : ns=2;s=DairyWorks.Vla.{Area}.{Equipment}.{tag}
               machine-object    ns=2;s=DairyWorks.Vla.{Area}.{Equipment}
               batch line-object ns=2;s=DairyWorks.Vla.Batch

Env:
    OPCUA_ENDPOINT   default opc.tcp://0.0.0.0:4840/DairyWorks
    WRITE_INTERVAL   status write cadence in seconds (default 0.5)
    TICK_INTERVAL    physics tick cadence in seconds (default 0.2)
    AUTOSTART_RECIPE if set (e.g. chocolate-vla-1L) auto-start a batch at boot
"""

from __future__ import annotations

import asyncio
import logging
import os

from asyncua import Server, ua
from asyncua.common.methods import uamethod

from physics import VlaProcess, RECIPES

log = logging.getLogger("vla-factory")
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)

NS_URI = "urn:dairyworks"
SITE = "DairyWorks"
LINE = "Vla"
ENDPOINT = os.environ.get("OPCUA_ENDPOINT", "opc.tcp://0.0.0.0:4840/DairyWorks")
WRITE_INTERVAL = float(os.environ.get("WRITE_INTERVAL", "0.5"))
TICK_INTERVAL = float(os.environ.get("TICK_INTERVAL", "0.2"))
AUTOSTART_RECIPE = os.environ.get("AUTOSTART_RECIPE") or None

# ISA-95 tag map (LOCK — exact browsenames + OPC-UA types) per contract §ISA-95.
# tuple: (tag, VariantType, writable)
AREA_TAGS: dict[tuple[str, str], list[tuple[str, ua.VariantType, bool]]] = {
    ("Receiving", "receiving-tank-01"): [
        ("level_L", ua.VariantType.Double, False),
        ("temp_C", ua.VariantType.Double, False),
        ("fat_setpoint_pct", ua.VariantType.Double, True),
    ],
    ("Mixing", "process-tank-01"): [
        ("level_L", ua.VariantType.Double, False),
        ("temp_C", ua.VariantType.Double, False),
        ("agitator_rpm", ua.VariantType.Double, True),
        ("dose_milk_actual_kg", ua.VariantType.Double, False),
        ("dose_sugar_actual_kg", ua.VariantType.Double, False),
        ("dose_starch_actual_kg", ua.VariantType.Double, False),
        ("dose_cocoa_actual_kg", ua.VariantType.Double, False),
        ("phase", ua.VariantType.String, False),
        ("dose_milk_setpoint_kg", ua.VariantType.Double, True),
        ("dose_sugar_setpoint_kg", ua.VariantType.Double, True),
        ("dose_starch_setpoint_kg", ua.VariantType.Double, True),
        ("dose_cocoa_setpoint_kg", ua.VariantType.Double, True),
    ],
    ("Cook", "cook-unit-01"): [
        ("temp_C", ua.VariantType.Double, False),
        ("setpoint_C", ua.VariantType.Double, True),
        ("hold_sec", ua.VariantType.Double, True),
        ("hold_elapsed_sec", ua.VariantType.Double, False),
        ("viscosity_cP", ua.VariantType.Double, False),
    ],
    ("Cooling", "cooler-01"): [
        ("temp_C", ua.VariantType.Double, False),
        ("target_C", ua.VariantType.Double, True),
    ],
    ("Filling", "filler-01"): [
        ("packs_total", ua.VariantType.Int64, False),
        ("reject_count", ua.VariantType.Int64, False),
        ("pack_size_L", ua.VariantType.Double, False),
    ],
}

# how a writable status/setpoint node maps to a SetSetpoint target string
SETPOINT_TARGETS: dict[tuple[str, str, str], str] = {
    ("Receiving", "receiving-tank-01", "fat_setpoint_pct"): "receiving.fat",
    ("Mixing", "process-tank-01", "agitator_rpm"): "mixing.agitator_rpm",
    ("Mixing", "process-tank-01", "dose_milk_setpoint_kg"): "dose.milk",
    ("Mixing", "process-tank-01", "dose_sugar_setpoint_kg"): "dose.sugar",
    ("Mixing", "process-tank-01", "dose_starch_setpoint_kg"): "dose.starch",
    ("Mixing", "process-tank-01", "dose_cocoa_setpoint_kg"): "dose.cocoa",
    ("Cook", "cook-unit-01", "setpoint_C"): "cook.setpoint_C",
    ("Cook", "cook-unit-01", "hold_sec"): "cook.hold_sec",
    ("Cooling", "cooler-01", "target_C"): "cooler.target_C",
}

# nodes that mirror a live process setpoint (so the browsed value stays truthful)
SETPOINT_READBACK = {
    ("Mixing", "process-tank-01", "dose_milk_setpoint_kg"): lambda p: p.dose_setpoint_kg["milk"],
    ("Mixing", "process-tank-01", "dose_sugar_setpoint_kg"): lambda p: p.dose_setpoint_kg["sugar"],
    ("Mixing", "process-tank-01", "dose_starch_setpoint_kg"): lambda p: p.dose_setpoint_kg["starch"],
    ("Mixing", "process-tank-01", "dose_cocoa_setpoint_kg"): lambda p: p.dose_setpoint_kg["cocoa"],
    ("Cook", "cook-unit-01", "setpoint_C"): lambda p: p.cook_setpoint_C,
    ("Cook", "cook-unit-01", "hold_sec"): lambda p: p.hold_sec,
    ("Cooling", "cooler-01", "target_C"): lambda p: p.cool_target_C,
    ("Mixing", "process-tank-01", "agitator_rpm"): lambda p: p.agitator_setpoint_rpm,
    ("Receiving", "receiving-tank-01", "fat_setpoint_pct"): lambda p: p.fat_setpoint_pct,
}


def _default_for(vtype: ua.VariantType):
    if vtype == ua.VariantType.String:
        return ""
    if vtype == ua.VariantType.Int64:
        return 0
    return 0.0


def _coerce(vtype: ua.VariantType, value):
    if vtype == ua.VariantType.String:
        return str(value)
    if vtype == ua.VariantType.Int64:
        return int(value)
    return float(value)


class VlaServer:
    def __init__(self, process: VlaProcess) -> None:
        self.p = process
        self.server = Server()
        self.idx = 2  # will be set from register_namespace, asserted == 2
        # (area, eq, tag) -> node
        self.read_nodes: dict[tuple[str, str, str], object] = {}
        self.batch_nodes: dict[str, object] = {}
        # last value the SERVER wrote into each writable setpoint node; used to tell a
        # genuine client write apart from the server's own readback echo.
        self._last_setpoint_echo: dict[tuple[str, str, str], float] = {}

    async def init(self) -> None:
        await self.server.init()
        self.server.set_endpoint(ENDPOINT)
        self.server.set_server_name("DairyWorks Vla Factory (OPC-UA)")
        self.idx = await self.server.register_namespace(NS_URI)
        if self.idx != 2:
            log.warning("namespace index is %d, contract expects ns=2 "
                        "(node-id strings still fixed to DairyWorks.Vla.*)", self.idx)
        await self._build_address_space()

    async def _build_address_space(self) -> None:
        objects = self.server.nodes.objects
        idx = self.idx

        line = await objects.add_object(
            ua.NodeId(f"{SITE}.{LINE}", idx, ua.NodeIdType.String),
            ua.QualifiedName(LINE, idx),
        )

        area_objs: dict[str, object] = {}
        for (area, eq), tags in AREA_TAGS.items():
            if area not in area_objs:
                area_objs[area] = await line.add_object(
                    ua.NodeId(f"{SITE}.{LINE}.{area}", idx, ua.NodeIdType.String),
                    ua.QualifiedName(area, idx),
                )
            eq_obj = await area_objs[area].add_object(
                ua.NodeId(f"{SITE}.{LINE}.{area}.{eq}", idx, ua.NodeIdType.String),
                ua.QualifiedName(eq, idx),
            )
            for tag, vtype, writable in tags:
                nid = ua.NodeId(f"{SITE}.{LINE}.{area}.{eq}.{tag}", idx, ua.NodeIdType.String)
                var = await eq_obj.add_variable(
                    nid, ua.QualifiedName(tag, idx),
                    ua.Variant(_default_for(vtype), vtype),
                )
                if writable:
                    await var.set_writable()
                self.read_nodes[(area, eq, tag)] = var

        # --- line-level Batch object ---
        batch_obj = await line.add_object(
            ua.NodeId(f"{SITE}.{LINE}.Batch", idx, ua.NodeIdType.String),
            ua.QualifiedName("Batch", idx),
        )
        for tag in ("state", "batch_id", "active_recipe"):
            nid = ua.NodeId(f"{SITE}.{LINE}.Batch.{tag}", idx, ua.NodeIdType.String)
            var = await batch_obj.add_variable(nid, ua.QualifiedName(tag, idx),
                                               ua.Variant("", ua.VariantType.String))
            self.batch_nodes[tag] = var

        await self._add_batch_methods(batch_obj, idx)
        log.info("address space built: %d read/setpoint nodes + Batch object (methods) at %s (ns=%d)",
                 len(self.read_nodes), ENDPOINT, idx)

    async def _add_batch_methods(self, batch_obj, idx: int) -> None:
        p = self.p

        @uamethod
        def start_batch(parent, recipe_id: str) -> ua.Int32:
            return ua.Int32(p.start_batch(str(recipe_id)))

        @uamethod
        def stop(parent) -> ua.Int32:
            return ua.Int32(p.stop())

        @uamethod
        def set_setpoint(parent, target: str, value: float) -> ua.Int32:
            return ua.Int32(p.set_setpoint(str(target), float(value)))

        @uamethod
        def take_sample(parent, sample_type: str) -> ua.Int32:
            return ua.Int32(p.take_sample(str(sample_type)))

        @uamethod
        def inject_fault(parent, fault_id: str, magnitude: float) -> ua.Int32:
            return ua.Int32(p.inject_fault(str(fault_id), float(magnitude)))

        @uamethod
        def clear_fault(parent) -> ua.Int32:
            return ua.Int32(p.clear_fault())

        base = f"{SITE}.{LINE}.Batch"
        S, D, I = ua.VariantType.String, ua.VariantType.Double, ua.VariantType.Int32
        await batch_obj.add_method(ua.NodeId(base + ".StartBatch", idx, ua.NodeIdType.String),
                                   ua.QualifiedName("StartBatch", idx), start_batch, [S], [I])
        await batch_obj.add_method(ua.NodeId(base + ".Stop", idx, ua.NodeIdType.String),
                                   ua.QualifiedName("Stop", idx), stop, [], [I])
        await batch_obj.add_method(ua.NodeId(base + ".SetSetpoint", idx, ua.NodeIdType.String),
                                   ua.QualifiedName("SetSetpoint", idx), set_setpoint, [S, D], [I])
        await batch_obj.add_method(ua.NodeId(base + ".TakeSample", idx, ua.NodeIdType.String),
                                   ua.QualifiedName("TakeSample", idx), take_sample, [S], [I])
        await batch_obj.add_method(ua.NodeId(base + ".InjectFault", idx, ua.NodeIdType.String),
                                   ua.QualifiedName("InjectFault", idx), inject_fault, [S, D], [I])
        await batch_obj.add_method(ua.NodeId(base + ".ClearFault", idx, ua.NodeIdType.String),
                                   ua.QualifiedName("ClearFault", idx), clear_fault, [], [I])

    # ------------------------------------------------------------------ loops
    async def _apply_writable_setpoints(self) -> None:
        """Poll writable nodes; if a client wrote one (value differs from the value the
        server last echoed into that node), push it into the process."""
        for (area, eq, tag), target in SETPOINT_TARGETS.items():
            key = (area, eq, tag)
            node = self.read_nodes.get(key)
            if node is None:
                continue
            try:
                val = await node.read_value()
            except Exception:
                continue
            if val is None:
                continue
            echoed = self._last_setpoint_echo.get(key)
            # First pass (no echo yet) is server-initialisation, never a client write.
            if echoed is None:
                continue
            if abs(float(val) - float(echoed)) > 1e-6:
                self.p.set_setpoint(target, float(val))

    async def _write_status(self) -> None:
        snap = self.p.read()
        for (area, eq), tags in snap.items():
            for tag, value in tags.items():
                node = self.read_nodes.get((area, eq, tag))
                if node is None:
                    continue
                vtype = _vtype_for(area, eq, tag)
                try:
                    await node.write_value(ua.Variant(_coerce(vtype, value), vtype))
                except Exception:
                    pass
        # reflect live setpoints back so a browse shows the true value; remember the
        # echoed value so the next apply-pass can distinguish a real client write.
        for (area, eq, tag), readback in SETPOINT_READBACK.items():
            key = (area, eq, tag)
            node = self.read_nodes.get(key)
            if node is None:
                continue
            cur = float(readback(self.p))
            try:
                await node.write_value(ua.Variant(cur, ua.VariantType.Double))
                self._last_setpoint_echo[key] = cur
            except Exception:
                pass
        # batch object
        for tag, value in self.p.batch_status().items():
            node = self.batch_nodes.get(tag)
            if node is not None:
                try:
                    await node.write_value(ua.Variant(str(value), ua.VariantType.String))
                except Exception:
                    pass

    async def run(self) -> None:
        async with self.server:
            log.info("vla-factory OPC-UA server up at %s (ns=%d)", ENDPOINT, self.idx)
            if AUTOSTART_RECIPE:
                rc = self.p.start_batch(AUTOSTART_RECIPE)
                log.info("autostart recipe %s -> rc=%d", AUTOSTART_RECIPE, rc)
            last_write = 0.0
            elapsed = 0.0
            while True:
                self.p.tick(TICK_INTERVAL)
                elapsed += TICK_INTERVAL
                if elapsed - last_write >= WRITE_INTERVAL:
                    await self._apply_writable_setpoints()
                    await self._write_status()
                    last_write = elapsed
                # continuous demo: chain a fresh batch when one completes
                if AUTOSTART_RECIPE and self.p.state == "COMPLETE":
                    self.p.start_batch(AUTOSTART_RECIPE)
                await asyncio.sleep(TICK_INTERVAL)


def _vtype_for(area: str, eq: str, tag: str) -> ua.VariantType:
    for t, vtype, _w in AREA_TAGS.get((area, eq), []):
        if t == tag:
            return vtype
    return ua.VariantType.Double


async def main() -> None:
    process = VlaProcess()
    srv = VlaServer(process)
    await srv.init()
    await srv.run()


if __name__ == "__main__":
    asyncio.run(main())
