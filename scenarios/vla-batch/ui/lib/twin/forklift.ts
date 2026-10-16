/**
 * Heftruckritten als pure functies: een rit is een reeks segmenten (rijden,
 * heffen, wachten) en `poseAt` geeft de stand op tijdstip t. Geen React en
 * alleen relatieve imports, zodat e2e/twin-derive.spec.ts dit kan toetsen.
 *
 * Model: de vorken wijzen naar lokaal +z; rotatie `rot` is de hoek om y,
 * dus rot = atan2(dx, dz) kijkt in rijrichting (dx, dz). Een gedragen pallet
 * staat CARRY_OFFSET voor het midden van de truck.
 */

import { MAIN_AISLE_Z, PALLETISER_PICK, aisleStopZ, dockPosition, rackSlotCoords, rackSlotPosition, COLDSTORE } from "./layout";

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

export const PARK_PUTAWAY: Pose = { x: 14.6, z: MAIN_AISLE_Z, rot: Math.PI / 2, fork: FORK_REST, carrying: false };
export const PARK_LOADER: Pose = { x: 27.5, z: MAIN_AISLE_Z, rot: -Math.PI / 2, fork: FORK_REST, carrying: false };

/** Waar het vak voor de truck ligt: midden van het vak op x, het pad voor de rij op z. */
function slotStop(slot: number): [number, number] {
  const [x] = rackSlotPosition(slot);
  return [x, aisleStopZ(rackSlotCoords(slot).row)];
}

/** Door de opening in de westwand van de koelcel, op het hoofdpad. */
const DOOR_X = COLDSTORE.min[0];

/** Palletiser → vak: oppakken, door de deur, het pad in, heffen, neerzetten, terug naar het hoofdpad. */
export function putawayRoute(from: Pose, slot: number): Route {
  const [sx, sz] = slotStop(slot);
  const level = rackSlotPosition(slot)[1];
  const [px, pz] = PALLETISER_PICK;
  const segs: Segment[] = [];
  if (Math.abs(from.z - MAIN_AISLE_Z) > 0.05) segs.push({ kind: "lift", to: FORK_REST }, { kind: "drive", to: [from.x, MAIN_AISLE_Z], reverse: true });
  segs.push(
    { kind: "lift", to: FORK_REST },
    { kind: "drive", to: [px + CARRY_OFFSET + 1.2, MAIN_AISLE_Z], reverse: from.x < px + CARRY_OFFSET + 1.2 },
    { kind: "drive", to: [px + CARRY_OFFSET + 1.2, pz], reverse: true },
    // recht naar het westen, de vorken onder de pallet op de draaitafel
    { kind: "drive", to: [px + CARRY_OFFSET, pz] },
    { kind: "grab" },
    { kind: "drive", to: [px + CARRY_OFFSET + 1.2, pz], reverse: true },
    { kind: "drive", to: [px + CARRY_OFFSET + 1.2, MAIN_AISLE_Z] },
    { kind: "drive", to: [DOOR_X, MAIN_AISLE_Z] },
    { kind: "drive", to: [sx, MAIN_AISLE_Z] },
    { kind: "drive", to: [sx, sz] },
    { kind: "lift", to: level },
    { kind: "drop" },
    { kind: "lift", to: FORK_REST },
    { kind: "drive", to: [sx, MAIN_AISLE_Z], reverse: true },
  );
  return new Route(from, segs);
}

/** Vak → truck aan de dock: heffen, uitnemen, naar de dock, de trailer in. */
export function loadRoute(from: Pose, slot: number, dock: number): Route {
  const [sx, sz] = slotStop(slot);
  const level = rackSlotPosition(slot)[1];
  const [dx, , dz] = dockPosition(dock);
  const segs: Segment[] = [];
  if (Math.abs(from.z - MAIN_AISLE_Z) > 0.05) segs.push({ kind: "lift", to: FORK_REST }, { kind: "drive", to: [from.x, MAIN_AISLE_Z], reverse: true });
  segs.push(
    { kind: "drive", to: [sx, MAIN_AISLE_Z] },
    { kind: "drive", to: [sx, sz] },
    { kind: "lift", to: level },
    { kind: "grab" },
    { kind: "lift", to: FORK_REST },
    { kind: "drive", to: [sx, MAIN_AISLE_Z], reverse: true },
    { kind: "drive", to: [dx, MAIN_AISLE_Z] },
    { kind: "lift", to: TRAILER_FLOOR },
    { kind: "drive", to: [dx, dz + 0.4] },
    { kind: "drop" },
    { kind: "drive", to: [dx, MAIN_AISLE_Z], reverse: true },
    { kind: "lift", to: FORK_REST },
  );
  return new Route(from, segs);
}
