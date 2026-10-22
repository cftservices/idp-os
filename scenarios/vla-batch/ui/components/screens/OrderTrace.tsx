"use client";

import { CrossLink } from "@/components/nav/ScreenHeader";
import { StatusPill, type Status } from "@/components/toolkit/StatusPill";
import { LineBadge } from "@/components/toolkit/LineSwitch";
import { EMPTY, num, shortDateTime } from "@/lib/format";

/**
 * Tracering van één order: order → voorbereiding → batches → pallets →
 * vrachtwagen → klant.
 *
 * Bron is GET /orders/{id}/trace. Vertrokken vrachtwagens bewaart de engine in
 * dw_shipments, omdat de verzonden pallets na twee uur worden opgeruimd; zonder
 * die kopie verdween het bewijs van levering precies wanneer je het nodig had.
 */

type Shipment = {
  shipment_id: string;
  truck_id: string;
  customer: string | null;
  destination: string | null;
  stage: string | null;
  pallets: number;
  pallets_loaded: number;
  packs: number;
  loaded_at: string | null;
  eta: string | null;
};

type TracePrep = {
  prep_id: string;
  state: string;
  weigh_station: string | null;
  premix_tank: string | null;
  state_changed_at: string | null;
};

type TracePallet = {
  hu_id: string;
  status: string | null;
  packs: number | null;
  slot: number | null;
  putaway_by: string | null;
  loaded_by: string | null;
  truck_id: string | null;
  wrapped_at: string | null;
  stored_at: string | null;
  shipped_at: string | null;
};

type TraceEvent = {
  ts: string | null;
  batch_id: string | null;
  type: string | null;
  payload: Record<string, unknown>;
};

type TraceBatch = {
  batch_id: string;
  line?: string | null;
  prep?: TracePrep | null;
  state: string | null;
  verdict: string | null;
  packs: number | null;
  completed_at: string | null;
  pallets: number;
  step: string;
  shipment: Shipment | null;
  pallet_list?: TracePallet[];
};

export type Trace = {
  order_id: string;
  customer: string | null;
  line?: string | null;
  product_name?: string | null;
  prep_pending?: TracePrep[];
  steps: string[];
  batches: TraceBatch[];
  events?: TraceEvent[];
  events_truncated?: boolean;
  totals: {
    batches: number;
    pallets: number;
    packs: number;
    trucks_en_route: number;
    trucks_delivered: number;
    packs_delivered: number;
    per_step: Record<string, number>;
  };
};

function stepStatus(step: string): Status {
  if (step === "afgeleverd") return "done";
  if (step === "gepland") return "idle";
  if (step === "onderweg" || step === "laden") return "run";
  return "ok";
}

function truckLine(s: Shipment | null): string {
  if (!s) return EMPTY;
  if (s.stage === "delivered") return `afgeleverd ${shortDateTime(s.eta)}`;
  if (s.stage === "en_route") return `ETA ${shortDateTime(s.eta)}`;
  if (s.stage === "loading") return `laden ${s.pallets_loaded}/${s.pallets}`;
  return `wacht op laden · 0/${s.pallets}`;
}

const EVENT_LABEL: Record<string, string> = {
  order_created: "order aangemaakt",
  order_scanned: "order gescand",
  order_running: "order in productie",
  order_closed: "order gesloten",
  batch_created: "batch aangemaakt",
  batch_started: "batch gestart",
  setpoints_pushed: "setpoints naar PLC",
  filling_done: "vullen klaar",
  sample_taken: "monster genomen",
  verdict_set: "verdict",
  batch_complete: "batch klaar",
  batch_discarded: "batch afgekeurd",
  hu_scanned: "pallet gewikkeld",
  putaway_booked: "inslag geboekt",
  forklift_task: "heftruck",
  hu_shipped: "pallet verzonden",
  delivered: "afgeleverd bij klant",
};

function str(v: unknown): string | null {
  return typeof v === "string" || typeof v === "number" ? String(v) : null;
}

function eventLabel(e: TraceEvent): string {
  const t = e.type ?? "";
  if (t.startsWith("prep_")) return `voorbereiding ${t.slice(5)}`;
  return EVENT_LABEL[t] ?? t;
}

function eventDetail(e: TraceEvent): string {
  const p = e.payload ?? {};
  if (e.type === "forklift_task") {
    const what = p.task === "load" ? `laadt ${str(p.hu_id) ?? ""} in ${str(p.truck_id) ?? "?"}` : `slaat ${str(p.hu_id) ?? ""} in${p.slot != null ? ` (vak ${str(p.slot)})` : ""}`;
    return `${str(p.forklift_id) ?? "?"} ${what}`;
  }
  if (e.type === "verdict_set") return str(p.verdict) ?? "";
  return [str(p.hu_id), str(p.truck_id), str(p.sample_id)].filter(Boolean).join(" · ");
}

function PalletTable({ batches }: { batches: TraceBatch[] }) {
  const rows = batches.flatMap((b) => (b.pallet_list ?? []).map((p) => ({ ...p, batch_id: b.batch_id })));
  if (rows.length === 0) return null;
  return (
    <details className="text-[0.75rem]" data-testid="trace-pallets">
      <summary className="cursor-pointer font-semibold">
        Pallets <span className="num">{rows.length}</span>: wie sloeg in, wie laadde, welke wagen
      </summary>
      <div className="table-scroll">
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-line text-left">
              {["Pallet", "Batch", "Status", "Pakken", "Vak", "Ingeslagen door", "Geladen door", "Vrachtwagen", "Verzonden"].map((h) => (
                <th key={h} className="px-2 py-1 text-[0.625rem] font-semibold tracking-[0.06em] uppercase text-ink-faint">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.hu_id} className="border-b border-line last:border-b-0">
                <td className="px-2 py-1 mono">{p.hu_id}</td>
                <td className="px-2 py-1 mono">{p.batch_id}</td>
                <td className="px-2 py-1">{p.status ?? EMPTY}</td>
                <td className="px-2 py-1 num">{p.packs != null ? num(p.packs, 0) : EMPTY}</td>
                <td className="px-2 py-1 num">{p.slot ?? EMPTY}</td>
                <td className="px-2 py-1 mono">{p.putaway_by ?? EMPTY}</td>
                <td className="px-2 py-1 mono">{p.loaded_by ?? EMPTY}</td>
                <td className="px-2 py-1 mono">{p.truck_id ?? EMPTY}</td>
                <td className="px-2 py-1 num text-ink-muted">{p.shipped_at ? shortDateTime(p.shipped_at) : EMPTY}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

function EventTimeline({ events, truncated }: { events: TraceEvent[]; truncated: boolean }) {
  if (events.length === 0) return null;
  return (
    <details className="text-[0.75rem]" data-testid="trace-events">
      <summary className="cursor-pointer font-semibold">
        Alle gebeurtenissen <span className="num">{events.length}</span>
        {truncated && " (laatste, oudere afgekapt)"}
      </summary>
      <ol className="max-h-[22rem] overflow-y-auto">
        {events.map((e, i) => (
          <li key={`${e.ts}-${e.type}-${i}`} className="flex gap-2 border-b border-line py-0.5 last:border-b-0">
            <span className="num w-[7.5rem] shrink-0 text-ink-muted">{shortDateTime(e.ts)}</span>
            <span className="w-[10rem] shrink-0 font-semibold">{eventLabel(e)}</span>
            <span className="mono w-[7rem] shrink-0 text-ink-faint">{e.batch_id ?? ""}</span>
            <span className="text-ink-muted">{eventDetail(e)}</span>
          </li>
        ))}
      </ol>
    </details>
  );
}

export function OrderTrace({ trace }: { trace: Trace }) {
  const t = trace.totals;
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-2" data-testid="order-trace">
      <span className="flex flex-wrap items-center gap-2">
        <span className="eyebrow">Tracering</span>
        {trace.product_name && <span className="text-[0.75rem] font-semibold">{trace.product_name}</span>}
        <LineBadge line={trace.line} />
      </span>
      {(trace.prep_pending ?? []).length > 0 && (
        <p className="text-[0.75rem] text-ink-muted" data-testid="prep-pending">
          In voorbereiding:{" "}
          {(trace.prep_pending ?? []).map((p, i) => (
            <span key={p.prep_id}>
              {i > 0 && ", "}
              <span className="mono">{p.prep_id}</span> ({p.state})
            </span>
          ))}{" "}
          <CrossLink href="/voorbereiding">naar voorbereiding</CrossLink>
        </p>
      )}
      <ol className="flex flex-wrap items-center gap-1 text-[0.75rem]">
        {trace.steps.map((s, i) => (
          <li key={s} className="flex items-center gap-1">
            {i > 0 && <span className="text-ink-faint">→</span>}
            <span
              className={`border px-1.5 py-0.5 ${
                t.per_step[s] ? "border-line-strong font-semibold" : "border-line text-ink-faint"
              }`}
            >
              {s} <span className="num">{t.per_step[s] ?? 0}</span>
            </span>
          </li>
        ))}
        <li className="flex items-center gap-1">
          <span className="text-ink-faint">→</span>
          <span className="border border-line-strong px-1.5 py-0.5 font-semibold">
            {trace.customer ?? "klant onbekend"}
          </span>
        </li>
      </ol>
      <p className="text-[0.75rem] text-ink-muted">
        <span className="num">{t.batches}</span> batches ·{" "}
        <span className="num">{t.pallets}</span> pallets ·{" "}
        <span className="num">{t.trucks_en_route}</span> onderweg ·{" "}
        <span className="num">{t.trucks_delivered}</span> afgeleverd (
        <span className="num">{num(t.packs_delivered, 0)}</span> pakken)
      </p>
      <div className="table-scroll">
        <table className="w-full border-collapse text-[0.75rem]">
          <thead>
            <tr className="border-b border-line text-left">
              {["Batch", "Lijn", "Voorbereiding", "Verdict", "Pakken", "Pallets", "Stap", "Vrachtwagen", "Klant", "Levering"].map(
                (h) => (
                  <th
                    key={h}
                    className="px-2 py-1 text-[0.625rem] font-semibold tracking-[0.06em] uppercase text-ink-faint"
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {trace.batches.length === 0 && (
              <tr>
                <td colSpan={10} className="px-2 py-2 text-ink-muted">
                  Nog geen batches bij deze order.
                </td>
              </tr>
            )}
            {trace.batches.map((b) => (
              <tr key={b.batch_id} className="border-b border-line last:border-b-0">
                <td className="px-2 py-1 mono">
                  <CrossLink href={`/batches?batch=${b.batch_id}`}>{b.batch_id}</CrossLink>
                </td>
                <td className="px-2 py-1">
                  <LineBadge line={b.line} />
                </td>
                <td className="px-2 py-1 mono" title={b.prep ? `${b.prep.weigh_station ?? ""} → ${b.prep.premix_tank ?? ""}` : undefined}>
                  {b.prep ? `${b.prep.prep_id} · ${b.prep.state}` : EMPTY}
                </td>
                <td className="px-2 py-1">{b.verdict ?? EMPTY}</td>
                <td className="px-2 py-1 num">{b.packs != null ? num(b.packs, 0) : EMPTY}</td>
                <td className="px-2 py-1 num">{b.pallets || EMPTY}</td>
                <td className="px-2 py-1">
                  <StatusPill status={stepStatus(b.step)}>{b.step}</StatusPill>
                </td>
                <td className="px-2 py-1 mono">{b.shipment?.truck_id ?? EMPTY}</td>
                <td className="px-2 py-1">
                  {b.shipment ? `${b.shipment.customer ?? EMPTY} · ${b.shipment.destination ?? ""}` : EMPTY}
                </td>
                <td className="px-2 py-1 num text-ink-muted">{truckLine(b.shipment)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <PalletTable batches={trace.batches} />
      <EventTimeline events={trace.events ?? []} truncated={Boolean(trace.events_truncated)} />
    </div>
  );
}
