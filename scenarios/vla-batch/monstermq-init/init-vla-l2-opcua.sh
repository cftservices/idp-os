#!/bin/sh
# Register the L2 factory (Hal 2, smaaklijn) as OPC-UA device "vla-l2" in MonsterMQ.
#
# Zelfde fabrieksimage als vla-factory, dus dezelfde NodeIds (DairyWorks.Vla.*).
# De lijn-specifieke naamgeving zit alleen hier, in de topics:
#   namespace "DairyWorks/Vla-L2" + topic "{Area}/{eq-02}/Status/{tag}"
#   -> DairyWorks/Vla-L2/Mixing/process-tank-02/Status/dose_vanilla_actual_kg
# L1 (DairyWorks/Vla/#) blijft onaangeroerd: historian, Grafana en archief
# zien deze lijn niet tot ze er bewust op geabonneerd worden.

GQL="http://monstermq:4000/graphql"
NS="ns=2"
DEV="vla-l2"

echo "[vla-l2-init] Waiting for MonsterMQ..."
until curl -sf "http://monstermq:4000/" > /dev/null 2>&1; do sleep 3; done

gql() {
  curl -sf -X POST "$GQL" -H "Content-Type: application/json" -d "{\"query\":\"$1\"}"
}

# Idempotent PER ADRES (sinds 2026-11-19, zie init-vla-opcua.sh): bestaat het device,
# dan alleen de ontbrekende adressen toevoegen. Gefilterd op deze device-naam, want L1
# gebruikt dezelfde NodeIds op een andere server.
EXISTS=$(gql "{opcUaDevices{name}}")
HAVE=""
case "$EXISTS" in
  *'"vla-l2"'*)
    HAVE=$(gql "{opcUaDevices(name:\\\"vla-l2\\\"){config{addresses{address}}}}")
    case "$HAVE" in
      *'"addresses"'*) : ;;
      *) echo "[vla-l2-init] FOUT: adressen van 'vla-l2' niet op te vragen: $HAVE"; exit 1 ;;
    esac
    echo "[vla-l2-init] device 'vla-l2' bestaat al — alleen ontbrekende adressen toevoegen." ;;
esac

if [ -z "$HAVE" ]; then
ADD=$(gql "mutation{opcUaDevice{add(input:{name:\\\"vla-l2\\\",namespace:\\\"DairyWorks/Vla-L2\\\",nodeId:\\\"local\\\",enabled:true,config:{endpointUrl:\\\"opc.tcp://vla-factory-l2:4840/DairyWorks\\\",securityPolicy:None,subscriptionSamplingInterval:1000.0,writeConfig:{enabled:true,requestResponseEnabled:true}}}){success errors}}}")
echo "[vla-l2-init] add: $ADD"
case "$ADD" in *'"success":true'*) : ;; *) exit 1 ;; esac
fi

TAGS="
Receiving.receiving-tank-01.level_L|Receiving/receiving-tank-02/Status/level_L
Receiving.receiving-tank-01.temp_C|Receiving/receiving-tank-02/Status/temp_C
Receiving.receiving-tank-01.fat_setpoint_pct|Receiving/receiving-tank-02/Status/fat_setpoint_pct
Mixing.process-tank-01.level_L|Mixing/process-tank-02/Status/level_L
Mixing.process-tank-01.temp_C|Mixing/process-tank-02/Status/temp_C
Mixing.process-tank-01.agitator_rpm|Mixing/process-tank-02/Status/agitator_rpm
Mixing.process-tank-01.dose_milk_actual_kg|Mixing/process-tank-02/Status/dose_milk_actual_kg
Mixing.process-tank-01.dose_sugar_actual_kg|Mixing/process-tank-02/Status/dose_sugar_actual_kg
Mixing.process-tank-01.dose_starch_actual_kg|Mixing/process-tank-02/Status/dose_starch_actual_kg
Mixing.process-tank-01.dose_cocoa_actual_kg|Mixing/process-tank-02/Status/dose_cocoa_actual_kg
Mixing.process-tank-01.phase|Mixing/process-tank-02/Status/phase
Mixing.process-tank-01.dose_milk_setpoint_kg|Mixing/process-tank-02/Status/dose_milk_setpoint_kg
Mixing.process-tank-01.dose_sugar_setpoint_kg|Mixing/process-tank-02/Status/dose_sugar_setpoint_kg
Mixing.process-tank-01.dose_starch_setpoint_kg|Mixing/process-tank-02/Status/dose_starch_setpoint_kg
Mixing.process-tank-01.dose_cocoa_setpoint_kg|Mixing/process-tank-02/Status/dose_cocoa_setpoint_kg
Cook.cook-unit-01.temp_C|Cook/cook-unit-02/Status/temp_C
Cook.cook-unit-01.setpoint_C|Cook/cook-unit-02/Status/setpoint_C
Cook.cook-unit-01.hold_sec|Cook/cook-unit-02/Status/hold_sec
Cook.cook-unit-01.hold_elapsed_sec|Cook/cook-unit-02/Status/hold_elapsed_sec
Cook.cook-unit-01.viscosity_cP|Cook/cook-unit-02/Status/viscosity_cP
Cooling.cooler-01.temp_C|Cooling/cooler-02/Status/temp_C
Cooling.cooler-01.target_C|Cooling/cooler-02/Status/target_C
Filling.filler-01.packs_total|Filling/filler-02/Status/packs_total
Filling.filler-01.reject_count|Filling/filler-02/Status/reject_count
Filling.filler-01.pack_size_L|Filling/filler-02/Status/pack_size_L
Batch.state|Batch/Status/state
Batch.batch_id|Batch/Status/batch_id
Batch.active_recipe|Batch/Status/active_recipe
Mixing.process-tank-01.dose_vanilla_actual_kg|Mixing/process-tank-02/Status/dose_vanilla_actual_kg
Mixing.process-tank-01.dose_vanilla_setpoint_kg|Mixing/process-tank-02/Status/dose_vanilla_setpoint_kg
Mixing.process-tank-01.dose_banana_actual_kg|Mixing/process-tank-02/Status/dose_banana_actual_kg
Mixing.process-tank-01.dose_banana_setpoint_kg|Mixing/process-tank-02/Status/dose_banana_setpoint_kg
Mixing.process-tank-01.dose_caramel_actual_kg|Mixing/process-tank-02/Status/dose_caramel_actual_kg
Mixing.process-tank-01.dose_caramel_setpoint_kg|Mixing/process-tank-02/Status/dose_caramel_setpoint_kg
Mixing.process-tank-01.dose_strawberry_actual_kg|Mixing/process-tank-02/Status/dose_strawberry_actual_kg
Mixing.process-tank-01.dose_strawberry_setpoint_kg|Mixing/process-tank-02/Status/dose_strawberry_setpoint_kg
Receiving.pasteurizer-01.product_flow_kg_s|Receiving/pasteurizer-02/Status/product_flow_kg_s
Receiving.pasteurizer-01.cold_t_in_C|Receiving/pasteurizer-02/Status/cold_t_in_C
Receiving.pasteurizer-01.regen_t_out_C|Receiving/pasteurizer-02/Status/regen_t_out_C
Receiving.pasteurizer-01.hold_t_C|Receiving/pasteurizer-02/Status/hold_t_C
Receiving.pasteurizer-01.hot_t_in_C|Receiving/pasteurizer-02/Status/hot_t_in_C
Receiving.pasteurizer-01.hot_t_out_C|Receiving/pasteurizer-02/Status/hot_t_out_C
Receiving.pasteurizer-01.dp_bar|Receiving/pasteurizer-02/Status/dp_bar
Receiving.pasteurizer-01.run_h_since_cip|Receiving/pasteurizer-02/Status/run_h_since_cip
Utilities.energy-meter-01.heat_kwh_total|Utilities/energy-meter-02/Status/heat_kwh_total
Utilities.energy-meter-01.electricity_kwh_total|Utilities/energy-meter-02/Status/electricity_kwh_total
Utilities.energy-meter-01.steam_kg_total|Utilities/energy-meter-02/Status/steam_kg_total
Utilities.water-meter-01.water_L_total|Utilities/water-meter-02/Status/water_L_total
Utilities.cip-station-01.step|Utilities/cip-station-02/Status/step
Utilities.cip-station-01.cip_count|Utilities/cip-station-02/Status/cip_count
Utilities.cip-station-01.last_water_L|Utilities/cip-station-02/Status/last_water_L
Utilities.cip-station-01.last_caustic_kg|Utilities/cip-station-02/Status/last_caustic_kg
Utilities.cip-station-01.last_acid_kg|Utilities/cip-station-02/Status/last_acid_kg
Utilities.cip-station-01.last_heat_kwh|Utilities/cip-station-02/Status/last_heat_kwh
Utilities.cip-station-01.last_electricity_kwh|Utilities/cip-station-02/Status/last_electricity_kwh
Utilities.cip-station-01.last_duration_s|Utilities/cip-station-02/Status/last_duration_s
Utilities.cip-station-01.last_past_run_h|Utilities/cip-station-02/Status/last_past_run_h
"

OK=0; FAIL=0; SKIP=0
for LINE in $TAGS; do
  NODE=${LINE%%|*}
  TOPIC=${LINE##*|}
  case "$HAVE" in *"s=DairyWorks.Vla.${NODE}\""*) SKIP=$((SKIP+1)); continue ;; esac
  RES=$(gql "mutation{opcUaDevice{addAddress(deviceName:\\\"$DEV\\\",input:{address:\\\"NodeId://$NS;s=DairyWorks.Vla.$NODE\\\",topic:\\\"$TOPIC\\\",publishMode:SEPARATE}){success errors}}}")
  case "$RES" in
    *'"success":true'*) OK=$((OK+1)) ;;
    *) FAIL=$((FAIL+1)); echo "[vla-l2-init] WARN addAddress $NODE -> $RES" ;;
  esac
done
echo "[vla-l2-init] $OK adressen toegevoegd, $SKIP al aanwezig, $FAIL mislukt."

# Lijn 2 hoort in het Mongo-archief, net als L1 (sinds 2026-11-20). MonsterMQ
# bewaart zijn archiefgroepen in Mongo en leest config.yaml alleen bij een lege
# installatie, dus een bestaande broker krijgt het filter alleen via GraphQL.
# Een groep moet uit voordat hij gewijzigd kan worden: dat geeft een gat van
# ~0,1 s in het archief. Alle instellingen gaan expliciet mee, zodat update niets
# leegmaakt. Bestaat de groep niet, dan heeft config.yaml hem al met beide lijnen.
AG=$(gql "{archiveGroups{name topicFilter}}")
case "$AG" in
  *'"dw_uns_archive","topicFilter":['*'DairyWorks/Vla-L2/#'*)
    echo "[vla-l2-init] archief: L2 staat al in dw_uns_archive" ;;
  *'"dw_uns_archive"'*)
    gql "mutation{archiveGroup{disable(name:\\\"dw_uns_archive\\\"){success}}}" > /dev/null
    UPD=$(gql "mutation{archiveGroup{update(input:{name:\\\"dw_uns_archive\\\",topicFilter:[\\\"DairyWorks/Vla/#\\\",\\\"DairyWorks/Vla-L2/#\\\"],retainedOnly:false,lastValType:NONE,archiveType:MONGODB,payloadFormat:DEFAULT,queueType:\\\"NONE\\\",queueSize:100000,bulkSize:4000,bulkTimeoutMs:250,queueDiskPath:\\\"data/queue\\\",lastValReadOnly:false,archiveReadOnly:false}){success message}}}")
    EN=$(gql "mutation{archiveGroup{enable(name:\\\"dw_uns_archive\\\"){success message}}}")
    echo "[vla-l2-init] archief: update $UPD, enable $EN"
    case "$EN" in *'"success":true'*) ;; *) FAIL=$((FAIL+1)) ;; esac ;;
  *) echo "[vla-l2-init] archief: groep dw_uns_archive niet gevonden, overgeslagen" ;;
esac

[ "$FAIL" -gt 0 ] && exit 1
echo "[vla-l2-init] Done -> DairyWorks/Vla-L2/#"
