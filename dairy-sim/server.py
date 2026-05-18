"""DairyPlant OPC-UA simulator.

Simuleert een realistische melkfabriek met ISA-95 hierarchical namespace
(Site/Area/Line/Equipment). Bedoeld als demo-bron voor workshop 1 CONNECT.

Namespace: urn:techflow:dairy-plant
Endpoint:  opc.tcp://0.0.0.0:4841/
"""
import asyncio
import math
import random

from asyncua import Server, ua


PORT = 4841
NS_URI = "urn:techflow:dairy-plant"


async def create_var(parent, idx, node_id_str, name, value, vtype):
    node_id = ua.NodeId(node_id_str, idx, ua.NodeIdType.String)
    qn = ua.QualifiedName(name, idx)
    var = await parent.add_variable(node_id, qn, ua.Variant(value, vtype))
    await var.set_writable()
    return var


async def add_object(parent, idx, name):
    return await parent.add_object(
        ua.NodeId(name, idx, ua.NodeIdType.String),
        ua.QualifiedName(name, idx),
    )


async def main():
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://0.0.0.0:{PORT}/")
    server.set_server_name("DairyPlant OPC-UA Simulator")
    idx = await server.register_namespace(NS_URI)
    root = server.nodes.objects

    # ISA-95 hierarchy: Site (DairyPlant) > Area > Line > Equipment > Tag
    dairy = await add_object(root, idx, "DairyPlant")

    # ---- Area: Receiving ---------------------------------------------------
    receiving = await add_object(dairy, idx, "Receiving")
    tank01 = await add_object(receiving, idx, "Tank01")
    in_temp = await create_var(tank01, idx, "DairyPlant.Receiving.Tank01.in_temp_C",
                               "in_temp_C", 5.0, ua.VariantType.Float)
    in_flow = await create_var(tank01, idx, "DairyPlant.Receiving.Tank01.flow_L_min",
                               "flow_L_min", 1000.0, ua.VariantType.Float)

    # ---- Area: Process -----------------------------------------------------
    process = await add_object(dairy, idx, "Process")

    separator = await add_object(process, idx, "Separator")
    sep_rpm = await create_var(separator, idx, "DairyPlant.Process.Separator.RPM",
                               "RPM", 6000.0, ua.VariantType.Float)
    sep_fat = await create_var(separator, idx, "DairyPlant.Process.Separator.fat_pct",
                               "fat_pct", 3.5, ua.VariantType.Float)

    pasteur = await add_object(process, idx, "Pasteurizer")
    htst_temp = await create_var(pasteur, idx, "DairyPlant.Process.Pasteurizer.HTST_temp_C",
                                 "HTST_temp_C", 72.0, ua.VariantType.Float)
    htst_hold = await create_var(pasteur, idx, "DairyPlant.Process.Pasteurizer.hold_sec",
                                 "hold_sec", 15, ua.VariantType.Int32)
    htst_divert = await create_var(pasteur, idx, "DairyPlant.Process.Pasteurizer.divert_valve_status",
                                   "divert_valve_status", False, ua.VariantType.Boolean)

    homog = await add_object(process, idx, "Homogenizer")
    homog_p = await create_var(homog, idx, "DairyPlant.Process.Homogenizer.pressure_bar",
                               "pressure_bar", 180.0, ua.VariantType.Float)

    # ---- Area: Packaging ---------------------------------------------------
    packaging = await add_object(dairy, idx, "Packaging")
    bottler = await add_object(packaging, idx, "Bottler")
    bot_rate = await create_var(bottler, idx, "DairyPlant.Packaging.Bottler.bottles_per_min",
                                "bottles_per_min", 120.0, ua.VariantType.Float)
    bot_reject = await create_var(bottler, idx, "DairyPlant.Packaging.Bottler.reject_count",
                                  "reject_count", 0, ua.VariantType.Int32)
    bot_fill = await create_var(bottler, idx, "DairyPlant.Packaging.Bottler.fill_volume_mL",
                                "fill_volume_mL", 1000.0, ua.VariantType.Float)

    print(f"DairyPlant OPC-UA server ready on opc.tcp://0.0.0.0:{PORT}/")

    async with server:
        tick = 0
        reject_counter = 0
        while True:
            t = tick

            # Receiving: koel-tank houdt 4-6°C, flow varieert
            await in_temp.write_value(ua.Variant(
                round(5.0 + 0.5 * math.sin(t * 0.03) + random.gauss(0, 0.05), 2),
                ua.VariantType.Float))
            await in_flow.write_value(ua.Variant(
                round(1000.0 + 100.0 * math.sin(t * 0.05) + random.gauss(0, 10.0), 1),
                ua.VariantType.Float))

            # Separator: hoge RPM met kleine spreiding, fat% rond 3.5
            await sep_rpm.write_value(ua.Variant(
                round(6000.0 + random.gauss(0, 30.0), 1), ua.VariantType.Float))
            await sep_fat.write_value(ua.Variant(
                round(3.5 + random.gauss(0, 0.05), 3), ua.VariantType.Float))

            # Pasteurizer: HTST regelt op 72°C ±0.5; divert_valve trekt op
            # zodra HTST_temp onder 71.5 zakt (safety)
            htst_value = 72.0 + 0.4 * math.sin(t * 0.08) + random.gauss(0, 0.1)
            await htst_temp.write_value(ua.Variant(round(htst_value, 2), ua.VariantType.Float))
            await htst_divert.write_value(ua.Variant(htst_value < 71.5, ua.VariantType.Boolean))
            # hold_sec is een PLC parameter, blijft 15
            if t == 0:
                await htst_hold.write_value(ua.Variant(15, ua.VariantType.Int32))

            # Homogenizer: druk ~180 bar
            await homog_p.write_value(ua.Variant(
                round(180.0 + 5.0 * math.sin(t * 0.06) + random.gauss(0, 1.0), 1),
                ua.VariantType.Float))

            # Bottler: ~120 bottles/min, fill volume 1000±2 mL, occasional reject
            await bot_rate.write_value(ua.Variant(
                round(120.0 + random.gauss(0, 2.0), 1), ua.VariantType.Float))
            await bot_fill.write_value(ua.Variant(
                round(1000.0 + random.gauss(0, 1.5), 2), ua.VariantType.Float))
            if random.random() < 0.02:
                reject_counter += 1
                await bot_reject.write_value(ua.Variant(reject_counter, ua.VariantType.Int32))

            tick += 1
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
