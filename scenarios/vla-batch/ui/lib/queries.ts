"use client";

import { useQuery, type UseQueryOptions } from "@tanstack/react-query";
import { get, getUi } from "./client";
import type { Refusal } from "./refusal";
import type { Alarm, KpiSummary, LineLive, TagReading, UiModel } from "./types";

/**
 * Query-keys en poll-intervallen op een plek.
 *
 * De oude SPA had zes losse setInterval-loops die nooit werden opgeruimd, met
 * handmatige guards per tab. Drie gedragingen daaruit moeten blijven en worden
 * hier door TanStack Query gedekt in plaats van met de hand:
 *
 *   1. Een verborgen view pollt niet. Een ongemounte route observeert zijn
 *      query niet meer, en `refetchIntervalInBackground: false` dekt het
 *      geval waarin het tabblad zelf naar de achtergrond gaat.
 *   2. Een geopende view refresht direct. `staleTime: 0` laat een nieuwe
 *      observer meteen fetchen in plaats van tot de volgende tick te wachten.
 *   3. Een geopend detailpaneel bevriest zijn lijst-loop. Dat is de `enabled`
 *      -vlag op de lijstquery; zie het `frozen`-argument.
 *
 * De connectie-indicator is de uitzondering: die pollt altijd door, want hij is
 * de enige bron voor "engine offline".
 */

export const POLL = {
  /** Live procesbeeld: de snelste loop, voedt ook de connectie-indicator. */
  live: 1500,
  /** Lijsten die tijdens een demo zichtbaar bewegen. */
  lists: 2500,
  /** Aggregaten: duurder om te berekenen, veranderen trager. */
  aggregates: 5000,
} as const;

export const keys = {
  tags: ["tags"] as const,
  line: ["line"] as const,
  batches: ["batches"] as const,
  batch: (id: string) => ["batch", id] as const,
  orders: ["orders"] as const,
  order: (id: string) => ["order", id] as const,
  orderTrace: (id: string) => ["order-trace", id] as const,
  inventory: ["inventory"] as const,
  materials: ["materials"] as const,
  samples: (batchId: string) => ["samples", batchId] as const,
  hu: (batchId: string) => ["hu", batchId] as const,
  equipment: ["equipment", "health"] as const,
  oee: ["oee"] as const,
  alarms: (filter: string) => ["alarms", filter] as const,
  kpi: (window: string, compare: boolean) => ["kpi", window, compare] as const,
  model: ["ui", "model"] as const,
  logistics: ["logistics"] as const,
};

type Extra<T> = Omit<UseQueryOptions<T, Refusal, T>, "queryKey" | "queryFn">;

function usePolled<T>(
  queryKey: readonly unknown[],
  path: string,
  refetchInterval: number,
  extra?: Extra<T>,
) {
  return useQuery<T, Refusal, T>({
    queryKey,
    queryFn: ({ signal }) => get<T>(path, signal),
    refetchInterval,
    refetchIntervalInBackground: false,
    staleTime: 0,
    // Laat de laatst gerenderde inhoud staan als een refresh faalt. Per-blok
    // foutafhandeling: een falende call mag het scherm niet leegmaken.
    retry: 1,
    ...extra,
  });
}

export function useKpiSummary(window: string, compare = true, extra?: Extra<KpiSummary>) {
  return usePolled<KpiSummary>(
    keys.kpi(window, compare),
    `/kpi/summary?window=${encodeURIComponent(window)}&compare=${compare}`,
    POLL.aggregates,
    extra,
  );
}

export function useTags<T = Record<string, unknown>>(extra?: Extra<T>) {
  return usePolled<T>(keys.tags, "/tags", POLL.live, extra);
}

/** `frozen` bevriest de lijst zolang er een detailpaneel open staat. */
export function useBatches<T = unknown[]>(frozen = false, extra?: Extra<T>) {
  return usePolled<T>(keys.batches, "/batches", POLL.lists, { enabled: !frozen, ...extra });
}

export function useEquipmentHealth<T = unknown[]>(extra?: Extra<T>) {
  return usePolled<T>(keys.equipment, "/equipment/health", POLL.lists, extra);
}

/**
 * Masterdata: drempels, specbanden, materialen. Verandert alleen bij een
 * deploy, dus geen poll.
 */
export function useUiModel(extra?: Extra<UiModel>) {
  return useQuery<UiModel, Refusal, UiModel>({
    queryKey: keys.model,
    queryFn: ({ signal }) => getUi<UiModel>("/model", signal),
    staleTime: Infinity,
    retry: 1,
    ...extra,
  });
}

/* ------------------------------------------------------------ operatorkant */

export function useLineLive(extra?: Extra<LineLive>) {
  return usePolled<LineLive>(keys.line, "/line/live", POLL.live, extra);
}

export function useAlarms(filter = "", extra?: Extra<Alarm[]>) {
  const qs = filter ? `?priority=${encodeURIComponent(filter)}` : "";
  return usePolled<Alarm[]>(keys.alarms(filter || "alle"), `/alarms${qs}`, POLL.lists, extra);
}

/** Verbose tags: met ts, quality en leeftijd, zodat stale herkenbaar is. */
export function useTagsVerbose(extra?: Extra<Record<string, TagReading>>) {
  return usePolled<Record<string, TagReading>>(
    ["tags", "verbose"],
    "/tags?verbose=1",
    POLL.live,
    extra,
  );
}

/* ---------------------------------------------------------- overige schermen */

export function useOrders<T = unknown[]>(extra?: Extra<T>) {
  return usePolled<T>(keys.orders, "/orders", POLL.lists, extra);
}

/** Tracering van een order: batches, pallets, vrachtwagens, klant. */
export function useOrderTrace<T = Record<string, unknown>>(id: string | null, extra?: Extra<T>) {
  return usePolled<T>(keys.orderTrace(id ?? "none"), `/orders/${id}/trace`, POLL.lists, {
    enabled: Boolean(id),
    ...extra,
  });
}

/** Detail bevriest de lijst-loop: zie useBatches(frozen). */
export function useBatch<T = Record<string, unknown>>(id: string | null, extra?: Extra<T>) {
  return usePolled<T>(keys.batch(id ?? "none"), `/batches/${id}`, POLL.lists, {
    enabled: Boolean(id),
    ...extra,
  });
}

export function useOee<T = unknown[]>(extra?: Extra<T>) {
  return usePolled<T>(keys.oee, "/oee", POLL.aggregates, extra);
}

export function useInventory<T = Record<string, unknown>>(extra?: Extra<T>) {
  return usePolled<T>(keys.inventory, "/inventory", POLL.aggregates, extra);
}

/** Materialenmaster. Levert de uom en de bestelgrens die /inventory niet per rij geeft. */
export function useMaterials<T = unknown[]>(extra?: Extra<T>) {
  return usePolled<T>(keys.materials, "/materials", POLL.aggregates, extra);
}

/* ------------------------------------------------- werkvloer: monsters en HU */

/**
 * Beide pollen alleen met een batch in beeld. Zonder `enabled` zou de
 * werkvloerroute twee lege lijsten blijven ophalen zolang er niet gescand is.
 */
export function useSamples<T = unknown[]>(batchId: string | null, extra?: Extra<T>) {
  return usePolled<T>(
    keys.samples(batchId ?? "geen"),
    `/samples?batch_id=${encodeURIComponent(batchId ?? "")}`,
    POLL.lists,
    { enabled: Boolean(batchId), ...extra },
  );
}

export function useHandlingUnits<T = unknown[]>(batchId: string | null, extra?: Extra<T>) {
  return usePolled<T>(
    keys.hu(batchId ?? "geen"),
    `/hu?batch_id=${encodeURIComponent(batchId ?? "")}`,
    POLL.lists,
    { enabled: Boolean(batchId), ...extra },
  );
}

/**
 * Alle HU's over alle batches: zonder batch_id geeft de engine de hele lijst.
 * Eigen key ("*"), zodat hij nooit botst met een batch die toevallig zo heet
 * of met de uitgeschakelde "geen"-query hierboven.
 */
export function useAllHandlingUnits<T = unknown[]>(extra?: Extra<T>) {
  return usePolled<T>(keys.hu("*"), "/hu", POLL.lists, extra);
}

/** Demo-logistiek (zendingen, heftrucks, tankwagen): `source: "demo-sim"`, geen transportsysteem. */
export function useLogistics<T = Record<string, unknown>>(extra?: Extra<T>) {
  return usePolled<T>(keys.logistics, "/logistics", POLL.lists, extra);
}
