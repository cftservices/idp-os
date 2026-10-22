/**
 * Eén bron voor wat er te selecteren is: het tabpaneel, het zoekvak, de
 * zwevende labels, de camerafocus en de detailkaart lezen allemaal hieruit,
 * zodat de 3D-scène en de DOM nooit iets anders zeggen.
 */

import type { Visual } from "@/lib/twin/derive";
import {
  BATCH_STEPS,
  SHIP_STEPS,
  batchStepIndex,
  departPose,
  forkliftVisual,
  inboundBay,
  inboundName,
  inboundVisual,
  shipStepIndex,
  shipmentProgress,
  shipmentVisual,
  tankerPose,
} from "@/lib/twin/flow";
import { PARK_LOADER, PARK_PUTAWAY } from "@/lib/twin/forklift";
import { COLDSTORE, PALLETISER, ROAD_Z, dockPosition, type Vec3 } from "@/lib/twin/layout";
import type { TwinModel } from "@/lib/twin/useTwinData";
import { EMPTY, age, num, shortTime } from "@/lib/format";

export type TwinGroup = "Lijn" | "Grondstoffen" | "Magazijn" | "Heftrucks" | "Trucks";
export const GROUPS: TwinGroup[] = ["Lijn", "Grondstoffen", "Magazijn", "Heftrucks", "Trucks"];

export type TwinItem = {
  id: string;
  group: TwinGroup;
  name: string;
  /** Korte procesregel onder de naam en in het zwevende label, bv. "92,4 °C". */
  meta: string;
  visual: Visual;
  pos: Vec3;
  /** Hoogte van het zwevende label boven het object. */
  labelHeight: number;
  /** 0..100 voor het minibalkje in de objectlijst (vulgraad, laden, accu). */
  pct?: number;
};

export type Detail = {
  title: string;
  kind: string;
  visual: Visual;
  rows: Array<[string, string]>;
  links: Array<{ href: string; label: string }>;
  progress?: { label: string; pct: number };
  timeline?: { steps: readonly string[]; index: number };
  /** Sleutels in de browserhistorie voor minigrafiekjes. */
  spark?: Array<{ key: string; label: string; unit: string }>;
  note?: string;
};

const COLD_CENTER: Vec3 = [(COLDSTORE.min[0] + COLDSTORE.max[0]) / 2, 0, (COLDSTORE.min[1] + COLDSTORE.max[1]) / 2];
const SIM_NOTE = "Demo-simulatie van de engine (source: demo-sim), geen transportsysteem.";

function primaryText(e: TwinModel["equipment"][number]): string {
  const p = e.live?.primary;
  if (!p || p.value == null) return "";
  return `${num(p.value)} ${p.unit}`.trim();
}

/** Sleutel voor de historie van het hoofdproces van een procesdeel. */
export const sparkKey = (id: string) => `eq:${id}`;

export function shipmentPos(m: TwinModel, s: NonNullable<TwinModel["sim"]>["shipments"][number], lane = 0): Vec3 {
  const d = m.logistics.docks.find((x) => x.batch_id === s.batch_id);
  if (d) {
    const [x, , z] = dockPosition(d.dock);
    return [x, 0, z + 3.6];
  }
  const p = departPose(s, m.now, lane, ROAD_Z);
  return [p.x, 0, p.z];
}

export function listItems(m: TwinModel): TwinItem[] {
  const items: TwinItem[] = m.equipment.map((e) => ({
    id: `equipment:${e.id}`,
    group: "Lijn",
    name: e.name,
    meta: primaryText(e),
    visual: e.visual,
    pos: e.pos,
    labelHeight: 4.2,
    pct: e.kind === "filler" && m.filling?.packs_progress_pct != null ? m.filling.packs_progress_pct : undefined,
  }));
  for (const s of m.silos) {
    items.push({
      id: `silo:${s.material.material_id}`,
      group: "Grondstoffen",
      name: s.material.name,
      meta: s.fill != null ? `${Math.round(s.fill * 100)}%` : "",
      visual: s.visual,
      pos: s.pos,
      labelHeight: 6.4,
      pct: s.fill != null ? s.fill * 100 : undefined,
    });
  }
  for (const i of m.sim?.inbounds ?? []) {
    const p = tankerPose(i, m.now);
    const bay = inboundBay(i);
    items.push({
      id: `inbound:${i.material_id ?? "milk"}`,
      group: "Grondstoffen",
      name: inboundName(i),
      meta: inboundVisual(i).label,
      visual: inboundVisual(i),
      pos: p.visible ? [p.x, 0, p.z] : [bay.x, 0, bay.z],
      labelHeight: 4.4,
      pct: i.stage === "unloading" ? i.unloaded_pct : undefined,
    });
  }
  const l = m.logistics;
  items.push({
    id: "palletiser",
    group: "Magazijn",
    name: "Palletiser",
    meta: m.husAvailable ? `${l.palletiser.length} HU` : "",
    visual: m.logisticsVisual.palletiser,
    pos: PALLETISER,
    labelHeight: 3.6,
  });
  items.push({
    id: "coldstore",
    group: "Magazijn",
    name: "Koelcel",
    meta: m.husAvailable ? `${l.totals.stored} pallets` : "",
    visual: m.logisticsVisual.coldstore,
    pos: COLD_CENTER,
    labelHeight: 6.6,
  });
  for (const d of l.docks) {
    items.push({
      id: `dock:${d.batch_id}`,
      group: "Magazijn",
      name: `Dock ${d.dock + 1}`,
      meta: `${d.loaded.length}/${d.loaded.length + d.waiting.length} HU`,
      visual: m.logisticsVisual.dock,
      pos: dockPosition(d.dock),
      labelHeight: 4.4,
      pct: d.progress * 100,
    });
  }
  for (const f of m.sim?.forklifts ?? []) {
    const park = f.role === "putaway" ? PARK_PUTAWAY : PARK_LOADER;
    items.push({
      id: `forklift:${f.id}`,
      group: "Heftrucks",
      name: `Heftruck ${f.id}`,
      // De accu staat al in het statuslabel ("rijdt · 76%"); hier niet nog eens.
      meta: `${f.trips_today} ritten`,
      visual: forkliftVisual(f),
      pos: [park.x, 0, park.z],
      labelHeight: 3.2,
      pct: f.battery_pct,
    });
  }
  (m.sim?.shipments ?? []).forEach((s, i) => {
    items.push({
      id: `shipment:${s.shipment_id}`,
      group: "Trucks",
      name: `${s.truck_id} · ${s.customer}`,
      meta: shipmentVisual(s).label,
      visual: shipmentVisual(s),
      pos: shipmentPos(m, s, i),
      labelHeight: 4.6,
      pct: s.stage === "loading" || s.stage === "en_route" ? shipmentProgress(s, m.now) * 100 : undefined,
    });
  });
  return items;
}

export function positionOf(id: string, m: TwinModel): Vec3 | null {
  return listItems(m).find((i) => i.id === id)?.pos ?? null;
}

function batchLinks(batchId: string | undefined | null) {
  return batchId
    ? [
        { href: `/report/${encodeURIComponent(batchId)}`, label: `Batchrapport ${batchId}` },
        { href: "/batches", label: "Alle batches" },
      ]
    : [{ href: "/batches", label: "Alle batches" }];
}

function hhmm(iso: string | null | undefined) {
  return iso ? shortTime(iso) : EMPTY;
}

export function describe(id: string, m: TwinModel): Detail | null {
  const [kind, key] = id.includes(":") ? [id.slice(0, id.indexOf(":")), id.slice(id.indexOf(":") + 1)] : [id, ""];
  const batchTimeline = {
    steps: BATCH_STEPS,
    index: batchStepIndex(m.batch?.state, m.hus, m.batch?.batch_id),
  };

  if (kind === "equipment") {
    const e = m.equipment.find((x) => x.id === key);
    if (!e) return null;
    const rows: Array<[string, string]> = [["Procesdeel", e.id]];
    const p = e.live?.primary;
    if (p) rows.push([p.label, `${num(p.value)} ${p.unit}`.trim()]);
    for (const s of e.live?.secondary ?? []) rows.push([s.label, s.value == null ? EMPTY : `${num(s.value)} ${s.unit}`.trim()]);
    rows.push(["Draaiuren", e.health?.running_hours != null ? num(e.health.running_hours, 0) : EMPTY]);
    rows.push(["Batches sinds CIP", e.health?.batches_since_cip != null ? String(e.health.batches_since_cip) : EMPTY]);
    rows.push(["Laatste CIP", e.health?.last_cip_at ? age(e.health.last_cip_at, m.now) : EMPTY]);
    rows.push(["Open alarmen", String(e.alarms.length)]);
    const filling = e.kind === "filler" && m.filling?.packs_progress_pct != null;
    return {
      title: e.name,
      kind: "Procesdeel",
      visual: e.visual,
      rows,
      progress: filling ? { label: "Vullen", pct: m.filling!.packs_progress_pct! } : undefined,
      timeline: m.batch ? batchTimeline : undefined,
      spark: p ? [{ key: sparkKey(e.id), label: p.label, unit: p.unit }] : undefined,
      links: [
        { href: "/equipment", label: "Equipment-overzicht" },
        { href: "/line", label: "Lijnscherm" },
        ...(e.alarms.length > 0 ? [{ href: "/alarms", label: "Alarmen" }] : []),
      ],
    };
  }

  if (kind === "silo") {
    const s = m.silos.find((x) => x.material.material_id === key);
    if (!s) return null;
    return {
      title: s.material.name,
      kind: "Grondstof",
      visual: s.visual,
      rows: [
        ["Voorraad", s.material.stock_qty != null ? `${num(s.material.stock_qty, 0)} ${s.material.uom}` : EMPTY],
        ["Bestelgrens", s.material.reorder_level != null ? `${num(s.material.reorder_level, 0)} ${s.material.uom}` : EMPTY],
      ],
      progress: s.fill != null ? { label: "Vulgraad", pct: s.fill * 100 } : undefined,
      spark: [{ key: `silo:${key}`, label: "Voorraad", unit: s.material.uom }],
      links: [{ href: "/voorraad", label: "Voorraad" }],
    };
  }

  if (kind === "inbound") {
    const mid = key === "tanker" ? "milk" : key;
    const i = m.sim?.inbounds.find((x) => (x.material_id ?? "milk") === mid);
    if (!i) return null;
    const lading = i.litres > 0 ? `${num(i.litres, 0)} L` : i.load_qty != null ? `${num(i.load_qty, 0)} ${i.uom ?? "kg"}` : EMPTY;
    return {
      title: inboundName(i),
      kind: "Inkomend transport",
      visual: inboundVisual(i),
      rows: [
        ["Lading", lading],
        ["Ontvangen", i.received_kg != null ? `${num(i.received_kg, 0)} ${i.uom ?? "kg"}` : EMPTY],
        ["Status", inboundVisual(i).label],
        [i.stage === "en_route" ? "Verwacht" : i.stage === "unloading" ? "Klaar met lossen" : "Volgende", hhmm(i.next_at)],
      ],
      progress: { label: "Lossen", pct: i.unloaded_pct },
      links: [{ href: "/voorraad", label: "Voorraad" }],
      note: SIM_NOTE,
    };
  }

  if (kind === "palletiser") {
    const p = m.logistics.palletiser;
    return {
      title: "Palletiser",
      kind: "Magazijn",
      visual: m.logisticsVisual.palletiser,
      rows: [
        ["Gewikkelde HU's", m.husAvailable ? String(p.length) : EMPTY],
        ["Laatste HU", p.length ? `${p[p.length - 1].hu_id} · ${shortTime(p[p.length - 1].ts)}` : EMPTY],
      ],
      timeline: m.batch ? batchTimeline : undefined,
      links: batchLinks(m.batch?.batch_id),
    };
  }

  if (kind === "coldstore") {
    const l = m.logistics;
    return {
      title: "Koelcel",
      kind: "Magazijn",
      visual: m.logisticsVisual.coldstore,
      rows: [
        ["HU's wachtend op verzending", m.husAvailable ? String(l.totals.stored) : EMPTY],
        ["Packs in koelcel", m.husAvailable ? num(l.totals.packsStored, 0) : EMPTY],
        ["Buiten de getekende vakken", m.husAvailable ? String(l.rackOverflow) : EMPTY],
      ],
      spark: [{ key: "coldstore", label: "Pallets", unit: "" }],
      links: [{ href: "/voorraad", label: "Voorraad" }],
    };
  }

  if (kind === "dock") {
    const d = m.logistics.docks.find((x) => x.batch_id === key);
    if (!d) return null;
    const s = m.sim?.shipments.find((x) => x.batch_id === d.batch_id);
    return {
      title: `Dock ${d.dock + 1}`,
      kind: "Dock",
      visual: m.logisticsVisual.dock,
      rows: [
        ["Status", d.state === "laden" ? "Laden" : "Aangemeld, laden start zo"],
        ...(s ? ([["Order", s.order_id ?? EMPTY], ["Klant", `${s.customer}, ${s.destination}`], ["Truck", s.truck_id]] as Array<[string, string]>) : []),
        ["Batch", d.batch_id],
        ["Geladen", `${d.loaded.length} / ${d.loaded.length + d.waiting.length} HU`],
      ],
      progress: { label: "Laden", pct: d.progress * 100 },
      timeline: s ? { steps: SHIP_STEPS, index: shipStepIndex(s.stage) } : undefined,
      links: batchLinks(d.batch_id),
      note: s ? SIM_NOTE : "Truck afgeleid uit HU-status, niet uit een transportsysteem.",
    };
  }

  if (kind === "forklift") {
    const f = m.sim?.forklifts.find((x) => x.id === key);
    if (!f) return null;
    return {
      title: `Heftruck ${f.id}`,
      kind: f.role === "putaway" ? "Heftruck · inslag" : "Heftruck · laden",
      visual: forkliftVisual(f),
      rows: [
        ["Ritten vandaag", String(f.trips_today)],
        ["Laatste taak", f.last_task ?? EMPTY],
        ["Laatste rit", f.last_move ? age(f.last_move, m.now) : EMPTY],
      ],
      progress: { label: f.state === "charging" ? "Accu (laden)" : "Accu", pct: f.battery_pct },
      spark: [{ key: `forklift:${f.id}`, label: "Accu", unit: "%" }],
      links: [{ href: "/voorraad", label: "Voorraad" }],
      note: SIM_NOTE,
    };
  }

  if (kind === "shipment") {
    const s = m.sim?.shipments.find((x) => x.shipment_id === key);
    if (!s) return null;
    return {
      title: `${s.shipment_id} · ${s.truck_id}`,
      kind: "Zending",
      visual: shipmentVisual(s),
      rows: [
        ["Order", s.order_id ?? EMPTY],
        ["Klant", s.customer],
        ["Bestemming", s.destination],
        ["Batch", s.batch_id],
        ["Pallets", `${s.pallets_loaded} / ${s.pallets}`],
        ["Packs", num(s.packs, 0)],
        ["Besteld", hhmm(s.ordered_at)],
        ["Vertrokken", hhmm(s.loaded_at)],
        ["Aankomst", s.eta ? hhmm(s.eta) : EMPTY],
      ],
      progress: { label: s.stage === "en_route" ? "Rit" : "Laden", pct: shipmentProgress(s, m.now) * 100 },
      timeline: { steps: SHIP_STEPS, index: shipStepIndex(s.stage) },
      links: batchLinks(s.batch_id),
      note: SIM_NOTE,
    };
  }

  if (kind === "truck") {
    const t = m.logistics.departures.find((x) => x.batch_id === key);
    if (!t) return null;
    return {
      title: `Vertrek ${t.batch_id}`,
      kind: "Truck (afgeleid uit HU-status)",
      visual: m.logisticsVisual.truck,
      rows: [
        ["HU's", String(t.hus)],
        ["Packs", num(t.packs, 0)],
        ["Vertrokken", age(t.ts, m.now)],
      ],
      links: batchLinks(t.batch_id),
    };
  }
  return null;
}
