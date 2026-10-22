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

EXISTS=$(gql "{opcUaDevices{name}}")
case "$EXISTS" in
  *'"vla-l2"'*) echo "[vla-l2-init] device 'vla-l2' bestaat al — skip."; exit 0 ;;
esac

ADD=$(gql "mutation{opcUaDevice{add(input:{name:\\\"vla-l2\\\",namespace:\\\"DairyWorks/Vla-L2\\\",nodeId:\\\"local\\\",enabled:true,config:{endpointUrl:\\\"opc.tcp://vla-factory-l2:4840/DairyWorks\\\",securityPolicy:None,subscriptionSamplingInterval:1000.0,writeConfig:{enabled:true,requestResponseEnabled:true}}}){success errors}}}")
echo "[vla-l2-init] add: $ADD"
case "$ADD" in *'"success":true'*) : ;; *) exit 1 ;; esac

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
"

OK=0; FAIL=0
for LINE in $TAGS; do
  NODE=${LINE%%|*}
  TOPIC=${LINE##*|}
  RES=$(gql "mutation{opcUaDevice{addAddress(deviceName:\\\"$DEV\\\",input:{address:\\\"NodeId://$NS;s=DairyWorks.Vla.$NODE\\\",topic:\\\"$TOPIC\\\",publishMode:SEPARATE}){success errors}}}")
  case "$RES" in
    *'"success":true'*) OK=$((OK+1)) ;;
    *) FAIL=$((FAIL+1)); echo "[vla-l2-init] WARN addAddress $NODE -> $RES" ;;
  esac
done
echo "[vla-l2-init] $OK addresses OK, $FAIL failed."
[ "$FAIL" -gt 0 ] && exit 1
echo "[vla-l2-init] Done -> DairyWorks/Vla-L2/#"
