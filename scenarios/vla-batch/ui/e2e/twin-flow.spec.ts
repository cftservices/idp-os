import { test, expect } from "@playwright/test";
import {
  BATCH_STEPS,
  asSimLogistics,
  batchStepIndex,
  departPose,
  labelBudget,
  needsPin,
  searchItems,
  shipStepIndex,
  shipmentProgress,
  siteChip,
  tankerPose,
  type SimInbound,
  type SimShipment,
} from "../lib/twin/flow";
import { Ring, delta, sparkPath } from "../lib/twin/history";
import type { Hu, Visual, VisualState } from "../lib/twin/derive";

/**
 * De pure regels achter de WareTrack-overlay: tijdlijnen, voortgang,
 * labelvoorrang, zoeken, rijpaden en de sparkline-historie. Zonder browser.
 */

const vis = (state: VisualState): Visual => ({ state, shape: "●", label: state, reasons: [] });
const T0 = Date.parse("2026-10-01T10:00:00Z");

function ship(p: Partial<SimShipment>): SimShipment {
  return {
    shipment_id: "S1",
    truck_id: "TR-01",
    batch_id: "B1",
    customer: "Klant",
    destination: "Stad",
    drive_min: 30,
    stage: "order",
    pallets: 4,
    pallets_loaded: 0,
    packs: 160,
    ordered_at: new Date(T0).toISOString(),
    loaded_at: null,
    eta: null,
    ...p,
  };
}

test("batchtijdlijn volgt de lijnstatus en daarna de HU's", () => {
  expect(batchStepIndex(null, [], null)).toBe(-1);
  expect(batchStepIndex("COOKING", [], "B1")).toBe(1);
  expect(batchStepIndex("COMPLETE", [], "B1")).toBe(4);
  const hu = (status: string): Hu => ({ hu_id: "H", batch_id: "B1", packs_count: 1, location: null, status, ts: "" }) as Hu;
  expect(batchStepIndex("COMPLETE", [hu("wrapped")], "B1")).toBe(5);
  expect(batchStepIndex("COMPLETE", [hu("shipped"), hu("shipped")], "B1")).toBe(BATCH_STEPS.length - 1);
  expect(batchStepIndex("COMPLETE", [hu("shipped")], "ANDERE")).toBe(4);
});

test("zendingvoortgang: pallets bij laden, rijtijd onderweg", () => {
  expect(shipStepIndex("order")).toBe(1);
  expect(shipStepIndex(null)).toBe(-1);
  expect(shipmentProgress(ship({ stage: "loading", pallets_loaded: 1 }), T0)).toBeCloseTo(0.25);
  const onderweg = ship({
    stage: "en_route",
    loaded_at: new Date(T0).toISOString(),
    eta: new Date(T0 + 1000_000).toISOString(),
  });
  expect(shipmentProgress(onderweg, T0 + 500_000)).toBeCloseTo(0.5);
  expect(shipmentProgress(onderweg, T0 + 9e9)).toBe(1);
  expect(shipmentProgress(ship({ stage: "en_route" }), T0)).toBe(0);
});

test("een vertrekkende truck is alleen zichtbaar aan het begin van de rit", () => {
  const s = ship({ stage: "en_route", loaded_at: new Date(T0).toISOString(), eta: new Date(T0 + 1000_000).toISOString() });
  expect(departPose(s, T0 + 10_000, 0, 20).visible).toBe(true);
  expect(departPose(s, T0 + 500_000, 0, 20).visible).toBe(false);
  expect(departPose(ship({ stage: "loading" }), T0, 0, 20).visible).toBe(false);
});

test("tankwagen: rijdt in, staat stil bij lossen, verdwijnt daarna", () => {
  const at = (stage: SimInbound["stage"], leftS: number): SimInbound => ({
    id: "T",
    kind: "melk",
    stage,
    litres: 20000,
    unloaded_pct: 0,
    next_at: new Date(T0 + leftS * 1000).toISOString(),
  });
  expect(tankerPose(null, T0).visible).toBe(false);
  expect(tankerPose(at("en_route", 200), T0).visible).toBe(false);
  expect(tankerPose(at("en_route", 45), T0).visible).toBe(true);
  expect(tankerPose(at("unloading", 100), T0)).toMatchObject({ visible: true, rot: 0 });
  expect(tankerPose(at("gone", 450), T0).visible).toBe(true);
  expect(tankerPose(at("gone", 100), T0).visible).toBe(false);
});

test("elke grondstof lost op zijn eigen plaats; oude API zonder inbounds blijft werken", () => {
  const lossen = (material_id: string): SimInbound => ({
    id: "X",
    kind: "bulkwagen",
    material_id,
    stage: "unloading",
    litres: 0,
    unloaded_pct: 50,
    next_at: new Date(T0 + 60_000).toISOString(),
  });
  const xs = ["milk", "sugar", "starch", "cocoa"].map((m) => tankerPose(lossen(m), T0).x);
  expect(new Set(xs).size).toBe(4);
  const oud = asSimLogistics({ source: "sim", inbound: { id: "T", kind: "melk", stage: "unloading", litres: 1, unloaded_pct: 0, next_at: "" } });
  expect(oud?.inbounds).toHaveLength(1);
  expect(oud?.inbounds[0]).toMatchObject({ material_id: "milk", vehicle: "tanker" });
});

test("labels: geselecteerd eerst, dan alarm; stil nooit; nooit meer dan het budget", () => {
  const items = [
    { id: "a", visual: vis("idle") },
    { id: "b", visual: vis("run") },
    { id: "c", visual: vis("alarm") },
    { id: "d", visual: vis("warn") },
    { id: "e", visual: vis("unknown") },
  ];
  expect([...labelBudget(items, null, null, 6)].sort()).toEqual(["b", "c", "d"]);
  expect([...labelBudget(items, "a", null, 2)]).toEqual(["a", "c"]);
  expect(labelBudget(items, null, null, 1).has("c")).toBe(true);
  expect(needsPin(vis("alarm"))).toBe(true);
  expect(needsPin(vis("run"))).toBe(false);
});

test("zoeken: naam en id, beginmatch eerst, leeg is niets", () => {
  const items = [
    { id: "equipment:cook-unit-01", name: "Kookunit" },
    { id: "silo:sugar", name: "Suiker" },
    { id: "forklift:FL-1", name: "Heftruck FL-1" },
  ];
  expect(searchItems(items, "")).toEqual([]);
  expect(searchItems(items, "cook").map((i) => i.id)).toEqual(["equipment:cook-unit-01"]);
  expect(searchItems(items, "s")[0].name).toBe("Suiker");
  expect(searchItems(items, "e", 1)).toHaveLength(1);
});

test("sitechip toont een streepje zonder koelceldata, nooit een nul", () => {
  expect(siteChip({ stored: null, docksInUse: 1 })).toMatch(/ - koelcel/);
  expect(siteChip({ stored: 0, docksInUse: 0 })).toMatch(/0% koelcel/);
});

test("demo-logistiek zonder bron telt als uit", () => {
  expect(asSimLogistics(null)).toBeNull();
  expect(asSimLogistics({ shipments: [] })).toBeNull();
  expect(asSimLogistics({ source: "demo" })).toMatchObject({ shipments: [], forklifts: [], inbound: null });
});

test("historie: ring weigert terug in de tijd, delta en pad hebben twee punten nodig", () => {
  const r = new Ring(3);
  r.push({ t: 1, v: 10 });
  r.push({ t: 1, v: 99 });
  expect(r.size).toBe(1);
  expect(delta(r.values())).toBeNull();
  expect(sparkPath(r.values(), 100, 20)).toBe("");
  for (const [t, v] of [[2, 12], [3, 8], [4, 14]]) r.push({ t, v });
  expect(r.size).toBe(3);
  expect(delta(r.values())).toBe(2);
  const d = sparkPath(r.values(), 100, 20);
  expect(d.startsWith("M0.0,")).toBe(true);
  expect(d.split("L")).toHaveLength(3);
});
