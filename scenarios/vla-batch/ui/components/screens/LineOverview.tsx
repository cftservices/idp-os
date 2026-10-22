"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AlarmBar } from "@/components/toolkit/AlarmBar";
import { DeviationBand, StaleVeil } from "@/components/toolkit/Indicators";
import { StatusPill, type Status } from "@/components/toolkit/StatusPill";
import { getUi, post } from "@/lib/client";
import { LINE_LABEL, LineSwitch, useLineParam } from "@/components/toolkit/LineSwitch";
import { EMPTY, num, shortTime, value as fmt } from "@/lib/format";
import { POLL, useAlarms, useKpiSummary } from "@/lib/queries";
import type { Alarm } from "@/lib/types";

/**
 * Scherm 13, het L1-fabrieksoverzicht (spec §13).
 *
 * Het beeld dat de operator openhoudt. Beantwoordt in een blik: draait de lijn,
 * waar loopt iets uit de band, en hoeveel ruimte is er nog tot een grens.
 *
 * Twee dingen die dit scherm anders doet dan de oude SPA:
 *   - functioneel en niet schematisch. Geen P&ID met leidingen en getallen,
 *     maar vijf processtappen met kwalitatieve indicatoren;
 *   - nooit een kaal getal. Elke kernmeting heeft een band, een marker en de
 *     afstand tot de grens.
 */

type Step = {
  id: string;
  name: string;
  equipment: string;
  primary: {
    label: string;
    unit: string;
    value: number | null;
    ts: string | null;
    age_s: number | null;
    quality: string;
  };
  secondary: Array<{
    label: string;
    unit: string;
    value: number | null;
    stale: boolean;
    /** Uit het recept in plaats van van de bus: configuratie, geen meting. */
    from_config: boolean;
  }>;
  spec: { min: number; max: number } | null;
  /** Schaal met bron, of null als er geen te verantwoorden schaal is. */
  scale: { min: number; max: number; source: string } | null;
  /** Reden waarom de spec nog niet wordt toegepast, of null. */
  spec_pending: string | null;
  distance: { label: string; value: number; unit: string } | null;
  tone: "neutral" | "warn" | "alarm";
  stale: boolean;
};

type LinePayload = {
  available: boolean;
  engine_reachable: boolean;
  bus_has_tags: boolean;
  model_available: boolean;
  model_path?: string;
  stale_threshold_s: number;
  stale_threshold_from_model: boolean;
  steps: Step[];
  line: {
    available?: boolean;
    batch?: { batch_id: string; state: string; verdict: string | null };
    doses?: Array<{
      material_id: string;
      qty_target: number;
      qty_actual: number | null;
      tol_min: number | null;
      tol_max: number | null;
      in_tolerance: boolean | null;
    }>;
    dose_totals?: { target_kg: number; actual_kg: number; pct: number | null };
    filling?: {
      packs_total: number;
      packs_target: number | null;
      packs_progress_pct: number | null;
      packs_rate_per_min: number;
      pallets: number | null;
    };
  } | null;
};

const PHASES = ["DOSING", "COOKING", "COOLING", "FILLING", "COMPLETE"] as const;

function stepStatus(step: Step): Status {
  if (step.stale) return "stale";
  if (step.tone === "alarm") return "alarm";
  if (step.tone === "warn") return "warn";
  if (step.primary.value === null) return "unset";
  // Nog niet beoordeeld is GEEN groen. Anders leest "wordt nog gekookt" als
  // "voldoet aan de spec", en dat is precies het vals-groen dat de scan-gate
  // elders met een derde toestand voorkomt.
  if (step.spec_pending) return "unset";
  return "ok";
}

function StepCard({ step }: { step: Step }) {
  const status = stepStatus(step);
  const body = (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-col gap-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className="text-[0.6875rem] text-ink-muted">{step.primary.label}</span>
          <span
            className={`text-2xl leading-none font-semibold tracking-[-0.02em] num ${
              step.stale
                ? "text-ink-faint"
                : step.tone === "alarm"
                  ? "text-status-alarm"
                  : step.tone === "warn"
                    ? "text-status-warn"
                    : ""
            }`}
          >
            {step.primary.value === null ? EMPTY : fmt(step.primary.value, step.primary.unit)}
            {step.primary.unit && (
              <span className="ml-0.5 text-[0.6875rem] font-medium text-ink-muted">
                {step.primary.unit}
              </span>
            )}
          </span>
        </div>
        {/* Nooit een kaal getal: band, marker en de afstand tot de grens. */}
        <DeviationBand
          value={step.primary.value}
          spec={step.spec}
          min={step.scale?.min}
          max={step.scale?.max}
          tone={step.stale ? "neutral" : step.tone}
        />
      </div>

      {step.spec_pending && (
        <p className="text-[0.6875rem] text-ink-faint">{step.spec_pending}</p>
      )}

      {step.distance && (
        <div className="flex items-baseline justify-between gap-2 text-[0.8125rem]">
          <span className="text-[0.6875rem] text-ink-muted">{step.distance.label}</span>
          <span
            className={`font-semibold num ${
              step.tone === "alarm"
                ? "text-status-alarm"
                : step.tone === "warn"
                  ? "text-status-warn"
                  : ""
            }`}
          >
            {num(step.distance.value, 0)} {step.distance.unit}
          </span>
        </div>
      )}

      {step.secondary.map((s) => (
        <div key={s.label} className="flex items-baseline justify-between gap-2 text-[0.8125rem]">
          <span className="text-[0.6875rem] text-ink-muted">
            {s.label}
            {/* Herkomst hoort zichtbaar te zijn. Een setpoint uit het recept
                naast een live meting zetten zonder onderscheid, is liegen over
                waar het getal vandaan komt. */}
            {s.from_config && (
              <span className="ml-1 text-[0.625rem] text-ink-faint" title="Uit het recept, geen meting">
                recept
              </span>
            )}
          </span>
          <span className={`font-semibold num ${s.from_config ? "text-ink-muted" : ""}`}>
            {s.value === null ? EMPTY : `${fmt(s.value, s.unit)} ${s.unit}`.trim()}
          </span>
        </div>
      ))}
    </div>
  );

  return (
    <article
      className="flex min-w-0 flex-col gap-2.5 bg-surface p-[var(--tile-pad)]"
      style={
        step.tone !== "neutral" && !step.stale
          ? {
              boxShadow: `inset 3px 0 0 var(--color-status-${step.tone === "alarm" ? "alarm" : "warn"})`,
            }
          : undefined
      }
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[0.8125rem] font-semibold">{step.name}</span>
        <StatusPill status={status} />
      </div>
      {/* Het procesdeel is een doorklik naar zijn onderhoud: draaiuren, CIP,
          opwarmtrend. Dat is de vraag die volgt op "deze stap loopt uit band". */}
      <div className="mono text-ink-faint">
        <Link
          href="/equipment"
          title={`${step.equipment}: CIP, draaiuren, OEE`}
          className="underline underline-offset-2 hover:text-ink-muted"
        >
          {step.equipment}
        </Link>
      </div>

      {step.stale ? <StaleVeil>{body}</StaleVeil> : body}

      {step.stale && (
        <span className="inline-flex items-center gap-1.5 text-[0.6875rem] font-semibold text-status-stale">
          {step.primary.age_s !== null ? `${Math.round(step.primary.age_s)} s oud` : "geen live data"}
          {step.primary.quality !== "GOOD" && ` · quality ${step.primary.quality}`}
        </span>
      )}
    </article>
  );
}

export function LineOverview() {
  const lineId = useLineParam();
  const line = useQuery<LinePayload>({
    queryKey: ["ui", "line", lineId],
    queryFn: ({ signal }) => getUi<LinePayload>(`/line?line=${lineId}`, signal),
    refetchInterval: POLL.live,
    refetchIntervalInBackground: false,
    staleTime: 0,
    retry: 1,
  });

  const alarms = useAlarms();
  const kpi = useKpiSummary("shift", false);

  async function ack(id: string) {
    await post(`/alarms/${id}/ack`, { operator_id: "op-01" });
    await alarms.refetch();
  }

  const batch = line.data?.line?.batch;
  const filling = line.data?.line?.filling;
  const doses = line.data?.line?.doses ?? [];
  const totals = line.data?.line?.dose_totals;

  return (
    <main className="mx-auto flex max-w-[1500px] flex-col gap-3 p-2 pb-16 sm:gap-4 sm:p-4">
      <AlarmBar
        alarms={(alarms.data ?? []) as Alarm[]}
        load={kpi.data?.alarm_load}
        onAck={ack}
      />

      <LineSwitch />

      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <span className="eyebrow">Level 1, operator</span>
          <h1 className="text-lg font-semibold tracking-[-0.015em]">DairyWorks, {LINE_LABEL[lineId]}</h1>
          <div className="flex flex-wrap gap-x-6 gap-y-1 text-[0.8125rem] text-ink-muted">
            {batch ? (
              <span>
                Batch{" "}
                <Link
                  href={`/batches?batch=${batch.batch_id}`}
                  className="mono font-semibold text-accent underline underline-offset-2 hover:text-ink"
                >
                  {batch.batch_id}
                </Link>{" "}
                &middot; {batch.state}
              </span>
            ) : (
              <span>
                Geen actieve batch,{" "}
                <Link
                  href="/scada"
                  className="font-semibold text-accent underline underline-offset-2 hover:text-ink"
                >
                  start er een
                </Link>
              </span>
            )}
            <span className="num">{shortTime(new Date().toISOString())}</span>
            <span className="flex gap-3">
              <Link href="/shopfloor" className="underline underline-offset-2 hover:text-ink">
                werkvloer
              </Link>
              <Link href="/scada" className="underline underline-offset-2 hover:text-ink">
                bediening
              </Link>
              <Link href="/alarms" className="underline underline-offset-2 hover:text-ink">
                alarmen
              </Link>
            </span>
          </div>
        </div>

        {batch && (
          <div className="flex border border-line-strong" role="list" aria-label="Batchfase">
            {PHASES.map((p) => {
              const done = PHASES.indexOf(p) < PHASES.indexOf(batch.state as never);
              const now = p === batch.state;
              return (
                <span
                  key={p}
                  role="listitem"
                  aria-current={now ? "step" : undefined}
                  className={`border-r border-line px-3 py-1 text-[0.6875rem] font-semibold tracking-[0.06em] uppercase last:border-r-0 ${
                    now
                      ? "bg-ink text-canvas"
                      : done
                        ? "text-status-done"
                        : "text-ink-faint"
                  }`}
                >
                  {p}
                </span>
              );
            })}
          </div>
        )}
      </header>

      {line.isError || line.data?.engine_reachable === false ? (
        <section className="tile flex flex-col items-start gap-2">
          <StatusPill status="alarm">Engine niet bereikbaar</StatusPill>
          <button
            type="button"
            onClick={() => line.refetch()}
            className="border border-line-strong px-3 py-1 text-[0.8125rem] font-semibold hover:border-ink-muted"
          >
            Opnieuw proberen
          </button>
        </section>
      ) : (
        <>
          <section
            aria-label="Processtappen"
            className="grid gap-px border border-line-strong bg-line-strong [grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr))]"
          >
            {(line.data?.steps ?? []).map((s) => (
              <StepCard key={s.id} step={s} />
            ))}
          </section>

          {line.data?.model_available === false && (
            <p className="flex items-center gap-2 text-[0.8125rem] text-ink-muted">
              <StatusPill status="unset">Geen fabrieksmodel</StatusPill>
              De specbanden en drempels ontbreken, dus de stappen tonen een waarde zonder band in
              plaats van een verzonnen grens. Gezocht op{" "}
              <span className="mono">{line.data?.model_path}</span>.
            </p>
          )}

          {!line.data?.bus_has_tags && (
            <p className="text-[0.8125rem] text-ink-muted">
              Geen tags op de bus. De stappen tonen een streepje in plaats van een verzonnen waarde;
              de MES-gegevens hieronder komen wel door.
            </p>
          )}

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="tile flex flex-col gap-3">
              <div>
                <span className="eyebrow">Doseren tegen tolerantie</span>
                <h2 className="text-base font-semibold">Charge</h2>
              </div>
              {doses.length === 0 ? (
                <p className="text-[0.8125rem] text-ink-muted">Nog geen doseringen geboekt.</p>
              ) : (
                <div className="flex flex-col gap-1.5">
                  {doses.map((d) => {
                    const pct =
                      d.qty_actual !== null && d.qty_target
                        ? Math.min(100, (d.qty_actual / d.qty_target) * 100)
                        : 0;
                    // De tolerantie komt van de engine mee; geen drempel in de client.
                    const out = d.in_tolerance === false;
                    return (
                      <div
                        key={d.material_id}
                        className="grid grid-cols-[3.5rem_1fr_3.5rem] items-center gap-2 sm:grid-cols-[4.5rem_1fr_4.5rem]"
                      >
                        <span className="text-[0.6875rem] text-ink-muted">{d.material_id}</span>
                        <span className="relative h-2 border border-line bg-surface-sunken">
                          {d.tol_min !== null && d.tol_max !== null && d.qty_target > 0 && (
                            <i
                              className="absolute inset-y-0 bg-band-outer"
                              style={{
                                left: `${(d.tol_min / d.qty_target) * 100}%`,
                                width: `${((d.tol_max - d.tol_min) / d.qty_target) * 100}%`,
                              }}
                            />
                          )}
                          <i
                            className={`absolute top-1/2 left-0 h-[3px] -translate-y-1/2 ${
                              out ? "bg-status-alarm" : "bg-ink"
                            }`}
                            style={{ width: `${pct}%` }}
                          />
                        </span>
                        <span className="text-right text-[0.6875rem] font-semibold num">
                          {d.qty_actual === null ? EMPTY : num(d.qty_actual, 0)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
              {totals && (
                <div className="flex items-baseline justify-between border-t border-line pt-2 text-[0.8125rem]">
                  <span className="text-[0.6875rem] text-ink-muted">Totaal</span>
                  <span className="font-semibold num">
                    {num(totals.actual_kg, 0)} van {num(totals.target_kg, 0)} kg
                    {totals.pct !== null && ` (${num(totals.pct, 0)} %)`}
                  </span>
                </div>
              )}
            </section>

            <section className="tile flex flex-col gap-3">
              <div>
                <span className="eyebrow">Vullen</span>
                <h2 className="text-base font-semibold">Voortgang</h2>
              </div>
              {filling ? (
                <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-[0.8125rem]">
                  <dt className="text-[0.6875rem] text-ink-muted">Pakken</dt>
                  <dd className="text-right font-semibold num">
                    {num(filling.packs_total, 0)}
                    {filling.packs_target !== null && ` van ${num(filling.packs_target, 0)}`}
                  </dd>
                  <dt className="text-[0.6875rem] text-ink-muted">Voortgang</dt>
                  <dd className="text-right font-semibold num">
                    {filling.packs_progress_pct === null
                      ? EMPTY
                      : `${num(filling.packs_progress_pct, 0)} %`}
                  </dd>
                  <dt className="text-[0.6875rem] text-ink-muted">Snelheid</dt>
                  <dd className="text-right font-semibold num">
                    {num(filling.packs_rate_per_min, 0)} pk/min
                  </dd>
                  <dt className="text-[0.6875rem] text-ink-muted">Pallets</dt>
                  <dd className="text-right font-semibold num">
                    {filling.pallets === null ? EMPTY : filling.pallets}
                  </dd>
                </dl>
              ) : (
                <p className="text-[0.8125rem] text-ink-muted">Nog geen productie geboekt.</p>
              )}
              <p className="text-[0.6875rem] text-ink-faint">
                Snelheid, voortgang en pallets komen kant-en-klaar uit de engine, zodat het scherm
                en het batchrapport hetzelfde getal tonen.
              </p>
            </section>
          </div>
        </>
      )}
    </main>
  );
}
