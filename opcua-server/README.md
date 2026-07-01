# opcua-server — DairyWorks OPC-UA facade (primary external surface)

Bridges the MQTT-only packml-sim (libremfg PackML) to OPC-UA, **both directions**:

- **READ** — subscribes MonsterMQ `DairyWorks/#/Status/#`, mirrors each value into an
  OPC-UA read node `ns=2;s=DairyWorks.<Area>.<Equipment>.<tag>`.
- **WRITE** — each equipment exposes OPC-UA **methods** that publish the matching MQTT
  Command on MonsterMQ, so the simulator is driven over OPC-UA:
  `Start()`, `Stop()`, `Reset()`, `Hold()`, `Unhold()`,
  `SetMachSpeed(Double)`, `InjectFault(String, Double)`, `ClearFault()`.

MQTT stays the internal bus; OPC-UA is the external face (Eugene's world + historian
credibility). Address space is generated from `factory-model/isa95-dairyworks.json`.

## Endpoint
`opc.tcp://<host>:4840/DairyWorks` · namespace `urn:dairyworks` (ns=2)

## Run (standalone, dev)
```bash
pip install -r requirements.txt
FACTORY_MODEL=../scenarios/dairyworks/factory-model/isa95-dairyworks.json \
MQTT_HOST=localhost python server.py
```
In the stack it runs as a container (see `scenarios/dairyworks/docker-compose.dairyworks.yml`),
with the factory-model mounted at `/model` and `MQTT_HOST=monstermq`.

## Verify
Connect any OPC-UA client (UaExpert / asyncua Client) to the endpoint:
- browse `DairyWorks.Processing.pasteurizer-01.HTST_temp_C` → live value.
- call `DairyWorks.Processing.pasteurizer-01.InjectFault("f12", 0.4)` → sim temp droops →
  divert trips (Solve) — visible on the read node `divert_valve_status`.
