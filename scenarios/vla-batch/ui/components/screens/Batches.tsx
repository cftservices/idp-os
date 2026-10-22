"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { NextStep } from "@/components/nav/NextStep";
import { CrossLink, ScreenHeader } from "@/components/nav/ScreenHeader";
import { DeviationBand } from "@/components/toolkit/Indicators";
import { RefusalPanel } from "@/components/toolkit/RefusalPanel";
import { StatusPill, type Status } from "@/components/toolkit/StatusPill";
import { post } from "@/lib/client";
import { EMPTY, num, shortDateTime, value as fmt } from "@/lib/format";
import { useBatch, useBatches, useUiModel } from "@/lib/queries";
import type { Refusal } from "@/lib/refusal";
import { LineSwitch, useLineParam } from "@/components/toolkit/LineSwitch";
import { ProductTag, useRecipeIndex } from "@/components/toolkit/Product";

/**
 * Scherm 2, batches (lijst plus detail), met scherm 3 (recept) als uitklapblok.
 *
 * Het recept was in v0.5 een eigen route met zeven read-only getallen die alle
 * drie elders al stonden, zonder enige actie behalve een dropdown. Dat de
 * hold-tijd tussen recept en de nieuwere schermen al uiteenliep, bewees het
 * punt: een tweede plek met dezelfde waarden loopt uit elkaar.
 *
 * De geopende drawer BEVRIEST de lijst-loop. Zonder dat re-rendert het
 * detailpaneel onder de gebruiker weg zodra de lijst ververst.
 */

type VerdictAck = { operator_id: string | null; ts: string };

type Batch = {
  batch_id: string;
  state: string;
  verdict: string | null;
  verdict_ack?: VerdictAck | null;
  recipe_id: string;
  line?: string;
  order_id?: string | null;
  planned_L: number | null;
  packs_total: number | null;
  end_viscosity_cP: number | null;
  started_at: string | null;
  completed_at: string | null;
};

type BatchDetail = Batch & {
  doses?: Array<{
    material_id: string;
    qty_target: number;
    qty_actual: number | null;
    in_tolerance: boolean | null;
  }>;
  samples?: Array<{ sample_id: string; sample_type: string; value: number | null; status: string }>;
  telemetry_summary?: Record<string, unknown>;
};

function verdictStatus(v: string | null): Status {
  switch (v) {
    case "APPROVED":
      return "ok";
    case "HOLD":
      return "warn";
    case "REJECTED":
      return "alarm";
    default:
      return "unset";
  }
}

function stateStatus(s: string): Status {
  return s === "COMPLETE" ? "done" : s === "IDLE" ? "idle" : "run";
}

const OPERATOR = "op-01";

export function Batches() {
  const params = useSearchParams();
  const router = useRouter();
  const selected = params.get("batch");
  const verdictFilter = params.get("verdict");
  const orderFilter = params.get("order");
  const lineFilter = useLineParam(true);
  const recipes = useRecipeIndex();

  // De lijst pollt niet zolang er een detail open staat.
  const list = useBatches<Batch[]>(Boolean(selected));
  const detail = useBatch<BatchDetail>(selected);
  const model = useUiModel();

  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [busy, setBusy] = useState(false);

  function open(id: string | null) {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("batch", id);
    else next.delete("batch");
    router.replace(next.toString() ? `?${next.toString()}` : "?", { scroll: false });
  }

  /**
   * De handtekening onder het verdict. Idempotent aan de engine-kant, dus een
   * dubbele klik doet geen kwaad; de knop verdwijnt zodra de ack er staat.
   */
  async function ackVerdict(batchId: string) {
    setBusy(true);
    setRefusal(null);
    try {
      await post(`/batches/${batchId}/ack-verdict`, { operator_id: OPERATOR });
      await Promise.all([detail.refetch(), list.refetch()]);
    } catch (err) {
      setRefusal(err as Refusal);
    } finally {
      setBusy(false);
    }
  }

  const rows = (list.data ?? [])
    .filter((b) => !verdictFilter || b.verdict === verdictFilter)
    .filter((b) => !orderFilter || b.order_id === orderFilter)
    .filter((b) => !lineFilter || (b.line ?? "L1") === lineFilter);
  const spec = model.data?.recipe?.viscosity_spec_cP ?? null;
  const unsigned = (list.data ?? []).filter(
    (b) => b.state === "COMPLETE" && b.verdict && !b.verdict_ack,
  );

  return (
    <main className="mx-auto flex max-w-[1440px] flex-col gap-4 p-3 pb-16 sm:gap-5 sm:p-6">
      <ScreenHeader
        eyebrow="Batches"
        title="Batchoverzicht"
        subtitle={
          unsigned.length > 0
            ? `${unsigned.length} ${unsigned.length === 1 ? "batch wacht" : "batches wachten"} op een handtekening onder het verdict.`
            : "Verloop, doseringen en verdict per batch."
        }
        actions={[
          { href: "/orders", label: "Orders", title: "Order aanmaken of sluiten" },
          { href: "/shopfloor", label: "Werkvloer" },
          { href: "/reports?type=batch", label: "Batchrapport" },
        ]}
      />

      <LineSwitch allowAll />

      {(verdictFilter || orderFilter) && (
        <p className="flex flex-wrap items-center gap-2 text-[0.8125rem]">
          gefilterd op
          {verdictFilter && (
            <StatusPill status={verdictStatus(verdictFilter)}>{verdictFilter}</StatusPill>
          )}
          {orderFilter && (
            <CrossLink href={`/orders?order=${orderFilter}`}>order {orderFilter}</CrossLink>
          )}
          <Link href="/batches" className="text-accent underline underline-offset-2">
            alles tonen
          </Link>
        </p>
      )}

      {refusal && (
        <RefusalPanel refusal={refusal} operatorId={OPERATOR} onCleared={() => setRefusal(null)} />
      )}

      <div className="grid items-start gap-5 lg:[grid-template-columns:minmax(0,1.4fr)_minmax(0,1fr)]">
        <section className="tile table-scroll p-0">
          <table className="w-full border-collapse text-[0.8125rem]">
            <thead>
              <tr className="border-b border-line text-left">
                {["Batch", "Product", "Order", "Status", "Verdict", "Pakken", "Viscositeit", "Afgerond"].map((h) => (
                  <th key={h} className="px-3 py-2 text-[0.6875rem] font-semibold tracking-[0.06em] uppercase text-ink-faint">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={8} className="px-3 py-4 text-ink-muted">
                    Geen batches in deze selectie.{" "}
                    <CrossLink href="/orders">Maak een order en start een batch</CrossLink>.
                  </td>
                </tr>
              )}
              {rows.map((b) => (
                <tr
                  key={b.batch_id}
                  // Onderscheidt een echte batchrij van de lege-staat-rij. Die
                  // laatste is ook een <tr> en werd daardoor voor een batch
                  // aangezien, inclusief de doorklik die erin staat.
                  data-row="batch"
                  onClick={() => open(b.batch_id)}
                  className={`cursor-pointer border-b border-line last:border-b-0 hover:bg-surface-sunken ${
                    selected === b.batch_id ? "bg-surface-sunken" : ""
                  }`}
                >
                  <td className="px-3 py-[var(--density-row)] mono">{b.batch_id}</td>
                  <td className="px-3 py-[var(--density-row)] whitespace-nowrap">
                    <ProductTag recipeId={b.recipe_id} index={recipes} showLine />
                  </td>
                  <td className="px-3 py-[var(--density-row)] mono">
                    {b.order_id ? (
                      // De doorklik mag het detailpaneel niet ook openen.
                      <span onClick={(e) => e.stopPropagation()}>
                        <CrossLink href={`/orders?order=${b.order_id}`}>{b.order_id}</CrossLink>
                      </span>
                    ) : (
                      <span className="text-ink-faint">{EMPTY}</span>
                    )}
                  </td>
                  <td className="px-3 py-[var(--density-row)]">
                    <StatusPill status={stateStatus(b.state)}>{b.state}</StatusPill>
                  </td>
                  <td className="px-3 py-[var(--density-row)]">
                    <span className="flex items-center gap-1.5">
                      <StatusPill status={verdictStatus(b.verdict)}>
                        {b.verdict ?? "PENDING"}
                      </StatusPill>
                      {/* Ondertekend of niet: dat is een audit-feit, geen kleur. */}
                      {b.verdict && !b.verdict_ack && (
                        <span className="text-[0.6875rem] text-ink-faint">niet getekend</span>
                      )}
                    </span>
                  </td>
                  <td className="px-3 py-[var(--density-row)] num">
                    {b.packs_total === null ? EMPTY : num(b.packs_total, 0)}
                  </td>
                  <td className="px-3 py-[var(--density-row)] num">
                    {b.end_viscosity_cP === null ? EMPTY : `${num(b.end_viscosity_cP, 0)} cP`}
                  </td>
                  <td className="px-3 py-[var(--density-row)] num text-ink-muted">
                    {shortDateTime(b.completed_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {selected && (
          <aside className="tile flex flex-col gap-4">
            <div className="flex items-start justify-between gap-2">
              <div>
                <span className="eyebrow">Batchdetail</span>
                <h2 className="mono text-base font-semibold">{selected}</h2>
              </div>
              <button
                type="button"
                onClick={() => open(null)}
                className="text-[0.8125rem] text-ink-muted hover:text-ink"
              >
                Sluiten
              </button>
            </div>

            {detail.isPending ? (
              <p className="text-[0.8125rem] text-ink-muted">Laden.</p>
            ) : detail.data ? (
              <>
                <div className="flex flex-col gap-1.5">
                  <span className="eyebrow">Kwaliteitsstap</span>
                  <span className="text-2xl leading-none font-semibold num">
                    {detail.data.end_viscosity_cP === null
                      ? EMPTY
                      : `${num(detail.data.end_viscosity_cP, 0)} cP`}
                  </span>
                  <DeviationBand
                    value={detail.data.end_viscosity_cP}
                    spec={spec}
                    tone={
                      spec && detail.data.end_viscosity_cP !== null
                        ? detail.data.end_viscosity_cP < spec.min ||
                          detail.data.end_viscosity_cP > spec.max
                          ? "alarm"
                          : "neutral"
                        : "neutral"
                    }
                  />
                </div>

                {detail.data.doses && detail.data.doses.length > 0 && (
                  <div className="flex flex-col gap-1.5">
                    <span className="eyebrow">Doseringen</span>
                    {detail.data.doses.map((d) => (
                      <div
                        key={d.material_id}
                        className="flex items-baseline justify-between gap-2 text-[0.8125rem]"
                      >
                        <span className="mono">{d.material_id}</span>
                        <span className="flex items-center gap-2">
                          <span className="font-semibold num">
                            {d.qty_actual === null ? EMPTY : num(d.qty_actual, 1)} /{" "}
                            {num(d.qty_target, 1)}
                          </span>
                          {d.in_tolerance === false && <StatusPill status="alarm">Buiten band</StatusPill>}
                        </span>
                      </div>
                    ))}
                  </div>
                )}

                {/* Scherm 3 als uitklapblok: dezelfde waarden, een plek. */}
                <details className="border-t border-line pt-3">
                  <summary className="cursor-pointer text-[0.8125rem] font-semibold">
                    Recept
                  </summary>
                  <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[0.8125rem] text-ink-muted">
                    <dt className="font-semibold">Recept</dt>
                    <dd>
                    <ProductTag recipeId={detail.data.recipe_id} index={recipes} showLine />
                  </dd>
                    <dt className="font-semibold">Gepland</dt>
                    <dd className="num">
                      {detail.data.planned_L === null ? EMPTY : `${num(detail.data.planned_L, 0)} L`}
                    </dd>
                    {model.data?.recipe && (
                      <>
                        <dt className="font-semibold">Kook-setpoint</dt>
                        <dd className="num">{fmt(model.data.recipe.cook_setpoint_C, "°C")} °C</dd>
                        <dt className="font-semibold">Hold</dt>
                        <dd className="num">{model.data.recipe.hold_sec} s</dd>
                        <dt className="font-semibold">Koeldoel</dt>
                        <dd className="num">{fmt(model.data.recipe.cool_target_C, "°C")} °C</dd>
                        <dt className="font-semibold">Viscositeitsband</dt>
                        <dd className="num">
                          {spec ? `${spec.min} tot ${spec.max} cP` : EMPTY}
                        </dd>
                      </>
                    )}
                  </dl>
                </details>

                {/* Het verdict ondertekenen. Zonder deze knop was het EBR een
                    rapport zonder handtekening: het bewijsstuk noemt een
                    beoordeling die niemand heeft bevestigd. */}
                <div className="flex flex-col gap-2 border-t border-line pt-3">
                  <span className="eyebrow">Verdict</span>
                  {detail.data.verdict === null ? (
                    <p className="text-[0.8125rem] text-ink-muted">
                      Nog geen verdict. Dat komt bij het afronden van de batch.
                    </p>
                  ) : detail.data.verdict_ack ? (
                    <p className="text-[0.8125rem] text-ink-muted">
                      Ondertekend door{" "}
                      <span className="mono">{detail.data.verdict_ack.operator_id ?? "onbekend"}</span>{" "}
                      op <span className="num">{shortDateTime(detail.data.verdict_ack.ts)}</span>.
                    </p>
                  ) : (
                    <>
                      <p className="text-[0.8125rem] text-ink-muted">
                        Verdict <StatusPill status={verdictStatus(detail.data.verdict)}>
                          {detail.data.verdict}
                        </StatusPill>{" "}
                        wacht op een handtekening.
                      </p>
                      <button
                        type="button"
                        disabled={busy || detail.data.state !== "COMPLETE"}
                        onClick={() => ackVerdict(detail.data!.batch_id)}
                        className="self-start border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
                      >
                        Ondertekenen als {OPERATOR}
                      </button>
                      {detail.data.state !== "COMPLETE" && (
                        <span className="text-[0.6875rem] text-ink-faint">
                          Kan pas als de batch COMPLETE is.
                        </span>
                      )}
                    </>
                  )}
                </div>

                <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
                  <Link
                    href={`/report/${selected}`}
                    className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted"
                  >
                    Batchrapport
                  </Link>
                  <CrossLink href={`/reports?type=batch&dimension=${selected}`}>EBR als PDF</CrossLink>
                  {detail.data.order_id && (
                    <CrossLink href={`/orders?order=${detail.data.order_id}`}>
                      order {detail.data.order_id}
                    </CrossLink>
                  )}
                  <CrossLink href={`/shopfloor?code=${selected}`}>op de werkvloer openen</CrossLink>
                </div>
              </>
            ) : (
              <p className="text-[0.8125rem] text-ink-muted">Batch niet gevonden.</p>
            )}
          </aside>
        )}
      </div>

      <NextStep
        steps={[
          {
            href: "/shopfloor",
            label: "Verpakken en verzenden",
            why: "Een goedgekeurde batch mag op de pallet.",
          },
          {
            href: "/orders",
            label: "Order sluiten",
            why: "Als alle batches van de order af zijn.",
          },
          {
            href: "/equipment",
            label: "CIP na de batch",
            why: "Na een aantal batches weigert de lijn zonder reiniging.",
          },
        ]}
      />
    </main>
  );
}
