"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BATCH_STEPS, batchStepIndex, labelBudget, siteChip } from "@/lib/twin/flow";
import { useSeries } from "@/lib/twin/history";
import { useTwinPalette } from "@/lib/twin/palette";
import { useTwinData } from "@/lib/twin/useTwinData";
import { useOrders } from "@/lib/queries";
import { num } from "@/lib/format";
import type { TwinCtx } from "./context";
import { CameraControls, DetailCard, KpiCards, TabPanel, Timeline, TopBar, type ActiveOrder } from "./overlay";
import { describe, listItems, type TwinGroup } from "./selection";
import { DatalayerPanel, type DlTab } from "./datalayer";
import { TOURS, TourBar } from "./tour";
import type { LensMarker } from "./objects/LensMarkers";
import { useQualityMap, useWorkOrders } from "@/lib/twin/datalayer";

const SiteScene = dynamic(() => import("./SiteScene"), {
  ssr: false,
  loading: () => <SceneHolder text="3D-scène laden" />,
});

function SceneHolder({ text }: { text: string }) {
  return (
    <div className="twin-holder" role="status">
      {text}
    </div>
  );
}

function useReducedMotion() {
  const [v, setV] = useState(false);
  useEffect(() => {
    const q = window.matchMedia("(prefers-reduced-motion: reduce)");
    const on = () => setV(q.matches);
    on();
    q.addEventListener("change", on);
    return () => q.removeEventListener("change", on);
  }, []);
  return v;
}

function hasWebGL(): boolean {
  try {
    const c = document.createElement("canvas");
    return !!(c.getContext("webgl2") || c.getContext("webgl"));
  } catch {
    return false;
  }
}

const BATCH_WORD: Record<string, string> = {
  IDLE: "stil",
  DOSING: "doseren",
  COOKING: "koken",
  COOLING: "koelen",
  FILLING: "vullen",
  COMPLETE: "gereed",
};

/** Per fase het getal dat ertoe doet, in plaats van overal "vullen 0%". */
const PHASE_EQUIPMENT: Record<string, string> = {
  DOSING: "process-tank-01",
  COOKING: "cook-unit-01",
  COOLING: "cooler-01",
};

const BATCH_FOLLOW = "batch";
/** Rijdende objecten die de camera kan volgen. */
const FOLLOWABLE = /^(forklift|shipment|inbound):/;
function batchTarget(state: string | undefined): string | null {
  if (!state) return null;
  if (state === "FILLING") return "equipment:filler-01";
  const eq = PHASE_EQUIPMENT[state];
  return eq ? `equipment:${eq}` : "palletiser";
}

function phaseText(m: ReturnType<typeof useTwinData>): string {
  const state = m.batch?.state;
  if (!state) return "";
  if (state === "FILLING") {
    const p = m.filling?.packs_progress_pct;
    if (p != null && p > 0) return `vullen ${Math.round(p)}%`;
    const total = m.filling?.packs_total;
    return total ? `vullen · ${num(total, 0)} packs` : p != null ? `vullen ${Math.round(p)}%` : "";
  }
  const eq = m.equipment.find((e) => e.id === PHASE_EQUIPMENT[state]);
  if (!eq?.live) return "";
  const parts: string[] = [];
  const val = (label: string) => {
    const all = [eq.live?.primary, ...(eq.live?.secondary ?? [])];
    return all.find((x) => x?.label === label) ?? null;
  };
  const fmt = (x: { value: number | null; unit: string } | null) => (x && x.value != null ? `${num(x.value)} ${x.unit}`.trim() : null);
  if (state === "DOSING") parts.push(fmt(val("Tankniveau")) ?? "");
  if (state === "COOKING") {
    parts.push(fmt(val("Temperatuur")) ?? "");
    const hold = val("Hold");
    if (hold?.value != null) parts.push(`hold ${num(hold.value, 0)} s`);
  }
  if (state === "COOLING") {
    const t = fmt(val("Temperatuur"));
    const d = fmt(val("Doel"));
    parts.push(t && d ? `${t} → ${d}` : t ?? "");
  }
  return parts.filter(Boolean).join(" · ");
}

export default function SiteTwin() {
  const model = useTwinData();
  const palette = useTwinPalette();
  const reducedMotion = useReducedMotion();
  const series = useSeries(90);
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [presenting, setPresenting] = useState(false);
  const [resetSignal, setResetSignal] = useState(0);
  const [zoom, setZoom] = useState<{ n: number; dir: 1 | -1 }>({ n: 0, dir: 1 });
  const [tab, setTab] = useState<TwinGroup>("Lijn");
  const [drawer, setDrawer] = useState(false);
  const [webgl, setWebgl] = useState<boolean | null>(null);
  const [, setTick] = useState(0);
  const [follow, setFollow] = useState<string | null>(null);
  const [showPath, setShowPath] = useState(false);
  const [lens, setLens] = useState(false);
  const [dlTab, setDlTab] = useState<DlTab>("bronnen");
  const [tourId, setTourId] = useState<string | null>(null);
  const [tourStep, setTourStep] = useState(0);
  const rootRef = useRef<HTMLElement>(null);

  useEffect(() => setWebgl(hasWebGL()), []);
  useEffect(() => {
    const p = new URLSearchParams(window.location.search);
    const t = p.get("tour");
    if (t && TOURS[t]) setTourId(t);
    if (p.get("lens") === "1") setLens(true);
  }, []);

  const quality = useQualityMap(lens);
  const workOrders = useWorkOrders(lens);
  const lensMarkers: LensMarker[] | null = useMemo(() => {
    if (!lens) return null;
    const q = new Map((quality.data?.equipment ?? []).map((r) => [r.equipment_id, r]));
    const open = (workOrders.data?.work_orders ?? []).filter((w) => w.status !== "done");
    const wo = new Map(open.map((w) => [w.equipment_id, w.wo_id]));
    const down = new Set(open.filter((w) => w.kind === "breakdown").map((w) => w.equipment_id));
    return [...model.equipment, ...model.line2.equipment].map((e) => ({
      id: e.id,
      name: e.name,
      pos: e.pos,
      quality: q.get(e.id)?.quality ?? "NO_DATA",
      wo: wo.get(e.id) ?? null,
      down: down.has(e.id),
    }));
  }, [lens, quality.data, workOrders.data, model.equipment, model.line2.equipment]);

  const items = useMemo(() => listItems(model), [model]);
  const orders = useOrders<ActiveOrder[]>();
  // De klantorder waar de lijn nu aan werkt: RUNNING eerst, anders de eerstvolgende OPEN.
  const order = useMemo(() => {
    const list = Array.isArray(orders.data) ? orders.data : [];
    const due = (o: ActiveOrder) => (o.due_date ? Date.parse(o.due_date) : Infinity);
    return (
      list.find((o) => o.status === "RUNNING") ??
      list.filter((o) => o.status === "OPEN").sort((a, b) => due(a) - due(b))[0] ??
      null
    );
  }, [orders.data]);

  // Historie voor sparklines en delta's. Vult zich in de browser: er is geen historie-endpoint.
  useEffect(() => {
    const v: Record<string, number | null | undefined> = {
      coldstore: model.husAvailable ? model.logistics.totals.stored : null,
      produced: model.produced.packs,
    };
    for (const e of model.equipment) v[`eq:${e.id}`] = e.live?.primary?.value;
    for (const s of model.silos) v[`silo:${s.material.material_id}`] = s.material.stock_qty;
    for (const f of model.sim?.forklifts ?? []) v[`forklift:${f.id}`] = f.battery_pct;
    for (const k of model.kpis) v[`kpi:${k.kpi_id}`] = k.status === "UNSET" ? null : k.value;
    series.record(model.now, v);
    setTick((t) => t + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [model]);

  const select = useCallback(
    (id: string | null) => {
      setSelected(id);
      if (id) {
        const it = items.find((i) => i.id === id);
        if (it) setTab(it.group);
        setDrawer(false);
      }
    },
    [items],
  );

  const togglePresent = useCallback(() => {
    setPresenting((p) => {
      const next = !p;
      const el = rootRef.current;
      if (next && el && !document.fullscreenElement) el.requestFullscreen?.().catch(() => {});
      if (!next && document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
      return next;
    });
  }, []);

  useEffect(() => {
    const onFs = () => {
      if (!document.fullscreenElement) setPresenting(false);
    };
    document.addEventListener("fullscreenchange", onFs);
    return () => document.removeEventListener("fullscreenchange", onFs);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key === "Escape") {
        setSelected(null);
        setDrawer(false);
        setFollow(null);
      }
      if ((e.key === "p" || e.key === "P") && !e.metaKey && !e.ctrlKey && !e.altKey) togglePresent();
      if ((e.key === "d" || e.key === "D") && !e.metaKey && !e.ctrlKey && !e.altKey) setLens((l) => !l);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePresent]);

  // Een selectie die uit de data verdwijnt (truck vertrokken), sluit het paneel.
  useEffect(() => {
    if (selected && !describe(selected, model)) setSelected(null);
  }, [selected, model]);

  // Volgen: een rijdend object, of "batch" = het apparaat waar de batch nu zit.
  const followId = follow === BATCH_FOLLOW ? batchTarget(model.batch?.state) : follow;
  useEffect(() => {
    if (follow && follow !== BATCH_FOLLOW && !describe(follow, model)) setFollow(null);
    if (follow === BATCH_FOLLOW && !model.batch) setFollow(null);
  }, [follow, model]);

  const labelled = useMemo(() => labelBudget(items, selected, hovered, lens ? 0 : 6), [items, selected, hovered, lens]);
  const meta = useMemo(() => Object.fromEntries(items.map((i) => [i.id, i.meta])), [items]);

  const ctx: TwinCtx | null = useMemo(
    () =>
      palette ? { palette, selected, hovered, select, hover: setHovered, reducedMotion, labelled, meta } : null,
    [palette, selected, hovered, select, reducedMotion, labelled, meta],
  );

  const tour = tourId ? TOURS[tourId] : null;
  const goStep = useCallback(
    (n: number) => {
      if (!tourId) return;
      const st = TOURS[tourId].steps[n];
      if (!st) return;
      setTourStep(n);
      setLens(true);
      if (st.tab) setDlTab(st.tab);
      if (st.select !== undefined) setSelected(st.select);
    },
    [tourId],
  );
  useEffect(() => {
    if (tourId) goStep(0);
  }, [tourId, goStep]);

  const batch = model.batch;
  const docksInUse = model.sim ? model.sim.shipments.filter((s) => s.stage === "loading").length : model.logistics.docks.length;
  const chip = siteChip({ stored: model.husAvailable ? model.logistics.totals.stored : null, docksInUse });
  const get = (k: string) => series.get(k);

  return (
    <main ref={rootRef} className="twin-root" data-twin-root="" data-presenting={presenting ? "" : undefined}>
      <div className="twin-stage">
        {webgl === false ? (
          <SceneHolder text="Deze browser ondersteunt geen WebGL. De objectlijst toont dezelfde status." />
        ) : ctx && webgl ? (
          <SiteScene
            model={model}
            ctx={ctx}
            presenting={presenting}
            resetSignal={resetSignal}
            zoom={zoom}
            follow={followId}
            order={order}
            showPath={showPath}
            lens={lensMarkers}
          />
        ) : (
          <SceneHolder text="3D-scène laden" />
        )}
      </div>

      <TopBar
        m={model}
        items={items}
        chip={chip}
        onPick={select}
        presenting={presenting}
        onPresent={togglePresent}
        onDrawer={() => setDrawer((d) => !d)}
        drawerOpen={drawer}
        lens={lens}
        onLens={() => setLens((l) => !l)}
      />

      <KpiCards m={model} series={get} order={order} showPath={showPath} onTogglePath={() => setShowPath((s) => !s)} />

      {lens ? (
        <DatalayerPanel tab={dlTab} onTab={setDlTab} onSelect={select} onClose={() => setLens(false)} />
      ) : (
      <TabPanel
        items={items}
        tab={tab}
        onTab={setTab}
        selected={selected}
        hovered={hovered}
        onSelect={select}
        onHover={setHovered}
        open={drawer}
        simOn={!!model.sim}
      />
      )}

      {tour && (
        <TourBar
          tour={tour}
          step={tourStep}
          onStep={goStep}
          onClose={() => {
            setTourId(null);
            setTourStep(0);
          }}
        />
      )}

      {selected && (
        <DetailCard
          id={selected}
          m={model}
          series={get}
          onClose={() => setSelected(null)}
          following={follow === selected}
          onFollow={FOLLOWABLE.test(selected) ? () => setFollow((f) => (f === selected ? null : selected)) : undefined}
          onSelect={select}
        />
      )}

      <Timeline
        title={
          <span data-twin-batch={batch?.state ?? ""}>
            {batch ? (
              <>
                Batch <span className="mono">{batch.batch_id}</span> · {BATCH_WORD[batch.state] ?? batch.state.toLowerCase()}
              </>
            ) : (
              <>Geen actieve batch</>
            )}
          </span>
        }
        right={phaseText(model)}
        steps={BATCH_STEPS}
        index={batchStepIndex(batch?.state, model.hus, batch?.batch_id)}
        following={follow === BATCH_FOLLOW}
        onFollow={batch ? () => setFollow((f) => (f === BATCH_FOLLOW ? null : BATCH_FOLLOW)) : undefined}
      />

      <CameraControls
        onZoom={(dir) => setZoom((z) => ({ n: z.n + 1, dir }))}
        onReset={() => {
          setFollow(null);
          setResetSignal((n) => n + 1);
        }}
      />
    </main>
  );
}
