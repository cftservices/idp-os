"use client";

import { useState } from "react";
import Link from "next/link";
import { NextStep } from "@/components/nav/NextStep";
import { CrossLink, ScreenHeader } from "@/components/nav/ScreenHeader";
import { Sparkline } from "@/components/toolkit/Indicators";
import { StatusPill, type Status } from "@/components/toolkit/StatusPill";
import { post } from "@/lib/client";
import { EMPTY, num } from "@/lib/format";
import { useEquipmentHealth, useKpiSummary, useOee } from "@/lib/queries";
import type { Refusal } from "@/lib/refusal";
import { LineSwitch, lineOfEquipment, useLineParam } from "@/components/toolkit/LineSwitch";

/**
 * Scherm 8, equipment health, met scherm 9 (OEE) erin gevouwen.
 *
 * Waarom invouwen: OEE was een tweede weergave van dezelfde vijf rijen, met
 * wederzijdse doorklik. Erger, performance staat voor alles behalve de
 * kookketel hardgezet op 1.0 en quality is lijnbreed, dus vier van de vijf
 * rijen verschilden uitsluitend in beschikbaarheid. Vijf zelfstandig ogende
 * OEE-getallen die feitelijk een getal met vier kopieen zijn, verdienen twee
 * kolommen en geen eigen scherm.
 *
 * De degradatietrend staat als sparkline met basislijnband en de afstand tot de
 * CBM-drempel als apart getal. Nooit `120 -> 138 -> 156` als kale tekst.
 */

type Health = {
  equipment_id: string;
  area?: string;
  state: string;
  running_hours?: number;
  batches_since_cip?: number;
  dirty?: boolean;
  heatup_trend?: Array<{ heatup_sec: number }>;
  /** Basislijn en CBM-drempel, berekend in de engine. Niet hier. */
  heatup_baseline_sec?: number | null;
  heatup_threshold_sec?: number | null;
  open_cbm_alerts?: Array<{ message: string; alert_type: string }>;
};

type Oee = {
  equipment_id: string;
  availability: number;
  performance: number;
  quality: number;
  oee: number;
};

function eqStatus(e: Health): Status {
  switch (e.state) {
    case "Running":
      return "run";
    case "Dirty":
      return "warn";
    // Nooit grijs: een defect procesdeel mag er niet uitzien als een
    // stilstaand procesdeel.
    case "Down":
    case "Error":
      return "alarm";
    case "Allocated":
      return "idle";
    default:
      return "idle";
  }
}

export function EquipmentHealth() {
  const healthAll = useEquipmentHealth<Health[]>();
  const lineFilter = useLineParam(true);
  // Filteren in de client: de lijst is klein en dan deelt dit scherm zijn
  // query met de rest van de app.
  const health = {
    ...healthAll,
    data: (healthAll.data ?? []).filter((e) => !lineFilter || lineOfEquipment(e.equipment_id) === lineFilter),
  };
  const oee = useOee<Oee[]>();
  const kpi = useKpiSummary("week", false);
  const [busy, setBusy] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);

  // De OEE-norm komt uit kpi_targets in het model, niet uit een drempel in de
  // client. De oude SPA had 0,75 hardgecodeerd.
  const oeeTarget = kpi.data?.kpis.find((k) => k.kpi_id === "oee")?.target ?? null;

  async function cip(id: string) {
    setBusy(id);
    setRefusal(null);
    try {
      await post(`/equipment/${id}/cip`, { operator_id: "op-01" });
      await Promise.all([health.refetch(), oee.refetch()]);
    } catch (e) {
      setRefusal(e as Refusal);
    } finally {
      setBusy(null);
    }
  }

  const byId = new Map((oee.data ?? []).map((o) => [o.equipment_id, o]));

  return (
    <main className="mx-auto flex max-w-[1440px] flex-col gap-4 p-3 pb-16 sm:gap-5 sm:p-6">
      <ScreenHeader
        eyebrow="Equipment health, inclusief OEE"
        title="Procesdelen"
        subtitle="Reinigen (CIP), draaiuren, opwarmtrend en beschikbaarheid per procesdeel."
        actions={[
          { href: "/alarms", label: "Alarmen", title: "Meldingen van deze procesdelen" },
          { href: "/reports?type=equipment", label: "Onderhoudsrapport" },
          { href: `/line?line=${lineFilter || "L1"}`, label: `Lijn ${lineFilter || "L1"}` },
        ]}
      >
        {oeeTarget !== null && (
          <span className="text-[0.8125rem] text-ink-muted num">
            OEE-norm {num(oeeTarget * 100, 0)} %
          </span>
        )}
      </ScreenHeader>

      <LineSwitch allowAll />

      {refusal && (
        <section className="tile flex flex-col items-start gap-2 border-l-[3px] border-l-status-alarm">
          <StatusPill status="alarm">Geweigerd</StatusPill>
          <p className="text-[0.8125rem]">{refusal.message}</p>
        </section>
      )}

      <section className="tile table-scroll p-0">
        <table className="w-full border-collapse text-[0.8125rem]">
          <thead>
            <tr className="border-b border-line text-left">
              {["Procesdeel", "Status", "Draaiuren", "Sinds CIP", "Opwarmtrend", "Beschikb.", "OEE", ""].map(
                (h) => (
                  <th
                    key={h}
                    className="px-3 py-2 text-[0.6875rem] font-semibold tracking-[0.06em] uppercase text-ink-faint"
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {(health.data ?? []).length === 0 && (
              <tr>
                <td colSpan={8} className="px-3 py-4 text-ink-muted">
                  Geen procesdelen bekend. Draait de engine, en is er al een batch geweest?
                </td>
              </tr>
            )}
            {(health.data ?? []).map((e) => {
              const o = byId.get(e.equipment_id);
              const trend = (e.heatup_trend ?? []).map((h) => h.heatup_sec);
              // Basislijn en drempel komen uit de ENGINE. Tot de audit van
              // 02-08 stond hier `base * 1.35` met een commentaar dat beweerde
              // dat het uit de configuratie kwam. Dat was onwaar: de UI
              // verzon de drempel zelf, en dat is precies de regel die dit
              // project overal oplegt (een KPI wordt op een plek berekend).
              // Ontbreekt de drempel, dan tonen we hem niet in plaats van er
              // een te bedenken.
              const base = e.heatup_baseline_sec ?? null;
              const threshold = e.heatup_threshold_sec ?? null;
              const last = trend.length ? trend[trend.length - 1] : null;
              const under = oeeTarget !== null && o ? o.oee < oeeTarget : false;

              return (
                <tr key={e.equipment_id} className="border-b border-line last:border-b-0">
                  <td className="px-3 py-[var(--density-row)] mono">
                    <CrossLink
                      href={`/reports?type=equipment&dimension=${e.equipment_id}`}
                      title="Onderhoudsrapport van dit procesdeel"
                    >
                      {e.equipment_id}
                    </CrossLink>
                  </td>
                  <td className="px-3 py-[var(--density-row)]">
                    <StatusPill status={eqStatus(e)}>{e.state}</StatusPill>
                  </td>
                  <td className="px-3 py-[var(--density-row)] num">
                    {e.running_hours === undefined ? EMPTY : `${num(e.running_hours, 0)} u`}
                  </td>
                  <td className="px-3 py-[var(--density-row)] num">
                    {e.batches_since_cip ?? EMPTY}
                  </td>
                  <td className="w-40 px-3 py-[var(--density-row)]">
                    {trend.length > 1 ? (
                      <div className="flex flex-col gap-0.5">
                        <Sparkline
                          values={trend}
                          band={base !== null && threshold !== null ? [base, threshold] : null}
                          tone={
                            last !== null && threshold !== null && last > threshold
                              ? "warn"
                              : "neutral"
                          }
                          height={22}
                          label={`opwarmtrend ${e.equipment_id}`}
                        />
                        {last !== null && threshold !== null && (
                          <span className="text-[0.625rem] text-ink-faint num">
                            {last > threshold
                              ? `${num(last - threshold, 0)} s boven drempel`
                              : `${num(threshold - last, 0)} s tot drempel`}
                          </span>
                        )}
                      </div>
                    ) : (
                      <span className="text-ink-faint">{EMPTY}</span>
                    )}
                  </td>
                  <td className="px-3 py-[var(--density-row)] num">
                    {o ? `${num(o.availability * 100, 0)} %` : EMPTY}
                  </td>
                  <td className="px-3 py-[var(--density-row)] num">
                    {o ? (
                      <span className={under ? "font-semibold text-status-warn" : ""}>
                        {num(o.oee * 100, 0)} %
                      </span>
                    ) : (
                      EMPTY
                    )}
                  </td>
                  <td className="px-3 py-[var(--density-row)] text-right">
                    <span className="flex items-center justify-end gap-1.5">
                      <Link
                        href={`/alarms?equipment=${e.equipment_id}`}
                        title="Meldingen van dit procesdeel"
                        className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase text-ink-muted hover:border-ink-muted hover:text-ink"
                      >
                        Meldingen
                      </Link>
                      <button
                        type="button"
                        disabled={busy === e.equipment_id}
                        onClick={() => cip(e.equipment_id)}
                        className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase text-ink-muted hover:border-ink-muted hover:text-ink disabled:opacity-40"
                      >
                        CIP
                      </button>
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      {(health.data ?? []).some((e) => (e.open_cbm_alerts ?? []).length > 0) && (
        <section className="tile flex flex-col gap-2">
          <span className="eyebrow">Open onderhoudsmeldingen</span>
          {(health.data ?? []).flatMap((e) =>
            (e.open_cbm_alerts ?? []).map((a, i) => (
              <div key={`${e.equipment_id}-${i}`} className="flex items-center gap-2 text-[0.8125rem]">
                <StatusPill status="warn">{a.alert_type}</StatusPill>
                <span>{a.message}</span>
                <Link
                  href={`/reports?type=equipment&dimension=${e.equipment_id}`}
                  className="text-accent underline underline-offset-2"
                >
                  rapport
                </Link>
              </div>
            )),
          )}
        </section>
      )}

      <p className="text-[0.8125rem] text-ink-muted">
        OEE staat hier als diagnostische kolom en niet als ranglijst. Voor vier van de vijf
        procesdelen is performance vastgezet en is quality lijnbreed, dus die rijen verschillen
        uitsluitend in beschikbaarheid. Een OEE-ranglijst zou een verschil suggereren dat er niet is.
      </p>

      <NextStep
        steps={[
          {
            href: "/scada",
            label: "Batch aanmaken na de CIP",
            why: "Een gereinigd procesdeel accepteert weer een batch.",
          },
          {
            href: "/management",
            label: "Wat kost deze stilstand",
            why: "Het verliesblok zet de uren om in geld.",
          },
          {
            href: "/reports?type=period&days=30",
            label: "Periodrapport, 30 dagen",
            why: "Draaiuren en meldingen over de maand.",
          },
        ]}
      />
    </main>
  );
}
