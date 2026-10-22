import { COLDSTORE, LINE2_EQUIPMENT, LINE_EQUIPMENT, PALLETISERS, ROAD_X, ROAD_Z, dockPosition, type Vec3 } from "./layout";
import { huLine, type LogisticsLine } from "./derive";
import type { TwinModel } from "./useTwinData";

/** Wat het orderpad van een order nodig heeft: de batches die erbij horen. */
export type OrderLike = { order_id: string; progress?: { batch_ids?: string[] | null } | null };

export type OrderStop = { key: string; label: string; value: string; at: Vec3 };

const FILLERS: Record<LogisticsLine, Vec3> = {
  L1: LINE_EQUIPMENT.find((e) => e.step === "filling")?.pos ?? ([9, 0, 0] as Vec3),
  L2: LINE2_EQUIPMENT.find((e) => e.step === "filling")?.pos ?? ([9, 0, -17] as Vec3),
};
const COLD_CENTRE: Vec3 = [(COLDSTORE.min[0] + COLDSTORE.max[0]) / 2, 0, (COLDSTORE.min[1] + COLDSTORE.max[1]) / 2];
/** Elke lijn heeft een eigen deur in de westwand van de koelcel. */
const COLD_DOORS: Record<LogisticsLine, Vec3> = {
  L1: [COLDSTORE.min[0], 0, (COLDSTORE.door[0] + COLDSTORE.door[1]) / 2],
  L2: [COLDSTORE.min[0], 0, (COLDSTORE.door2[0] + COLDSTORE.door2[1]) / 2],
};
const EXIT: Vec3 = [ROAD_X[1] - 2, 0, ROAD_Z];

const STATE_WORD: Record<string, string> = {
  DOSING: "doseren",
  COOKING: "koken",
  COOLING: "koelen",
  FILLING: "vullen",
  COMPLETE: "gereed",
};

/** Op welke lijn de order loopt: uit de HU's, anders de lopende batch van hal 2. */
export function orderLine(order: OrderLike, model: TwinModel): LogisticsLine {
  const ids = orderBatchIds(order, model);
  const lg = model.logistics;
  const hu = [...(lg.palletisers?.L1 ?? lg.palletiser), ...(lg.palletisers?.L2 ?? []), ...lg.rack.map((r) => r.hu)].find(
    (h) => ids.has(h.batch_id) && h.line,
  );
  if (hu) return huLine(hu);
  const b2 = model.line2?.batch?.batch_id;
  return b2 && ids.has(b2) ? "L2" : "L1";
}

/** De batches van een order: wat /orders meegeeft, anders de lopende batch. */
export function orderBatchIds(order: OrderLike, model: TwinModel): Set<string> {
  const ids = new Set(order.progress?.batch_ids ?? []);
  if (ids.size === 0 && model.batch) ids.add(model.batch.batch_id);
  return ids;
}

/**
 * De haltes van een order door de fabriek, met per halte wat er van deze
 * order staat. De scène tekent ze als genummerd pad, de overlay als lijst.
 */
export function orderStops(order: OrderLike, model: TwinModel): OrderStop[] {
  const ids = orderBatchIds(order, model);
  const lg = model.logistics;
  const docks = lg.docks.filter((d) => ids.has(d.batch_id));
  const stored = lg.rack.filter((r) => ids.has(r.hu.batch_id)).length;
  const line = orderLine(order, model);
  const wrapped = [...(lg.palletisers?.L1 ?? lg.palletiser), ...(lg.palletisers?.L2 ?? [])].filter((h) => ids.has(h.batch_id)).length;
  const shipments = (model.sim?.shipments ?? []).filter((s) => s.batch_id && ids.has(s.batch_id));
  const enRoute = shipments.filter((s) => s.stage === "en_route").length;
  const delivered = shipments.filter((s) => s.stage === "delivered").length;
  const b2 = model.line2?.batch;
  const running =
    model.batch && ids.has(model.batch.batch_id) ? model.batch.state : b2 && ids.has(b2.batch_id) ? b2.state : null;
  const loaded = docks.reduce((n, d) => n + d.loaded.length, 0);
  const total = docks.reduce((n, d) => n + d.loaded.length + d.waiting.length, 0);
  const dockAt = docks.length ? dockPosition(docks[0].dock) : dockPosition(0);
  return [
    { key: "batch", label: "Batch", value: running ? STATE_WORD[running] ?? running.toLowerCase() : `${ids.size} batch${ids.size === 1 ? "" : "es"}`, at: FILLERS[line] },
    { key: "palletiser", label: `Palletiser ${line}`, value: `${wrapped} HU`, at: PALLETISERS[line] },
    { key: "coldstore", label: "Koelcel", value: `${stored} HU`, at: COLD_CENTRE },
    { key: "dock", label: docks.length ? `Dock ${docks.map((d) => d.dock + 1).join(", ")}` : "Dock", value: total ? `${loaded}/${total} HU` : "nog niet", at: dockAt },
    { key: "customer", label: "Klant", value: `${enRoute} onderweg · ${delivered} geleverd`, at: EXIT },
  ];
}

/** De lijn van het pad: langs de haltes, door de deur van de koelcel en via de weg. */
export function orderPathPoints(stops: OrderStop[]): Vec3[] {
  const dock = stops.find((s) => s.key === "dock")?.at ?? dockPosition(0);
  const pal = stops.find((s) => s.key === "palletiser")?.at ?? PALLETISERS.L1;
  const line: LogisticsLine = pal[2] < COLDSTORE.door[0] - 6 ? "L2" : "L1";
  return [stops.find((s) => s.key === "batch")?.at ?? FILLERS[line], pal, COLD_DOORS[line], COLD_CENTRE, [dock[0], 0, COLDSTORE.max[1] - 1], dock, [dock[0], 0, ROAD_Z], EXIT];
}
