import { test, expect } from "@playwright/test";
import { orderBatchIds, orderPathPoints, orderStops } from "../lib/twin/orderPath";
import type { TwinModel } from "../lib/twin/useTwinData";

/** De haltes en de lijn van het orderpad, zonder browser. */

const hu = (batch_id: string) => ({ hu_id: "H", batch_id, packs_count: 1, location: null, status: "stored", ts: "" });

function model(): TwinModel {
  return {
    batch: { batch_id: "B2", state: "FILLING" },
    logistics: {
      docks: [{ dock: 2, batch_id: "B1", loaded: [hu("B1")], waiting: [hu("B1"), hu("B1")] }],
      rack: [{ hu: hu("B1") }, { hu: hu("B1") }, { hu: hu("X") }],
      palletiser: [hu("B2")],
    },
    sim: {
      shipments: [
        { batch_id: "B1", stage: "en_route" },
        { batch_id: "B1", stage: "delivered" },
        { batch_id: "X", stage: "en_route" },
      ],
    },
  } as unknown as TwinModel;
}

test("zonder batch_ids valt het orderpad terug op de lopende batch", () => {
  expect([...orderBatchIds({ order_id: "PO" }, model())]).toEqual(["B2"]);
  expect([...orderBatchIds({ order_id: "PO", progress: { batch_ids: ["B1"] } }, model())]).toEqual(["B1"]);
});

test("haltes tellen alleen wat bij de order hoort", () => {
  const stops = orderStops({ order_id: "PO", progress: { batch_ids: ["B1", "B2"] } }, model());
  expect(stops.map((s) => s.key)).toEqual(["batch", "palletiser", "coldstore", "dock", "customer"]);
  const v = Object.fromEntries(stops.map((s) => [s.key, s.value]));
  expect(v.batch).toBe("vullen");
  expect(v.palletiser).toBe("1 HU");
  expect(v.coldstore).toBe("2 HU");
  expect(v.dock).toBe("1/3 HU");
  expect(stops[3].label).toBe("Dock 3");
  expect(v.customer).toBe("1 onderweg · 1 geleverd");
});

test("de lijn loopt via het dock van de order naar de uitgang", () => {
  const stops = orderStops({ order_id: "PO", progress: { batch_ids: ["B1"] } }, model());
  const pts = orderPathPoints(stops);
  expect(pts).toContainEqual(stops[3].at);
  expect(pts[pts.length - 1]).toEqual(stops[4].at);
  expect(pts[0]).toEqual(stops[0].at);
});
