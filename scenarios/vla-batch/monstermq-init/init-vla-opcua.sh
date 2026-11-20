#!/bin/sh
# Register the Vla factory as an OPC-UA device in MonsterMQ via GraphQL.
#
# This is the PRIMARY ingest path for the Vla demo: MonsterMQ's own OPC-UA
# client subscribes to vla-factory and republishes onto the UNS. Runs once on
# first deploy (the device config persists in MongoDB). The separate
# `vla-connector` container is only a FALLBACK (compose profile "fallback").
#
# GraphQL shape (rocworks/monstermq >= 2026-06 schema): mutations are nested
# namespaces — device eerst via  opcUaDevice.add(input:{...config zonder
# addresses...}),  daarna elke tag apart via  opcUaDevice.addAddress(
# deviceName, input:{address, topic, publishMode}).  De oude vlakke
# addOpcUaDevice-mutation (met addresses[] in config) bestaat niet meer.
#
# namespace "DairyWorks/Vla" + per-address topic "{Area}/{Equipment}/Status/{tag}"
#   -> published on  DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}   (UNS canon).
# Batch tags use topic "Batch/Status/{tag}" -> DairyWorks/Vla/Batch/Status/{tag}.
#
# nodeId = MonsterMQ cluster-node die de client draait (single node = "local").
#
# ⚠ ns-index: the factory (asyncua) registers urn:dairyworks and the NodeIds
#   below assume it lands on ns=2 (default + local namespaces precede it). If
#   topics don't flow, verify the ACTUAL ns index in the MonsterMQ logs
#   (`docker logs monstermq | grep -i opcua`) and adjust NS here, OR fall back
#   to the connector:  docker compose ... --profile fallback up -d vla-connector.

GQL="http://monstermq:4000/graphql"
NS="ns=2"

echo "[vla-opcua-init] Waiting for MonsterMQ to be ready..."
until curl -sf "http://monstermq:4000/" > /dev/null 2>&1; do
  sleep 3
done
echo "[vla-opcua-init] MonsterMQ ready. Registering vla OPC-UA device..."

gql() {
  # $1 = GraphQL mutation/query (single line, escaped quotes)
  curl -sf -X POST "$GQL" -H "Content-Type: application/json" \
    -d "{\"query\":\"$1\"}"
}

# Idempotent PER ADRES: bestaat device 'vla' al (config persist in Mongo), dan worden
# alleen de adressen toegevoegd die nog ontbreken. Tot 2026-11-19 stopte het script
# hier helemaal, waardoor een nieuwe tag in het model op een draaiende stack NOOIT
# werd opgehaald: geen fout, alleen stilte. Gefilterd op deze device-naam, want L2
# gebruikt dezelfde NodeIds op een andere server.
EXISTS=$(gql "{opcUaDevices{name}}")
HAVE=""
case "$EXISTS" in
  *'"vla"'*)
    HAVE=$(gql "{opcUaDevices(name:\\\"vla\\\"){config{addresses{address}}}}")
    case "$HAVE" in
      *'"addresses"'*) : ;;
      *) echo "[vla-opcua-init] FOUT: adressen van 'vla' niet op te vragen: $HAVE"; exit 1 ;;
    esac
    echo "[vla-opcua-init] device 'vla' bestaat al — alleen ontbrekende adressen toevoegen." ;;
esac

if [ -z "$HAVE" ]; then
# 1) Device (config ZONDER addresses — die gaan per stuk via addAddress)
ADD=$(gql "mutation{opcUaDevice{add(input:{name:\\\"vla\\\",namespace:\\\"DairyWorks/Vla\\\",nodeId:\\\"local\\\",enabled:true,config:{endpointUrl:\\\"opc.tcp://vla-factory:4840/DairyWorks\\\",securityPolicy:None,subscriptionSamplingInterval:1000.0,writeConfig:{enabled:true,requestResponseEnabled:true}}}){success errors}}}")
echo "[vla-opcua-init] add: $ADD"
case "$ADD" in
  *'"success":true'*) : ;;
  *) echo "[vla-opcua-init] WARN: opcUaDevice.add failed — check schema/logs; overweeg --profile fallback (vla-connector)."; exit 1 ;;
esac
fi

# 2) Addresses — NodeId scheme: $NS;s=DairyWorks.Vla.{Area}.{Equipment}.{tag}
#    Topic scheme:  {Area}/{Equipment}/Status/{tag}  (Batch -> Batch/Status/{tag})
# BEGIN GENERATED (tools/gen-park.py) — uit isa95-vla.json, lijn Vla
TAGS="
Receiving.receiving-tank-01.level_L|Receiving/receiving-tank-01/Status/level_L
Receiving.receiving-tank-01.temp_C|Receiving/receiving-tank-01/Status/temp_C
Receiving.receiving-tank-01.fat_setpoint_pct|Receiving/receiving-tank-01/Status/fat_setpoint_pct
Receiving.pasteurizer-01.product_flow_kg_s|Receiving/pasteurizer-01/Status/product_flow_kg_s
Receiving.pasteurizer-01.cold_t_in_C|Receiving/pasteurizer-01/Status/cold_t_in_C
Receiving.pasteurizer-01.regen_t_out_C|Receiving/pasteurizer-01/Status/regen_t_out_C
Receiving.pasteurizer-01.hold_t_C|Receiving/pasteurizer-01/Status/hold_t_C
Receiving.pasteurizer-01.hot_t_in_C|Receiving/pasteurizer-01/Status/hot_t_in_C
Receiving.pasteurizer-01.hot_t_out_C|Receiving/pasteurizer-01/Status/hot_t_out_C
Receiving.pasteurizer-01.dp_bar|Receiving/pasteurizer-01/Status/dp_bar
Receiving.pasteurizer-01.run_h_since_cip|Receiving/pasteurizer-01/Status/run_h_since_cip
Mixing.process-tank-01.level_L|Mixing/process-tank-01/Status/level_L
Mixing.process-tank-01.temp_C|Mixing/process-tank-01/Status/temp_C
Mixing.process-tank-01.agitator_rpm|Mixing/process-tank-01/Status/agitator_rpm
Mixing.process-tank-01.dose_milk_actual_kg|Mixing/process-tank-01/Status/dose_milk_actual_kg
Mixing.process-tank-01.dose_sugar_actual_kg|Mixing/process-tank-01/Status/dose_sugar_actual_kg
Mixing.process-tank-01.dose_starch_actual_kg|Mixing/process-tank-01/Status/dose_starch_actual_kg
Mixing.process-tank-01.dose_cocoa_actual_kg|Mixing/process-tank-01/Status/dose_cocoa_actual_kg
Mixing.process-tank-01.phase|Mixing/process-tank-01/Status/phase
Mixing.process-tank-01.dose_milk_setpoint_kg|Mixing/process-tank-01/Status/dose_milk_setpoint_kg
Mixing.process-tank-01.dose_sugar_setpoint_kg|Mixing/process-tank-01/Status/dose_sugar_setpoint_kg
Mixing.process-tank-01.dose_starch_setpoint_kg|Mixing/process-tank-01/Status/dose_starch_setpoint_kg
Mixing.process-tank-01.dose_cocoa_setpoint_kg|Mixing/process-tank-01/Status/dose_cocoa_setpoint_kg
Cook.cook-unit-01.temp_C|Cook/cook-unit-01/Status/temp_C
Cook.cook-unit-01.setpoint_C|Cook/cook-unit-01/Status/setpoint_C
Cook.cook-unit-01.hold_sec|Cook/cook-unit-01/Status/hold_sec
Cook.cook-unit-01.hold_elapsed_sec|Cook/cook-unit-01/Status/hold_elapsed_sec
Cook.cook-unit-01.viscosity_cP|Cook/cook-unit-01/Status/viscosity_cP
Cooling.cooler-01.temp_C|Cooling/cooler-01/Status/temp_C
Cooling.cooler-01.target_C|Cooling/cooler-01/Status/target_C
Filling.filler-01.packs_total|Filling/filler-01/Status/packs_total
Filling.filler-01.reject_count|Filling/filler-01/Status/reject_count
Filling.filler-01.pack_size_L|Filling/filler-01/Status/pack_size_L
Utilities.energy-meter-01.heat_kwh_total|Utilities/energy-meter-01/Status/heat_kwh_total
Utilities.energy-meter-01.electricity_kwh_total|Utilities/energy-meter-01/Status/electricity_kwh_total
Utilities.energy-meter-01.steam_kg_total|Utilities/energy-meter-01/Status/steam_kg_total
Utilities.water-meter-01.water_L_total|Utilities/water-meter-01/Status/water_L_total
Utilities.cip-station-01.step|Utilities/cip-station-01/Status/step
Utilities.cip-station-01.cip_count|Utilities/cip-station-01/Status/cip_count
Utilities.cip-station-01.last_water_L|Utilities/cip-station-01/Status/last_water_L
Utilities.cip-station-01.last_caustic_kg|Utilities/cip-station-01/Status/last_caustic_kg
Utilities.cip-station-01.last_acid_kg|Utilities/cip-station-01/Status/last_acid_kg
Utilities.cip-station-01.last_heat_kwh|Utilities/cip-station-01/Status/last_heat_kwh
Utilities.cip-station-01.last_electricity_kwh|Utilities/cip-station-01/Status/last_electricity_kwh
Utilities.cip-station-01.last_duration_s|Utilities/cip-station-01/Status/last_duration_s
Utilities.cip-station-01.last_past_run_h|Utilities/cip-station-01/Status/last_past_run_h
Batch.state|Batch/Status/state
Batch.batch_id|Batch/Status/batch_id
Batch.active_recipe|Batch/Status/active_recipe
"
# END GENERATED

OK=0; FAIL=0; SKIP=0
for LINE in $TAGS; do
  NODE=${LINE%%|*}
  TOPIC=${LINE##*|}
  # het afsluitende aanhalingsteken voorkomt dat temp_C matcht op temp_C_iets
  case "$HAVE" in *"s=DairyWorks.Vla.${NODE}\""*) SKIP=$((SKIP+1)); continue ;; esac
  RES=$(gql "mutation{opcUaDevice{addAddress(deviceName:\\\"vla\\\",input:{address:\\\"NodeId://$NS;s=DairyWorks.Vla.$NODE\\\",topic:\\\"$TOPIC\\\",publishMode:SEPARATE}){success errors}}}")
  case "$RES" in
    *'"success":true'*) OK=$((OK+1)) ;;
    *) FAIL=$((FAIL+1)); echo "[vla-opcua-init] WARN addAddress $NODE -> $RES" ;;
  esac
done

echo "[vla-opcua-init] vla device: $OK adressen toegevoegd, $SKIP al aanwezig, $FAIL mislukt."
[ "$FAIL" -gt 0 ] && exit 1
echo "[vla-opcua-init] Done. MonsterMQ ingests vla-factory natively via OPC-UA -> DairyWorks/Vla/#"
