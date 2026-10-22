"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getUi } from "@/lib/client";
import {
  POLL,
  useAlarms,
  useAllHandlingUnits,
  useBatches,
  useEquipmentHealth,
  useInventory,
  useKpiSummary,
  useLines,
  useLogistics,
  usePrepOrders,
} from "@/lib/queries";
import { asSimLogistics, type SimLogistics } from "./flow";
import type { KpiValue, LineId, ProductLine } from "@/lib/types";
import { batchFlavours, flavourOfBatch, flavourOfRecipe, prepStationView, prepVisual, type Flavour, type PrepView } from "./lines";
import {
  deriveLogistics,
  logisticsVisuals,
  equipmentVisual,
  siloFill,
  siloVisual,
  type AlarmLike,
  type HealthLike,
  type Hu,
  type Logistics,
  type MaterialLike,
  type StepLike,
  type Visual,
} from "./derive";
import { LINE2_EQUIPMENT, LINE_EQUIPMENT, PREP_STATIONS, SILO_SLOTS, type LineEquipment, type PrepStationLayout, type Vec3 } from "./layout";

type LinePayload = {
  available?: boolean;
  engine_reachable: boolean;
  steps: Array<StepLike & { secondary?: Array<{ label: string; unit: string; value: number | null; stale?: boolean }> }>;
  line: {
    batch?: { batch_id: string; state: string; verdict: string | null };
    filling?: { packs_total: number; packs_target: number | null; packs_progress_pct: number | null; pallets: number | null };
  } | null;
  stale_threshold_s?: number;
};

type Inventory = { materials?: MaterialLike[]; produced_packs?: number | null; produced_L?: number | null };

export type TwinEquipment = Omit<LineEquipment, "id" | "pos" | "name"> & {
  id: string;
  name: string;
  pos: Vec3;
  line: LineId;
  visual: Visual;
  health: HealthLike | null;
  live: LinePayload["steps"][number] | null;
  alarms: AlarmLike[];
};

/** Lijn L2 in hal 2: dezelfde vorm als de L1-velden op het model. */
export type TwinLine2 = {
  engineReachable: boolean | null;
  batch: { batch_id: string; state: string; verdict: string | null } | null;
  filling: NonNullable<LinePayload["line"]>["filling"] | null;
  equipment: TwinEquipment[];
  /** Smaak van wat L2 nu maakt (lopende batch, anders het autopilot-recept). */
  flavour: Flavour;
};

export type TwinPrepStation = PrepStationLayout & { view: PrepView; visual: Visual };

export type TwinSilo = { slot: number; pos: Vec3; material: MaterialLike; visual: Visual; fill: number | null };

export type TwinModel = {
  /** null zolang er nog geen enkel antwoord binnen is: dan weten we niets. */
  engineReachable: boolean | null;
  batch: { batch_id: string; state: string; verdict: string | null } | null;
  filling: NonNullable<LinePayload["line"]>["filling"] | null;
  /** Lijn L1 (hal 1). */
  equipment: TwinEquipment[];
  line2: TwinLine2;
  prep: TwinPrepStation[];
  /** batch_id -> smaak, voor pakken en pallets in de kleur van het product. */
  flavours: Record<string, Flavour>;
  lines: ProductLine[];
  silos: TwinSilo[];
  logistics: Logistics;
  logisticsVisual: ReturnType<typeof logisticsVisuals>;
  husAvailable: boolean;
  /** Ruwe HU-lijst, voor de batchtijdlijn na COMPLETE. */
  hus: Hu[];
  /** Demo-logistiek van de engine (zendingen, heftrucks, tankwagen); null als die uit staat. */
  sim: SimLogistics | null;
  kpis: KpiValue[];
  kpiWindow: string;
  alarmsOpen: AlarmLike[];
  produced: { packs: number | null; litres: number | null };
  now: number;
};

/** Eén klok voor de hele scène, zodat trucks en leeftijden niet elk een eigen tijd hebben. */
function useNow(ms: number) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

export function useTwinData(): TwinModel {
  const line = useQuery<LinePayload>({
    queryKey: ["ui", "line"],
    queryFn: ({ signal }) => getUi<LinePayload>("/line", signal),
    refetchInterval: POLL.live,
    refetchIntervalInBackground: false,
    retry: 1,
  });
  const line2 = useQuery<LinePayload>({
    queryKey: ["ui", "line", "L2"],
    queryFn: ({ signal }) => getUi<LinePayload>("/line?line=L2", signal),
    refetchInterval: POLL.live,
    refetchIntervalInBackground: false,
    retry: 1,
  });
  const lines = useLines();
  const prepOrders = usePrepOrders();
  const batches = useBatches<Array<{ batch_id: string; recipe_id?: string | null; product_name?: string | null }>>();
  const health = useEquipmentHealth<HealthLike[]>();
  const alarms = useAlarms();
  const inventory = useInventory<Inventory>();
  const hus = useAllHandlingUnits<Hu[]>();
  const kpiWindow = "day";
  const kpi = useKpiSummary(kpiWindow, false);
  const sim = useLogistics<unknown>();
  const now = useNow(1000);

  return useMemo(() => {
    const engineReachable = line.isError ? false : line.data ? line.data.engine_reachable : null;
    const openAlarms = (alarms.data ?? []).filter((a) => a.state === "open") as AlarmLike[];
    const steps = line.data?.steps ?? [];
    const healthRows = Array.isArray(health.data) ? health.data : [];

    const build = (
      list: ReadonlyArray<Omit<LineEquipment, "id" | "pos" | "name"> & { id: string; name: string; pos: Vec3 }>,
      lineId: LineId,
      lineSteps: LinePayload["steps"],
      reachable: boolean | null,
    ): TwinEquipment[] =>
      list.map((eq) => {
        const h = healthRows.find((r) => r.equipment_id === eq.id) ?? null;
        const step = lineSteps.find((s) => s.id === eq.step) ?? null;
        const mine = openAlarms.filter((a) => a.equipment_id === eq.id);
        return {
          ...eq,
          line: lineId,
          health: h,
          live: step,
          alarms: mine,
          visual: equipmentVisual({ health: h, step, alarms: mine, engineReachable: reachable ?? undefined }),
        };
      });
    const equipment = build(LINE_EQUIPMENT, "L1", steps, engineReachable);

    const lineDefs = Array.isArray(lines.data) ? lines.data : [];
    const flavours = batchFlavours(Array.isArray(batches.data) ? batches.data : [], lineDefs);
    const reachable2 = line2.isError ? false : line2.data ? line2.data.engine_reachable : null;
    const batch2 = line2.data?.line?.batch ?? null;
    const auto2 = lineDefs.find((l) => l.line_id === "L2")?.autopilot?.recipe_id ?? null;
    const l2: TwinLine2 = {
      engineReachable: reachable2,
      batch: batch2,
      filling: line2.data?.line?.filling ?? null,
      equipment: build(LINE2_EQUIPMENT, "L2", line2.data?.steps ?? [], reachable2),
      flavour: batch2 && flavours[batch2.batch_id] ? flavours[batch2.batch_id] : auto2 ? flavourOfRecipe(auto2, lineDefs) : "vanilla",
    };

    const prepData = prepOrders.data;
    const prepAvailable = !!prepData && Array.isArray(prepData.sets);
    const prep: TwinPrepStation[] = PREP_STATIONS.map((st) => {
      const view = prepStationView(st, prepData?.stations, prepData?.sets, lineDefs);
      return { ...st, view, visual: prepVisual(view, prepAvailable) };
    });

    const materials = (inventory.data?.materials ?? []).filter((m) => !m.is_finished_good);
    const silos: TwinSilo[] = materials.slice(0, SILO_SLOTS.length).map((m, slot) => ({
      slot,
      pos: SILO_SLOTS[slot],
      material: m,
      fill: siloFill(m),
      visual: siloVisual(m, engineReachable !== false),
    }));

    const husAvailable = Array.isArray(hus.data);
    const logistics = deriveLogistics(hus.data, now);

    return {
      engineReachable,
      batch: line.data?.line?.batch ?? null,
      filling: line.data?.line?.filling ?? null,
      equipment,
      line2: l2,
      prep,
      flavours,
      lines: lineDefs,
      silos,
      logistics,
      logisticsVisual: logisticsVisuals(logistics, husAvailable, engineReachable),
      husAvailable,
      hus: husAvailable ? (hus.data as Hu[]) : [],
      sim: asSimLogistics(sim.data),
      kpis: kpi.data?.kpis ?? [],
      kpiWindow,
      alarmsOpen: openAlarms,
      produced: {
        packs: inventory.data?.produced_packs ?? null,
        litres: inventory.data?.produced_L ?? null,
      },
      now,
    };
  }, [line.data, line.isError, line2.data, line2.isError, lines.data, prepOrders.data, batches.data, health.data, alarms.data, inventory.data, hus.data, kpi.data, sim.data, now]);
}

/** Smaak van een HU of batch op het model; onbekend is chocolade (L1). */
export function flavourOf(m: Pick<TwinModel, "flavours">, batchId: string | null | undefined): Flavour {
  return flavourOfBatch(batchId, m.flavours);
}
