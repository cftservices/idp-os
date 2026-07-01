#!/bin/sh
# Register the Vla factory as an OPC-UA device in MonsterMQ via GraphQL.
#
# This is the PRIMARY ingest path for the Vla demo: MonsterMQ's own OPC-UA
# client subscribes to vla-factory and republishes onto the UNS. Runs once on
# first deploy (the addOpcUaDevice config persists in MongoDB). The separate
# `vla-connector` container is only a FALLBACK (compose profile "fallback").
#
# GraphQL shape: see monster-mq-src/doc/opcua-client.md ("Example - add a
# device" + "Writing to OPC UA").
#
# namespace "DairyWorks/Vla" + per-address topic "{Area}/{Equipment}/Status/{tag}"
#   -> published on  DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}   (UNS canon).
# Batch tags use topic "Batch/Status/{tag}" -> DairyWorks/Vla/Batch/Status/{tag}.
#
# writeConfig enables manual node-writes via  DairyWorks/Vla/write/{nodeId}
# (fire&forget) and request/response via DairyWorks/Vla/request|response/{nodeId}.
#
# ⚠ ns-index: the factory (asyncua) registers urn:dairyworks and the NodeIds
#   below assume it lands on ns=2 (default + local namespaces precede it). If
#   topics don't flow, verify the ACTUAL ns index in the MonsterMQ logs
#   (`docker logs monstermq | grep -i opcua`) and adjust ns=2 here, OR fall back
#   to the connector:  docker compose ... --profile fallback up -d vla-connector.

GQL="http://monstermq:4000/graphql"

echo "[vla-opcua-init] Waiting for MonsterMQ to be ready..."
until curl -sf "http://monstermq:4000/" > /dev/null 2>&1; do
  sleep 3
done
echo "[vla-opcua-init] MonsterMQ ready. Registering vla OPC-UA device..."

# One device 'vla' covering all Status tags from the build contract (§ISA-95).
# NodeId scheme: ns=2;s=DairyWorks.Vla.{Area}.{Equipment}.{tag}
# Topic scheme:  {Area}/{Equipment}/Status/{tag}   (Batch -> Batch/Status/{tag})
curl -sf -X POST "$GQL" \
  -H "Content-Type: application/json" \
  -d '{"query":"mutation{addOpcUaDevice(input:{name:\"vla\",namespace:\"DairyWorks/Vla\",nodeId:\"vla\",enabled:true,config:{endpointUrl:\"opc.tcp://vla-factory:4840/DairyWorks\",securityPolicy:None,subscriptionSamplingInterval:1000.0,writeConfig:{enabled:true,requestResponseEnabled:true},addresses:[{address:\"NodeId://ns=2;s=DairyWorks.Vla.Receiving.receiving-tank-01.level_L\",topic:\"Receiving/receiving-tank-01/Status/level_L\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Receiving.receiving-tank-01.temp_C\",topic:\"Receiving/receiving-tank-01/Status/temp_C\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Receiving.receiving-tank-01.fat_setpoint_pct\",topic:\"Receiving/receiving-tank-01/Status/fat_setpoint_pct\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Mixing.process-tank-01.level_L\",topic:\"Mixing/process-tank-01/Status/level_L\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Mixing.process-tank-01.temp_C\",topic:\"Mixing/process-tank-01/Status/temp_C\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Mixing.process-tank-01.agitator_rpm\",topic:\"Mixing/process-tank-01/Status/agitator_rpm\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Mixing.process-tank-01.dose_milk_actual_kg\",topic:\"Mixing/process-tank-01/Status/dose_milk_actual_kg\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Mixing.process-tank-01.dose_sugar_actual_kg\",topic:\"Mixing/process-tank-01/Status/dose_sugar_actual_kg\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Mixing.process-tank-01.dose_starch_actual_kg\",topic:\"Mixing/process-tank-01/Status/dose_starch_actual_kg\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Mixing.process-tank-01.dose_cocoa_actual_kg\",topic:\"Mixing/process-tank-01/Status/dose_cocoa_actual_kg\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Mixing.process-tank-01.phase\",topic:\"Mixing/process-tank-01/Status/phase\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Cook.cook-unit-01.temp_C\",topic:\"Cook/cook-unit-01/Status/temp_C\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Cook.cook-unit-01.setpoint_C\",topic:\"Cook/cook-unit-01/Status/setpoint_C\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Cook.cook-unit-01.hold_sec\",topic:\"Cook/cook-unit-01/Status/hold_sec\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Cook.cook-unit-01.hold_elapsed_sec\",topic:\"Cook/cook-unit-01/Status/hold_elapsed_sec\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Cook.cook-unit-01.viscosity_cP\",topic:\"Cook/cook-unit-01/Status/viscosity_cP\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Cooling.cooler-01.temp_C\",topic:\"Cooling/cooler-01/Status/temp_C\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Cooling.cooler-01.target_C\",topic:\"Cooling/cooler-01/Status/target_C\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Filling.filler-01.packs_total\",topic:\"Filling/filler-01/Status/packs_total\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Filling.filler-01.reject_count\",topic:\"Filling/filler-01/Status/reject_count\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Filling.filler-01.pack_size_L\",topic:\"Filling/filler-01/Status/pack_size_L\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Batch.state\",topic:\"Batch/Status/state\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Batch.batch_id\",topic:\"Batch/Status/batch_id\",publishMode:SEPARATE},{address:\"NodeId://ns=2;s=DairyWorks.Vla.Batch.active_recipe\",topic:\"Batch/Status/active_recipe\",publishMode:SEPARATE}]}}){success errors}}"}' \
  && echo "" && echo "[vla-opcua-init] vla device registered (24 Status tags across Receiving/Mixing/Cook/Cooling/Filling/Batch)." \
  || echo "[vla-opcua-init] WARN: addOpcUaDevice failed — check GraphQL schema/ns-index; fall back to --profile fallback (vla-connector)."

echo "[vla-opcua-init] Done. MonsterMQ ingests vla-factory natively via OPC-UA -> DairyWorks/Vla/#"
