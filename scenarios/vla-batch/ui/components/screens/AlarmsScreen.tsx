"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { CrossLink, ScreenHeader } from "@/components/nav/ScreenHeader";
import { PRIORITY_STATUS } from "@/components/toolkit/AlarmBar";
import { StatusPill } from "@/components/toolkit/StatusPill";
import { post } from "@/lib/client";
import { num, shortDateTime, shortTime } from "@/lib/format";
import { useAlarms, useKpiSummary } from "@/lib/queries";
import type { Alarm, AlarmLoad } from "@/lib/types";
import { LineSwitch, lineOfEquipment, useLineParam } from "@/components/toolkit/LineSwitch";

/**
 * Scherm 14, alarmen (spec §14).
 *
 * Exact drie prioriteiten, elk met eigen kleur, vorm en cijfer. Geparkeerd is
 * een toestand en geen vierde prioriteit, en vereist altijd een reden en een
 * einddatum: zonder beide verdwijnt een alarm stilletjes, en dat is precies wat
 * alarmmanagement moet voorkomen.
 *
 * De belasting staat tegen de ISA-18.2-richtwaarden. Dat zijn AANBEVOLEN
 * waarden en geen verplichte grenzen; zo staat het er ook.
 */

const FILTERS = [
  { id: "", label: "Alle" },
  { id: "high", label: "Hoog" },
  { id: "medium", label: "Midden" },
  { id: "low", label: "Laag" },
] as const;

function ShelveDialog({
  alarm,
  onClose,
  onDone,
}: {
  alarm: Alarm;
  onClose: () => void;
  onDone: () => void;
}) {
  const [reason, setReason] = useState("");
  const [hours, setHours] = useState(4);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const until = new Date(Date.now() + hours * 3600_000).toISOString();
      await post(`/alarms/${alarm.alarm_id}/shelve`, {
        reason,
        until,
        operator_id: "op-01",
      });
      onDone();
      onClose();
    } catch (err) {
      setError((err as { message?: string })?.message ?? "parkeren mislukt");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={submit}
      className="flex flex-col gap-2 border-t border-line bg-surface-sunken p-3"
    >
      <span className="eyebrow">Parkeren vereist een reden en een einddatum</span>
      <div className="flex flex-wrap gap-2">
        <input
          required
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="Reden, bijvoorbeeld werkorder WO-118"
          className="min-w-[16rem] flex-1 border border-line-strong bg-surface px-2 py-1 text-[0.8125rem]"
        />
        <label className="flex items-center gap-1.5 text-[0.8125rem] text-ink-muted">
          tot over
          <input
            type="number"
            min={1}
            max={72}
            value={hours}
            onChange={(e) => setHours(Number(e.target.value))}
            className="w-16 border border-line-strong bg-surface px-2 py-1 text-right num"
          />
          uur
        </label>
        <button
          type="submit"
          disabled={busy || !reason.trim()}
          className="border border-line-strong px-3 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase disabled:opacity-40"
        >
          Parkeer
        </button>
        <button
          type="button"
          onClick={onClose}
          className="px-2 py-1 text-[0.8125rem] text-ink-muted hover:text-ink"
        >
          Annuleer
        </button>
      </div>
      {error && <span className="text-[0.8125rem] text-status-alarm">{error}</span>}
    </form>
  );
}

function Load({ load }: { load?: AlarmLoad }) {
  if (!load?.available) {
    return (
      <p className="text-[0.8125rem] text-ink-muted">Nog geen belastingsgegevens in dit venster.</p>
    );
  }
  const t = load.targets?.per_hour;
  const status = !t
    ? "unset"
    : (load.per_hour ?? 0) <= t.acceptable
      ? "ok"
      : (load.per_hour ?? 0) <= t.max
        ? "warn"
        : "alarm";

  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {[
          { k: "Per uur", v: load.per_hour, t: t ? `${t.acceptable} tot ${t.max}` : null },
          { k: "Per 10 min", v: load.per_10min, t: "1 tot 2" },
          {
            k: "Tijd in flood",
            v: load.flood_time_share === undefined ? null : load.flood_time_share * 100,
            t: "onder 1 %",
          },
        ].map((m) => (
          <div key={m.k} className="flex flex-col gap-0.5">
            <span className="text-lg leading-none font-semibold num">
              {m.v === null || m.v === undefined ? "-" : num(m.v, 1)}
            </span>
            <span className="text-[0.6875rem] text-ink-muted">{m.k}</span>
            {m.t && <span className="text-[0.6875rem] text-ink-faint">richtwaarde {m.t}</span>}
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between border-t border-line pt-2 text-[0.8125rem]">
        <span className="text-[0.6875rem] text-ink-muted">Belasting tegen de richtwaarde</span>
        <StatusPill status={status as never} />
      </div>

      {load.mix_pct && (
        <div className="flex items-baseline justify-between text-[0.8125rem]">
          <span className="text-[0.6875rem] text-ink-muted">Verdeling hoog/midden/laag</span>
          <span className="font-semibold num">
            {num(load.mix_pct.high ?? 0, 0)} / {num(load.mix_pct.medium ?? 0, 0)} /{" "}
            {num(load.mix_pct.low ?? 0, 0)} %
            <span className="ml-1 font-normal text-ink-faint">
              (richtwaarde {load.mix_targets_pct?.high}/{load.mix_targets_pct?.medium}/
              {load.mix_targets_pct?.low})
            </span>
          </span>
        </div>
      )}

      <div className="flex items-baseline justify-between text-[0.8125rem]">
        <span className="text-[0.6875rem] text-ink-muted">Verouderd boven 24 uur</span>
        <span className="font-semibold num">{load.stale_over_24h ?? "-"}</span>
      </div>
    </div>
  );
}

export function AlarmsScreen() {
  const params = useSearchParams();
  const router = useRouter();
  const filter = params.get("priority") ?? "";
  // Doorklik vanaf equipment health: laat alleen de meldingen van dat
  // procesdeel zien. Filteren gebeurt client-side; /alarms kent geen
  // equipment-parameter en die erbij verzinnen zou het contract oprekken.
  const equipmentFilter = params.get("equipment");
  const lineFilter = useLineParam(true);
  const [shelving, setShelving] = useState<string | null>(null);

  const alarms = useAlarms(filter);
  const kpi = useKpiSummary("day", false);
  const load = kpi.data?.alarm_load;

  function setFilter(id: string) {
    const next = new URLSearchParams(params.toString());
    if (id) next.set("priority", id);
    else next.delete("priority");
    router.replace(next.toString() ? `?${next.toString()}` : "?", { scroll: false });
  }

  async function ack(id: string) {
    await post(`/alarms/${id}/ack`, { operator_id: "op-01" });
    await alarms.refetch();
  }

  const rows = ((alarms.data ?? []) as Alarm[]).filter(
    (a) =>
      (!equipmentFilter || a.equipment_id === equipmentFilter) &&
      // Een alarm zonder procesdeel (batchniveau) blijft bij elke lijn zichtbaar.
      (!lineFilter || !a.equipment_id || lineOfEquipment(a.equipment_id) === lineFilter),
  );

  return (
    <main className="mx-auto flex max-w-[1500px] flex-col gap-3 p-2 pb-16 sm:gap-4 sm:p-4">
      <ScreenHeader
        eyebrow="Operator"
        title="Alarmen"
        subtitle="Bevestigen en parkeren. Parkeren vraagt altijd een reden en een einddatum."
        actions={[
          { href: `/line?line=${lineFilter || "L1"}`, label: `Lijn ${lineFilter || "L1"}`, title: "Waar het alarm vandaan komt" },
          { href: "/equipment", label: "Equipment" },
          { href: "/scada", label: "SCADA" },
        ]}
      >
        <div
          role="group"
          aria-label="Filter op prioriteit"
          className="inline-flex border border-line-strong bg-surface"
        >
          {FILTERS.map((f) => (
            <button
              key={f.id}
              type="button"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
              className={`border-r border-line px-2.5 py-1 text-[0.8125rem] last:border-r-0 ${
                filter === f.id ? "bg-ink font-semibold text-canvas" : "text-ink-muted hover:text-ink"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </ScreenHeader>

      <LineSwitch allowAll />

      {equipmentFilter && (
        <p className="flex flex-wrap items-center gap-2 text-[0.8125rem]">
          alleen meldingen van <span className="mono">{equipmentFilter}</span>
          <Link href="/alarms" className="text-accent underline underline-offset-2">
            alles tonen
          </Link>
        </p>
      )}

      <div className="grid items-start gap-4 lg:[grid-template-columns:minmax(0,1.5fr)_minmax(0,1fr)]">
        <section className="tile p-0">
          <ul className="m-0 list-none p-0">
            {rows.length === 0 && (
              <li className="p-4 text-[0.8125rem] text-ink-muted">
                Geen alarmen in deze selectie.
              </li>
            )}
            {rows.map((a) => (
              <li key={a.alarm_id} className="border-b border-line last:border-b-0">
                <div
                  className={`grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-1 px-3 py-[var(--density-row)] sm:grid-cols-[5.5rem_minmax(0,1fr)_6rem_9rem] sm:items-center ${
                    a.state !== "open" ? "opacity-70" : ""
                  }`}
                >
                  <StatusPill
                    status={a.state === "shelved" ? "shelved" : PRIORITY_STATUS[a.priority]}
                  >
                    {a.state === "shelved"
                      ? "Geparkeerd"
                      : a.priority === "high"
                        ? "Hoog"
                        : a.priority === "medium"
                          ? "Midden"
                          : "Laag"}
                  </StatusPill>

                  <span className="flex min-w-0 flex-col gap-0.5">
                    <span className="text-[0.8125rem]">{a.message}</span>
                    {/* De herkomst is een doorklik, geen dood label. Een alarm
                        dat een equipment_id draagt en je niet naar dat
                        procesdeel brengt, laat je zelf zoeken. */}
                    <span className="mono text-ink-faint">
                      {a.equipment_id ? (
                        <CrossLink
                          href={`/equipment`}
                          title={`Procesdeel ${a.equipment_id}: CIP, draaiuren, OEE`}
                        >
                          {a.equipment_id}
                        </CrossLink>
                      ) : (
                        "lijn"
                      )}
                      {a.alarm_type ? `/${a.alarm_type}` : ""}
                      {a.batch_id && (
                        <>
                          {" · "}
                          <CrossLink href={`/batches?batch=${a.batch_id}`} title="Batchdetail">
                            {a.batch_id}
                          </CrossLink>
                        </>
                      )}
                      {a.state === "shelved" && a.shelved_reason
                        ? ` · ${a.shelved_reason} tot ${shortDateTime(a.shelved_until)}`
                        : ""}
                    </span>
                  </span>

                  <span className="col-start-2 text-[0.8125rem] text-ink-muted num sm:col-start-auto">{shortTime(a.ts)}</span>

                  <span className="col-start-2 flex flex-wrap justify-start gap-1.5 sm:col-start-auto sm:justify-end">
                    {a.state === "open" ? (
                      <>
                        <button
                          type="button"
                          onClick={() => ack(a.alarm_id)}
                          className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase text-ink-muted hover:border-ink-muted hover:text-ink"
                        >
                          Bevestig
                        </button>
                        <button
                          type="button"
                          onClick={() =>
                            setShelving(shelving === a.alarm_id ? null : a.alarm_id)
                          }
                          className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase text-ink-muted hover:border-ink-muted hover:text-ink"
                        >
                          Parkeer
                        </button>
                      </>
                    ) : (
                      <span className="text-[0.6875rem] font-semibold tracking-[0.04em] uppercase text-ink-faint">
                        {a.state === "shelved" ? "Geparkeerd" : "Bevestigd"}
                      </span>
                    )}
                  </span>
                </div>

                {shelving === a.alarm_id && (
                  <ShelveDialog
                    alarm={a}
                    onClose={() => setShelving(null)}
                    onDone={() => alarms.refetch()}
                  />
                )}
              </li>
            ))}
          </ul>
        </section>

        <div className="flex flex-col gap-4">
          <section className="tile flex flex-col gap-3">
            <div>
              <span className="eyebrow">Tegen de ISA-18.2-richtwaarden</span>
              <h2 className="text-base font-semibold">Alarmbelasting</h2>
            </div>
            <Load load={load} />
            <p className="text-[0.6875rem] text-ink-faint">
              Aanbevolen richtwaarden, geen verplichte grenzen. Ze gelden per operatorconsole en
              horen over minimaal dertig dagen gemeten te worden.
            </p>
          </section>

          <section className="tile flex flex-col gap-2">
            <div>
              <span className="eyebrow">Laatste periode</span>
              <h2 className="text-base font-semibold">Grootste veroorzakers</h2>
            </div>
            {load?.top_sources?.length ? (
              <>
                <ul className="m-0 list-none p-0">
                  {load.top_sources.map((s, i) => (
                    <li
                      key={s.source}
                      className="grid grid-cols-[1.25rem_minmax(0,1fr)_2.75rem] items-center gap-2 py-[var(--density-row)]"
                    >
                      <span className="text-[0.8125rem] font-semibold text-ink-faint num">
                        {i + 1}
                      </span>
                      <span className="flex min-w-0 flex-col gap-0.5">
                        <span className="mono truncate">
                          <CrossLink
                            href={`/alarms?equipment=${encodeURIComponent(s.source)}`}
                            title="Alleen de meldingen van deze bron"
                          >
                            {s.source}
                          </CrossLink>
                        </span>
                        <span className="h-1 bg-surface-sunken">
                          <i
                            className="block h-full bg-ink-muted"
                            style={{ width: `${s.share_pct ?? 0}%` }}
                          />
                        </span>
                      </span>
                      <span className="text-right text-[0.8125rem] font-semibold num">
                        {num(s.share_pct ?? 0, 0)} %
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="text-[0.6875rem] text-ink-faint">
                  Een eigenschap van deze data, geen geciteerd industriecijfer.
                </p>
              </>
            ) : (
              <p className="text-[0.8125rem] text-ink-muted">Geen meldingen in dit venster.</p>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
