/**
 * Eén bron voor wat er te selecteren is: de toegankelijke objectlijst, de
 * camerafocus en de inhoud van het infopaneel lezen allemaal hieruit, zodat
 * de 3D-scène en de DOM nooit iets anders zeggen.
 */

import type { Visual } from "@/lib/twin/derive";
import { COLDSTORE, PALLETISER, dockPosition, type Vec3 } from "@/lib/twin/layout";
import type { TwinModel } from "@/lib/twin/useTwinData";
import { EMPTY, age, num, shortTime } from "@/lib/format";

export type TwinGroup = "Lijn" | "Grondstoffen" | "Logistiek";

export type TwinItem = {
  id: string;
  group: TwinGroup;
  name: string;
  visual: Visual;
  pos: Vec3;
};

export type Detail = {
  title: string;
  kind: string;
  visual: Visual;
  rows: Array<[string, string]>;
  links: Array<{ href: string; label: string }>;
};

const COLD_CENTER: Vec3 = [(COLDSTORE.min[0] + COLDSTORE.max[0]) / 2, 0, (COLDSTORE.min[1] + COLDSTORE.max[1]) / 2];

export function listItems(m: TwinModel): TwinItem[] {
  const items: TwinItem[] = m.equipment.map((e) => ({
    id: `equipment:${e.id}`,
    group: "Lijn",
    name: e.name,
    visual: e.visual,
    pos: e.pos,
  }));
  for (const s of m.silos) {
    items.push({ id: `silo:${s.material.material_id}`, group: "Grondstoffen", name: s.material.name, visual: s.visual, pos: s.pos });
  }
  items.push({ id: "palletiser", group: "Logistiek", name: "Palletiser", visual: m.logisticsVisual.palletiser, pos: PALLETISER });
  items.push({ id: "coldstore", group: "Logistiek", name: "Koelcel", visual: m.logisticsVisual.coldstore, pos: COLD_CENTER });
  for (const d of m.logistics.docks) {
    items.push({ id: `dock:${d.batch_id}`, group: "Logistiek", name: `Dock ${d.dock + 1}`, visual: m.logisticsVisual.dock, pos: dockPosition(d.dock) });
  }
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

export function describe(id: string, m: TwinModel): Detail | null {
  const [kind, key] = id.includes(":") ? [id.slice(0, id.indexOf(":")), id.slice(id.indexOf(":") + 1)] : [id, ""];

  if (kind === "equipment") {
    const e = m.equipment.find((x) => x.id === key);
    if (!e) return null;
    const rows: Array<[string, string]> = [["Procesdeel", e.id]];
    if (e.live?.primary) {
      rows.push([e.live.primary.label, `${num(e.live.primary.value)} ${e.live.primary.unit}`.trim()]);
    }
    rows.push(["Draaiuren", e.health?.running_hours != null ? num(e.health.running_hours, 0) : EMPTY]);
    rows.push(["Batches sinds CIP", e.health?.batches_since_cip != null ? String(e.health.batches_since_cip) : EMPTY]);
    rows.push(["Laatste CIP", e.health?.last_cip_at ? age(e.health.last_cip_at, m.now) : EMPTY]);
    rows.push(["Open alarmen", String(e.alarms.length)]);
    return {
      title: e.name,
      kind: "Procesdeel",
      visual: e.visual,
      rows,
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
      links: [{ href: "/voorraad", label: "Voorraad" }],
    };
  }

  if (kind === "palletiser") {
    const p = m.logistics.palletiser;
    return {
      title: "Palletiser",
      kind: "Logistiek",
      visual: m.logisticsVisual.palletiser,
      rows: [
        ["Gewikkelde HU's", m.husAvailable ? String(p.length) : EMPTY],
        ["Laatste HU", p.length ? `${p[p.length - 1].hu_id} · ${shortTime(p[p.length - 1].ts)}` : EMPTY],
      ],
      links: batchLinks(m.batch?.batch_id),
    };
  }

  if (kind === "coldstore") {
    const l = m.logistics;
    return {
      title: "Koelcel",
      kind: "Logistiek",
      visual: m.logisticsVisual.coldstore,
      rows: [
        ["HU's wachtend op verzending", m.husAvailable ? String(l.totals.stored) : EMPTY],
        ["Packs in koelcel", m.husAvailable ? num(l.totals.packsStored, 0) : EMPTY],
        ["Buiten de getekende vakken", m.husAvailable ? String(l.rackOverflow) : EMPTY],
      ],
      links: [{ href: "/voorraad", label: "Voorraad" }],
    };
  }

  if (kind === "dock") {
    const d = m.logistics.docks.find((x) => x.batch_id === key);
    if (!d) return null;
    return {
      title: `Dock ${d.dock + 1}`,
      kind: "Truck (afgeleid uit HU-status)",
      visual: m.logisticsVisual.dock,
      rows: [
        ["Batch", d.batch_id],
        ["Geladen", `${d.loaded.length} / ${d.loaded.length + d.waiting.length} HU`],
      ],
      links: batchLinks(d.batch_id),
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
