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

echo "All OPC-UA devices registered."
