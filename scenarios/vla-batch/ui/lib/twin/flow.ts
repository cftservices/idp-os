/**
 * Pure afleidingen voor de overlay van de site-twin: tijdlijnen, voortgang,
 * labelvoorrang, de sitechip en zoeken. Geen React, zodat ze zonder browser
 * te toetsen zijn (e2e/twin-flow.spec.ts).
 */

import { MAX_DOCKS, RACK_SLOTS } from "./layout";
import type { Hu, Visual, VisualState } from "./derive";

/* ---------- demo-logistiek uit de engine (GET /logistics) ---------- */

export type SimShipment = {
  shipment_id: string;
  truck_id: string;
  batch_id: string;
  order_id?: string | null;
  customer: string;
  destination: string;
  drive_min: number;
  stage: "order" | "loading" | "en_route" | "delivered";
  pallets: number;
  pallets_loaded: number;
  packs: number;
  ordered_at: string;
  loaded_at: string | null;
  eta: string | null;
};

export type SimForklift = {
  id: string;
  role: "putaway" | "loader";
  state: "busy" | "idle" | "charging";
  battery_pct: number;
  trips_today: number;
  last_task: string | null;
  last_move: string | null;
};

export type SimInbound = {
  id: string;
  kind: string;
  /** Voertuigsoort: tankwagen (melk), bulkwagen (poeders), vrachtwagen met pallets. */
  vehicle?: "tanker" | "bulk" | "pallet";
  material_id?: string;
  stage: "en_route" | "unloading" | "gone";
  litres: number;
  load_qty?: number;
  uom?: string;
  unloaded_pct: number;
  received_kg?: number;
  next_at: string;
};

export type SimLogistics = {
  source: string | null;
  ts?: string;
  shipments: SimShipment[];
  forklifts: SimForklift[];
  inbound: SimInbound | null;
  /** Alle leveranciers, één (de laatste) per grondstof. */
  inbounds: SimInbound[];
};

export function asSimLogistics(raw: unknown): SimLogistics | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Partial<SimLogistics>;
  if (!r.source) return null;
  const inbound = r.inbound ?? null;
  return {
    source: r.source,
    ts: r.ts,
    shipments: Array.isArray(r.shipments) ? r.shipments : [],
    forklifts: Array.isArray(r.forklifts) ? r.forklifts : [],
    inbound,
    inbounds: Array.isArray(r.inbounds) ? r.inbounds : inbound ? [{ material_id: "milk", vehicle: "tanker", ...inbound }] : [],
  };
}

/* ---------- tijdlijnen ---------- */

export const BATCH_STEPS = ["Doseren", "Koken", "Koelen", "Vullen", "Gereed", "Gepalletiseerd", "Verzonden"] as const;
export const SHIP_STEPS = ["Order", "Gepickt", "Laden", "Onderweg", "Afgeleverd"] as const;

const BATCH_STATE_STEP: Record<string, number> = { DOSING: 0, COOKING: 1, COOLING: 2, FILLING: 3, COMPLETE: 4 };

/**
 * Waar een batch staat in de keten van doseren tot verzonden. De lijnstatus
 * zegt alleen iets tot COMPLETE; daarna bepalen de HU's van die batch het.
 * -1 als er niets bekend is.
 */
export function batchStepIndex(state: string | null | undefined, hus: Hu[] | null | undefined, batchId?: string | null): number {
  const own = batchId && Array.isArray(hus) ? hus.filter((h) => h.batch_id === batchId) : [];
  if (own.length > 0) {
    if (own.every((h) => h.status === "shipped")) return 6;
    if (own.some((h) => h.status === "wrapped" || h.status === "awaiting_shipment" || h.status === "shipped")) return 5;
  }
  if (!state) return -1;
  return BATCH_STATE_STEP[state] ?? -1;
}

const SHIP_STAGE_STEP: Record<SimShipment["stage"], number> = { order: 1, loading: 2, en_route: 3, delivered: 4 };

/** "order" betekent: pallets staan klaar in de koelcel, dus gepickt is al gebeurd. */
export function shipStepIndex(stage: SimShipment["stage"] | null | undefined): number {
  return stage ? SHIP_STAGE_STEP[stage] ?? -1 : -1;
}

function clamp01(v: number) {
  return Math.min(1, Math.max(0, v));
}

/** 0..1: laden telt pallets, onderweg telt de rijtijd. */
export function shipmentProgress(s: SimShipment, now: number): number {
  if (s.stage === "delivered") return 1;
  if (s.stage === "order") return 0;
  if (s.stage === "loading") return s.pallets > 0 ? clamp01(s.pallets_loaded / s.pallets) : 0;
  const from = s.loaded_at ? Date.parse(s.loaded_at) : NaN;
  const to = s.eta ? Date.parse(s.eta) : NaN;
  if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) return 0;
  return clamp01((now - from) / (to - from));
}

export function shipmentVisual(s: SimShipment): Visual {
  const label = { order: "order", loading: "laden", en_route: "onderweg", delivered: "afgeleverd" }[s.stage];
  const state: VisualState = s.stage === "loading" || s.stage === "en_route" ? "run" : "idle";
  return { state, shape: state === "run" ? "●" : "○", label, reasons: [] };
}

export function forkliftVisual(f: SimForklift): Visual {
  if (f.state === "charging") return { state: "warn", shape: "▲", label: `laden ${Math.round(f.battery_pct)}%`, reasons: ["Accu onder de laadgrens, de andere heftruck neemt het werk over."] };
  if (f.state === "busy") return { state: "run", shape: "●", label: `rijdt · ${Math.round(f.battery_pct)}%`, reasons: [] };
  return { state: "idle", shape: "○", label: `stil · ${Math.round(f.battery_pct)}%`, reasons: [] };
}

export function inboundVisual(i: SimInbound): Visual {
  if (i.stage === "unloading") return { state: "run", shape: "●", label: `lossen ${Math.round(i.unloaded_pct)}%`, reasons: [] };
  if (i.stage === "en_route") return { state: "run", shape: "●", label: "onderweg", reasons: [] };
  return { state: "idle", shape: "○", label: "vertrokken", reasons: [] };
}

/* ---------- zwevende labels: voorrang ---------- */

const RANK: Record<VisualState, number> = { alarm: 1, warn: 2, stale: 2, run: 3, idle: 9, unknown: 9 };

/**
 * Welke objecten een zwevend label krijgen. Volgorde: geselecteerd, alarm,
 * aandacht/verouderd, actief. Stil en onbekend nooit (behalve geselecteerd),
 * en nooit meer dan `max`: een diorama vol ballonnen is geen overzicht meer.
 * Met posities slaat het budget een label over dat in beeld op een eerder label
 * valt. Gemeten langs de assen van de startcamera: een ballon is breed (~14 m in
 * camera-x) en laag (~3 m in schermhoogte). Sinds Hal 2 vielen de ballonnen van
 * L2-tanks op 5 m afstand anders over elkaar.
 */
export function labelBudget(
  items: Array<{ id: string; visual: Visual; pos?: readonly number[] }>,
  selected: string | null,
  hovered: string | null,
  max = 6,
  gap: { x: number; y: number } = LABEL_GAP,
): Set<string> {
  const out = new Set<string>();
  if (selected) out.add(selected);
  if (hovered) out.add(hovered);
  const ranked = items
    // Docks en voorbereidingsstations dragen een eigen chip; een tweede ballon erboven overlapt de buren.
    .filter((i) => !out.has(i.id) && RANK[i.visual.state] < 9 && !i.id.startsWith("dock:") && !i.id.startsWith("prep:"))
    .sort((a, b) => RANK[a.visual.state] - RANK[b.visual.state]);
  const byId = new Map(items.map((i) => [i.id, i.pos]));
  // Docks en voorbereiding dragen een vaste chip: daar mag ook geen ballon overheen.
  const chips = items.filter((i) => i.id.startsWith("dock:") || i.id.startsWith("prep:")).map((i) => i.pos);
  const placed = [...[...out].map((id) => byId.get(id)), ...chips].filter((p): p is readonly number[] => !!p);
  const tooClose = (p?: readonly number[]) => !!p && placed.some((q) => labelsCollide(p, q, gap));
  for (const i of ranked) {
    if (out.size >= max) break;
    if (tooClose(i.pos)) continue;
    out.add(i.id);
    if (i.pos) placed.push(i.pos);
  }
  return out;
}

/** Schermassen van de startcamera op de vloer (CAMERA_HOME naar CAMERA_TARGET). */
const VIEW_RIGHT = [0.918, -0.396] as const;
const VIEW_DEPTH = [-0.396, -0.918] as const;
const VIEW_TILT = 0.61;
export const LABEL_GAP = { x: 14, y: 3 };

export function labelsCollide(p: readonly number[], q: readonly number[], gap = LABEL_GAP): boolean {
  const dx = p[0] - q[0];
  const dz = p[2] - q[2];
  const sx = Math.abs(dx * VIEW_RIGHT[0] + dz * VIEW_RIGHT[1]);
  const sy = Math.abs(dx * VIEW_DEPTH[0] + dz * VIEW_DEPTH[1]) * VIEW_TILT;
  return sx < gap.x && sy < gap.y;
}

/** Spelden alleen boven wat aandacht vraagt. */
export function needsPin(v: Visual): boolean {
  return v.state === "alarm" || v.state === "warn" || v.state === "stale";
}

/* ---------- sitechip ---------- */

export function siteChip(input: { stored: number | null; docksInUse: number; line?: string }): string {
  const cold = input.stored == null ? "-" : `${Math.round((100 * Math.min(input.stored, RACK_SLOTS)) / RACK_SLOTS)}%`;
  return `L1 · ${input.line ?? "Vla"} · ${cold} koelcel · ${input.docksInUse}/${MAX_DOCKS} docks`;
}

/* ---------- rijpaden (scène en camerafocus lezen dezelfde functie) ---------- */

export type DrivePose = { x: number; z: number; rot: number; visible: boolean };

const TANKER_APPROACH_S = 90;
const TANKER_LEAVE_S = 60;
const TANKER_GONE_S = 480;
/** Losplaats bij de silo's; de tankwagen staat met de cabine naar de weg. */
export const TANKER_BAY = { x: -19.5, z: 6.2 };
/** Losplaats per grondstof, naast elkaar langs het siloplein. */
export const INBOUND_BAYS: Record<string, { x: number; z: number }> = {
  milk: TANKER_BAY,
  sugar: { x: -23, z: 6.2 },
  starch: { x: -26.5, z: 6.2 },
  cocoa: { x: -16, z: 6.2 },
};

export function inboundBay(i: SimInbound | null | undefined): { x: number; z: number } {
  return INBOUND_BAYS[i?.material_id ?? "milk"] ?? { x: -30, z: 6.2 };
}

/** "Bulkwagen suiker SUI-123": de soort met hoofdletter en het kenteken. */
export function inboundName(i: SimInbound): string {
  const k = i.kind || "vrachtwagen";
  return `${k.charAt(0).toUpperCase()}${k.slice(1)} ${i.id}`;
}
const LANE_IN = 11.6;
const ROAD_W = -34;

function along(a: number, b: number, f: number) {
  return a + (b - a) * clamp01(f);
}

/** Pad: over de weg van west naar de afslag, dan de losplaats in. Terug in omgekeerde volgorde. */
export function tankerPose(i: SimInbound | null, now: number): DrivePose {
  const hidden = { x: ROAD_W, z: LANE_IN, rot: Math.PI / 2, visible: false };
  if (!i) return hidden;
  const bay = inboundBay(i);
  const next = Date.parse(i.next_at);
  if (!Number.isFinite(next)) return hidden;
  const left = (next - now) / 1000;
  if (i.stage === "unloading") return { x: bay.x, z: bay.z, rot: 0, visible: true };
  if (i.stage === "en_route") {
    if (left > TANKER_APPROACH_S) return hidden;
    const f = 1 - left / TANKER_APPROACH_S;
    const roadPart = (bay.x - ROAD_W) / (bay.x - ROAD_W + (LANE_IN - bay.z));
    if (f < roadPart) return { x: along(ROAD_W, bay.x, f / roadPart), z: LANE_IN, rot: Math.PI / 2, visible: true };
    return { x: bay.x, z: along(LANE_IN, bay.z, (f - roadPart) / (1 - roadPart)), rot: Math.PI, visible: true };
  }
  const since = TANKER_GONE_S - left;
  if (since > TANKER_LEAVE_S || since < 0) return hidden;
  const f = since / TANKER_LEAVE_S;
  if (f < 0.35) return { x: bay.x, z: along(bay.z, LANE_IN, f / 0.35), rot: 0, visible: true };
  return { x: along(bay.x, ROAD_W, (f - 0.35) / 0.65), z: LANE_IN, rot: -Math.PI / 2, visible: true };
}

const DEPART_FROM = 24;
const DEPART_TO = 44;
/** Deel van de rit dat binnen de kaart valt; daarna is de truck "onderweg" zonder model. */
export const DEPART_VISIBLE = 0.2;

export function departPose(s: SimShipment, now: number, lane: number, roadZ: number): DrivePose {
  const p = shipmentProgress(s, now);
  const visible = s.stage === "en_route" && p < DEPART_VISIBLE;
  return { x: along(DEPART_FROM, DEPART_TO, p / DEPART_VISIBLE) - lane * 7, z: roadZ + 1.4, rot: Math.PI / 2, visible };
}

/* ---------- zoeken ---------- */

export function searchItems<T extends { id: string; name: string }>(items: T[], q: string, max = 8): T[] {
  const s = q.trim().toLowerCase();
  if (!s) return [];
  const hit = (i: T) => i.name.toLowerCase().includes(s) || i.id.toLowerCase().includes(s);
  const starts = (i: T) => (i.name.toLowerCase().startsWith(s) ? 0 : 1);
  return items.filter(hit).sort((a, b) => starts(a) - starts(b) || a.name.localeCompare(b.name)).slice(0, max);
}
