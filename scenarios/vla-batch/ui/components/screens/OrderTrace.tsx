"use client";

import { CrossLink } from "@/components/nav/ScreenHeader";
import { StatusPill, type Status } from "@/components/toolkit/StatusPill";
import { EMPTY, num, shortDateTime } from "@/lib/format";

/**
 * Tracering van één order: order → batches → pallets → vrachtwagen → klant.
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

type TraceBatch = {
  batch_id: string;
  state: string | null;
  verdict: string | null;
  packs: number | null;
  completed_at: string | null;
  pallets: number;
  step: string;
  shipment: Shipment | null;
};

export type Trace = {
  order_id: string;
  customer: string | null;
  steps: string[];
  batches: TraceBatch[];
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

export function OrderTrace({ trace }: { trace: Trace }) {
  const t = trace.totals;
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-2" data-testid="order-trace">
      <span className="eyebrow">Tracering</span>
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
              {["Batch", "Verdict", "Pakken", "Pallets", "Stap", "Vrachtwagen", "Klant", "Levering"].map(
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
                <td colSpan={8} className="px-2 py-2 text-ink-muted">
                  Nog geen batches bij deze order.
                </td>
              </tr>
            )}
            {trace.batches.map((b) => (
              <tr key={b.batch_id} className="border-b border-line last:border-b-0">
                <td className="px-2 py-1 mono">
                  <CrossLink href={`/batches?batch=${b.batch_id}`}>{b.batch_id}</CrossLink>
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
    </div>
  );
}
