"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Glyph, StatusPill, statusFromKpi } from "@/components/toolkit/StatusPill";
import { PILL, SHAPE, type Visual, type VisualState } from "@/lib/twin/derive";
import { useTwinPalette } from "@/lib/twin/palette";
import { useTwinData, type TwinModel } from "@/lib/twin/useTwinData";
import { EMPTY, num } from "@/lib/format";
import type { TwinCtx } from "./context";
import { describe, listItems, type TwinGroup } from "./selection";

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

function VisualPill({ v }: { v: Visual }) {
  return <StatusPill status={PILL[v.state]}>{v.label}</StatusPill>;
}

function EngineChip({ reachable }: { reachable: boolean | null }) {
  if (reachable === null) return <StatusPill status="unset">engine: geen data</StatusPill>;
  return reachable ? (
    <StatusPill status="ok">engine live</StatusPill>
  ) : (
    <StatusPill status="stale">engine niet bereikbaar</StatusPill>
  );
}

const KPI_PREFERENCE = ["plan_attainment_pct", "quality_ratio", "throughput_rate", "otif_pct"];

function KpiHud({ m }: { m: TwinModel }) {
  const picked = useMemo(() => {
    const ranked = [...m.kpis].sort((a, b) => {
      const ia = KPI_PREFERENCE.indexOf(a.kpi_id);
      const ib = KPI_PREFERENCE.indexOf(b.kpi_id);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    return ranked.slice(0, 4);
  }, [m.kpis]);

  return (
    <dl className="twin-hud" data-twin-hud="">
      {picked.length === 0 ? (
        <div className="twin-hud-cell">
          <dt className="eyebrow">KPI&apos;s vandaag</dt>
          <dd className="num">{EMPTY}</dd>
          <dd>
            <StatusPill status="unset">geen data</StatusPill>
          </dd>
        </div>
      ) : (
        picked.map((k) => (
          <div key={k.kpi_id} className="twin-hud-cell" data-twin-kpi={k.kpi_id}>
            <dt className="eyebrow">{k.name}</dt>
            <dd className="num">{k.value == null || k.status === "UNSET" ? EMPTY : `${num(k.value)} ${k.unit}`.trim()}</dd>
            <dd>
              <StatusPill status={statusFromKpi(k.status)} />
            </dd>
          </div>
        ))
      )}
      <div className="twin-hud-cell">
        <dt className="eyebrow">Geproduceerd</dt>
        <dd className="num">{m.produced.packs == null ? EMPTY : `${num(m.produced.packs, 0)} packs`}</dd>
      </div>
    </dl>
  );
}

const GROUPS: TwinGroup[] = ["Lijn", "Grondstoffen", "Logistiek"];

function ObjectList({ m, selected, onSelect }: { m: TwinModel; selected: string | null; onSelect: (id: string) => void }) {
  const items = listItems(m);
  return (
    <nav className="twin-list" aria-label="Objecten op de site">
      {GROUPS.map((g) => {
        const rows = items.filter((i) => i.group === g);
        return (
          <section key={g}>
            <h2 className="eyebrow">{g}</h2>
            {rows.length === 0 ? (
              <p className="text-ink-muted">{g === "Grondstoffen" ? "geen data" : EMPTY}</p>
            ) : (
              <ul>
                {rows.map((i) => (
                  <li key={i.id}>
                    <button
                      type="button"
                      className="twin-list-item"
                      aria-pressed={selected === i.id}
                      data-twin-object={i.id}
                      data-twin-state={i.visual.state}
                      onClick={() => onSelect(i.id)}
                    >
                      <span className="twin-list-name">{i.name}</span>
                      <VisualPill v={i.visual} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </nav>
  );
}

function InfoPanel({ id, m, onClose }: { id: string; m: TwinModel; onClose: () => void }) {
  const d = describe(id, m);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true });
  }, [id]);
  if (!d) return null;
  return (
    <aside className="twin-panel tile" data-twin-panel={id} aria-label={`Details ${d.title}`}>
      <header className="twin-panel-head">
        <div>
          <p className="eyebrow">{d.kind}</p>
          <h2 className="twin-panel-title">{d.title}</h2>
        </div>
        <button ref={closeRef} type="button" className="twin-btn" onClick={onClose} aria-label="Sluiten (Escape)">
          ✕
        </button>
      </header>
      <VisualPill v={d.visual} />
      {d.visual.reasons.length > 0 && (
        <ul className="twin-reasons">
          {d.visual.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      <dl className="twin-rows">
        {d.rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd className="num">{v}</dd>
          </div>
        ))}
      </dl>
      <ul className="twin-links">
        {d.links.map((l) => (
          <li key={l.href}>
            <Link href={l.href}>{l.label} →</Link>
          </li>
        ))}
      </ul>
    </aside>
  );
}

const LEGEND: VisualState[] = ["run", "idle", "warn", "alarm", "stale", "unknown"];
const LEGEND_WORD: Record<VisualState, string> = {
  run: "draait",
  idle: "stil",
  warn: "aandacht",
  alarm: "alarm",
  stale: "verouderd",
  unknown: "geen data",
};

function Legend() {
  return (
    <div className="twin-legend" aria-label="Legenda">
      {LEGEND.map((s) => (
        <span key={s} className="twin-legend-item">
          {s === "unknown" ? <span aria-hidden="true">{SHAPE.unknown}</span> : <Glyph status={PILL[s]} />}
          {LEGEND_WORD[s]}
        </span>
      ))}
      <span className="twin-legend-note">Trucks afgeleid uit de HU-status, niet uit een transportsysteem.</span>
      <span className="twin-legend-note">
        In detail: <Link href="/line">Lijn L1</Link> · <Link href="/management">Management</Link> ·{" "}
        <Link href="/voorraad">Voorraad</Link>
      </span>
    </div>
  );
}

export default function SiteTwin() {
  const model = useTwinData();
  const palette = useTwinPalette();
  const reducedMotion = useReducedMotion();
  const [selected, setSelected] = useState<string | null>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const [presenting, setPresenting] = useState(false);
  const [resetSignal, setResetSignal] = useState(0);
  const [webgl, setWebgl] = useState<boolean | null>(null);
  const rootRef = useRef<HTMLElement>(null);

  useEffect(() => setWebgl(hasWebGL()), []);

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
      if (e.key === "Escape") setSelected(null);
      if ((e.key === "p" || e.key === "P") && !e.metaKey && !e.ctrlKey && !e.altKey) togglePresent();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [togglePresent]);

  // Een selectie die uit de data verdwijnt (truck vertrokken), sluit het paneel.
  useEffect(() => {
    if (selected && !describe(selected, model)) setSelected(null);
  }, [selected, model]);

  const ctx: TwinCtx | null = useMemo(
    () => (palette ? { palette, selected, hovered, select: setSelected, hover: setHovered, reducedMotion } : null),
    [palette, selected, hovered, reducedMotion],
  );

  const batch = model.batch;

  return (
    <main ref={rootRef} className="twin-root" data-twin-root="" data-presenting={presenting ? "" : undefined}>
      <header className="twin-head">
        <div>
          <p className="eyebrow">Site-twin</p>
          <h1 className="twin-title">DairyWorks Vla · Site L1</h1>
        </div>
        <div className="twin-head-status">
          <EngineChip reachable={model.engineReachable} />
          <span className="twin-batch" data-twin-batch={batch?.state ?? ""}>
            {batch ? (
              <>
                Batch <span className="mono">{batch.batch_id}</span> · {batch.state.toLowerCase()}
              </>
            ) : (
              <>Geen actieve batch</>
            )}
          </span>
        </div>
        <div className="twin-head-actions">
          <button type="button" className="twin-btn" onClick={() => setResetSignal((n) => n + 1)}>
            Camera reset
          </button>
          <button type="button" className="twin-btn" aria-pressed={presenting} onClick={togglePresent}>
            Presentatie (P)
          </button>
        </div>
      </header>

      <KpiHud m={model} />

      <div className="twin-body">
        <div className="twin-stage">
          {webgl === false ? (
            <SceneHolder text="Deze browser ondersteunt geen WebGL. De objectlijst toont dezelfde status." />
          ) : ctx && webgl ? (
            <SiteScene model={model} ctx={ctx} presenting={presenting} resetSignal={resetSignal} />
          ) : (
            <SceneHolder text="3D-scène laden" />
          )}
          {selected && <InfoPanel id={selected} m={model} onClose={() => setSelected(null)} />}
        </div>
        <ObjectList m={model} selected={selected} onSelect={setSelected} />
      </div>

      <Legend />
    </main>
  );
}
