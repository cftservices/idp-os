import { test, expect } from "@playwright/test";
import {
  ARRIVE_LEAD_MS,
  DEPART_WINDOW_MS,
  STORE_DWELL_MS,
  deriveLogistics,
  equipmentVisual,
  logisticsVisuals,
  siloFill,
  siloVisual,
  type Hu,
} from "../lib/twin/derive";
import {
  COLDSTORE,
  L2_AISLE_Z,
  MAIN_AISLE_Z,
  MAX_DOCKS,
  PALLETISER_PICK,
  PALLETISER_PICK_L2,
  RACK,
  RACK_SLOTS,
  RACK_ZONE_SLOTS,
  SILO_SLOTS,
  SPINE_X,
  dockPosition,
  rackSlotCoords,
  rackSlotPosition,
  rowAccess,
} from "../lib/twin/layout";
import { CARRY_OFFSET, PARK_LOADER, PARK_LOADER_2, PARK_PUTAWAY, PARK_PUTAWAY_L2, loadRoute, putawayRoute } from "../lib/twin/forklift";
import { HALL, HALL2, LINE2_EQUIPMENT, LINE_EQUIPMENT, PREP_ROOM, PREP_STATIONS } from "../lib/twin/layout";
import {
  FLAVOURS,
  batchFlavours,
  flavourKey,
  flavourOfBatch,
  flavourOfRecipe,
  lineOfEquipment,
  parseFlavour,
  prepColorKey,
  prepStationView,
  prepVisual,
} from "../lib/twin/lines";
import { TWIN_KEYS } from "../lib/twin/palette";
import type { PrepSet, PrepStation, ProductLine } from "../lib/types";

/**
 * De afleidingen van de 3D site-twin, zonder browser.
 *
 * Waarom hier en niet in de scène: HU's zijn lokaal niet te seeden (create_hu
 * eist een APPROVED batch, en dat vraagt de fabriekssimulatie), dus trucks en
 * stellingen zie je alleen op een draaiende fabriek. De regels die bepalen
 * wat de scène tekent, liggen in pure functies en worden hier getoetst.
 */

const NOW = Date.parse("2026-10-01T12:00:00Z");
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString();
let seq = 0;
function hu(batch: string, status: string, msAgo = 60_000, packs = 40): Hu {
  seq += 1;
  return { hu_id: `HU-${String(seq).padStart(4, "0")}`, batch_id: batch, packs_count: packs, location: null, status, ts: iso(msAgo) };
}

test.describe("equipmentVisual", () => {
  const idle = { equipment_id: "x", state: "Idle" };

  test("zonder health is het 'geen data', nooit 'stil'", () => {
    const v = equipmentVisual({ health: null, engineReachable: true });
    expect(v.state).toBe("unknown");
    expect(v.label).toBe("geen data");
  });

  test("een storing wint van een onbereikbare engine", () => {
    const v = equipmentVisual({ health: { equipment_id: "x", state: "Down" }, engineReachable: false });
    expect(v.state).toBe("alarm");
    expect(v.label).toBe("storing");
  });

  test("een open hoog alarm wint van stale", () => {
    const v = equipmentVisual({
      health: idle,
      engineReachable: false,
      alarms: [{ alarm_id: "a", message: "temp hoog", equipment_id: "x", priority: "high", state: "open" }],
    });
    expect(v.state).toBe("alarm");
    expect(v.reasons).toContain("temp hoog");
  });

  test("een onbereikbare engine maakt een stil procesdeel verouderd", () => {
    const v = equipmentVisual({ health: idle, engineReachable: false });
    expect(v.state).toBe("stale");
    expect(v.label).toBe("verouderd");
  });

  test("een erkend alarm telt niet meer als open", () => {
    const v = equipmentVisual({
      health: idle,
      engineReachable: true,
      alarms: [{ alarm_id: "a", message: "m", equipment_id: "x", priority: "high", state: "acknowledged" }],
    });
    expect(v.state).toBe("idle");
  });

  test("vervuild is aandacht met een reden", () => {
    const v = equipmentVisual({ health: { ...idle, dirty: true }, engineReachable: true });
    expect(v.state).toBe("warn");
    expect(v.label).toBe("vervuild");
    expect(v.reasons).toContain("CIP nodig");
  });

  test("draaien en stilstaan dragen elk hun eigen woord en vorm", () => {
    const run = equipmentVisual({ health: { equipment_id: "x", state: "Running" }, engineReachable: true });
    const stil = equipmentVisual({ health: idle, engineReachable: true });
    expect([run.state, run.label]).toEqual(["run", "draait"]);
    expect([stil.state, stil.label]).toEqual(["idle", "stil"]);
    expect(run.shape).not.toBe(stil.shape);
  });
});

test.describe("silo", () => {
  test("zonder bestelgrens is de vulgraad onbekend, geen nul", () => {
    expect(siloFill({ material_id: "m", name: "M", uom: "kg", stock_qty: 50, reorder_level: null })).toBeNull();
    expect(siloFill({ material_id: "m", name: "M", uom: "kg", stock_qty: 50, reorder_level: 0 })).toBeNull();
  });

  test("vulgraad blijft tussen 0 en 1", () => {
    expect(siloFill({ material_id: "m", name: "M", uom: "kg", stock_qty: 9999, reorder_level: 10 })).toBe(1);
    expect(siloFill({ material_id: "m", name: "M", uom: "kg", stock_qty: -5, reorder_level: 10 })).toBe(0);
  });

  test("onder de bestelgrens is aandacht, zonder voorraadcijfer is het geen data", () => {
    expect(siloVisual({ material_id: "m", name: "M", uom: "kg", stock_qty: 1, reorder_level: 10, below_reorder: true }).state).toBe("warn");
    expect(siloVisual({ material_id: "m", name: "M", uom: "kg", stock_qty: null, reorder_level: 10 }).state).toBe("unknown");
  });
});

test.describe("deriveLogistics", () => {
  test("elke lijn heeft een eigen palletiser en een eigen laatste putaway", () => {
    const a = { ...hu("A", "wrapped"), line: "L1" };
    const b = { ...hu("B", "wrapped"), line: "L2" };
    const c = { ...hu("C", "awaiting_shipment", 30_000), line: "L2", slot: RACK_ZONE_SLOTS + 1 };
    const d = { ...hu("D", "awaiting_shipment", 90_000), slot: 2 };
    const l = deriveLogistics([a, b, c, d] as Hu[], NOW);
    expect(l.palletisers.L1.map((h) => h.batch_id)).toEqual(["A"]);
    expect(l.palletisers.L2.map((h) => h.batch_id)).toEqual(["B"]);
    expect(l.lastPutawayByLine.L2?.hu.batch_id).toBe("C");
    expect(l.lastPutawayByLine.L1?.hu.batch_id, "zonder line is het L1").toBe("D");
    expect(l.lastLoads).toHaveLength(2);
  });

  test("geen HU's is een lege site, geen fout", () => {
    for (const input of [null, undefined, []]) {
      const l = deriveLogistics(input, NOW);
      expect(l.docks).toEqual([]);
      expect(l.departures).toEqual([]);
      expect(l.totals).toEqual({ wrapped: 0, stored: 0, shipped: 0, packsStored: 0 });
    }
  });

  test("hooguit MAX_DOCKS trucks aan een dock, de rest telt als overloop, oudste eerst", () => {
    const hus = Array.from({ length: MAX_DOCKS + 2 }, (_, i) =>     hu(`B${i}`, "awaiting_shipment", (20 - i) * 60_000));
    const l = deriveLogistics(hus, NOW);
    expect(l.docks).toHaveLength(MAX_DOCKS);
    expect(l.docksOverflow).toBe(2);
    expect(l.docks.map((d) => d.batch_id)).toEqual(Array.from({ length: MAX_DOCKS }, (_, i) => `B${i}`));
  });

      test("een truck meldt zich pas bij laadklaar, en laadt pas als er een pallet in zit", () => {
        const vers = deriveLogistics([hu("VERS", "awaiting_shipment", 60_000)], NOW);
        expect(vers.docks, "net opgeslagen batch heeft nog geen truck").toEqual([]);

        const bijnaKlaar = deriveLogistics([hu("KLAAR", "awaiting_shipment", STORE_DWELL_MS - ARRIVE_LEAD_MS + 1_000)], NOW);
        expect(bijnaKlaar.docks).toHaveLength(1);
        expect(bijnaKlaar.docks[0].state).toBe("aangemeld");

        const laadt = deriveLogistics([hu("L", "shipped", 1_000), hu("L", "awaiting_shipment", 60_000)], NOW);
        expect(laadt.docks[0].state).toBe("laden");
      });

  test("laadvoortgang telt geladen tegen het totaal van de batch", () => {
    const l = deriveLogistics(
      [hu("B1", "shipped"), hu("B1", "shipped"), hu("B1", "shipped"), hu("B1", "awaiting_shipment")],
      NOW,
    );
    expect(l.docks).toHaveLength(1);
    expect(l.docks[0].progress).toBe(0.75);
    expect(l.departures, "een batch met nog een wachtende HU vertrekt niet").toEqual([]);
  });

  test("een volledig geshipte batch vertrekt alleen binnen het venster", () => {
    const recent = deriveLogistics([hu("B1", "shipped", 60_000, 30), hu("B1", "shipped", 30_000, 10)], NOW);
    expect(recent.departures).toHaveLength(1);
    expect(recent.departures[0]).toMatchObject({ batch_id: "B1", hus: 2, packs: 40, ageMs: 30_000 });

    const oud = deriveLogistics([hu("B2", "shipped", DEPART_WINDOW_MS + 1)], NOW);
    expect(oud.departures).toEqual([]);
  });

  test("meer HU's dan stellingvakken telt als overloop", () => {
    const hus = Array.from({ length: RACK_SLOTS + 3 }, () => hu("B1", "awaiting_shipment"));
    const l = deriveLogistics(hus, NOW);
    expect(l.rack).toHaveLength(RACK_SLOTS);
    expect(l.rackOverflow).toBe(3);
    expect(logisticsVisuals(l, true, true).coldstore.state).toBe("warn");
  });

  test("een HU houdt het vak dat de engine koos; dubbele en ongeldige vakken schuiven naar een vrij vak", () => {
    const a = { ...hu("B1", "awaiting_shipment", 50_000), slot: 42 };
    const dubbel = { ...hu("B1", "awaiting_shipment", 40_000), slot: 42 };
    const ongeldig = { ...hu("B1", "awaiting_shipment", 30_000), slot: RACK_SLOTS + 5 };
    const l = deriveLogistics([a, dubbel, ongeldig], NOW);
    const slotOf = new Map(l.rack.map((r) => [r.hu.hu_id, r.slot]));
    expect(slotOf.get(a.hu_id)).toBe(42);
    expect(slotOf.get(dubbel.hu_id)).toBe(0);
    expect(slotOf.get(ongeldig.hu_id)).toBe(1);
    expect(logisticsVisuals(l, true, true).coldstore.label).toBe(`3/${RACK_SLOTS} vakken bezet`);
  });

  test("een vertrekkende pallet laat de andere op hun plek staan", () => {
    const blijft = { ...hu("B2", "awaiting_shipment", 50_000), slot: 7 };
    const voor = deriveLogistics([{ ...hu("B1", "awaiting_shipment", 60_000), slot: 3 }, blijft], NOW);
    const na = deriveLogistics([blijft], NOW);
    expect(voor.rack.find((r) => r.hu.hu_id === blijft.hu_id)?.slot).toBe(7);
    expect(na.rack.find((r) => r.hu.hu_id === blijft.hu_id)?.slot).toBe(7);
  });

  test("een batch die al laadt houdt zijn dock, ook als er oudere wachten", () => {
    const oud = Array.from({ length: MAX_DOCKS }, (_, i) => hu(`OUD${i}`, "awaiting_shipment", (20 - i) * 60_000));
    const laadt = [hu("NIEUW", "shipped", 1_000), hu("NIEUW", "awaiting_shipment", 2_000)];
    const l = deriveLogistics([...oud, ...laadt], NOW);
    expect(l.docks[0].batch_id).toBe("NIEUW");
    expect(new Set(l.docks.map((d) => d.dock)).size).toBe(MAX_DOCKS);
  });

  test("de nieuwste putaway en de nieuwste laadactie zijn de heftruckritten", () => {
    const oud = { ...hu("B1", "awaiting_shipment", 90_000), slot: 10 };
    const nieuw = { ...hu("B1", "awaiting_shipment", 5_000), slot: 11 };
    const geladen = { ...hu("B1", "shipped", 3_000), slot: 12 };
    const l = deriveLogistics([oud, nieuw, geladen], NOW);
    expect(l.lastPutaway).toMatchObject({ slot: 11 });
    expect(l.lastPutaway?.hu.hu_id).toBe(nieuw.hu_id);
    const dock = l.docks.find((d) => d.batch_id === "B1")!.dock;
    expect(l.lastLoad).toMatchObject({ slot: 12, dock });
  });

  test("een geshipte HU zonder dock (batch vertrokken) is geen laadrit", () => {
    const l = deriveLogistics([{ ...hu("B1", "shipped", 3_000), slot: 12 }], NOW);
    expect(l.lastLoad).toBeNull();
  });
});

test.describe("heftruckritten", () => {
  test("een putaway zet de pallet precies in het vak, op de juiste hoogte", () => {
    for (const slot of [0, 5, 47, RACK_SLOTS - 1]) {
      const r = putawayRoute(PARK_PUTAWAY, slot);
      expect(r.grabAt).not.toBeNull();
      expect(r.dropAt).not.toBeNull();
      expect(r.grabAt!).toBeLessThan(r.dropAt!);
      const p = r.poseAt(r.dropAt! - 0.01);
      const [sx, sy, sz] = rackSlotPosition(slot);
      expect(p.carrying).toBe(true);
      expect(p.x + Math.sin(p.rot) * CARRY_OFFSET).toBeCloseTo(sx, 3);
      expect(p.z + Math.cos(p.rot) * CARRY_OFFSET).toBeCloseTo(sz, 3);
      expect(p.fork).toBeCloseTo(sy, 3);
      expect(r.poseAt(r.duration).carrying).toBe(false);
      expect(r.poseAt(r.duration).z).toBeCloseTo(rowAccess(rackSlotCoords(slot).row).aisle, 3);
      expect(r.duration, "een rit moet ongeveer in het putaway-ritme passen").toBeLessThan(14);
    }
  });

  test("bij het oppakken staat de vork onder de pallet op de palletiser", () => {
    const r = putawayRoute(PARK_PUTAWAY, 20);
    const p = r.poseAt(r.grabAt!);
    expect(p.x + Math.sin(p.rot) * CARRY_OFFSET).toBeCloseTo(PALLETISER_PICK[0], 3);
    expect(p.z + Math.cos(p.rot) * CARRY_OFFSET).toBeCloseTo(PALLETISER_PICK[1], 3);
  });

  test("de inslagtruck van L2 pakt bij de palletiser van L2 en rijdt naar de noordelijke zone", () => {
    const slot = RACK_ZONE_SLOTS + 10;
    const r = putawayRoute(PARK_PUTAWAY_L2, slot, "L2");
    const g = r.poseAt(r.grabAt!);
    expect(g.x + Math.sin(g.rot) * CARRY_OFFSET).toBeCloseTo(PALLETISER_PICK_L2[0], 3);
    expect(g.z + Math.cos(g.rot) * CARRY_OFFSET).toBeCloseTo(PALLETISER_PICK_L2[1], 3);
    const p = r.poseAt(r.dropAt! - 0.01);
    const [sx, , sz] = rackSlotPosition(slot);
    expect(p.x + Math.sin(p.rot) * CARRY_OFFSET).toBeCloseTo(sx, 3);
    expect(p.z + Math.cos(p.rot) * CARRY_OFFSET).toBeCloseTo(sz, 3);
    expect(r.duration).toBeLessThan(16);
  });

  test("een L2-pallet in de zuidelijke zone steekt over via de dwarsgang", () => {
    const r = putawayRoute(PARK_PUTAWAY_L2, 5, "L2");
    let crossed = false;
    for (let t = 0; t <= r.duration; t += 0.05) {
      const p = r.poseAt(t);
      if (p.z > L2_AISLE_Z + 1 && p.z < MAIN_AISLE_Z - 1 && Math.abs(p.x - SPINE_X) < 0.05) crossed = true;
    }
    expect(crossed).toBe(true);
  });

  test("rijen 0-3 aan het hoofdpad, 4-7 aan het L2-pad, en elke stop ligt tussen twee rijen of naast een pad", () => {
    for (let row = 0; row < RACK.rows.length; row++) {
      const a = rowAccess(row);
      expect(a.aisle).toBe(row < 4 ? MAIN_AISLE_Z : L2_AISLE_Z);
      expect(Math.abs(a.stop - RACK.rows[row])).toBeCloseTo(1.3, 5);
      for (const other of RACK.rows) expect(Math.abs(a.stop - other)).toBeGreaterThan(RACK.depth / 2);
    }
  });

  test("vier parkeerplekken, allemaal verschillend, tussen palletiser en de oostwand van de koelcel", () => {
    const parks = [PARK_PUTAWAY, PARK_PUTAWAY_L2, PARK_LOADER, PARK_LOADER_2];
    expect(new Set(parks.map((p) => `${p.x},${p.z}`)).size).toBe(4);
    for (const p of parks) {
      expect(p.x).toBeGreaterThan(PALLETISER_PICK[0]);
      expect(p.x).toBeLessThan(COLDSTORE.max[0]);
      expect(p.z).toBeGreaterThan(COLDSTORE.min[1]);
      expect(p.z).toBeLessThan(COLDSTORE.max[1]);
    }
  });

  test("laden pakt de pallet uit het vak en zet hem in de trailer", () => {
    const r = loadRoute(PARK_LOADER, 30, 2);
    const g = r.poseAt(r.grabAt!);
    const [sx, sy, sz] = rackSlotPosition(30);
    expect(g.x + Math.sin(g.rot) * CARRY_OFFSET).toBeCloseTo(sx, 3);
    expect(g.z + Math.cos(g.rot) * CARRY_OFFSET).toBeCloseTo(sz, 3);
    expect(g.fork).toBeCloseTo(sy, 3);
    const d = r.poseAt(r.dropAt! - 0.01);
    const [dx, , dz] = dockPosition(2);
    expect(d.carrying).toBe(true);
    expect(d.x).toBeCloseTo(dx, 3);
    expect(d.z + Math.cos(d.rot) * CARRY_OFFSET).toBeGreaterThan(dz);
    expect(r.duration).toBeLessThan(14);
  });

  test("een rit vanaf een willekeurige pose begint daar en houdt de truck binnen de koelcel of het pad", () => {
    const start = { x: 22, z: -1.7 + 1.3, rot: Math.PI, fork: 2.12, carrying: false };
    const r = putawayRoute(start, 3);
    expect(r.poseAt(0).x).toBeCloseTo(22, 3);
    for (let t = 0; t <= r.duration; t += 0.1) {
      const p = r.poseAt(t);
      expect(p.x).toBeGreaterThan(PALLETISER_PICK[0]);
      expect(p.x).toBeLessThan(COLDSTORE.max[0]);
    }
  });
});

test.describe("logisticsVisuals", () => {
  const leeg = deriveLogistics([], NOW);

  test("zonder HU-antwoord is de koelcel 'geen data', niet 'leeg'", () => {
    const v = logisticsVisuals(leeg, false, true);
    expect(v.coldstore.label).toBe("geen data");
    expect(v.palletiser.state).toBe("unknown");
  });

  test("met een leeg antwoord is de koelcel echt leeg", () => {
    expect(logisticsVisuals(leeg, true, true).coldstore.label).toBe("leeg");
  });

  test("een onbereikbare engine maakt alle logistiek verouderd", () => {
    const v = logisticsVisuals(leeg, true, false);
    for (const k of ["palletiser", "coldstore", "dock", "truck"] as const) expect(v[k].state).toBe("stale");
  });
});

test.describe("twee hallen en de voorbereiding", () => {
  const LINES = [
    {
      line_id: "L2",
      name: "Lijn 2",
      hall: "Hal 2",
      uns_root: "DairyWorks/Vla-L2",
      recipes: [{ recipe_id: "R-200", product_name: "Vla 1L", fg_material_id: "FG-200", flavour: "aroma-banaan", rank: 1 }],
      equipment: {},
      autopilot: null,
    },
  ] as unknown as ProductLine[];

  const set = (over: Partial<PrepSet>): PrepSet => ({
    prep_id: "PS-1",
    line: "L2",
    recipe_id: "vanilla-vla-1L",
    order_id: "SO-1",
    batch_id: null,
    weigh_station: "weigh-station-01",
    premix_tank: "premix-tank-01",
    state: "afwegen",
    components: [{ material_id: "vanilla-aroma", qty_kg: 2.5, uom: "kg" }],
    created_at: "2026-10-01T11:00:00Z",
    state_changed_at: "2026-10-01T11:30:00Z",
    history: [],
    ...over,
  });

  test("L2 heeft dezelfde vijf machines met -02, binnen hal 2", () => {
    expect(LINE2_EQUIPMENT.map((e) => e.id)).toEqual(LINE_EQUIPMENT.map((e) => e.id.replace(/-01$/, "-02")));
    for (const e of LINE2_EQUIPMENT) {
      expect(lineOfEquipment(e.id)).toBe("L2");
      expect(e.pos[0]).toBeGreaterThan(HALL2.min[0]);
      expect(e.pos[0]).toBeLessThan(HALL2.max[0]);
      expect(e.pos[2]).toBeGreaterThan(HALL2.min[1]);
      expect(e.pos[2]).toBeLessThan(HALL2.max[1]);
    }
    for (const e of LINE_EQUIPMENT) expect(lineOfEquipment(e.id)).toBe("L1");
  });

  test("de voorbereiding staat west van beide hallen, de silo's daar weer west van", () => {
    expect(PREP_ROOM.max[0]).toBeLessThan(HALL.min[0]);
    expect(PREP_ROOM.max[0]).toBeLessThan(HALL2.min[0]);
    for (const s of SILO_SLOTS) expect(s[0] + 1.6).toBeLessThan(PREP_ROOM.min[0]);
    for (const st of PREP_STATIONS) {
      expect(st.pos[0]).toBeGreaterThan(PREP_ROOM.min[0]);
      expect(st.pos[0]).toBeLessThan(PREP_ROOM.max[0]);
      expect(st.pos[2]).toBeGreaterThan(PREP_ROOM.min[1]);
      expect(st.pos[2]).toBeLessThan(PREP_ROOM.max[1]);
    }
    expect(PREP_STATIONS.filter((s) => s.kind === "weigh")).toHaveLength(2);
    expect(PREP_STATIONS.filter((s) => s.kind === "premix")).toHaveLength(2);
  });

  test("elke smaak en elke prep-state heeft een kleur in het palet", () => {
    const keys = new Set<string>(TWIN_KEYS);
    for (const f of FLAVOURS) expect(keys.has(flavourKey(f))).toBe(true);
    for (const st of ["gepland", "afwegen", "premix", "gereed", "verbruikt", "vrij"] as const) expect(keys.has(prepColorKey(st))).toBe(true);
  });

  test("smaak uit recept-id, receptdefinitie of productnaam, met chocolade als terugval", () => {
    expect(parseFlavour("strawberry-vla-1L")).toBe("strawberry");
    expect(parseFlavour("Karamelvla")).toBe("caramel");
    expect(parseFlavour(null)).toBeNull();
    expect(flavourOfRecipe("vanilla-vla-1L")).toBe("vanilla");
    expect(flavourOfRecipe("R-200", LINES)).toBe("banana");
    expect(flavourOfRecipe("onbekend", LINES)).toBe("chocolate");
    const map = batchFlavours(
      [
        { batch_id: "B1", recipe_id: "chocolate-vla-1L" },
        { batch_id: "B2", recipe_id: "R-200" },
        { batch_id: "B3", product_name: "Aardbeienvla" },
      ],
      LINES,
    );
    expect(map).toEqual({ B1: "chocolate", B2: "banana", B3: "strawberry" });
    expect(flavourOfBatch("B2", map)).toBe("banana");
    expect(flavourOfBatch("B9", map)).toBe("chocolate");
  });

  test("een station toont de set die de engine erop meldt", () => {
    const stations: PrepStation[] = [
      { station_id: "weigh-station-01", kind: "weigh", line: "L2", state: "bezig", prep_id: "PS-2", recipe_id: "caramel-vla-1L" },
    ];
    const sets = [set({ prep_id: "PS-1" }), set({ prep_id: "PS-2", recipe_id: "caramel-vla-1L" })];
    const v = prepStationView({ id: "weigh-station-01", kind: "weigh" }, stations, sets);
    expect(v.set?.prep_id).toBe("PS-2");
    expect(v.flavour).toBe("caramel");
    expect(prepVisual(v, true)).toMatchObject({ state: "run", label: "afwegen" });
  });

  test("zonder stationsmelding: de nieuwste bezettende set, en vrij als er niets is", () => {
    const sets = [
      set({ prep_id: "PS-1", state: "gereed", state_changed_at: "2026-10-01T11:00:00Z" }),
      set({ prep_id: "PS-2", state: "premix", state_changed_at: "2026-10-01T11:40:00Z" }),
      set({ prep_id: "PS-3", state: "verbruikt", state_changed_at: "2026-10-01T11:50:00Z" }),
    ];
    const premix = prepStationView({ id: "premix-tank-01", kind: "premix" }, [], sets);
    expect(premix.set?.prep_id).toBe("PS-2");
    const weigh = prepStationView({ id: "weigh-station-01", kind: "weigh" }, [], sets);
    expect(weigh.state).toBe("vrij");
    expect(prepVisual(weigh, true)).toMatchObject({ state: "idle", label: "vrij" });
    // De engine meldt het station uitdrukkelijk vrij: dan geen gok uit de sets.
    const vrij: PrepStation[] = [{ station_id: "premix-tank-01", kind: "premix", line: "L2", state: "vrij", prep_id: null, recipe_id: null }];
    expect(prepStationView({ id: "premix-tank-01", kind: "premix" }, vrij, sets).state).toBe("vrij");
  });

  test("zonder engine is een station geen data, nooit vrij", () => {
    const v = prepStationView({ id: "premix-tank-02", kind: "premix" }, [], []);
    expect(prepVisual(v, false)).toMatchObject({ state: "unknown", label: "geen data" });
    expect(prepVisual({ state: "gereed", set: set({ state: "gereed" }), flavour: "vanilla" }, true).state).toBe("idle");
  });
});
