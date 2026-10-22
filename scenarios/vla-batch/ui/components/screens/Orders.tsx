"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { NextStep } from "@/components/nav/NextStep";
import { CrossLink, ScreenHeader } from "@/components/nav/ScreenHeader";
import { RefusalPanel } from "@/components/toolkit/RefusalPanel";
import { StatusPill, type Status } from "@/components/toolkit/StatusPill";
import { post } from "@/lib/client";
import { EMPTY, num, shortDateTime } from "@/lib/format";
import { useOrders, useOrderTrace, useUiModel } from "@/lib/queries";
import { OrderTrace, type Trace } from "@/components/screens/OrderTrace";
import type { Refusal } from "@/lib/refusal";

/**
 * Orders: aanmaken, volgen, batchen en sluiten.
 *
 * Dit scherm ontbrak in de v0.6-lijst. Orders stonden daar alleen als
 * voortgangsbalk op het plant overview, en het AANMAKEN viel hun uit. Zonder
 * order is er geen batch en zonder batch staat de scan-gate op de neutrale
 * toestand, dus dit was een echte functionele regressie ten opzichte van de
 * oude SPA.
 *
 * Twee gedragingen die uit die oude SPA moeten blijven:
 *   - een batch aanmaken bij een order wordt na een geslaagde CIP automatisch
 *     opnieuw geprobeerd;
 *   - een order sluiten en een order aanmaken worden dat BEWUST niet. Sluiten
 *     kent een stop-regel die weigert zolang er geen productie is geboekt, en
 *     die blind herhalen verbergt precies wat je moet zien.
 */

const OPERATOR = "op-01";

type Order = {
  order_id: string;
  recipe_id: string;
  status: string;
  target_qty_L: number;
  due_date: string | null;
  customer?: string | null;
  created_at?: string;
  progress?: {
    produced_L?: number;
    batched_L?: number;
    produced_packs?: number;
    progress_pct?: number;
    batches_count?: number;
  };
};

function orderStatus(s: string): Status {
  switch (s) {
    case "DONE":
      return "done";
    case "RUNNING":
      return "run";
    case "OPEN":
      return "idle";
    default:
      return "unset";
  }
}

export function Orders() {
  const params = useSearchParams();
  const router = useRouter();
  const selected = params.get("order");

  const orders = useOrders<Order[]>();
  const model = useUiModel();

  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [retry, setRetry] = useState<(() => Promise<void>) | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [qty, setQty] = useState(5000);
  const [due, setDue] = useState("");

  const recipeId = model.data?.recipe?.recipe_id ?? "chocolate-vla-1L";

  function open(id: string | null) {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("order", id);
    else next.delete("order");
    router.replace(next.toString() ? `?${next.toString()}` : "?", { scroll: false });
  }

  async function run(fn: () => Promise<unknown>, label: string, retryable?: () => Promise<void>) {
    setBusy(true);
    setRefusal(null);
    setMessage(null);
    try {
      await fn();
      await orders.refetch();
      setMessage(`${label} gelukt`);
    } catch (e) {
      setRefusal(e as Refusal);
      setRetry(() => retryable ?? null);
    } finally {
      setBusy(false);
    }
  }

  async function createOrder(e: React.FormEvent) {
    e.preventDefault();
    // Zonder due_date is OTIF niet berekenbaar en blijft die KPI op UNSET.
    // Daarom staat het veld hier prominent en niet verstopt.
    await run(
      () =>
        post("/orders", {
          recipe_id: recipeId,
          target_qty_L: qty,
          due_date: due ? new Date(due).toISOString() : null,
        }),
      "Order aangemaakt",
      // Bewust geen retry.
    );
  }

  const detail = (orders.data ?? []).find((o) => o.order_id === selected) ?? null;
  const trace = useOrderTrace<Trace>(detail ? detail.order_id : null);

  return (
    <main className="mx-auto flex max-w-[1440px] flex-col gap-4 p-3 pb-16 sm:gap-5 sm:p-6">
      <ScreenHeader
        eyebrow="Productieorders"
        title="Orders"
        subtitle="Aanmaken, batchen en sluiten. Zonder order is er geen batch en staat de scan-gate stil."
        actions={[
          { href: "/batches", label: "Batches", title: "Alle batches, met verdict" },
          { href: "/scada", label: "SCADA", title: "Setpoints en simulatie" },
          { href: "/", label: "Plant overview" },
        ]}
      />

      {refusal && (
        <RefusalPanel
          refusal={refusal}
          operatorId={OPERATOR}
          onCleared={() => setRefusal(null)}
          onRetry={retry ?? undefined}
        />
      )}
      {message && <p className="text-[0.8125rem] text-ink-muted">{message}</p>}

      <section className="tile flex flex-col gap-3">
        <div>
          <span className="eyebrow">Nieuwe order</span>
          <h2 className="text-base font-semibold">Aanmaken</h2>
        </div>
        <form onSubmit={createOrder} className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Recept
            <input
              readOnly
              value={recipeId}
              className="border border-line-strong bg-surface-sunken px-2 py-1.5 mono"
            />
          </label>
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Doelvolume
            <span className="flex items-center gap-1.5">
              <input
                type="number"
                min={100}
                step={100}
                value={qty}
                onChange={(e) => setQty(Number(e.target.value))}
                className="w-28 border border-line-strong bg-surface px-2 py-1.5 text-right num"
              />
              <span className="text-ink-muted">L</span>
            </span>
          </label>
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Leverdatum
            <input
              type="datetime-local"
              value={due}
              onChange={(e) => setDue(e.target.value)}
              className="border border-line-strong bg-surface px-2 py-1.5"
            />
          </label>
          <button
            type="submit"
            disabled={busy}
            className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
          >
            Order aanmaken
          </button>
        </form>
        <p className="text-[0.6875rem] text-ink-faint">
          Zonder leverdatum is OTIF niet berekenbaar en blijft die KPI op &quot;geen norm&quot;
          staan. Het veld is optioneel, maar laat het niet zomaar leeg.
        </p>
      </section>

      <section className="tile table-scroll p-0">
        <table className="w-full border-collapse text-[0.8125rem]">
          <thead>
            <tr className="border-b border-line text-left">
              {["Order", "Klant", "Status", "Doel", "Geproduceerd", "Voortgang", "Leverdatum", ""].map((h) => (
                <th
                  key={h}
                  className="px-3 py-2 text-[0.6875rem] font-semibold tracking-[0.06em] uppercase text-ink-faint"
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(orders.data ?? []).length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-4 text-ink-muted">
                  Nog geen orders. Maak er hierboven een aan.
                </td>
              </tr>
            )}
            {(orders.data ?? []).map((o) => (
              <tr
                key={o.order_id}
                onClick={() => open(o.order_id === selected ? null : o.order_id)}
                className={`cursor-pointer border-b border-line last:border-b-0 hover:bg-surface-sunken ${
                  selected === o.order_id ? "bg-surface-sunken" : ""
                }`}
              >
                <td className="px-3 py-[var(--density-row)] mono">
                  {/* De doorklik mag het detailpaneel niet ook openen. */}
                  <span onClick={(e) => e.stopPropagation()}>
                    <CrossLink href={`/batches?order=${o.order_id}`} title="Batches van deze order">
                      {o.order_id}
                    </CrossLink>
                  </span>
                </td>
                <td className="px-3 py-[var(--density-row)]">{o.customer ?? EMPTY}</td>
                <td className="px-3 py-[var(--density-row)]">
                  <StatusPill status={orderStatus(o.status)}>{o.status}</StatusPill>
                </td>
                <td className="px-3 py-[var(--density-row)] num">{num(o.target_qty_L, 0)} L</td>
                <td className="px-3 py-[var(--density-row)] num">
                  {num(o.progress?.produced_L ?? 0, 0)} L
                </td>
                <td className="px-3 py-[var(--density-row)]">
                  <span className="flex items-center gap-2">
                    <span className="h-1.5 w-16 bg-surface-sunken">
                      <i
                        className="block h-full bg-ink-muted"
                        style={{ width: `${Math.min(100, o.progress?.progress_pct ?? 0)}%` }}
                      />
                    </span>
                    <span className="num">{num(o.progress?.progress_pct ?? 0, 0)} %</span>
                  </span>
                </td>
                <td className="px-3 py-[var(--density-row)] num text-ink-muted">
                  {o.due_date ? shortDateTime(o.due_date) : EMPTY}
                </td>
                <td className="px-3 py-[var(--density-row)] text-right">
                  {o.status !== "DONE" && (
                    <span className="flex justify-end gap-1.5">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          const make = async () => {
                            await post(`/orders/${o.order_id}/batches`, { operator_id: OPERATOR });
                          };
                          // Enige plek met automatisch opnieuw proberen: na een
                          // geslaagde CIP is de batch alsnog aan te maken.
                          run(make, "Batch aangemaakt", make);
                        }}
                        className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase hover:border-ink-muted disabled:opacity-40"
                      >
                        Batch
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          // Bewust GEEN retry: de stop-regel weigert zolang er
                          // geen productie is geboekt, en dat moet je zien.
                          run(() => post(`/orders/${o.order_id}/close`, {}), "Order gesloten");
                        }}
                        className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase hover:border-ink-muted disabled:opacity-40"
                      >
                        Sluiten
                      </button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      {detail && (
        <section className="tile flex flex-col gap-2">
          <div className="flex items-start justify-between gap-2">
            <div>
              <span className="eyebrow">Orderdetail</span>
              <h2 className="mono text-base font-semibold">{detail.order_id}</h2>
            </div>
            {/* NIET "Sluiten": die knop staat in dezelfde tabel al voor het
                sluiten van een ORDER, en dat is onomkeerbaar. Twee knoppen met
                hetzelfde woord waarvan er een destructief is, is een valstrik. */}
            <button
              type="button"
              onClick={() => open(null)}
              className="text-[0.8125rem] text-ink-muted hover:text-ink"
            >
              Detail dichtklappen
            </button>
          </div>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-[0.8125rem]">
            <dt className="text-ink-muted">Klant</dt>
            <dd>{detail.customer ?? EMPTY}</dd>
            <dt className="text-ink-muted">Recept</dt>
            <dd className="mono">{detail.recipe_id}</dd>
            <dt className="text-ink-muted">Batches</dt>
            <dd className="num">{detail.progress?.batches_count ?? 0}</dd>
            <dt className="text-ink-muted">Gebatcht</dt>
            <dd className="num">{num(detail.progress?.batched_L ?? 0, 0)} L</dd>
            <dt className="text-ink-muted">Pakken</dt>
            <dd className="num">{num(detail.progress?.produced_packs ?? 0, 0)}</dd>
            <dt className="text-ink-muted">Aangemaakt</dt>
            <dd className="num">{shortDateTime(detail.created_at)}</dd>
          </dl>
          {trace.data && <OrderTrace trace={trace.data} />}
          <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line pt-2 text-[0.8125rem]">
            <CrossLink href={`/batches?order=${detail.order_id}`}>Batches van deze order</CrossLink>
            <CrossLink href="/shopfloor">Werkvloer: doseren en boeken</CrossLink>
            <CrossLink href={`/reports?type=period&days=1`}>Rapport van vandaag</CrossLink>
          </div>
        </section>
      )}

      <NextStep
        steps={[
          {
            href: "/scada",
            label: "Batch aanmaken bij een order",
            why: "Kan ook met de knop Batch in de tabel hierboven.",
            done: (orders.data ?? []).some((o) => (o.progress?.batches_count ?? 0) > 0),
          },
          {
            href: "/shopfloor",
            label: "Naar de werkvloer",
            why: "Scannen, afwegen, committen en productie boeken.",
          },
          {
            href: "/batches",
            label: "Verdict ondertekenen",
            why: "Een order sluit pas als de batches zijn afgehandeld.",
          },
        ]}
      />
    </main>
  );
}
