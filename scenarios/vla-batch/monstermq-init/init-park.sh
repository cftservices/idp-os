#!/bin/sh
# GEGENEREERD DOOR tools/gen-park.py — NIET MET DE HAND BEWERKEN
#
# Registreert de OPC-UA- en OPC-DA-machines van lijn Vla-B in MonsterMQ.
# Alleen die: Modbus, MQTT, REST en SQL lopen NIET via MonsterMQ's
# OPC-UA-client. Modbus en REST gaan via vla-park-poller, SQL via
# vla-park-gateway, en MQTT publiceert de sim rechtstreeks. Daardoor
# houdt MonsterMQ maar een handvol sessies open in plaats van twaalf.
#
# Idempotent PER DEVICE, niet in het geheel: een halve mislukking mag
# geen half-geregistreerde machine achterlaten die niemand opmerkt.
#
# Let op: `down -v` wist de in Mongo bewaarde device-config, dus dan moet
# dit script opnieuw. `down` zonder -v niet, en dan mag het niet dupliceren.

GQL="http://monstermq:4000/graphql"

echo "[park-init] Wachten op MonsterMQ..."
until curl -sf "http://monstermq:4000/" > /dev/null 2>&1; do sleep 3; done

gql() {
  curl -sf -X POST "$GQL" -H "Content-Type: application/json" \
    -d "{\"query\":\"$1\"}"
}

TOTAL_OK=0; TOTAL_FAIL=0

# ---------------------------------------------------------------- intake-silo-01
# profiel vendor-a, protocol opc-da
EXISTS=$(gql "{opcUaDevices{name}}")
case "$EXISTS" in
  *'"intake-silo-01"'*)
    echo "[park-init] intake-silo-01 bestaat al; enabled afdwingen."
    ON=$(gql "mutation{opcUaDevice{toggle(name:\\\"intake-silo-01\\\",enabled:true){success}}}")
    echo "[park-init] enable intake-silo-01: $ON"
    ;;
  *)
    ADD=$(gql "mutation{opcUaDevice{add(input:{name:\\\"intake-silo-01\\\",namespace:\\\"raw/vla-park\\\",nodeId:\\\"local\\\",enabled:true,config:{endpointUrl:\\\"opc.tcp://vla-intake-silo-01:4840/VendorA\\\",securityPolicy:None,subscriptionSamplingInterval:1000.0}}){success errors}}}")
    echo "[park-init] add intake-silo-01: $ADD"
    case "$ADD" in
      *'"success":true'*) : ;;
      *) echo "[park-init] FOUT: add intake-silo-01 mislukt"; TOTAL_FAIL=$((TOTAL_FAIL+1)) ;;
    esac
    ADDR_INTAKE_SILO_01="
Ch1.Dev2.XS_2007_PV|Ch1.Dev2.XS_2007_PV
Ch1.Dev2.XT_2014_PV|Ch1.Dev2.XT_2014_PV
Ch1.Dev2.XM_2021_PV|Ch1.Dev2.XM_2021_PV
Ch1.Dev2.ST_2028_PV|Ch1.Dev2.ST_2028_PV
Ch1.Dev2.ST_2035_PV|Ch1.Dev2.ST_2035_PV
Ch1.Dev2.LT_2042_PV|Ch1.Dev2.LT_2042_PV
Ch1.Dev2.AT_2049_PV|Ch1.Dev2.AT_2049_PV
Ch1.Dev2.TT_2056_PV|Ch1.Dev2.TT_2056_PV
Ch1.Dev2.FT_2063_PV|Ch1.Dev2.FT_2063_PV
Ch1.Dev2.FT_2070_PV|Ch1.Dev2.FT_2070_PV
Ch1.Dev2.LT_2077_PV|Ch1.Dev2.LT_2077_PV
Ch1.Dev2.WT_2084_PV|Ch1.Dev2.WT_2084_PV
Ch1.Dev2.AT_2091_PV|Ch1.Dev2.AT_2091_PV
Ch1.Dev2.LT_2098_PV|Ch1.Dev2.LT_2098_PV
Ch1.Dev2.TT_2105_PV|Ch1.Dev2.TT_2105_PV
Ch1.Dev2.AT_2112_PV|Ch1.Dev2.AT_2112_PV
Ch1.Dev2.XT_2119_PV|Ch1.Dev2.XT_2119_PV
Ch1.Dev2.ZT_2126_PV|Ch1.Dev2.ZT_2126_PV
Ch1.Dev2.YT_2133_PV|Ch1.Dev2.YT_2133_PV
Ch1.Dev2.KQ_2140_PV|Ch1.Dev2.KQ_2140_PV
Ch1.Dev2.KQ_2147_PV|Ch1.Dev2.KQ_2147_PV
Ch1.Dev2.WT_2154_PV|Ch1.Dev2.WT_2154_PV
Ch1.Dev2.KT_2161_PV|Ch1.Dev2.KT_2161_PV
Ch1.Dev2.JT_2168_PV|Ch1.Dev2.JT_2168_PV
Ch1.Dev2.VT_2175_PV|Ch1.Dev2.VT_2175_PV
Ch1.Dev2.TT_2182_PV|Ch1.Dev2.TT_2182_PV
Ch1.Dev2.JQ_2189_PV|Ch1.Dev2.JQ_2189_PV
Ch1.Dev2.KQ_2196_PV|Ch1.Dev2.KQ_2196_PV
Ch1.Dev2.KT_2203_PV|Ch1.Dev2.KT_2203_PV
Ch1.Dev2.XA_2210_PV|Ch1.Dev2.XA_2210_PV
Ch1.Dev2.XS_2007_PV.Q|Ch1.Dev2.XS_2007_PV.Q
Ch1.Dev2.XT_2014_PV.Q|Ch1.Dev2.XT_2014_PV.Q
Ch1.Dev2.XM_2021_PV.Q|Ch1.Dev2.XM_2021_PV.Q
Ch1.Dev2.ST_2028_PV.Q|Ch1.Dev2.ST_2028_PV.Q
Ch1.Dev2.ST_2035_PV.Q|Ch1.Dev2.ST_2035_PV.Q
Ch1.Dev2.LT_2042_PV.Q|Ch1.Dev2.LT_2042_PV.Q
Ch1.Dev2.AT_2049_PV.Q|Ch1.Dev2.AT_2049_PV.Q
Ch1.Dev2.TT_2056_PV.Q|Ch1.Dev2.TT_2056_PV.Q
Ch1.Dev2.FT_2063_PV.Q|Ch1.Dev2.FT_2063_PV.Q
Ch1.Dev2.FT_2070_PV.Q|Ch1.Dev2.FT_2070_PV.Q
Ch1.Dev2.LT_2077_PV.Q|Ch1.Dev2.LT_2077_PV.Q
Ch1.Dev2.WT_2084_PV.Q|Ch1.Dev2.WT_2084_PV.Q
Ch1.Dev2.AT_2091_PV.Q|Ch1.Dev2.AT_2091_PV.Q
Ch1.Dev2.LT_2098_PV.Q|Ch1.Dev2.LT_2098_PV.Q
Ch1.Dev2.TT_2105_PV.Q|Ch1.Dev2.TT_2105_PV.Q
Ch1.Dev2.AT_2112_PV.Q|Ch1.Dev2.AT_2112_PV.Q
Ch1.Dev2.XT_2119_PV.Q|Ch1.Dev2.XT_2119_PV.Q
Ch1.Dev2.ZT_2126_PV.Q|Ch1.Dev2.ZT_2126_PV.Q
Ch1.Dev2.YT_2133_PV.Q|Ch1.Dev2.YT_2133_PV.Q
Ch1.Dev2.KQ_2140_PV.Q|Ch1.Dev2.KQ_2140_PV.Q
Ch1.Dev2.KQ_2147_PV.Q|Ch1.Dev2.KQ_2147_PV.Q
Ch1.Dev2.WT_2154_PV.Q|Ch1.Dev2.WT_2154_PV.Q
Ch1.Dev2.KT_2161_PV.Q|Ch1.Dev2.KT_2161_PV.Q
Ch1.Dev2.JT_2168_PV.Q|Ch1.Dev2.JT_2168_PV.Q
Ch1.Dev2.VT_2175_PV.Q|Ch1.Dev2.VT_2175_PV.Q
Ch1.Dev2.TT_2182_PV.Q|Ch1.Dev2.TT_2182_PV.Q
Ch1.Dev2.JQ_2189_PV.Q|Ch1.Dev2.JQ_2189_PV.Q
Ch1.Dev2.KQ_2196_PV.Q|Ch1.Dev2.KQ_2196_PV.Q
Ch1.Dev2.KT_2203_PV.Q|Ch1.Dev2.KT_2203_PV.Q
Ch1.Dev2.XA_2210_PV.Q|Ch1.Dev2.XA_2210_PV.Q
"
    for LINE in $ADDR_INTAKE_SILO_01; do
      NODE=${LINE%%|*}; TOPIC=${LINE##*|}
      RES=$(gql "mutation{opcUaDevice{addAddress(deviceName:\\\"intake-silo-01\\\",input:{address:\\\"NodeId://ns=2;s=$NODE\\\",topic:\\\"intake-silo-01/$TOPIC\\\",publishMode:SEPARATE}){success errors}}}")
      case "$RES" in
        *'"success":true'*) TOTAL_OK=$((TOTAL_OK+1)) ;;
        *) TOTAL_FAIL=$((TOTAL_FAIL+1)); echo "[park-init] WARN $NODE -> $RES" ;;
      esac
    done
    ;;
esac

# ---------------------------------------------------------------- separator-01
# profiel vendor-b, protocol opc-ua
EXISTS=$(gql "{opcUaDevices{name}}")
case "$EXISTS" in
  *'"separator-01"'*)
    echo "[park-init] separator-01 bestaat al; enabled afdwingen."
    ON=$(gql "mutation{opcUaDevice{toggle(name:\\\"separator-01\\\",enabled:true){success}}}")
    echo "[park-init] enable separator-01: $ON"
    ;;
  *)
    ADD=$(gql "mutation{opcUaDevice{add(input:{name:\\\"separator-01\\\",namespace:\\\"raw/vla-park\\\",nodeId:\\\"local\\\",enabled:true,config:{endpointUrl:\\\"opc.tcp://vla-separator-01:4840/VendorB\\\",securityPolicy:None,subscriptionSamplingInterval:1000.0}}){success errors}}}")
    echo "[park-init] add separator-01: $ADD"
    case "$ADD" in
      *'"success":true'*) : ;;
      *) echo "[park-init] FOUT: add separator-01 mislukt"; TOTAL_FAIL=$((TOTAL_FAIL+1)) ;;
    esac
    ADDR_SEPARATOR_01="
Machine.State.StateCurrent|Machine.State.StateCurrent
Machine.State.StateCurrentStr|Machine.State.StateCurrentStr
Machine.State.UnitMode|Machine.State.UnitMode
Machine.State.MachSpeed|Machine.State.MachSpeed
Machine.State.CurMachSpeed|Machine.State.CurMachSpeed
Machine.Process.Rpm|Machine.Process.Rpm
Machine.Process.FatPct|Machine.Process.FatPct
Machine.Process.FeedLMin|Machine.Process.FeedLMin
Machine.Process.CreamLMin|Machine.Process.CreamLMin
Machine.Process.SkimLMin|Machine.Process.SkimLMin
Machine.Process.BowlDpBar|Machine.Process.BowlDpBar
Machine.Process.DischargeCount|Machine.Process.DischargeCount
Machine.Process.SolidsPct|Machine.Process.SolidsPct
Machine.Setpoint.SpPrimary|Machine.Setpoint.SpPrimary
Machine.Setpoint.SpSecondary|Machine.Setpoint.SpSecondary
Machine.Setpoint.SpSpeedPct|Machine.Setpoint.SpSpeedPct
Machine.Setpoint.SpProductMode|Machine.Setpoint.SpProductMode
Machine.Actuator.ValvePosPct|Machine.Actuator.ValvePosPct
Machine.Actuator.DriveOutPct|Machine.Actuator.DriveOutPct
Machine.Counter.UnitsTotal|Machine.Counter.UnitsTotal
Machine.Counter.RejectTotal|Machine.Counter.RejectTotal
Machine.Counter.MassTotalKg|Machine.Counter.MassTotalKg
Machine.Counter.RuntimeHours|Machine.Counter.RuntimeHours
Machine.Diagnostics.MotorCurrentA|Machine.Diagnostics.MotorCurrentA
Machine.Diagnostics.VibrationMmS|Machine.Diagnostics.VibrationMmS
Machine.Diagnostics.BearingTempC|Machine.Diagnostics.BearingTempC
Machine.Diagnostics.EnergyKWh|Machine.Diagnostics.EnergyKWh
Machine.Diagnostics.CyclesSinceCip|Machine.Diagnostics.CyclesSinceCip
Machine.Diagnostics.HoursSinceService|Machine.Diagnostics.HoursSinceService
Machine.Alarm.AlarmWord|Machine.Alarm.AlarmWord
"
    for LINE in $ADDR_SEPARATOR_01; do
      NODE=${LINE%%|*}; TOPIC=${LINE##*|}
      RES=$(gql "mutation{opcUaDevice{addAddress(deviceName:\\\"separator-01\\\",input:{address:\\\"NodeId://ns=2;s=$NODE\\\",topic:\\\"separator-01/$TOPIC\\\",publishMode:SEPARATE}){success errors}}}")
      case "$RES" in
        *'"success":true'*) TOTAL_OK=$((TOTAL_OK+1)) ;;
        *) TOTAL_FAIL=$((TOTAL_FAIL+1)); echo "[park-init] WARN $NODE -> $RES" ;;
      esac
    done
    ;;
esac

# ---------------------------------------------------------------- homogeniser-01
# profiel vendor-b, protocol opc-ua
EXISTS=$(gql "{opcUaDevices{name}}")
case "$EXISTS" in
  *'"homogeniser-01"'*)
    echo "[park-init] homogeniser-01 bestaat al; enabled afdwingen."
    ON=$(gql "mutation{opcUaDevice{toggle(name:\\\"homogeniser-01\\\",enabled:true){success}}}")
    echo "[park-init] enable homogeniser-01: $ON"
    ;;
  *)
    ADD=$(gql "mutation{opcUaDevice{add(input:{name:\\\"homogeniser-01\\\",namespace:\\\"raw/vla-park\\\",nodeId:\\\"local\\\",enabled:true,config:{endpointUrl:\\\"opc.tcp://vla-homogeniser-01:4840/VendorB\\\",securityPolicy:None,subscriptionSamplingInterval:1000.0}}){success errors}}}")
    echo "[park-init] add homogeniser-01: $ADD"
    case "$ADD" in
      *'"success":true'*) : ;;
      *) echo "[park-init] FOUT: add homogeniser-01 mislukt"; TOTAL_FAIL=$((TOTAL_FAIL+1)) ;;
    esac
    ADDR_HOMOGENISER_01="
Machine.State.StateCurrent|Machine.State.StateCurrent
Machine.State.StateCurrentStr|Machine.State.StateCurrentStr
Machine.State.UnitMode|Machine.State.UnitMode
Machine.State.MachSpeed|Machine.State.MachSpeed
Machine.State.CurMachSpeed|Machine.State.CurMachSpeed
Machine.Process.Stage1Bar|Machine.Process.Stage1Bar
Machine.Process.Stage2Bar|Machine.Process.Stage2Bar
Machine.Process.FlowLMin|Machine.Process.FlowLMin
Machine.Process.InletTempC|Machine.Process.InletTempC
Machine.Process.OutletTempC|Machine.Process.OutletTempC
Machine.Process.PistonLoadPct|Machine.Process.PistonLoadPct
Machine.Process.OilTempC|Machine.Process.OilTempC
Machine.Process.OilPressBar|Machine.Process.OilPressBar
Machine.Setpoint.SpPrimary|Machine.Setpoint.SpPrimary
Machine.Setpoint.SpSecondary|Machine.Setpoint.SpSecondary
Machine.Setpoint.SpSpeedPct|Machine.Setpoint.SpSpeedPct
Machine.Setpoint.SpProductMode|Machine.Setpoint.SpProductMode
Machine.Actuator.ValvePosPct|Machine.Actuator.ValvePosPct
Machine.Actuator.DriveOutPct|Machine.Actuator.DriveOutPct
Machine.Counter.UnitsTotal|Machine.Counter.UnitsTotal
Machine.Counter.RejectTotal|Machine.Counter.RejectTotal
Machine.Counter.MassTotalKg|Machine.Counter.MassTotalKg
Machine.Counter.RuntimeHours|Machine.Counter.RuntimeHours
Machine.Diagnostics.MotorCurrentA|Machine.Diagnostics.MotorCurrentA
Machine.Diagnostics.VibrationMmS|Machine.Diagnostics.VibrationMmS
Machine.Diagnostics.BearingTempC|Machine.Diagnostics.BearingTempC
Machine.Diagnostics.EnergyKWh|Machine.Diagnostics.EnergyKWh
Machine.Diagnostics.CyclesSinceCip|Machine.Diagnostics.CyclesSinceCip
Machine.Diagnostics.HoursSinceService|Machine.Diagnostics.HoursSinceService
Machine.Alarm.AlarmWord|Machine.Alarm.AlarmWord
"
    for LINE in $ADDR_HOMOGENISER_01; do
      NODE=${LINE%%|*}; TOPIC=${LINE##*|}
      RES=$(gql "mutation{opcUaDevice{addAddress(deviceName:\\\"homogeniser-01\\\",input:{address:\\\"NodeId://ns=2;s=$NODE\\\",topic:\\\"homogeniser-01/$TOPIC\\\",publishMode:SEPARATE}){success errors}}}")
      case "$RES" in
        *'"success":true'*) TOTAL_OK=$((TOTAL_OK+1)) ;;
        *) TOTAL_FAIL=$((TOTAL_FAIL+1)); echo "[park-init] WARN $NODE -> $RES" ;;
      esac
    done
    ;;
esac

# ---------------------------------------------------------------- pasteuriser-01
# profiel vendor-a, protocol opc-da
EXISTS=$(gql "{opcUaDevices{name}}")
case "$EXISTS" in
  *'"pasteuriser-01"'*)
    echo "[park-init] pasteuriser-01 bestaat al; enabled afdwingen."
    ON=$(gql "mutation{opcUaDevice{toggle(name:\\\"pasteuriser-01\\\",enabled:true){success}}}")
    echo "[park-init] enable pasteuriser-01: $ON"
    ;;
  *)
    ADD=$(gql "mutation{opcUaDevice{add(input:{name:\\\"pasteuriser-01\\\",namespace:\\\"raw/vla-park\\\",nodeId:\\\"local\\\",enabled:true,config:{endpointUrl:\\\"opc.tcp://vla-pasteuriser-01:4840/VendorA\\\",securityPolicy:None,subscriptionSamplingInterval:1000.0}}){success errors}}}")
    echo "[park-init] add pasteuriser-01: $ADD"
    case "$ADD" in
      *'"success":true'*) : ;;
      *) echo "[park-init] FOUT: add pasteuriser-01 mislukt"; TOTAL_FAIL=$((TOTAL_FAIL+1)) ;;
    esac
    ADDR_PASTEURISER_01="
Ch1.Dev1.XS_2007_PV|Ch1.Dev1.XS_2007_PV
Ch1.Dev1.XT_2014_PV|Ch1.Dev1.XT_2014_PV
Ch1.Dev1.XM_2021_PV|Ch1.Dev1.XM_2021_PV
Ch1.Dev1.ST_2028_PV|Ch1.Dev1.ST_2028_PV
Ch1.Dev1.ST_2035_PV|Ch1.Dev1.ST_2035_PV
Ch1.Dev1.TT_2042_PV|Ch1.Dev1.TT_2042_PV
Ch1.Dev1.TT_2049_PV|Ch1.Dev1.TT_2049_PV
Ch1.Dev1.TT_2056_PV|Ch1.Dev1.TT_2056_PV
Ch1.Dev1.FT_2063_PV|Ch1.Dev1.FT_2063_PV
Ch1.Dev1.KT_2070_PV|Ch1.Dev1.KT_2070_PV
Ch1.Dev1.AT_2077_PV|Ch1.Dev1.AT_2077_PV
Ch1.Dev1.AT_2084_PV|Ch1.Dev1.AT_2084_PV
Ch1.Dev1.AT_2091_PV|Ch1.Dev1.AT_2091_PV
Ch1.Dev1.TT_2098_PV|Ch1.Dev1.TT_2098_PV
Ch1.Dev1.FT_2105_PV|Ch1.Dev1.FT_2105_PV
Ch1.Dev1.AT_2112_PV|Ch1.Dev1.AT_2112_PV
Ch1.Dev1.XT_2119_PV|Ch1.Dev1.XT_2119_PV
Ch1.Dev1.ZT_2126_PV|Ch1.Dev1.ZT_2126_PV
Ch1.Dev1.YT_2133_PV|Ch1.Dev1.YT_2133_PV
Ch1.Dev1.KQ_2140_PV|Ch1.Dev1.KQ_2140_PV
Ch1.Dev1.KQ_2147_PV|Ch1.Dev1.KQ_2147_PV
Ch1.Dev1.WT_2154_PV|Ch1.Dev1.WT_2154_PV
Ch1.Dev1.KT_2161_PV|Ch1.Dev1.KT_2161_PV
Ch1.Dev1.JT_2168_PV|Ch1.Dev1.JT_2168_PV
Ch1.Dev1.VT_2175_PV|Ch1.Dev1.VT_2175_PV
Ch1.Dev1.TT_2182_PV|Ch1.Dev1.TT_2182_PV
Ch1.Dev1.JQ_2189_PV|Ch1.Dev1.JQ_2189_PV
Ch1.Dev1.KQ_2196_PV|Ch1.Dev1.KQ_2196_PV
Ch1.Dev1.KT_2203_PV|Ch1.Dev1.KT_2203_PV
Ch1.Dev1.XA_2210_PV|Ch1.Dev1.XA_2210_PV
Ch1.Dev1.XS_2007_PV.Q|Ch1.Dev1.XS_2007_PV.Q
Ch1.Dev1.XT_2014_PV.Q|Ch1.Dev1.XT_2014_PV.Q
Ch1.Dev1.XM_2021_PV.Q|Ch1.Dev1.XM_2021_PV.Q
Ch1.Dev1.ST_2028_PV.Q|Ch1.Dev1.ST_2028_PV.Q
Ch1.Dev1.ST_2035_PV.Q|Ch1.Dev1.ST_2035_PV.Q
Ch1.Dev1.TT_2042_PV.Q|Ch1.Dev1.TT_2042_PV.Q
Ch1.Dev1.TT_2049_PV.Q|Ch1.Dev1.TT_2049_PV.Q
Ch1.Dev1.TT_2056_PV.Q|Ch1.Dev1.TT_2056_PV.Q
Ch1.Dev1.FT_2063_PV.Q|Ch1.Dev1.FT_2063_PV.Q
Ch1.Dev1.KT_2070_PV.Q|Ch1.Dev1.KT_2070_PV.Q
Ch1.Dev1.AT_2077_PV.Q|Ch1.Dev1.AT_2077_PV.Q
Ch1.Dev1.AT_2084_PV.Q|Ch1.Dev1.AT_2084_PV.Q
Ch1.Dev1.AT_2091_PV.Q|Ch1.Dev1.AT_2091_PV.Q
Ch1.Dev1.TT_2098_PV.Q|Ch1.Dev1.TT_2098_PV.Q
Ch1.Dev1.FT_2105_PV.Q|Ch1.Dev1.FT_2105_PV.Q
Ch1.Dev1.AT_2112_PV.Q|Ch1.Dev1.AT_2112_PV.Q
Ch1.Dev1.XT_2119_PV.Q|Ch1.Dev1.XT_2119_PV.Q
Ch1.Dev1.ZT_2126_PV.Q|Ch1.Dev1.ZT_2126_PV.Q
Ch1.Dev1.YT_2133_PV.Q|Ch1.Dev1.YT_2133_PV.Q
Ch1.Dev1.KQ_2140_PV.Q|Ch1.Dev1.KQ_2140_PV.Q
Ch1.Dev1.KQ_2147_PV.Q|Ch1.Dev1.KQ_2147_PV.Q
Ch1.Dev1.WT_2154_PV.Q|Ch1.Dev1.WT_2154_PV.Q
Ch1.Dev1.KT_2161_PV.Q|Ch1.Dev1.KT_2161_PV.Q
Ch1.Dev1.JT_2168_PV.Q|Ch1.Dev1.JT_2168_PV.Q
Ch1.Dev1.VT_2175_PV.Q|Ch1.Dev1.VT_2175_PV.Q
Ch1.Dev1.TT_2182_PV.Q|Ch1.Dev1.TT_2182_PV.Q
Ch1.Dev1.JQ_2189_PV.Q|Ch1.Dev1.JQ_2189_PV.Q
Ch1.Dev1.KQ_2196_PV.Q|Ch1.Dev1.KQ_2196_PV.Q
Ch1.Dev1.KT_2203_PV.Q|Ch1.Dev1.KT_2203_PV.Q
Ch1.Dev1.XA_2210_PV.Q|Ch1.Dev1.XA_2210_PV.Q
"
    for LINE in $ADDR_PASTEURISER_01; do
      NODE=${LINE%%|*}; TOPIC=${LINE##*|}
      RES=$(gql "mutation{opcUaDevice{addAddress(deviceName:\\\"pasteuriser-01\\\",input:{address:\\\"NodeId://ns=2;s=$NODE\\\",topic:\\\"pasteuriser-01/$TOPIC\\\",publishMode:SEPARATE}){success errors}}}")
      case "$RES" in
        *'"success":true'*) TOTAL_OK=$((TOTAL_OK+1)) ;;
        *) TOTAL_FAIL=$((TOTAL_FAIL+1)); echo "[park-init] WARN $NODE -> $RES" ;;
      esac
    done
    ;;
esac

# ---------------------------------------------------------------- cip-skid-01
# profiel vendor-a, protocol opc-ua
EXISTS=$(gql "{opcUaDevices{name}}")
case "$EXISTS" in
  *'"cip-skid-01"'*)
    echo "[park-init] cip-skid-01 bestaat al; enabled afdwingen."
    ON=$(gql "mutation{opcUaDevice{toggle(name:\\\"cip-skid-01\\\",enabled:true){success}}}")
    echo "[park-init] enable cip-skid-01: $ON"
    ;;
  *)
    ADD=$(gql "mutation{opcUaDevice{add(input:{name:\\\"cip-skid-01\\\",namespace:\\\"raw/vla-park\\\",nodeId:\\\"local\\\",enabled:true,config:{endpointUrl:\\\"opc.tcp://vla-cip-skid-01:4840/VendorA\\\",securityPolicy:None,subscriptionSamplingInterval:1000.0}}){success errors}}}")
    echo "[park-init] add cip-skid-01: $ADD"
    case "$ADD" in
      *'"success":true'*) : ;;
      *) echo "[park-init] FOUT: add cip-skid-01 mislukt"; TOTAL_FAIL=$((TOTAL_FAIL+1)) ;;
    esac
    ADDR_CIP_SKID_01="
Ch1.Dev3.XS_2007_PV|Ch1.Dev3.XS_2007_PV
Ch1.Dev3.XT_2014_PV|Ch1.Dev3.XT_2014_PV
Ch1.Dev3.XM_2021_PV|Ch1.Dev3.XM_2021_PV
Ch1.Dev3.ST_2028_PV|Ch1.Dev3.ST_2028_PV
Ch1.Dev3.ST_2035_PV|Ch1.Dev3.ST_2035_PV
Ch1.Dev3.XT_2042_PV|Ch1.Dev3.XT_2042_PV
Ch1.Dev3.KT_2049_PV|Ch1.Dev3.KT_2049_PV
Ch1.Dev3.TT_2056_PV|Ch1.Dev3.TT_2056_PV
Ch1.Dev3.AT_2063_PV|Ch1.Dev3.AT_2063_PV
Ch1.Dev3.FT_2070_PV|Ch1.Dev3.FT_2070_PV
Ch1.Dev3.CT_2077_PV|Ch1.Dev3.CT_2077_PV
Ch1.Dev3.KT_2084_PV|Ch1.Dev3.KT_2084_PV
Ch1.Dev3.KT_2091_PV|Ch1.Dev3.KT_2091_PV
Ch1.Dev3.TT_2098_PV|Ch1.Dev3.TT_2098_PV
Ch1.Dev3.FT_2105_PV|Ch1.Dev3.FT_2105_PV
Ch1.Dev3.AT_2112_PV|Ch1.Dev3.AT_2112_PV
Ch1.Dev3.XT_2119_PV|Ch1.Dev3.XT_2119_PV
Ch1.Dev3.ZT_2126_PV|Ch1.Dev3.ZT_2126_PV
Ch1.Dev3.YT_2133_PV|Ch1.Dev3.YT_2133_PV
Ch1.Dev3.KQ_2140_PV|Ch1.Dev3.KQ_2140_PV
Ch1.Dev3.KQ_2147_PV|Ch1.Dev3.KQ_2147_PV
Ch1.Dev3.WT_2154_PV|Ch1.Dev3.WT_2154_PV
Ch1.Dev3.KT_2161_PV|Ch1.Dev3.KT_2161_PV
Ch1.Dev3.JT_2168_PV|Ch1.Dev3.JT_2168_PV
Ch1.Dev3.VT_2175_PV|Ch1.Dev3.VT_2175_PV
Ch1.Dev3.TT_2182_PV|Ch1.Dev3.TT_2182_PV
Ch1.Dev3.JQ_2189_PV|Ch1.Dev3.JQ_2189_PV
Ch1.Dev3.KQ_2196_PV|Ch1.Dev3.KQ_2196_PV
Ch1.Dev3.KT_2203_PV|Ch1.Dev3.KT_2203_PV
Ch1.Dev3.XA_2210_PV|Ch1.Dev3.XA_2210_PV
Ch1.Dev3.XS_2007_PV.Q|Ch1.Dev3.XS_2007_PV.Q
Ch1.Dev3.XT_2014_PV.Q|Ch1.Dev3.XT_2014_PV.Q
Ch1.Dev3.XM_2021_PV.Q|Ch1.Dev3.XM_2021_PV.Q
Ch1.Dev3.ST_2028_PV.Q|Ch1.Dev3.ST_2028_PV.Q
Ch1.Dev3.ST_2035_PV.Q|Ch1.Dev3.ST_2035_PV.Q
Ch1.Dev3.XT_2042_PV.Q|Ch1.Dev3.XT_2042_PV.Q
Ch1.Dev3.KT_2049_PV.Q|Ch1.Dev3.KT_2049_PV.Q
Ch1.Dev3.TT_2056_PV.Q|Ch1.Dev3.TT_2056_PV.Q
Ch1.Dev3.AT_2063_PV.Q|Ch1.Dev3.AT_2063_PV.Q
Ch1.Dev3.FT_2070_PV.Q|Ch1.Dev3.FT_2070_PV.Q
Ch1.Dev3.CT_2077_PV.Q|Ch1.Dev3.CT_2077_PV.Q
Ch1.Dev3.KT_2084_PV.Q|Ch1.Dev3.KT_2084_PV.Q
Ch1.Dev3.KT_2091_PV.Q|Ch1.Dev3.KT_2091_PV.Q
Ch1.Dev3.TT_2098_PV.Q|Ch1.Dev3.TT_2098_PV.Q
Ch1.Dev3.FT_2105_PV.Q|Ch1.Dev3.FT_2105_PV.Q
Ch1.Dev3.AT_2112_PV.Q|Ch1.Dev3.AT_2112_PV.Q
Ch1.Dev3.XT_2119_PV.Q|Ch1.Dev3.XT_2119_PV.Q
Ch1.Dev3.ZT_2126_PV.Q|Ch1.Dev3.ZT_2126_PV.Q
Ch1.Dev3.YT_2133_PV.Q|Ch1.Dev3.YT_2133_PV.Q
Ch1.Dev3.KQ_2140_PV.Q|Ch1.Dev3.KQ_2140_PV.Q
Ch1.Dev3.KQ_2147_PV.Q|Ch1.Dev3.KQ_2147_PV.Q
Ch1.Dev3.WT_2154_PV.Q|Ch1.Dev3.WT_2154_PV.Q
Ch1.Dev3.KT_2161_PV.Q|Ch1.Dev3.KT_2161_PV.Q
Ch1.Dev3.JT_2168_PV.Q|Ch1.Dev3.JT_2168_PV.Q
Ch1.Dev3.VT_2175_PV.Q|Ch1.Dev3.VT_2175_PV.Q
Ch1.Dev3.TT_2182_PV.Q|Ch1.Dev3.TT_2182_PV.Q
Ch1.Dev3.JQ_2189_PV.Q|Ch1.Dev3.JQ_2189_PV.Q
Ch1.Dev3.KQ_2196_PV.Q|Ch1.Dev3.KQ_2196_PV.Q
Ch1.Dev3.KT_2203_PV.Q|Ch1.Dev3.KT_2203_PV.Q
Ch1.Dev3.XA_2210_PV.Q|Ch1.Dev3.XA_2210_PV.Q
"
    for LINE in $ADDR_CIP_SKID_01; do
      NODE=${LINE%%|*}; TOPIC=${LINE##*|}
      RES=$(gql "mutation{opcUaDevice{addAddress(deviceName:\\\"cip-skid-01\\\",input:{address:\\\"NodeId://ns=2;s=$NODE\\\",topic:\\\"cip-skid-01/$TOPIC\\\",publishMode:SEPARATE}){success errors}}}")
      case "$RES" in
        *'"success":true'*) TOTAL_OK=$((TOTAL_OK+1)) ;;
        *) TOTAL_FAIL=$((TOTAL_FAIL+1)); echo "[park-init] WARN $NODE -> $RES" ;;
      esac
    done
    ;;
esac


# ---------------------------------------------------------- archive group
# Alleen de 12 kritieke topics naar Mongo, 14 dagen. De overige 348
# parksignalen gaan naar TDengine: dat is een historian en Mongo niet.
# Zonder deze beperking is het park ~4,3 miljoen documenten per dag.
AG=$(gql "{archiveGroups{name}}")
case "$AG" in
  *'"dw_park_critical"'*)
    # Bestaat al. Toch enable aanroepen: idempotent hoort CONVERGEREND te
    # zijn, niet 'bestaat al, klaar'. Een groep die ooit uitgeschakeld is
    # aangemaakt blijft anders voor altijd stil, en het init-script meldt
    # doodleuk succes. enable is veilig herhaalbaar.
    EN=$(gql "mutation{archiveGroup{enable(name:\"dw_park_critical\"){success message}}}")
    echo "[park-init] archive group bestond al, enable: $EN"
    ;;
  *)
    RES=$(gql "mutation{archiveGroup{create(input:{name:\\\"dw_park_critical\\\",topicFilter:[\\\"DairyWorks/Vla-B/Receiving/intake-silo-01/Status/level_L\\\",\\\"DairyWorks/Vla-B/Standardisation/separator-01/Status/fat_pct\\\",\\\"DairyWorks/Vla-B/Standardisation/homogeniser-01/Status/stage1_bar\\\",\\\"DairyWorks/Vla-B/Mixing/blend-tank-01/Status/dose_milk_kg\\\",\\\"DairyWorks/Vla-B/Cook/pasteuriser-01/Status/hold_temp_C\\\",\\\"DairyWorks/Vla-B/Cook/preheater-01/Status/temp_out_C\\\",\\\"DairyWorks/Vla-B/Cooling/plate-cooler-01/Status/prod_out_C\\\",\\\"DairyWorks/Vla-B/Filling/filler-01/Status/quality_pct\\\",\\\"DairyWorks/Vla-B/Filling/bottle-filler-01/Status/quality_pct\\\",\\\"DairyWorks/Vla-B/Packaging/case-packer-01/Status/output_rate_ph\\\",\\\"DairyWorks/Vla-B/Packaging/palletiser-01/Status/packs_on_pallet\\\",\\\"DairyWorks/Vla-B/Utilities/cip-skid-01/Status/return_cond_mS\\\"],lastValType:NONE,archiveType:MONGODB,archiveRetention:\\\"14d\\\"}){success message}}}")
    echo "[park-init] archive group: $RES"
    case "$RES" in
      *'"success":true'*)
        # Een archive group komt UITGESCHAKELD ter wereld. Zonder deze
        # enable-stap archiveert hij niets, en dat merk je pas als er een
        # batchrapport ontbreekt. De create-melding zegt het er zelf bij:
        # 'created successfully (disabled by default)'.
        EN=$(gql "mutation{archiveGroup{enable(name:\"dw_park_critical\"){success message}}}")
        echo "[park-init] archive group enable: $EN"
        ;;
      *) echo "[park-init] WAARSCHUWING: archive group niet aangemaakt. Het park draait dan wel, maar er gaat niets naar Mongo." ;;
    esac
    ;;
esac

echo "[park-init] klaar: $TOTAL_OK adressen OK, $TOTAL_FAIL mislukt."
[ "$TOTAL_FAIL" -gt 0 ] && exit 1
echo "[park-init] MonsterMQ ingest 5 OPC-machines -> raw/vla-park/#"
exit 0
