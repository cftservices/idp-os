"use client";

/**
 * De glazen overlay boven de 3D-scène, in de WareTrack-indeling: topbalk met
 * zoeken en live-klok, KPI-kaartjes, een tabpaneel links, een detailkaart
 * rechts, een procestijdlijn onder en cameraknoppen. Alles leest uit
 * selection.ts, zodat de DOM en de scène nooit iets anders zeggen.
 */

import Link from "next/link";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Glyph, StatusPill, statusFromKpi } from "@/components/toolkit/StatusPill";
import { PILL, SHAPE, type Visual, type VisualState } from "@/lib/twin/derive";
import { needsPin, searchItems } from "@/lib/twin/flow";
import { orderStops, type OrderStop } from "@/lib/twin/orderPath";
import { delta, sparkPath, type Sample } from "@/lib/twin/history";
import type { TwinModel } from "@/lib/twin/useTwinData";
import { EMPTY, compact, num, shortTime } from "@/lib/format";
import { GROUPS, describe, type TwinGroup, type TwinItem } from "./selection";

export function VisualPill({ v }: { v: Visual }) {
  return <StatusPill status={PILL[v.state]}>{v.label}</StatusPill>;
}

export function Sparkline({ samples, w = 96, h = 26, label }: { samples: Sample[]; w?: number; h?: number; label: string }) {
  const d = sparkPath(samples, w, h);
  if (!d) return <span className="wt-spark-empty">verzamelt…</span>;
  return (
    <svg className="wt-spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Verloop ${label}`}>
      <path d={d} />
    </svg>
  );
}

function clock(now: number) {
  return new Date(now).toLocaleTimeString("nl-NL", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/* ---------------- topbalk ---------------- */

export function TopBar({
  m,
  items,
  chip,
  onPick,
  presenting,
  onPresent,
  onDrawer,
  drawerOpen,
}: {
  m: TwinModel;
  items: TwinItem[];
  chip: string;
  onPick: (id: string) => void;
  presenting: boolean;
  onPresent: () => void;
  onDrawer: () => void;
  drawerOpen: boolean;
}) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const hits = useMemo(() => (q.trim() ? searchItems(items, q) : []), [items, q]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key === "/") {
        e.preventDefault();
        input.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function choose(id: string) {
    onPick(id);
    setQ("");
    setOpen(false);
    input.current?.blur();
  }

  const loud = items.filter((i) => needsPin(i.visual));
  const bell = m.alarmsOpen.length + loud.filter((i) => i.visual.state === "alarm").length;
  const live = m.engineReachable;

  return (
    <header className="wt-top wt-glass" data-twin-top="">
      <div className="wt-brand">
        <span className="wt-logo" aria-hidden="true">
          D
        </span>
        <div>
          <h1 className="wt-title">DairyWorks · Site-twin</h1>
          <p className="wt-sub">Vla-lijn L1, live uit de batch-engine</p>
        </div>
      </div>
      <div className="wt-top-mid">
        <div className="wt-search">
          <input
            ref={input}
            type="search"
            role="combobox"
            aria-expanded={open && hits.length > 0}
            aria-controls="wt-search-results"
            aria-autocomplete="list"
            aria-label="Zoek een object (sneltoets /)"
            placeholder="Zoek tank, truck, batch…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setActive(0);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onBlur={() => setTimeout(() => setOpen(false), 120)}
            onKeyDown={(e) => {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setActive((a) => Math.min(hits.length - 1, a + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setActive((a) => Math.max(0, a - 1));
              } else if (e.key === "Enter" && hits[active]) {
                // Anders landt dezelfde toets op de sluitknop van de kaart, die focus krijgt.
                e.preventDefault();
                choose(hits[active].id);
              } else if (e.key === "Escape") {
                setQ("");
                input.current?.blur();
              }
            }}
          />
          <span className="wt-kbd" aria-hidden="true">
            /
          </span>
          {open && hits.length > 0 && (
            <ul id="wt-search-results" className="wt-results wt-glass" role="listbox" aria-label="Zoekresultaten">
              {hits.map((h, i) => (
                <li
                  key={h.id}
                  role="option"
                  aria-selected={i === active}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    choose(h.id);
                  }}
                  onMouseEnter={() => setActive(i)}
                >
                  <span>{h.name}</span>
                  <span className="text-ink-muted">{h.group}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
        <span className="wt-chip" data-twin-chip="">
          {chip}
        </span>
      </div>
      <div className="wt-top-end">
        <span className="wt-chip wt-live" data-off={live ? undefined : ""} data-twin-live={live === null ? "unknown" : String(live)}>
          {live ? <span className="wt-live-dot" aria-hidden="true" /> : null}
          {live ? "Live" : live === null ? "geen data" : "niet bereikbaar"} ·{" "}
          {/* De pagina is statisch voorgerenderd; de buildtijd mag de klok niet zijn. */}
          <time suppressHydrationWarning>{clock(m.now)}</time>
        </span>
        {loud.length > 0 ? (
          <button
            type="button"
            className="wt-btn wt-bell"
            aria-label={`${bell} alarm(en), ${loud.length} object(en) vragen aandacht; toon het eerste`}
            onClick={() => onPick(loud[0].id)}
          >
            <span aria-hidden="true">🔔</span>
            {bell > 0 && <span className="wt-bell-count">{bell}</span>}
          </button>
        ) : (
          <Link className="wt-btn wt-bell" href="/alarms" aria-label={`${m.alarmsOpen.length} open alarmen`}>
            <span aria-hidden="true">🔔</span>
            {m.alarmsOpen.length > 0 && <span className="wt-bell-count">{m.alarmsOpen.length}</span>}
          </Link>
        )}
        <button type="button" className="wt-btn wt-drawer-btn" aria-expanded={drawerOpen} aria-controls="wt-panel" onClick={onDrawer}>
          Objecten
        </button>
        <button type="button" className="wt-btn" aria-pressed={presenting} onClick={onPresent} title="Presentatie (P)">
          <span aria-hidden="true">⛶</span>
          <span className="sr-only">Presentatie (P)</span>
        </button>
      </div>
    </header>
  );
}

/* ---------------- KPI-kaartjes ---------------- */

const KPI_PREFERENCE = ["plan_attainment_pct", "quality_ratio", "throughput_rate", "otif_pct"];

function Delta({ samples, unit }: { samples: Sample[]; unit: string }) {
  const d = delta(samples);
  if (d == null) return <span className="wt-delta" data-dir="flat">{EMPTY}</span>;
  const dir = Math.abs(d) < 1e-9 ? "flat" : d > 0 ? "up" : "down";
  return (
    <span className="wt-delta" data-dir={dir}>
      {dir === "up" ? "▲" : dir === "down" ? "▼" : "■"} {num(Math.abs(d))}
      {unit ? ` ${unit}` : ""}
    </span>
  );
}

/** De lopende klantorder, zoals de engine hem in /orders teruggeeft. */
export type ActiveOrder = {
  order_id: string;
  customer?: string | null;
  due_date?: string | null;
  target_qty_L?: number | null;
  status: string;
  progress?: {
    progress_pct?: number | null;
    produced_L?: number | null;
    batches_count?: number | null;
    batch_ids?: string[] | null;
  } | null;
};

function KpiValue({ v, unit }: { v: number | null | undefined; unit: string }) {
  if (v == null) return <dd className="num wt-kpi-val">{EMPTY}</dd>;
  return (
    <dd className="num wt-kpi-val" title={`${num(v)} ${unit}`.trim()}>
      {unit === "%" ? num(v) : compact(v)}
      {unit ? <span className="wt-kpi-unit">{unit === "%" ? "%" : ` ${unit}`}</span> : null}
    </dd>
  );
}

function OrderCard({
  order,
  now,
  showPath,
  onTogglePath,
  stops,
}: {
  order: ActiveOrder | null;
  now: number;
  showPath?: boolean;
  onTogglePath?: () => void;
  stops?: OrderStop[];
}) {
  if (!order) {
    return (
      <div className="wt-kpi wt-kpi-order wt-glass" data-twin-order="">
        <dt>Klantorder</dt>
        <dd className="wt-order-cust">geen lopende order</dd>
      </div>
    );
  }
  const pct = Math.max(0, Math.min(100, order.progress?.progress_pct ?? 0));
  const due = order.due_date ? Date.parse(order.due_date) : NaN;
  const late = Number.isFinite(due) && due < now;
  return (
    <div className="wt-kpi wt-kpi-order wt-glass" data-twin-order={order.order_id} style={{ animationDelay: "250ms" }}>
      <dt title={order.order_id}>
        Klantorder · <Link href="/orders">{order.order_id}</Link>
      </dt>
      <dd className="wt-order-cust" title={order.customer ?? ""}>
        {order.customer || "zonder klant"}
      </dd>
      <dd className="wt-order-bar" role="progressbar" aria-label="Ordervoortgang" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
        <span style={{ width: `${pct}%` }} />
      </dd>
      <dd className="wt-kpi-foot">
        <span className="num">
          {num(pct, pct > 0 && pct < 100 ? 1 : 0)}% · {compact(order.progress?.produced_L ?? 0, 0)}/{compact(order.target_qty_L ?? null, 0)} L
        </span>
        <span className="num wt-order-due" data-late={late ? "" : undefined}>
          {late ? "te laat · " : "lever "}
          {order.due_date ? shortTime(order.due_date) : EMPTY}
        </span>
      </dd>
      {onTogglePath && (
        <dd>
          <button
            type="button"
            className="wt-chip-btn"
            aria-pressed={showPath ? "true" : "false"}
            onClick={onTogglePath}
            data-twin-path-toggle=""
          >
            {showPath ? "Orderpad verbergen" : "Toon orderpad"}
          </button>
        </dd>
      )}
      {showPath && stops && (
        <dd>
          <ol className="wt-path-list" aria-label="Orderpad" data-twin-path-list="">
            {stops.map((s) => (
              <li key={s.key} data-twin-path-chip={s.key}>
                <span>{s.label}</span>
                <span className="num">{s.value}</span>
              </li>
            ))}
          </ol>
        </dd>
      )}
    </div>
  );
}

const DIRECTION_WORD = { higher_is_better: "hoger is beter", lower_is_better: "lager is beter" } as const;

function KpiInfo({ k, samples, onClose }: { k: TwinModel["kpis"][number]; samples: Sample[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.focus({ preventScroll: true });
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    const down = (e: PointerEvent) => {
      const t = e.target as HTMLElement | null;
      if (ref.current && t && !ref.current.contains(t) && !t.closest("[data-twin-kpi]")) onClose();
    };
    window.addEventListener("keydown", key, true);
    window.addEventListener("pointerdown", down);
    return () => {
      window.removeEventListener("keydown", key, true);
      window.removeEventListener("pointerdown", down);
    };
  }, [onClose]);
  const fmt = (v: number | null) => (v == null ? EMPTY : `${num(v)} ${k.unit}`.trim());
  return (
    <div ref={ref} tabIndex={-1} className="wt-kpi-info wt-glass" role="dialog" aria-label={`Uitleg ${k.name}`} data-twin-kpi-info={k.kpi_id}>
      <header className="wt-detail-head">
        <div>
          <p className="wt-eyebrow">{k.iso_ref || "KPI"}</p>
          <h2 className="wt-detail-title">{k.name}</h2>
        </div>
        <button type="button" className="wt-btn" onClick={onClose} aria-label="Sluiten (Escape)">
          ✕
        </button>
      </header>
      <div className="wt-kpi-info-now">
        <span className="num">{k.value == null || k.status === "UNSET" ? EMPTY : fmt(k.value)}</span>
        <StatusPill status={statusFromKpi(k.status)} />
      </div>
      <Sparkline samples={samples} w={300} h={64} label={k.name} />
      <dl className="wt-rows">
        <div>
          <dt>Formule</dt>
          <dd>{k.formula || EMPTY}</dd>
        </div>
        <div>
          <dt>Richting</dt>
          <dd>{DIRECTION_WORD[k.direction] ?? k.direction}</dd>
        </div>
        <div>
          <dt>Doel · waarschuwing · kritiek</dt>
          <dd className="num">
            {fmt(k.target)} · {fmt(k.warn)} · {fmt(k.critical)}
          </dd>
        </div>
        <div>
          <dt>Venster</dt>
          <dd>{k.window || EMPTY}</dd>
        </div>
        {k.timing && (
          <div>
            <dt>Timing</dt>
            <dd>{k.timing}</dd>
          </div>
        )}
      </dl>
      <p className="wt-note">Verloop: laatste waarnemingen in deze browser.</p>
    </div>
  );
}

export function KpiCards({
  m,
  series,
  order,
  showPath,
  onTogglePath,
}: {
  m: TwinModel;
  series: (k: string) => Sample[];
  order: ActiveOrder | null;
  showPath?: boolean;
  onTogglePath?: () => void;
}) {
  const [open, setOpen] = useState<string | null>(null);
  const close = useMemo(() => () => setOpen(null), []);
  const picked = useMemo(() => {
    const ranked = [...m.kpis].sort((a, b) => {
      const ia = KPI_PREFERENCE.indexOf(a.kpi_id);
      const ib = KPI_PREFERENCE.indexOf(b.kpi_id);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
    });
    return ranked.slice(0, 4);
  }, [m.kpis]);

  return (
    <>
    <dl className="wt-kpis" data-twin-hud="">
      {picked.length === 0 ? (
        <div className="wt-kpi wt-glass">
          <dt>KPI&apos;s vandaag</dt>
          <dd className="num wt-kpi-val">{EMPTY}</dd>
          <dd>
            <StatusPill status="unset">geen data</StatusPill>
          </dd>
        </div>
      ) : (
        picked.map((k, i) => {
          const s = series(`kpi:${k.kpi_id}`);
          const toggle = () => setOpen((o) => (o === k.kpi_id ? null : k.kpi_id));
          return (
            <div
              key={k.kpi_id}
              className="wt-kpi wt-kpi-click wt-glass"
              data-twin-kpi={k.kpi_id}
              style={{ animationDelay: `${i * 50}ms` }}
              role="button"
              tabIndex={0}
              aria-expanded={open === k.kpi_id}
              aria-label={`${k.name}: uitleg en verloop`}
              onClick={toggle}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  toggle();
                }
              }}
            >
              <dt title={k.name}>{k.name}</dt>
              <KpiValue v={k.value == null || k.status === "UNSET" ? null : k.value} unit={k.unit} />
              <dd className="wt-kpi-foot">
                <StatusPill status={statusFromKpi(k.status)} />
                <Delta samples={s} unit="" />
              </dd>
            </div>
          );
        })
      )}
      <div className="wt-kpi wt-glass" style={{ animationDelay: "200ms" }}>
        <dt>Geproduceerd</dt>
        <KpiValue v={m.produced.packs} unit="packs" />
        <dd className="wt-kpi-foot">
          <Sparkline samples={series("produced")} w={72} h={18} label="geproduceerd" />
        </dd>
      </div>
      <OrderCard order={order} now={m.now} showPath={showPath} onTogglePath={onTogglePath} stops={order && showPath ? orderStops(order, m) : undefined} />
    </dl>
    {open &&
      (() => {
        const k = m.kpis.find((x) => x.kpi_id === open);
        return k ? <KpiInfo k={k} samples={series(`kpi:${k.kpi_id}`)} onClose={close} /> : null;
      })()}
    </>
  );
}

/* ---------------- tabpaneel ---------------- */

const LEGEND: VisualState[] = ["run", "idle", "warn", "alarm", "stale", "unknown"];
const LEGEND_WORD: Record<VisualState, string> = {
  run: "draait",
  idle: "stil",
  warn: "aandacht",
  alarm: "alarm",
  stale: "verouderd",
  unknown: "geen data",
};

export function TabPanel({
  items,
  tab,
  onTab,
  selected,
  hovered,
  onSelect,
  onHover,
  open,
  simOn,
}: {
  items: TwinItem[];
  tab: TwinGroup;
  onTab: (g: TwinGroup) => void;
  selected: string | null;
  hovered: string | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
  open: boolean;
  simOn: boolean;
}) {
  const rows = items.filter((i) => i.group === tab);
  return (
    <nav id="wt-panel" className="wt-panel wt-glass" aria-label="Objecten op de site" data-open={open ? "" : undefined}>
      <div className="wt-tabs" role="tablist" aria-label="Groepen">
        {GROUPS.map((g) => (
          <button
            key={g}
            type="button"
            role="tab"
            className="wt-tab"
            aria-selected={tab === g}
            aria-controls="wt-tab-list"
            onClick={() => onTab(g)}
          >
            {g}
            <span className="wt-tab-count">{items.filter((i) => i.group === g).length}</span>
          </button>
        ))}
      </div>
      <ul id="wt-tab-list" className="wt-list" role="tabpanel" aria-label={tab}>
        {rows.length === 0 ? (
          <li className="wt-empty">{tab === "Heftrucks" || tab === "Trucks" ? (simOn ? "Nu niets onderweg." : "Demo-logistiek staat uit.") : "geen data"}</li>
        ) : (
          rows.map((i) => (
            <li key={i.id}>
              <button
                type="button"
                className="wt-row"
                aria-pressed={selected === i.id}
                data-hover={hovered === i.id ? "" : undefined}
                data-twin-object={i.id}
                data-twin-state={i.visual.state}
                onClick={() => onSelect(i.id)}
                onMouseEnter={() => onHover(i.id)}
                onMouseLeave={() => onHover(null)}
                onFocus={() => onHover(i.id)}
                onBlur={() => onHover(null)}
              >
                <span className="wt-row-name">{i.name}</span>
                <span className="wt-row-meta">{i.meta || EMPTY}</span>
                <VisualPill v={i.visual} />
                {i.pct != null && (
                  <span className="wt-row-bar" aria-hidden="true">
                    <span style={{ width: `${Math.max(0, Math.min(100, i.pct))}%` }} />
                  </span>
                )}
              </button>
            </li>
          ))
        )}
      </ul>
      <div className="wt-legend" aria-label="Legenda">
        {LEGEND.map((s) => (
          <span key={s}>
            {s === "unknown" ? <span aria-hidden="true">{SHAPE.unknown}</span> : <Glyph status={PILL[s]} />}
            {LEGEND_WORD[s]}
          </span>
        ))}
        <span>
          {simOn ? "Zendingen, heftrucks en tankwagen: demo-simulatie." : "Trucks afgeleid uit de HU-status, niet uit een transportsysteem."}
        </span>
        <span>
          <Link href="/line">Lijn L1</Link> · <Link href="/management">Management</Link> · <Link href="/voorraad">Voorraad</Link>
        </span>
      </div>
    </nav>
  );
}

/* ---------------- detailkaart ---------------- */

export function DetailCard({
  id,
  m,
  series,
  onClose,
  following,
  onFollow,
}: {
  id: string;
  m: TwinModel;
  series: (k: string) => Sample[];
  onClose: () => void;
  following?: boolean;
  onFollow?: () => void;
}) {
  const d = describe(id, m);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus({ preventScroll: true });
  }, [id]);
  if (!d) return null;
  const pct = d.progress ? Math.max(0, Math.min(100, d.progress.pct)) : 0;
  return (
    <aside key={id} className="wt-detail wt-glass" data-twin-panel={id} aria-label={`Details ${d.title}`}>
      <header className="wt-detail-head">
        <div>
          <p className="wt-eyebrow">{d.kind}</p>
          <h2 className="wt-detail-title">{d.title}</h2>
        </div>
        <button ref={closeRef} type="button" className="wt-btn" onClick={onClose} aria-label="Sluiten (Escape)">
          ✕
        </button>
      </header>
      <VisualPill v={d.visual} />
      {onFollow && (
        <button type="button" className="wt-chip-btn" aria-pressed={following ? "true" : "false"} onClick={onFollow} data-twin-follow={id}>
          {following ? "■ Stop volgen" : "◎ Volg met camera"}
        </button>
      )}
      {d.visual.reasons.length > 0 && (
        <ul className="wt-reasons">
          {d.visual.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      )}
      {d.progress && (
        <div className="wt-progress">
          <div className="wt-progress-head">
            <span>{d.progress.label}</span>
            <span>{num(pct, 0)}%</span>
          </div>
          <div className="wt-bar" role="progressbar" aria-label={d.progress.label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(pct)}>
            <span style={{ width: `${pct}%` }} />
          </div>
        </div>
      )}
      {d.spark && d.spark.length > 0 && (
        <div className="wt-sparks">
          {d.spark.map((s) => {
            const samples = series(s.key);
            const last = samples.length ? samples[samples.length - 1].v : null;
            return (
              <div key={s.key} className="wt-spark-row">
                <div>
                  <small>{s.label} · laatste minuten</small>
                  <span className="num">{last == null ? EMPTY : `${num(last)} ${s.unit}`.trim()}</span>
                </div>
                <Sparkline samples={samples} label={s.label} />
              </div>
            );
          })}
        </div>
      )}
      <dl className="wt-rows">
        {d.rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd className="num">{v}</dd>
          </div>
        ))}
      </dl>
      {d.timeline && d.timeline.index >= 0 && <MiniSteps steps={d.timeline.steps} index={d.timeline.index} />}
      <ul className="wt-links">
        {d.links.map((l) => (
          <li key={l.href}>
            <Link href={l.href}>{l.label} →</Link>
          </li>
        ))}
      </ul>
      {d.note && <p className="wt-note">{d.note}</p>}
    </aside>
  );
}

function MiniSteps({ steps, index }: { steps: readonly string[]; index: number }) {
  return (
    <ol className="wt-steps" aria-label="Voortgang">
      {steps.map((s, i) => (
        <li
          key={s}
          className="wt-step"
          data-done={i < index ? "" : undefined}
          data-current={i === index ? "" : undefined}
          aria-current={i === index ? "step" : undefined}
        >
          <span className="wt-step-dot" aria-hidden="true" />
          <span className="wt-step-label">{s}</span>
        </li>
      ))}
    </ol>
  );
}

/* ---------------- tijdlijn onder ---------------- */

export function Timeline({
  title,
  right,
  steps,
  index,
  following,
  onFollow,
}: {
  title: ReactNode;
  right: ReactNode;
  steps: readonly string[];
  index: number;
  following?: boolean;
  onFollow?: () => void;
}) {
  return (
    <section className="wt-timeline wt-glass" aria-label="Procestijdlijn" data-twin-timeline="">
      <div className="wt-timeline-head">
        <span>{title}</span>
        <span className="wt-timeline-right">
          {right}
          {onFollow && (
            <button type="button" className="wt-chip-btn" aria-pressed={following ? "true" : "false"} onClick={onFollow} data-twin-follow-batch="">
              {following ? "■ Stop volgen" : "◎ Volg batch"}
            </button>
          )}
        </span>
      </div>
      <MiniSteps steps={steps} index={index} />
    </section>
  );
}

/* ---------------- camera ---------------- */

export function CameraControls({ onZoom, onReset }: { onZoom: (dir: 1 | -1) => void; onReset: () => void }) {
  return (
    <div className="wt-cam wt-glass" role="group" aria-label="Camera">
      <button type="button" className="wt-btn" onClick={() => onZoom(1)} aria-label="Inzoomen">
        +
      </button>
      <button type="button" className="wt-btn" onClick={() => onZoom(-1)} aria-label="Uitzoomen">
        −
      </button>
      <button type="button" className="wt-btn" onClick={onReset} aria-label="Camera reset">
        ⌂
      </button>
    </div>
  );
}
