import asyncio
import random
import math
from asyncua import Server, ua


async def create_var(parent, idx, node_path, name, value, vtype):
    node_id = ua.NodeId(node_path, idx, ua.NodeIdType.String)
    qn = ua.QualifiedName(name, idx)
    var = await parent.add_variable(node_id, qn, ua.Variant(value, vtype))
    await var.set_writable()
    return var


async def main():
    server = Server()
    await server.init()
    server.set_endpoint("opc.tcp://0.0.0.0:4840/")
    server.set_server_name("IDP OPC-UA Simulator")
    idx = await server.register_namespace("urn:industrial-data-platform:simulator")
    objects = server.nodes.objects

    plc01 = await objects.add_object(
        ua.NodeId("PLC_01", idx, ua.NodeIdType.String),
        ua.QualifiedName("PLC_01", idx),
    )
    t01 = await create_var(plc01, idx, "PLC_01/Temperature", "Temperature", 65.0, ua.VariantType.Float)
    p01 = await create_var(plc01, idx, "PLC_01/Pressure",    "Pressure",    4.5,  ua.VariantType.Float)
    f01 = await create_var(plc01, idx, "PLC_01/Flow",        "Flow",        250.0, ua.VariantType.Float)
    a01 = await create_var(plc01, idx, "PLC_01/Alarm",       "Alarm",       False, ua.VariantType.Boolean)

    plc02 = await objects.add_object(
        ua.NodeId("PLC_02", idx, ua.NodeIdType.String),
        ua.QualifiedName("PLC_02", idx),
    )
    m1 = await create_var(plc02, idx, "PLC_02/Motor1_RPM", "Motor1_RPM", 1450.0, ua.VariantType.Float)
    m2 = await create_var(plc02, idx, "PLC_02/Motor2_RPM", "Motor2_RPM", 1450.0, ua.VariantType.Float)
    m3 = await create_var(plc02, idx, "PLC_02/Motor3_RPM", "Motor3_RPM",  960.0, ua.VariantType.Float)
    pw = await create_var(plc02, idx, "PLC_02/Power_kW",   "Power_kW",    18.5,  ua.VariantType.Float)
    fb = await create_var(plc02, idx, "PLC_02/FaultBits",  "FaultBits",   0,     ua.VariantType.Int32)

    plc03 = await objects.add_object(
        ua.NodeId("PLC_03", idx, ua.NodeIdType.String),
        ua.QualifiedName("PLC_03", idx),
    )
    bc = await create_var(plc03, idx, "PLC_03/BatchCounter",    "BatchCounter",    0,     ua.VariantType.Int32)
    ri = await create_var(plc03, idx, "PLC_03/RecipeID",        "RecipeID",        101,   ua.VariantType.Int32)
    ph = await create_var(plc03, idx, "PLC_03/Phase",           "Phase",           1,     ua.VariantType.Int32)
    pr = await create_var(plc03, idx, "PLC_03/ProductionRate",  "ProductionRate",  120.0, ua.VariantType.Float)

    print("OPC-UA server ready on opc.tcp://0.0.0.0:4840/")

    async with server:
        tick = 0
        batch_count = 0
        while True:
            t = tick

            # PLC_01: sinusoidal process values with noise
            await t01.write_value(ua.Variant(round(65.0 + 10.0 * math.sin(t * 0.1) + random.gauss(0, 0.2), 2), ua.VariantType.Float))
            await p01.write_value(ua.Variant(round(4.5 + 0.3 * math.sin(t * 0.05) + random.gauss(0, 0.02), 3), ua.VariantType.Float))
            await f01.write_value(ua.Variant(round(250.0 + 20.0 * math.sin(t * 0.07) + random.gauss(0, 1.0), 1), ua.VariantType.Float))
            await a01.write_value(ua.Variant(random.random() < 0.02, ua.VariantType.Boolean))

            # PLC_02: motor drives
            await m1.write_value(ua.Variant(round(1450.0 + random.gauss(0, 5.0), 1), ua.VariantType.Float))
            await m2.write_value(ua.Variant(round(1450.0 + random.gauss(0, 5.0), 1), ua.VariantType.Float))
            motor3_on = t % 60 < 40
            await m3.write_value(ua.Variant(round(960.0 + random.gauss(0, 3.0) if motor3_on else 0.0, 1), ua.VariantType.Float))
            await pw.write_value(ua.Variant(round(18.5 + 2.0 * math.sin(t * 0.08) + random.gauss(0, 0.3), 2), ua.VariantType.Float))
            await fb.write_value(ua.Variant(0 if random.random() > 0.01 else random.randint(1, 15), ua.VariantType.Int32))

            # PLC_03: batch process (updates every 5 ticks)
            if t % 5 == 0:
                phase = (t // 30) % 4 + 1
                await ph.write_value(ua.Variant(phase, ua.VariantType.Int32))
                await pr.write_value(ua.Variant(round(120.0 + random.gauss(0, 5.0), 1), ua.VariantType.Float))
                if phase == 1 and t > 0 and t % 120 == 0:
                    batch_count += 1
                    await bc.write_value(ua.Variant(batch_count, ua.VariantType.Int32))
                    await ri.write_value(ua.Variant(random.choice([101, 102, 103, 201]), ua.VariantType.Int32))

            tick += 1
            await asyncio.sleep(1.0)


if __name__ == "__main__":
    asyncio.run(main())
