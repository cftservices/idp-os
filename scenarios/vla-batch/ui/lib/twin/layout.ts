/**
 * De plattegrond van de site, in scène-eenheden (ongeveer een meter).
 * x loopt van west (melkontvangst) naar oost (expeditie), z van noord naar
 * zuid (de weg). Alleen geometrie: welke status een object heeft, bepaalt
 * derive.ts.
 */

export type Vec3 = [number, number, number];

export const LINE_EQUIPMENT = [
  { id: "receiving-tank-01", step: "receiving", name: "Ontvangsttank", kind: "tank", pos: [-11, 0, 0] as Vec3 },
  { id: "process-tank-01", step: "mixing", name: "Mengtank", kind: "mixer", pos: [-6, 0, 0] as Vec3 },
  { id: "cook-unit-01", step: "cooking", name: "Kookunit", kind: "cook", pos: [-1, 0, 0] as Vec3 },
  { id: "cooler-01", step: "cooling", name: "Koeler", kind: "cooler", pos: [4, 0, 0] as Vec3 },
  { id: "filler-01", step: "filling", name: "Vuller", kind: "filler", pos: [9, 0, 0] as Vec3 },
] as const;

export type LineEquipment = (typeof LINE_EQUIPMENT)[number];

/** Silo's voor de grondstoffen, in de volgorde waarin /inventory ze levert. */
export const SILO_SLOTS: Vec3[] = [
  [-21, 0, -4],
  [-21, 0, 0.5],
  [-17.5, 0, -4],
  [-17.5, 0, 0.5],
];

export const HALL = { min: [-14, -5.5] as [number, number], max: [13.5, 5.5] as [number, number], wall: 1.4 };

export const PALLETISER: Vec3 = [12, 0, 2.6];

export const COLDSTORE = {
  min: [16, -8] as [number, number],
  max: [30, 4.5] as [number, number],
  wall: 4.8,
  /** Opening in de westwand (z-bereik) waardoor de heftruck van de palletiser binnenrijdt. */
  door: [1.6, 4.5] as [number, number],
};

/**
 * Stellingen: enkelzijdige rijen langs x, elk bereikbaar vanuit het pad aan
 * de zuidkant. Vier rijen, zes vakken, vier niveaus = 96 palletplaatsen.
 * RACK_SLOTS moet gelijk zijn aan RACK_SLOTS in batch-engine/vla/warehouse.py:
 * de engine kiest het vak en zet het op de HU.
 */
export const RACK = {
  rows: [-6.9, -4.3, -1.7, 0.9] as number[],
  xStart: 18.4,
  slotsPerRow: 6,
  pitch: 1.75,
  levels: 4,
  levelHeight: 1.0,
  depth: 1.1,
};
export const RACK_SLOTS = RACK.rows.length * RACK.slotsPerRow * RACK.levels;

/** Hoogte van de onderkant van een pallet op een niveau. */
export function rackLevelY(level: number): number {
  return 0.12 + level * RACK.levelHeight;
}

export function rackSlotCoords(slot: number): { row: number; col: number; level: number } {
  const perRow = RACK.slotsPerRow * RACK.levels;
  const row = Math.floor(slot / perRow);
  const inRow = slot % perRow;
  return { row, col: Math.floor(inRow / RACK.levels), level: inRow % RACK.levels };
}

export function rackSlotPosition(slot: number): Vec3 {
  const { row, col, level } = rackSlotCoords(slot);
  return [RACK.xStart + col * RACK.pitch, rackLevelY(level), RACK.rows[row] ?? 0];
}

/** Hoofdpad langs de voorgevel, waar de heftruck van en naar de docks rijdt. */
export const MAIN_AISLE_Z = 3.3;
/** Waar de heftruck stopt om een pallet in een rij te zetten: de pallet (op CARRY_OFFSET voor de truck) staat dan precies in het vak. */
export function aisleStopZ(row: number): number {
  return (RACK.rows[row] ?? 0) + 1.3;
}
/** De pallet op de draaitafel van de palletiser. */
export const PALLETISER_PICK: [number, number] = [PALLETISER[0], PALLETISER[2]];

export const MAX_DOCKS = 4;
/** Docks aan de zuidgevel van het koelmagazijn; trucks staan er met de neus naar de weg. */
export function dockPosition(i: number): Vec3 {
  return [18.5 + i * 3.4, 0, COLDSTORE.max[1]];
}

export const ROAD_Z = 13;
export const ROAD_X = [-34, 46] as [number, number];

export const CAMERA_HOME: Vec3 = [30, 44, 58];
export const CAMERA_TARGET: Vec3 = [5, 0, 1];
