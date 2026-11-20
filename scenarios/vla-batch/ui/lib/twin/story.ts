/**
 * Story mode for the livestream (/site?broadcast=1&story=1): the camera follows the product
 * through the plant, stop by stop, with an English caption for engineers (PLC/SCADA and data
 * engineers, the YouTube audience).
 *
 * Every stop has a fixed explanation and a set of live lines from the twin model. Facts in the
 * fixed text come from demos/vla-batch/PROCESS.md and the layout; numbers that change (setpoint,
 * temperatures, levels, counts) are always read live, never typed in here.
 *
 * Roughly six minutes per round: intro 20 s, then 25 s per stop.
 */
import type { TwinEquipment, TwinModel } from "./useTwinData";
import {
  CAMERA_TARGET,
  COLDSTORE,
  HALL2_Z,
  MAX_DOCKS,
  PALLETISER,
  PREP_STATIONS,
  RACK_SLOTS,
  SILO_SLOTS,
  dockPosition,
  type Vec3,
} from "./layout";

export type StoryLine = [label: string, value: string];

export type StoryStop = {
  id: string;
  /** Short heading, e.g. "Step 3 · Cooking and pasteurising". */
  title: string;
  /** What happens here; fixed text. */
  text: string;
  /** Point the camera orbits during this stop. */
  target: (m: TwinModel) => Vec3;
  /** Camera distance to the target (OrbitControls clamps to 18 … 200). */
  distance: number;
  seconds: number;
  live: (m: TwinModel) => StoryLine[];
};

const DASH = "—";

const LABEL_EN: Record<string, string> = {
  Niveau: "Level",
  Tankniveau: "Tank level",
  Temperatuur: "Temperature",
  Vetgehalte: "Fat content",
  Roerder: "Agitator",
  Viscositeit: "Viscosity",
  Setpoint: "Setpoint",
  Hold: "Hold time",
  Doel: "Target",
  Pakken: "Packs",
  Afgekeurd: "Rejected",
  Pakgrootte: "Pack size",
};
const UNIT_EN: Record<string, string> = { tpm: "rpm" };

const STATE_EN: Record<string, string> = {
  IDLE: "idle",
  DOSING: "dosing",
  COOKING: "cooking",
  COOLING: "cooling",
  FILLING: "filling",
  COMPLETE: "complete",
};

function num(v: number | null | undefined, digits = 1): string {
  if (v == null || !Number.isFinite(v)) return DASH;
  return v.toLocaleString("en-GB", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
}

function withUnit(v: number | null | undefined, unit: string, digits = 1): string {
  const n = num(v, digits);
  if (n === DASH) return n;
  const u = UNIT_EN[unit] ?? unit;
  return u ? `${n} ${u}` : n;
}

/** Where a batch is, per batch state: the machine of that step on line 1 or line 2. */
const STATE_MACHINE: Record<string, string> = {
  DOSING: "process-tank",
  COOKING: "cook-unit",
  COOLING: "cooler",
  FILLING: "filler",
};
const MACHINE_EN: Record<string, string> = {
  "receiving-tank": "receiving tank",
  "process-tank": "mixing tank",
  "cook-unit": "cook unit",
  cooler: "cooler",
  filler: "filler",
};

/** The machine the batch of a line is in right now, or null when no batch is in a process step. */
function activeMachine(m: TwinModel, line: 1 | 2): string | null {
  const b = line === 1 ? m.batch : m.line2?.batch ?? null;
  const base = b ? STATE_MACHINE[b.state] : undefined;
  return base ? `${base}-0${line}` : null;
}

function machineName(id: string): string {
  return MACHINE_EN[id.replace(/-0\d$/, "")] ?? id;
}

function equipment(m: TwinModel, id: string): TwinEquipment | undefined {
  return m.equipment.find((e) => e.id === id) ?? m.line2?.equipment.find((e) => e.id === id);
}

function posOf(m: TwinModel, id: string, fallback: Vec3): Vec3 {
  return equipment(m, id)?.pos ?? fallback;
}

/** The live values of one machine, with English labels. */
function machineLines(m: TwinModel, id: string): StoryLine[] {
  const e = equipment(m, id);
  const live = e?.live;
  if (!e || !live) return [["Live data", "not available"]];
  const out: StoryLine[] = [];
  const line = /-02$/.test(id) ? 2 : 1;
  const batch = line === 1 ? m.batch : m.line2?.batch ?? null;
  if (batch && activeMachine(m, line) === id) out.push(["Batch here now", batch.batch_id]);
  const p = live.primary;
  if (p) out.push([LABEL_EN[p.label] ?? p.label, withUnit(p.value, p.unit)]);
  for (const s of live.secondary ?? []) {
    if (s.value == null) continue;
    out.push([LABEL_EN[s.label] ?? s.label, withUnit(s.value, s.unit)]);
  }
  if (e.alarms.length > 0) out.push(["Open alarms", String(e.alarms.length)]);
  return out.slice(0, 4);
}

function batchLine(label: string, b: TwinModel["batch"]): StoryLine {
  return [label, b ? `${b.batch_id} · ${STATE_EN[b.state] ?? b.state.toLowerCase()}` : "no batch running"];
}

function centre(points: readonly Vec3[]): Vec3 {
  const n = points.length || 1;
  const s = points.reduce((a, p) => [a[0] + p[0], a[1] + p[1], a[2] + p[2]], [0, 0, 0]);
  return [s[0] / n, s[1] / n, s[2] / n];
}

const STOP_S = 25;

export const STORY: StoryStop[] = [
  {
    id: "intro",
    title: "DairyWorks · a vla plant on an open-source data layer",
    text:
      "A simulated chocolate-custard (vla) plant: raw materials, a weighing and premix room, two process lines, " +
      "a cold store and a truck yard. Everything moving here is driven live by the data layer: OPC UA from the " +
      "line, MonsterMQ as the Unified Namespace with ISA-95 context, TDengine as historian.",
    target: () => CAMERA_TARGET,
    distance: 78,
    seconds: 20,
    live: (m) => [
      batchLine("Line 1", m.batch),
      batchLine("Line 2", m.line2?.batch ?? null),
      ["Open alarms", String(m.alarmsOpen.length)],
    ],
  },
  {
    id: "silos",
    title: "Raw materials",
    text:
      "Bulk ingredients wait in silos. Tankers and bulk trucks deliver milk and dry goods; every delivery " +
      "is booked into inventory, so stock levels are data, not guesses.",
    target: () => centre(SILO_SLOTS),
    distance: 36,
    seconds: STOP_S,
    live: (m) =>
      m.silos.slice(0, 4).map((s): StoryLine => [s.material.name, s.fill == null ? DASH : `${Math.round(s.fill * 100)}% full`]),
  },
  {
    id: "prep",
    title: "Weighing and premix",
    text:
      "Minor ingredients are weighed per batch at two weighing stations and pre-dispersed in a premix tank. " +
      "From there the premix is pumped to the mixing tank of line 1 or line 2.",
    target: () => centre(PREP_STATIONS.map((s) => s.pos)),
    distance: 20,
    seconds: STOP_S,
    live: (m) =>
      (m.prep ?? []).slice(0, 4).map((st): StoryLine => {
        const nr = st.id.match(/(\d+)$/)?.[1] ?? "";
        const name = `${st.kind === "weigh" ? "Weighing station" : "Premix tank"} ${Number(nr) || ""}`.trim();
        const set = st.view.set;
        return [name, set ? `${set.line} · ${st.view.flavour ?? set.recipe_id}` : "idle"];
      }),
  },
  {
    id: "receiving",
    title: "Step 1 · Receiving",
    text: "Milk is buffered in the receiving tank and standardised: the fat content is set before the batch starts.",
    target: (m) => posOf(m, "receiving-tank-01", [-11, 0, 0]),
    distance: 19,
    seconds: STOP_S,
    live: (m) => machineLines(m, "receiving-tank-01"),
  },
  {
    id: "mixing",
    title: "Step 2 · Dosing and mixing",
    text:
      "Milk, sugar, starch and cocoa are dosed into the process tank. The agitator mixes them into a " +
      "homogeneous slurry. Each dosing is recorded per ingredient in the batch record.",
    target: (m) => posOf(m, "process-tank-01", [-6, 0, 0]),
    distance: 19,
    seconds: STOP_S,
    live: (m) => machineLines(m, "process-tank-01"),
  },
  {
    id: "cooking",
    title: "Step 3 · Cooking and pasteurising",
    text:
      "The quality step. The mix is heated to the recipe setpoint and held: the starch gelatinises, so the " +
      "custard thickens, and the product is pasteurised. Viscosity is the derived quality signal.",
    target: (m) => posOf(m, "cook-unit-01", [-1, 0, 0]),
    distance: 19,
    seconds: STOP_S,
    live: (m) => machineLines(m, "cook-unit-01"),
  },
  {
    id: "cooling",
    title: "Step 4 · Cooling",
    text: "The hot product is cooled back to filling temperature before it reaches the filler.",
    target: (m) => posOf(m, "cooler-01", [4, 0, 0]),
    distance: 19,
    seconds: STOP_S,
    live: (m) => machineLines(m, "cooler-01"),
  },
  {
    id: "filling",
    title: "Step 5 · Filling",
    text: "The custard is filled into 1-litre packs and counted per batch. Rejected packs are counted separately.",
    target: (m) => posOf(m, "filler-01", [9, 0, 0]),
    distance: 19,
    seconds: STOP_S,
    live: (m) => {
      const lines = machineLines(m, "filler-01");
      const pct = m.filling?.packs_progress_pct;
      if (pct != null) lines.unshift(["Batch filled", `${num(pct, 0)}%`]);
      return lines.slice(0, 4);
    },
  },
  {
    id: "follow-l1",
    title: "Follow the batch · line 1",
    text:
      "Where is the batch right now? The batch state comes from the line over OPC UA; the camera goes to the " +
      "machine of that step. The ISA-95 model links batch, equipment and live values.",
    target: (m) => {
      const id = activeMachine(m, 1);
      return id ? posOf(m, id, CAMERA_TARGET) : posOf(m, "cook-unit-01", [-1, 0, 0]);
    },
    distance: 19,
    seconds: STOP_S,
    live: (m) => {
      const id = activeMachine(m, 1);
      if (!m.batch || !id) return [batchLine("Line 1", m.batch), ["Machine", "no batch in a process step"]];
      return [
        ["Batch", m.batch.batch_id],
        ["Step", `${STATE_EN[m.batch.state] ?? m.batch.state} · ${machineName(id)}`],
        ...machineLines(m, id).filter(([k]) => k !== "Batch here now").slice(0, 2),
      ];
    },
  },
  {
    id: "palletiser",
    title: "Palletiser",
    text:
      "Packs are stacked and wrapped into pallets. Every pallet becomes a handling unit with its own ID, so it " +
      "can be traced from batch to customer.",
    target: () => PALLETISER,
    distance: 20,
    seconds: STOP_S,
    live: (m) => [
      ["Pallets wrapped", num(m.logistics.totals.wrapped, 0)],
      ["Waiting at line 1", num(m.logistics.palletisers.L1?.length ?? null, 0)],
      ["Waiting at line 2", num(m.logistics.palletisers.L2?.length ?? null, 0)],
    ],
  },
  {
    id: "coldstore",
    title: "Cold store",
    text:
      "Forklifts move each pallet into the cold store. The warehouse knows every rack position, so stock and " +
      "shelf position are always live.",
    target: () => [(COLDSTORE.min[0] + COLDSTORE.max[0]) / 2, 0, (COLDSTORE.min[1] + COLDSTORE.max[1]) / 2],
    distance: 38,
    seconds: STOP_S,
    live: (m) => [
      ["Rack positions used", `${m.logistics.rack.length} / ${RACK_SLOTS}`],
      ["Packs in store", num(m.logistics.totals.packsStored, 0)],
      ["Pallets shipped", num(m.logistics.totals.shipped, 0)],
    ],
  },
  {
    id: "docks",
    title: "Docks and trucks",
    text:
      "Trucks dock, get loaded pallet by pallet and leave for the customer. Each order is tracked from " +
      "order to delivery, which is where on-time-in-full comes from.",
    target: () => dockPosition((MAX_DOCKS - 1) / 2),
    distance: 26,
    seconds: STOP_S,
    live: (m) => {
      const ships = m.sim?.shipments ?? [];
      return [
        ["Docks in use", `${m.logistics.docks.length} / ${MAX_DOCKS}`],
        ["Trucks loading", String(ships.filter((s) => s.stage === "loading").length)],
        ["Trucks on the road", String(ships.filter((s) => s.stage === "en_route").length)],
      ];
    },
  },
  {
    id: "line2",
    title: "Hall 2 · Line 2, following its batch",
    text:
      "Line 2 runs the same five steps with the same ISA-95 structure: connecting a second line is " +
      "configuration, not a new project. The camera follows the batch of line 2 to its current machine.",
    target: (m) => {
      const id = activeMachine(m, 2);
      return id ? posOf(m, id, [-1, 0, HALL2_Z]) : posOf(m, "cook-unit-02", [-1, 0, HALL2_Z]);
    },
    distance: 21,
    seconds: STOP_S,
    live: (m) => {
      const id = activeMachine(m, 2);
      const b = m.line2?.batch ?? null;
      return [
        ["Product", m.line2?.flavour ?? DASH],
        b && id ? ["Batch", `${b.batch_id} · ${STATE_EN[b.state] ?? b.state} · ${machineName(id)}`] : batchLine("Batch", b),
        ...(id ? machineLines(m, id).filter(([k]) => k !== "Batch here now").slice(0, 2) : []),
      ];
    },
  },
  {
    id: "outro",
    title: "One data layer, many consumers",
    text:
      "The same data feeds the KPIs (ISO 22400), the historian, Grafana dashboards, batch reports and local AI. " +
      "Next up: the KPI screens and the Grafana dashboards.",
    target: () => CAMERA_TARGET,
    distance: 78,
    seconds: STOP_S,
    live: (m) =>
      m.kpis
        .filter((k) => k.value != null)
        .slice(0, 4)
        .map((k): StoryLine => [k.name, withUnit(k.value, k.unit === "%" ? "%" : k.unit, 1)]),
  },
];

export const STORY_SECONDS = STORY.reduce((s, x) => s + x.seconds, 0);
