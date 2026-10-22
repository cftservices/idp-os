/**
 * Heftruckritten als pure functies: een rit is een reeks segmenten (rijden,
 * heffen, wachten) en `poseAt` geeft de stand op tijdstip t. Geen React en
 * alleen relatieve imports, zodat e2e/twin-derive.spec.ts dit kan toetsen.
 *
 * Model: de vorken wijzen naar lokaal +z; rotatie `rot` is de hoek om y,
 * dus rot = atan2(dx, dz) kijkt in rijrichting (dx, dz). Een gedragen pallet
 * staat CARRY_OFFSET voor het midden van de truck.
 */

import {
  COLDSTORE,
  L2_AISLE_Z,
  MAIN_AISLE_Z,
  PALLETISER_PICK,
  PALLETISER_PICK_L2,
  SPINE_X,
  dockPosition,
  rackSlotCoords,
  rackSlotPosition,
  rowAccess,
} from "./layout";

export const DRIVE_SPEED = 6; // scène-eenheden per seconde
export const LIFT_SPEED = 3;
export const CARRY_OFFSET = 1.3;
export const FORK_REST = 0.15;
/** Laadvloer van de trailer (zie TruckModel): de pallet gaat op die hoogte de truck in. */
export const TRAILER_FLOOR = 0.45;

export type Pose = { x: number; z: number; rot: number; fork: number; carrying: boolean };

export type Segment =
  | { kind: "drive"; to: [number, number]; reverse?: boolean }
  | { kind: "lift"; to: number }
  | { kind: "wait"; s: number }
  | { kind: "grab" }
  | { kind: "drop" };

type Timed = { seg: Segment; from: Pose; to: Pose; dur: number };

function heading(dx: number, dz: number, reverse: boolean): number {
  const r = Math.atan2(dx, dz);
  return reverse ? r + Math.PI : r;
}

function angleLerp(a: number, b: number, k: number): number {
  let d = (b - a) % (Math.PI * 2);
  if (d > Math.PI) d -= Math.PI * 2;
  if (d < -Math.PI) d += Math.PI * 2;
  return a + d * k;
}

export class Route {
  readonly parts: Timed[] = [];
  readonly duration: number;
  /** Tijdstip waarop de pallet wordt neergezet (putaway) of opgepakt; null als dat niet gebeurt. */
  readonly dropAt: number | null;
  /** Tijdstip waarop de vork de pallet oppakt; null als dat niet gebeurt. */
  readonly grabAt: number | null;

  constructor(start: Pose, segments: Segment[]) {
    let cur = start;
    let total = 0;
    let dropAt: number | null = null;
    let grabAt: number | null = null;
    for (const seg of segments) {
      let next: Pose = cur;
      let dur = 0;
      if (seg.kind === "drive") {
        const dx = seg.to[0] - cur.x;
        const dz = seg.to[1] - cur.z;
        const dist = Math.hypot(dx, dz);
        if (dist < 1e-6) continue;
        next = { ...cur, x: seg.to[0], z: seg.to[1], rot: heading(dx, dz, !!seg.reverse) };
        dur = dist / DRIVE_SPEED + 0.25; // korte draai aan het begin
      } else if (seg.kind === "lift") {
        next = { ...cur, fork: seg.to };
        dur = Math.abs(seg.to - cur.fork) / LIFT_SPEED;
      } else if (seg.kind === "wait") {
        dur = seg.s;
      } else if (seg.kind === "grab") {
        next = { ...cur, carrying: true };
        dur = 0.3;
        grabAt = total + dur / 2;
      } else {
        next = { ...cur, carrying: false };
        dur = 0.3;
        dropAt = total + dur / 2;
      }
      this.parts.push({ seg, from: cur, to: next, dur });
      total += dur;
      cur = next;
    }
    this.duration = total;
    this.dropAt = dropAt;
    this.grabAt = grabAt;
  }

  get end(): Pose {
    return this.parts.length ? this.parts[this.parts.length - 1].to : { x: 0, z: 0, rot: 0, fork: FORK_REST, carrying: false };
  }

  poseAt(t: number): Pose {
    let rest = Math.max(0, t);
    for (const p of this.parts) {
      if (rest <= p.dur) {
        const k = p.dur > 0 ? rest / p.dur : 1;
        if (p.seg.kind === "drive") {
          const turn = Math.min(1, rest / 0.25);
          const move = Math.max(0, (rest - 0.25) / (p.dur - 0.25));
          return {
            x: p.from.x + (p.to.x - p.from.x) * move,
            z: p.from.z + (p.to.z - p.from.z) * move,
            rot: angleLerp(p.from.rot, p.to.rot, turn),
            fork: p.from.fork,
            carrying: p.from.carrying,
          };
        }
        if (p.seg.kind === "lift") return { ...p.from, fork: p.from.fork + (p.to.fork - p.from.fork) * k };
        // grab/drop wisselen halverwege, zodat de pallet niet al zweeft voor de vork stilstaat
        return k < 0.5 ? p.from : p.to;
      }
      rest -= p.dur;
    }
    return this.end;
  }
}

/** Door de openingen in de westwand van de koelcel. */
const DOOR_X = COLDSTORE.min[0];

export const PARK_PUTAWAY: Pose = { x: 14.6, z: MAIN_AISLE_Z, rot: Math.PI / 2, fork: FORK_REST, carrying: false };
export const PARK_PUTAWAY_L2: Pose = { x: 14.6, z: L2_AISLE_Z, rot: Math.PI / 2, fork: FORK_REST, carrying: false };
export const PARK_LOADER: Pose = { x: 31.6, z: MAIN_AISLE_Z, rot: -Math.PI / 2, fork: FORK_REST, carrying: false };
export const PARK_LOADER_2: Pose = { x: 31.6, z: L2_AISLE_Z, rot: -Math.PI / 2, fork: FORK_REST, carrying: false };

/** Per lijn: waar de pallet klaarstaat, over welk pad en door welke deur de truck rijdt. */
const LINE_ROUTE: Record<string, { pick: [number, number]; aisle: number }> = {
  L1: { pick: PALLETISER_PICK, aisle: MAIN_AISLE_Z },
  L2: { pick: PALLETISER_PICK_L2, aisle: L2_AISLE_Z },
};

/** Waar het vak voor de truck ligt: midden van het vak op x, het pad voor de rij op z. */
function slotStop(slot: number): { x: number; z: number; aisle: number } {
  const [x] = rackSlotPosition(slot);
  const a = rowAccess(rackSlotCoords(slot).row);
  return { x, z: a.stop, aisle: a.aisle };
}

const MID_Z = (MAIN_AISLE_Z + L2_AISLE_Z) / 2;
function aisleOf(z: number): number {
  return z > MID_Z ? MAIN_AISLE_Z : L2_AISLE_Z;
}

/** Terug naar een pad als de truck in een rij of bij een palletiser staat. */
function onAisle(from: Pose, segs: Segment[]): [number, number] {
  const aisle = aisleOf(from.z);
  if (Math.abs(from.z - aisle) > 0.05) segs.push({ kind: "lift", to: FORK_REST }, { kind: "drive", to: [from.x, aisle], reverse: true });
  return [from.x, aisle];
}

/** Over de paden naar (x, aisle): een ander pad bereik je via het dwarspad aan de oostkant. */
function viaAisles(cur: [number, number], x: number, aisle: number, segs: Segment[], reverseLast = false) {
  if (Math.abs(cur[1] - aisle) > 0.05) {
    segs.push({ kind: "drive", to: [SPINE_X, cur[1]] }, { kind: "drive", to: [SPINE_X, aisle] });
    cur = [SPINE_X, aisle];
  }
  segs.push({ kind: "drive", to: [x, aisle], reverse: reverseLast && cur[0] < x });
}

/** Palletiser → vak: oppakken, door de deur van de lijn, het pad in, heffen, neerzetten, terug naar het pad. */
export function putawayRoute(from: Pose, slot: number, line = "L1"): Route {
  const st = slotStop(slot);
  const level = rackSlotPosition(slot)[1];
  const { pick, aisle } = LINE_ROUTE[line] ?? LINE_ROUTE.L1;
  const [px, pz] = pick;
  const bx = px + CARRY_OFFSET + 1.2;
  const segs: Segment[] = [];
  const cur = onAisle(from, segs);
  segs.push({ kind: "lift", to: FORK_REST });
  viaAisles(cur, bx, aisle, segs, true);
  segs.push(
    { kind: "drive", to: [bx, pz], reverse: true },
    // recht naar het westen, de vorken onder de pallet op de draaitafel
    { kind: "drive", to: [px + CARRY_OFFSET, pz] },
    { kind: "grab" },
    { kind: "drive", to: [bx, pz], reverse: true },
    { kind: "drive", to: [bx, aisle] },
    { kind: "drive", to: [DOOR_X, aisle] },
  );
  viaAisles([DOOR_X, aisle], st.x, st.aisle, segs);
  segs.push(
    { kind: "drive", to: [st.x, st.z] },
    { kind: "lift", to: level },
    { kind: "drop" },
    { kind: "lift", to: FORK_REST },
    { kind: "drive", to: [st.x, st.aisle], reverse: true },
  );
  return new Route(from, segs);
}

/** Vak → truck aan de dock: heffen, uitnemen, naar de dock, de trailer in. */
export function loadRoute(from: Pose, slot: number, dock: number): Route {
  const st = slotStop(slot);
  const level = rackSlotPosition(slot)[1];
  const [dx, , dz] = dockPosition(dock);
  const segs: Segment[] = [];
  const cur = onAisle(from, segs);
  viaAisles(cur, st.x, st.aisle, segs);
  segs.push(
    { kind: "drive", to: [st.x, st.z] },
    { kind: "lift", to: level },
    { kind: "grab" },
    { kind: "lift", to: FORK_REST },
    { kind: "drive", to: [st.x, st.aisle], reverse: true },
  );
  viaAisles([st.x, st.aisle], dx, MAIN_AISLE_Z, segs);
  segs.push(
    { kind: "lift", to: TRAILER_FLOOR },
    { kind: "drive", to: [dx, dz + 0.4] },
    { kind: "drop" },
    { kind: "drive", to: [dx, MAIN_AISLE_Z], reverse: true },
    { kind: "lift", to: FORK_REST },
  );
  return new Route(from, segs);
}
