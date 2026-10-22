import { COLDSTORE, LINE_EQUIPMENT, PALLETISER, ROAD_X, ROAD_Z, dockPosition, type Vec3 } from "./layout";
import type { TwinModel } from "./useTwinData";

/** Wat het orderpad van een order nodig heeft: de batches die erbij horen. */
export type OrderLike = { order_id: string; progress?: { batch_ids?: string[] | null } | null };

export type OrderStop = { key: string; label: string; value: string; at: Vec3 };

const FILLER = LINE_EQUIPMENT.find((e) => e.step === "filling")?.pos ?? ([9, 0, 0] as Vec3);
const COLD_CENTRE: Vec3 = [(COLDSTORE.min[0] + COLDSTORE.max[0]) / 2, 0, (COLDSTORE.min[1] + COLDSTORE.max[1]) / 2];
const COLD_DOOR: Vec3 = [COLDSTORE.min[0], 0, (COLDSTORE.door[0] + COLDSTORE.door[1]) / 2];
const EXIT: Vec3 = [ROAD_X[1] - 2, 0, ROAD_Z];

const STATE_WORD: Record<string, string> = {
  DOSING: "doseren",
  COOKING: "koken",
  COOLING: "koelen",
  FILLING: "vullen",
  COMPLETE: "gereed",
};

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
  const wrapped = lg.palletiser.filter((h) => ids.has(h.batch_id)).length;
  const shipments = (model.sim?.shipments ?? []).filter((s) => s.batch_id && ids.has(s.batch_id));
  const enRoute = shipments.filter((s) => s.stage === "en_route").length;
  const delivered = shipments.filter((s) => s.stage === "delivered").length;
  const running = model.batch && ids.has(model.batch.batch_id) ? model.batch.state : null;
  const loaded = docks.reduce((n, d) => n + d.loaded.length, 0);
  const total = docks.reduce((n, d) => n + d.loaded.length + d.waiting.length, 0);
  const dockAt = docks.length ? dockPosition(docks[0].dock) : dockPosition(0);
  return [
    { key: "batch", label: "Batch", value: running ? STATE_WORD[running] ?? running.toLowerCase() : `${ids.size} batch${ids.size === 1 ? "" : "es"}`, at: FILLER },
    { key: "palletiser", label: "Palletiser", value: `${wrapped} HU`, at: PALLETISER },
    { key: "coldstore", label: "Koelcel", value: `${stored} HU`, at: COLD_CENTRE },
    { key: "dock", label: docks.length ? `Dock ${docks.map((d) => d.dock + 1).join(", ")}` : "Dock", value: total ? `${loaded}/${total} HU` : "nog niet", at: dockAt },
    { key: "customer", label: "Klant", value: `${enRoute} onderweg · ${delivered} geleverd`, at: EXIT },
  ];
}

/** De lijn van het pad: langs de haltes, door de deur van de koelcel en via de weg. */
export function orderPathPoints(stops: OrderStop[]): Vec3[] {
  const dock = stops.find((s) => s.key === "dock")?.at ?? dockPosition(0);
  return [FILLER, PALLETISER, COLD_DOOR, COLD_CENTRE, [dock[0], 0, COLDSTORE.max[1] - 1], dock, [dock[0], 0, ROAD_Z], EXIT];
}
