/**
 * Pure afleidingen voor de 3D site-twin: van de bestaande engine-payloads
 * naar wat de scène tekent. Geen React, geen fetch, alleen relatieve imports,
 * zodat e2e/twin-derive.spec.ts dit zonder browser kan toetsen.
 *
 * Drie regels die ook in de gamelook blijven gelden:
 *   1. Een status draagt altijd een vorm en een woord, nooit alleen een kleur.
 *   2. Ontbrekende data is "-" en "geen data", nooit een verzonnen "stil".
 *   3. Verouderde data wordt nooit getoond alsof hij live is.
 */

import { MAX_DOCKS, RACK_SLOTS } from "./layout";

export type VisualState = "run" | "idle" | "warn" | "alarm" | "stale" | "unknown";

export const SHAPE: Record<VisualState, string> = {
  alarm: "◆",
  warn: "▲",
  stale: "▨",
  run: "●",
  idle: "▬",
  unknown: "-",
};

/** Op de statustaal van de rest van de UI (StatusPill). */
export const PILL: Record<VisualState, "run" | "idle" | "warn" | "alarm" | "stale" | "unset"> = {
  run: "run",
  idle: "idle",
  warn: "warn",
  alarm: "alarm",
  stale: "stale",
  unknown: "unset",
};

export type Visual = { state: VisualState; shape: string; label: string; reasons: string[] };

export type HealthLike = {
  equipment_id: string;
  state: string;
  dirty?: boolean;
  running_hours?: number | null;
  batches_since_cip?: number | null;
  last_cip_at?: string | null;
  open_cbm_alerts?: Array<{ message: string; alert_type?: string }>;
};

export type StepLike = {
  id: string;
  name?: string;
  equipment?: string;
  primary?: { label: string; unit: string; value: number | null; age_s?: number | null } | null;
  tone?: "neutral" | "warn" | "alarm";
  stale?: boolean;
};

export type AlarmLike = {
  alarm_id: string;
  message: string;
  equipment_id: string | null;
  priority: "high" | "medium" | "low";
  state: "open" | "acknowledged" | "shelved";
};

const HEALTH_WORD: Record<string, string> = {
  Running: "draait",
  Idle: "stil",
  Allocated: "gereserveerd",
  Dirty: "vervuild",
  Down: "storing",
  Error: "fout",
};

function visual(state: VisualState, label: string, reasons: string[]): Visual {
  return { state, shape: SHAPE[state], label, reasons };
}

/**
 * Eén procesdeel. Volgorde: een harde storing of een open hoog alarm wint
 * altijd, ook van stale (een defect mag nooit wegvallen achter "verouderd").
 * Daarna stale, daarna aandacht, daarna de bedrijfstoestand.
 */
export function equipmentVisual(input: {
  health?: HealthLike | null;
  step?: StepLike | null;
  alarms?: AlarmLike[];
  engineReachable?: boolean;
}): Visual {
  const { health, step } = input;
  const open = (input.alarms ?? []).filter((a) => a.state === "open");
  const high = open.filter((a) => a.priority === "high");
  const other = open.filter((a) => a.priority !== "high");
  const reasons: string[] = [];

  if (health && (health.state === "Down" || health.state === "Error")) {
    reasons.push(`procesdeel in ${HEALTH_WORD[health.state]}`);
    for (const a of high) reasons.push(a.message);
    return visual("alarm", HEALTH_WORD[health.state], reasons);
  }
  if (high.length > 0) {
    return visual("alarm", "alarm", high.map((a) => a.message));
  }
  if (input.engineReachable === false) {
    return visual("stale", "verouderd", ["engine niet bereikbaar"]);
  }
  if (step?.tone === "alarm") {
    reasons.push(`${step.primary?.label ?? "meetwaarde"} buiten de grens`);
    return visual("alarm", "buiten grens", reasons);
  }
  if (step?.stale) {
    return visual("stale", "verouderd", ["meetwaarde is verouderd"]);
  }
  if (!health) {
    return visual("unknown", "geen data", []);
  }
  if (health.dirty || health.state === "Dirty") reasons.push("CIP nodig");
  for (const c of health.open_cbm_alerts ?? []) reasons.push(c.message);
  if (step?.tone === "warn") reasons.push(`${step.primary?.label ?? "meetwaarde"} nadert de grens`);
  for (const a of other) reasons.push(a.message);
  if (reasons.length > 0) {
    const label = health.dirty || health.state === "Dirty" ? "vervuild" : "aandacht";
    return visual("warn", label, reasons);
  }
  if (health.state === "Running") return visual("run", "draait", []);
  return visual("idle", HEALTH_WORD[health.state] ?? "stil", []);
}

export type MaterialLike = {
  material_id: string;
  name: string;
  uom: string;
  stock_qty: number | null;
  reorder_level: number | null;
  below_reorder?: boolean;
  stock_pct?: number | null;
  is_finished_good?: boolean;
};

/** Vulgraad 0..1 voor de silo. Vier keer de bestelgrens is "vol"; dat is een
 *  beeldkeuze, geen capaciteit, en staat daarom niet als getal in de UI. */
export function siloFill(m: MaterialLike): number | null {
  if (m.stock_qty == null || m.reorder_level == null || m.reorder_level <= 0) return null;
  return Math.max(0, Math.min(1, m.stock_qty / (m.reorder_level * 4)));
}

export function siloVisual(m: MaterialLike | null | undefined, engineReachable = true): Visual {
  if (!m || m.stock_qty == null) return visual("unknown", "geen data", []);
  if (!engineReachable) return visual("stale", "verouderd", ["engine niet bereikbaar"]);
  if (m.below_reorder) return visual("warn", "onder bestelgrens", ["bijbestellen"]);
  return visual("idle", "op voorraad", []);
}

export type Hu = {
  hu_id: string;
  batch_id: string;
  packs_count: number;
  location: string | null;
  status: string;
  operator_id?: string | null;
  ts: string;
  /** Stellingvak, gezet door de magazijnstroom van de engine. */
  slot?: number | null;
};

export type DockSlot = {
  dock: number;
  batch_id: string;
  waiting: Hu[];
  loaded: Hu[];
  /** 0..1, hoeveel van de batch al in de truck zit. */
  progress: number;
  /** aangemeld: truck staat klaar, de koelceltijd loopt bijna af; laden: heftruck laadt. */
  state: "aangemeld" | "laden";
};

export type Departure = { batch_id: string; hus: number; packs: number; ts: string; ageMs: number };

/** De laatste heftruckrit: een pallet naar een vak (putaway) of van een vak naar een dock (laden). */
export type Move = { hu: Hu; slot: number; dock?: number };

export type Logistics = {
  palletiser: Hu[];
  rack: Array<{ slot: number; hu: Hu }>;
  rackOverflow: number;
  docks: DockSlot[];
  docksOverflow: number;
  departures: Departure[];
  lastPutaway: Move | null;
  lastLoad: Move | null;
  totals: { wrapped: number; stored: number; shipped: number; packsStored: number };
};

export const DEPART_WINDOW_MS = 10 * 60 * 1000;
/** Koelceltijd van de engine (warehouse.STORE_DWELL_S): pas daarna wordt een batch geladen. */
export const STORE_DWELL_MS = 12 * 60 * 1000;
/** Zo lang vóór het einde van de koelceltijd meldt de truck zich aan het dock. */
export const ARRIVE_LEAD_MS = 90 * 1000;

function t(iso: string): number {
  const v = Date.parse(iso);
  return Number.isFinite(v) ? v : 0;
}

function validSlot(s: unknown): s is number {
  return typeof s === "number" && Number.isInteger(s) && s >= 0 && s < RACK_SLOTS;
}

/** Een vaste voorkeursdock per batch, zodat een truck niet verspringt als een andere vertrekt. */
function preferredDock(batchId: string): number {
  let h = 0;
  for (let i = 0; i < batchId.length; i++) h = (h * 31 + batchId.charCodeAt(i)) >>> 0;
  return h % MAX_DOCKS;
}

/**
 * Trucks zitten niet in het datamodel. Ze worden afgeleid uit de HU-status:
 *   - een batch krijgt pas een truck aan een dock als het laden begonnen is,
 *     of als de koelceltijd van zijn laatste pallet bijna om is (de truck
 *     meldt zich aan). Een lege truck die twaalf minuten op 0/11 staat te
 *     wachten is geen logistiek, dat is een misverstand op het scherm.
 *     Eerst de batches die al laden, dan de oudste, maximaal MAX_DOCKS.
 *     Elke batch heeft een vaste voorkeursdock;
 *   - een batch waarvan alles geshipt is en de laatste ship binnen het
 *     vertrekvenster valt, is een vertrekkende truck.
 * Dat is een visualisatie van de HU-stroom en in de UI ook zo benoemd.
 *
 * Stellingvakken: een HU met een geldig `slot` staat in dat vak, zodat een
 * pallet blijft staan als een andere vertrekt. HU's zonder vak (handmatig
 * geboekt) vullen de laagste vrije vakken, oudste eerst.
 */
export function deriveLogistics(hus: Hu[] | null | undefined, now: number): Logistics {
  const list = Array.isArray(hus) ? hus : [];
  const wrapped = list.filter((h) => h.status === "wrapped");
  const stored = list
    .filter((h) => h.status === "awaiting_shipment")
    .sort((a, b) => t(a.ts) - t(b.ts) || a.hu_id.localeCompare(b.hu_id));
  const shipped = list.filter((h) => h.status === "shipped");

  const taken = new Set<number>();
  const rack: Array<{ slot: number; hu: Hu }> = [];
  const unplaced: Hu[] = [];
  for (const hu of stored) {
    if (validSlot(hu.slot) && !taken.has(hu.slot)) {
      taken.add(hu.slot);
      rack.push({ slot: hu.slot, hu });
    } else unplaced.push(hu);
  }
  let next = 0;
  let overflow = 0;
  for (const hu of unplaced) {
    while (next < RACK_SLOTS && taken.has(next)) next++;
    if (next >= RACK_SLOTS) {
      overflow++;
      continue;
    }
    taken.add(next);
    rack.push({ slot: next, hu });
  }
  const slotOf = new Map(rack.map((r) => [r.hu.hu_id, r.slot]));

  const byBatch = new Map<string, { waiting: Hu[]; loaded: Hu[] }>();
  for (const h of [...stored, ...shipped]) {
    const e = byBatch.get(h.batch_id) ?? { waiting: [], loaded: [] };
    (h.status === "shipped" ? e.loaded : e.waiting).push(h);
    byBatch.set(h.batch_id, e);
  }

  const atDock = [...byBatch.entries()]
    .filter(([, e]) => {
      if (e.waiting.length === 0) return false;
      if (e.loaded.length > 0) return true;
      const lastIn = Math.max(...e.waiting.map((h) => t(h.ts)));
      return now - lastIn >= STORE_DWELL_MS - ARRIVE_LEAD_MS;
    })
    .sort(
      ([ia, a], [ib, b]) =>
        Number(b.loaded.length > 0) - Number(a.loaded.length > 0) ||
        t(a.waiting[0].ts) - t(b.waiting[0].ts) ||
        ia.localeCompare(ib),
    );

  const usedDocks = new Set<number>();
  const docks: DockSlot[] = atDock.slice(0, MAX_DOCKS).map(([batch_id, e]) => {
    let dock = preferredDock(batch_id);
    while (usedDocks.has(dock)) dock = (dock + 1) % MAX_DOCKS;
    usedDocks.add(dock);
    return {
      dock,
      batch_id,
      waiting: e.waiting,
      loaded: e.loaded,
      progress: e.loaded.length / (e.loaded.length + e.waiting.length),
      state: e.loaded.length > 0 ? "laden" : "aangemeld",
    };
  });

  const departures: Departure[] = [];
  for (const [batch_id, e] of byBatch) {
    if (e.waiting.length > 0 || e.loaded.length === 0) continue;
    const last = Math.max(...e.loaded.map((h) => t(h.ts)));
    const ageMs = now - last;
    if (ageMs >= 0 && ageMs <= DEPART_WINDOW_MS) {
      departures.push({
        batch_id,
        hus: e.loaded.length,
        packs: e.loaded.reduce((s, h) => s + (Number(h.packs_count) || 0), 0),
        ts: new Date(last).toISOString(),
        ageMs,
      });
    }
  }
  departures.sort((a, b) => a.ageMs - b.ageMs);

  const newest = (hs: Hu[]) => hs.reduce<Hu | null>((m, h) => (!m || t(h.ts) > t(m.ts) ? h : m), null);
  const putHu = newest(rack.map((r) => r.hu));
  const lastPutaway = putHu ? { hu: putHu, slot: slotOf.get(putHu.hu_id)! } : null;
  const loadHu = newest(shipped.filter((h) => validSlot(h.slot)));
  const loadDock = loadHu ? docks.find((d) => d.batch_id === loadHu.batch_id) : undefined;
  const lastLoad = loadHu && loadDock ? { hu: loadHu, slot: loadHu.slot as number, dock: loadDock.dock } : null;

  return {
    palletiser: wrapped,
    rack,
    rackOverflow: overflow,
    docks,
    docksOverflow: Math.max(0, atDock.length - MAX_DOCKS),
    departures,
    lastPutaway,
    lastLoad,
    totals: {
      wrapped: wrapped.length,
      stored: stored.length,
      shipped: shipped.length,
      packsStored: stored.reduce((s, h) => s + (Number(h.packs_count) || 0), 0),
    },
  };
}

/** Hoe ver een vertrekkende truck over de weg is, 0..1. */
export function departureProgress(ageMs: number): number {
  return Math.max(0, Math.min(1, ageMs / DEPART_WINDOW_MS));
}

/**
 * Status van de logistieke objecten. `available` is false zolang er geen
 * HU-antwoord is: dan is de koelcel "geen data", niet "leeg".
 */
export function logisticsVisuals(log: Logistics, available: boolean, engineReachable: boolean | null) {
  const none = visual("unknown", "geen data", []);
  if (!available) return { palletiser: none, coldstore: none, dock: none, truck: none };
  const stale = engineReachable === false;
  const st = (v: Visual) => (stale ? visual("stale", "verouderd", ["engine niet bereikbaar"]) : v);
  return {
    palletiser: st(
      log.totals.wrapped > 0
        ? visual("run", `${log.totals.wrapped} HU gewikkeld`, [])
        : visual("idle", "geen pallets", []),
    ),
    coldstore: st(
      log.rackOverflow > 0
        ? visual("warn", "stelling vol", [`${log.rackOverflow} HU buiten de getekende vakken`])
        : log.totals.stored > 0
          ? visual("idle", `${log.rack.length}/${RACK_SLOTS} vakken bezet`, [])
          : visual("idle", "leeg", []),
    ),
    dock: st(visual("run", "laden", [])),
    truck: st(visual("idle", "vertrokken", [])),
  };
}
