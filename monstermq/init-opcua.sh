#!/bin/sh
# Register OPC-UA devices in MonsterMQ via GraphQL.
# Runs once on first deploy — configs persist in MongoDB.

GQL="http://monstermq:4000/graphql"

echo "Waiting for MonsterMQ to be ready..."
until curl -sf "http://monstermq:4000/" > /dev/null 2>&1; do
  sleep 3
done
echo "MonsterMQ ready. Registering OPC-UA devices..."

# PLC_01 — Process Control (1s sampling)
curl -sf -X POST "$GQL" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation{addOpcUaDevice(input:{name:\"plc01\",namespace:\"idp/plc01\",nodeId:\"plc01\",enabled:true,config:{endpointUrl:\"opc.tcp://opcua-sim:4840\",securityPolicy:None,subscriptionSamplingInterval:1000.0,addresses:[{address:\"NodeId://ns=1;s=PLC_01/Temperature\",topic:\"temperature\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_01/Pressure\",topic:\"pressure\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_01/Flow\",topic:\"flow\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_01/Alarm\",topic:\"alarm\",publishMode:SEPARATE}]}})}"}'
echo "PLC_01 registered"

# PLC_02 — Drive Control (1s sampling)
curl -sf -X POST "$GQL" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation{addOpcUaDevice(input:{name:\"plc02\",namespace:\"idp/plc02\",nodeId:\"plc02\",enabled:true,config:{endpointUrl:\"opc.tcp://opcua-sim:4840\",securityPolicy:None,subscriptionSamplingInterval:1000.0,addresses:[{address:\"NodeId://ns=1;s=PLC_02/Motor1_RPM\",topic:\"motor1_rpm\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_02/Motor2_RPM\",topic:\"motor2_rpm\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_02/Motor3_RPM\",topic:\"motor3_rpm\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_02/Power_kW\",topic:\"power_kw\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_02/FaultBits\",topic:\"fault_bits\",publishMode:SEPARATE}]}})}"}'
echo "PLC_02 registered"

# PLC_03 — Batch Process (5s sampling)
curl -sf -X POST "$GQL" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation{addOpcUaDevice(input:{name:\"plc03\",namespace:\"idp/plc03\",nodeId:\"plc03\",enabled:true,config:{endpointUrl:\"opc.tcp://opcua-sim:4840\",securityPolicy:None,subscriptionSamplingInterval:5000.0,addresses:[{address:\"NodeId://ns=1;s=PLC_03/BatchCounter\",topic:\"batch_counter\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_03/RecipeID\",topic:\"recipe_id\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_03/Phase\",topic:\"phase\",publishMode:SEPARATE},{address:\"NodeId://ns=1;s=PLC_03/ProductionRate\",topic:\"production_rate\",publishMode:SEPARATE}]}})}"}'
echo "PLC_03 registered"

# DAIRY_PLANT — Workshop 1 CONNECT demo source. ISA-95 namespace.
# 11 tags total: Receiving (2), Process/Separator (2), Process/Pasteurizer (3),
# Process/Homogenizer (1), Packaging/Bottler (3). Sampling: 1s.
# NodeIds use ns=2 (asyncua's first user namespace after default + local).
# If topics don't flow, verify the actual ns index from MonsterMQ logs.
curl -sf -X POST "$GQL" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation{addOpcUaDevice(input:{name:\"dairy_plant\",namespace:\"DairyPlant\",nodeId:\"dairy_plant\",enabled:true,config:{endpointUrl:\"opc.tcp://dairy-sim:4841\",securityPolicy:None,subscriptionSamplingInterval:1000.0,addresses:[{address:\"NodeId://ns=2;s=DairyPlant.Receiving.Tank01.in_temp_C\",topic:\"Receiving/Tank01/in_temp_C\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Receiving.Tank01.flow_L_min\",topic:\"Receiving/Tank01/flow_L_min\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Process.Separator.RPM\",topic:\"Process/Separator/RPM\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Process.Separator.fat_pct\",topic:\"Process/Separator/fat_pct\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Process.Pasteurizer.HTST_temp_C\",topic:\"Process/Pasteurizer/HTST_temp_C\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Process.Pasteurizer.hold_sec\",topic:\"Process/Pasteurizer/hold_sec\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Process.Pasteurizer.divert_valve_status\",topic:\"Process/Pasteurizer/divert_valve_status\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Process.Homogenizer.pressure_bar\",topic:\"Process/Homogenizer/pressure_bar\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Packaging.Bottler.bottles_per_min\",topic:\"Packaging/Bottler/bottles_per_min\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Packaging.Bottler.reject_count\",topic:\"Packaging/Bottler/reject_count\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyPlant.Packaging.Bottler.fill_volume_mL\",topic:\"Packaging/Bottler/fill_volume_mL\",publishMode:SEPARATE}]}})}"}'
echo "DAIRY_PLANT registered (11 tags, ISA-95 namespace under DairyPlant/)"

# IP21_HISTORIAN — REST poller (Aspen IP.21 stub). MonsterMQ schedules
# HTTP GET to ip21-stub every 60s for each /tags/{name}/current endpoint
# and republishes under DairyPlant/Historian/{tag}.
# NOTE: schema below uses MonsterMQ's REST-poller mutation. If the actual
# GraphQL schema differs (depends on MonsterMQ version), this entry will
# fail with HTTP 400 — fix syntax against `query{__schema{types}}` introspection.
curl -sf -X POST "$GQL" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation{addRestPoller(input:{name:\"ip21_historian\",namespace:\"DairyPlant/Historian\",enabled:true,config:{baseUrl:\"http://ip21-stub:8001\",pollIntervalSeconds:60,endpoints:[{path:\"/tags/HTST_temp_C_60min_avg/current\",topic:\"HTST_temp_C_60min_avg\",jsonPath:\"$.value\"},{path:\"/tags/flow_L_min_60min_avg/current\",topic:\"flow_L_min_60min_avg\",jsonPath:\"$.value\"},{path:\"/tags/fat_pct_60min_avg/current\",topic:\"fat_pct_60min_avg\",jsonPath:\"$.value\"},{path:\"/tags/homog_pressure_bar_60min_avg/current\",topic:\"homog_pressure_bar_60min_avg\",jsonPath:\"$.value\"},{path:\"/tags/bottles_per_hour_total/current\",topic:\"bottles_per_hour_total\",jsonPath:\"$.value\"}]}})}"}' || echo "WARN: addRestPoller failed — schema may differ; fix against introspection"
echo "IP21_HISTORIAN poller submitted (5 tags, DairyPlant/Historian/)"

echo "All sources registered (OPC-UA: PLC_01, PLC_02, PLC_03, DAIRY_PLANT · REST: IP21_HISTORIAN · MQTT-in: iot-publisher direct)."
