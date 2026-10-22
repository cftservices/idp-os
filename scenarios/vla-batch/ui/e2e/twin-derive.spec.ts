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
import { COLDSTORE, MAIN_AISLE_Z, MAX_DOCKS, PALLETISER_PICK, RACK_SLOTS, dockPosition, rackSlotPosition } from "../lib/twin/layout";
import { CARRY_OFFSET, PARK_LOADER, PARK_PUTAWAY, loadRoute, putawayRoute } from "../lib/twin/forklift";

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
    expect(l.docks.map((d) => d.batch_id)).toEqual(["B0", "B1", "B2", "B3"]);
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
      expect(r.poseAt(r.duration).z).toBeCloseTo(MAIN_AISLE_Z, 3);
      expect(r.duration, "een rit moet ongeveer in het putaway-ritme passen").toBeLessThan(14);
    }
  });

  test("bij het oppakken staat de vork onder de pallet op de palletiser", () => {
    const r = putawayRoute(PARK_PUTAWAY, 20);
    const p = r.poseAt(r.grabAt!);
    expect(p.x + Math.sin(p.rot) * CARRY_OFFSET).toBeCloseTo(PALLETISER_PICK[0], 3);
    expect(p.z + Math.cos(p.rot) * CARRY_OFFSET).toBeCloseTo(PALLETISER_PICK[1], 3);
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
