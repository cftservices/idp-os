"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getUi } from "@/lib/client";
import {
  POLL,
  useAlarms,
  useAllHandlingUnits,
  useEquipmentHealth,
  useInventory,
  useKpiSummary,
  useLogistics,
} from "@/lib/queries";
import { asSimLogistics, type SimLogistics } from "./flow";
import type { KpiValue } from "@/lib/types";
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
import { LINE_EQUIPMENT, SILO_SLOTS, type LineEquipment, type Vec3 } from "./layout";

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

export type TwinEquipment = LineEquipment & {
  visual: Visual;
  health: HealthLike | null;
  live: LinePayload["steps"][number] | null;
  alarms: AlarmLike[];
};

export type TwinSilo = { slot: number; pos: Vec3; material: MaterialLike; visual: Visual; fill: number | null };

export type TwinModel = {
  /** null zolang er nog geen enkel antwoord binnen is: dan weten we niets. */
  engineReachable: boolean | null;
  batch: { batch_id: string; state: string; verdict: string | null } | null;
  filling: NonNullable<LinePayload["line"]>["filling"] | null;
  equipment: TwinEquipment[];
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

    const equipment: TwinEquipment[] = LINE_EQUIPMENT.map((eq) => {
      const h = healthRows.find((r) => r.equipment_id === eq.id) ?? null;
      const step = steps.find((s) => s.id === eq.step) ?? null;
      const mine = openAlarms.filter((a) => a.equipment_id === eq.id);
      return {
        ...eq,
        health: h,
        live: step,
        alarms: mine,
        visual: equipmentVisual({ health: h, step, alarms: mine, engineReachable: engineReachable ?? undefined }),
      };
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
  }, [line.data, line.isError, health.data, alarms.data, inventory.data, hus.data, kpi.data, sim.data, now]);
}
