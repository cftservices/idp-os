/**
 * Twee lijnen en een gedeelde voorbereiding in de site-twin: welke smaak een
 * batch of HU heeft, en wat een weeg- of premixstation nu doet. Pure functies,
 * zodat e2e/twin-derive.spec.ts ze zonder browser kan toetsen.
 */
import type { PrepSet, PrepState, PrepStation, ProductLine } from "../types";
import { SHAPE, type Visual } from "./derive";

export const FLAVOURS = ["chocolate", "vanilla", "banana", "caramel", "strawberry"] as const;
export type Flavour = (typeof FLAVOURS)[number];

export const FLAVOUR_WORD: Record<Flavour, string> = {
  chocolate: "chocolade",
  vanilla: "vanille",
  banana: "banaan",
  caramel: "karamel",
  strawberry: "aardbei",
};

/** Paletsleutel van de smaakkleur, zonder het "--twin-"-voorvoegsel. */
export function flavourKey(f: Flavour): `flavour-${Flavour}` {
  return `flavour-${f}`;
}

const FLAVOUR_ALIASES: Array<[RegExp, Flavour]> = [
  [/chocola|cocoa|cacao/i, "chocolate"],
  [/vanil/i, "vanilla"],
  [/banan|banaan/i, "banana"],
  [/caram|karam/i, "caramel"],
  [/strawb|aardbei/i, "strawberry"],
];

/** Smaak uit een vrije tekst (recept-id, productnaam, smaakmateriaal); null als hij niet te herkennen is. */
export function parseFlavour(text: string | null | undefined): Flavour | null {
  if (!text) return null;
  for (const [re, f] of FLAVOUR_ALIASES) if (re.test(text)) return f;
  return null;
}

/**
 * Smaak van een recept. Eerst het recept-id zelf (chocolate-vla-1L), dan de
 * receptdefinitie uit /lines. Onbekend valt terug op chocolade: dat is het
 * enige product van L1 en de historische kleur van het pak.
 */
export function flavourOfRecipe(recipeId: string | null | undefined, lines: ProductLine[] = []): Flavour {
  const direct = parseFlavour(recipeId);
  if (direct) return direct;
  for (const l of lines) {
    const r = l.recipes.find((x) => x.recipe_id === recipeId);
    if (r) return parseFlavour(r.flavour) ?? parseFlavour(r.product_name) ?? parseFlavour(r.fg_material_id) ?? "chocolate";
  }
  return "chocolate";
}

type BatchLike = { batch_id: string; recipe_id?: string | null; product_name?: string | null; line?: string | null };

/** batch_id -> smaak, voor HU's (die alleen een batch_id dragen). */
export function batchFlavours(batches: BatchLike[] | null | undefined, lines: ProductLine[] = []): Record<string, Flavour> {
  const out: Record<string, Flavour> = {};
  for (const b of batches ?? []) {
    if (!b?.batch_id) continue;
    out[b.batch_id] = b.recipe_id ? flavourOfRecipe(b.recipe_id, lines) : parseFlavour(b.product_name) ?? "chocolate";
  }
  return out;
}

export function flavourOfBatch(batchId: string | null | undefined, map: Record<string, Flavour>): Flavour {
  return (batchId && map[batchId]) || "chocolate";
}

/* ---------------- voorbereiding ---------------- */

export type PrepView = {
  /** "vrij" als er geen set op dit station staat. */
  state: PrepState | "vrij";
  set: PrepSet | null;
  flavour: Flavour | null;
};

export const PREP_STATE_WORD: Record<PrepState | "vrij", string> = {
  gepland: "gepland",
  afwegen: "afwegen",
  premix: "premix",
  gereed: "gereed",
  verbruikt: "verbruikt",
  vrij: "vrij",
};

/** Welke states van een set een station bezet houden. */
const HOLDS: Record<"weigh" | "premix", PrepState[]> = {
  weigh: ["afwegen"],
  premix: ["premix", "gereed"],
};

/** Paletsleutel voor de kleur van een stationsstatus. */
export function prepColorKey(state: PrepState | "vrij"): `prep-${PrepState}` | "status-idle" {
  return state === "vrij" ? "status-idle" : `prep-${state}`;
}

/**
 * Wat een station nu doet. De engine meldt de bezetting per station
 * (stations[].prep_id); ontbreekt die, dan de meest recente set die het
 * station in een bezettende state noemt.
 */
export function prepStationView(
  station: { id: string; kind: "weigh" | "premix" },
  stations: PrepStation[] | null | undefined,
  sets: PrepSet[] | null | undefined,
  lines: ProductLine[] = [],
): PrepView {
  const all = sets ?? [];
  const reported = (stations ?? []).find((s) => s.station_id === station.id);
  let set: PrepSet | null = null;
  if (reported?.prep_id) set = all.find((s) => s.prep_id === reported.prep_id) ?? null;
  if (!set && reported?.state !== "vrij") {
    const mine = all.filter(
      (s) => (station.kind === "weigh" ? s.weigh_station : s.premix_tank) === station.id && HOLDS[station.kind].includes(s.state),
    );
    mine.sort((a, b) => Date.parse(b.state_changed_at) - Date.parse(a.state_changed_at));
    set = mine[0] ?? null;
  }
  if (!set) return { state: "vrij", set: null, flavour: null };
  return { state: set.state, set, flavour: flavourOfRecipe(set.recipe_id, lines) };
}

/**
 * De status op de statustaal van de twin. Afwegen en premix draaien; een
 * gereedstaande premix wacht op de lijn (idle). Zonder antwoord van de engine
 * is het "geen data", nooit "vrij".
 */
export function prepVisual(view: PrepView, available: boolean): Visual {
  if (!available) return { state: "unknown", shape: SHAPE.unknown, label: "geen data", reasons: [] };
  const run = view.state === "afwegen" || view.state === "premix";
  const state = run ? "run" : "idle";
  const reasons = view.set ? [`${view.set.prep_id} · ${view.set.line} · ${view.set.recipe_id}`] : [];
  return { state, shape: SHAPE[state], label: PREP_STATE_WORD[view.state], reasons };
}

/** Een lijn-id uit een equipment-id: alles op -02 hoort bij L2. */
export function lineOfEquipment(id: string): "L1" | "L2" {
  return /-02$/.test(id) ? "L2" : "L1";
}
