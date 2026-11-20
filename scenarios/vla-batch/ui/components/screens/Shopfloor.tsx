"use client";

import { useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { NextStep } from "@/components/nav/NextStep";
import { CrossLink, ScreenHeader } from "@/components/nav/ScreenHeader";
import { RefusalPanel } from "@/components/toolkit/RefusalPanel";
import { StatusPill, type Status } from "@/components/toolkit/StatusPill";
import { post } from "@/lib/client";
import { EMPTY, num, shortTime } from "@/lib/format";
import { useBatch, useHandlingUnits, useSamples } from "@/lib/queries";
import type { Refusal } from "@/lib/refusal";

/**
 * De werkvloerflow: de HELE dienst, van scannen tot verzenden.
 *
 * In v0.5 deed scherm 4 vijf banen tegelijk: setpoint-console, batchbesturing,
 * de volledige scan- en weegapplicatie, de HU-flow en fault-injectie. Dat is
 * een werkvloerapplicatie in een SCADA-scherm gepropt, terwijl de operator die
 * dat werk doet nooit een kook-setpoint zet. Die splitsing blijft.
 *
 * Maar bij het uit elkaar halen zijn vijf handelingen kwijtgeraakt die de oude
 * applicatie wel had: monster nemen, monsterlabel herdrukken, productie boeken,
 * een pallet maken en die inslaan en verschepen. Zonder die vijf loopt de keten
 * dood na het commit en is er niets te verschepen. Ze staan hier weer, in de
 * volgorde waarin de operator ze doorloopt.
 *
 * DE SCAN-GATE HEEFT DRIE TOESTANDEN, NIET TWEE:
 *   groen    order gescand en er is een batch, panel open
 *   NEUTRAAL order bestaat maar heeft nog geen batch: panel DICHT, kaarten
 *            geleegd, met de tekst dat er eerst een batch moet komen
 *   rood     geweigerd
 *
 * Die middelste is er bewust ingezet om vals-groen te voorkomen. Zonder hem
 * lijkt de gate open terwijl er niets te wegen valt.
 *
 * Een AFGERONDE batch opent alleen het logistieke deel (pallet, inslag,
 * verzending): wegen en boeken kan daar niet meer. Ook een afgekeurde batch
 * opent zo, met opzet: de weigering "niet goedgekeurd" bij de pallet is het
 * punt dat de demo laat zien.
 */

type GateState = "idle" | "open" | "logistics" | "no-batch" | "rejected";

/** Antwoord van POST /scan/order (vla/scan.py scan_order). */
type ScanResult = {
  order_id?: string | null;
  batch_id?: string | null;
  phase?: "dosing" | "logistics" | null;
  materials?: Array<{
    material_id: string;
    qty_target: number;
    qty_staged?: number | null;
    remaining?: number | null;
  }>;
};

type Sample = {
  sample_id: string;
  sample_type: string;
  status: string;
  result: string;
  value: number | null;
  unit: string | null;
  ts: string;
};

type Hu = {
  hu_id: string;
  batch_id: string;
  packs_count: number;
  status: string;
  location?: string | null;
};

type BatchDetail = {
  batch_id: string;
  state: string;
  verdict: string | null;
  packs_total?: number;
};

const OPERATOR = "op-01";

/** Uit vla/model.py SAMPLE_TYPES. Afwijken hiervan levert een 400. */
const SAMPLE_TYPES = [
  { id: "viscosity", label: "viscositeit" },
  { id: "cook_temp", label: "kooktemperatuur" },
  { id: "hold", label: "hold" },
  { id: "dose_check", label: "doseercontrole" },
] as const;

/** Uit vla/model.py HU_STATUSES. */
const HU_LABEL: Record<string, string> = {
  wrapped: "gewikkeld",
  stored: "opgeslagen",
  awaiting_shipment: "wacht op verzending",
  shipped: "verzonden",
};

function huStatus(status: string): Status {
  if (status === "shipped") return "done";
  if (status === "awaiting_shipment") return "run";
  return "idle";
}

function sampleStatus(result: string): Status {
  return result === "fail" ? "alarm" : "ok";
}

export function Shopfloor() {
  const params = useSearchParams();
  const router = useRouter();

  const [code, setCode] = useState(params.get("code") ?? "");
  const [gate, setGate] = useState<GateState>("idle");
  const [result, setResult] = useState<ScanResult | null>(null);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [sampleType, setSampleType] = useState<string>("viscosity");
  const [packs, setPacks] = useState(100);
  const [huPacks, setHuPacks] = useState(50);
  const [lot, setLot] = useState("");
  const [labelMaterial, setLabelMaterial] = useState("");

  const batchId =
    gate === "open" || gate === "logistics" ? (result?.batch_id ?? null) : null;
  const batch = useBatch<BatchDetail>(batchId);
  const samples = useSamples<Sample[]>(batchId);
  const hus = useHandlingUnits<Hu[]>(batchId);

  /**
   * Een handeling met eenduidige foutafhandeling. Bewust GEEN retry meegegeven:
   * een weging, een boeking of een verzending nog eens uitvoeren zonder dat de
   * operator dat vraagt is dubbel boeken.
   */
  async function run(fn: () => Promise<unknown>, label: string) {
    setBusy(true);
    setRefusal(null);
    setMessage(null);
    try {
      await fn();
      setMessage(`${label} geboekt`);
    } catch (err) {
      setRefusal(err as Refusal);
    } finally {
      setBusy(false);
    }
  }

  async function scan(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setRefusal(null);
    setMessage(null);
    try {
      const res = await post<ScanResult>("/scan/order", { code, operator_id: OPERATOR });
      setResult(res);
      // De middelste toestand: order gevonden, batch nog niet.
      setGate(
        !res.batch_id ? "no-batch" : res.phase === "logistics" ? "logistics" : "open",
      );
      // De code in de URL houden maakt de dienst deelbaar en overleeft een
      // refresh op de tablet.
      const next = new URLSearchParams(params.toString());
      next.set("code", code);
      router.replace(`?${next.toString()}`, { scroll: false });
    } catch (err) {
      setResult(null);
      setGate("rejected");
      setRefusal(err as Refusal);
    } finally {
      setBusy(false);
    }
  }

  /** Een losse labelscan. Een grondstof die niet in het recept zit weigert. */
  async function scanLabel(e: React.FormEvent) {
    e.preventDefault();
    const material = labelMaterial.trim();
    if (!batchId || !material || !lot.trim()) return;
    await run(
      () =>
        post("/scan/label", {
          batch_id: batchId,
          material_id: material,
          lot_no: lot.trim(),
          operator_id: OPERATOR,
        }),
      `Label ${material}`,
    );
  }

  async function weigh(materialId: string, qty: number, total: boolean) {
    if (!batchId) return;
    await run(async () => {
      // Altijd eerst de labelscan, dan pas de weging. Twee calls, zoals de
      // engine ze verwacht; er is geen transactie.
      await post("/scan/label", {
        batch_id: batchId,
        material_id: materialId,
        lot_no: lot.trim(),
        operator_id: OPERATOR,
      });
      const g = await post<{ booked: number; remaining: number }>("/scan/weigh", {
        batch_id: batchId,
        material_id: materialId,
        qty_kg: total ? null : qty,
        lot_no: lot.trim(),
        operator_id: OPERATOR,
        total,
      });
      // De weging geeft de nieuwe stand terug; zonder dit blijft de regel
      // de stand van het scanmoment tonen.
      setResult((r) =>
        r
          ? {
              ...r,
              materials: (r.materials ?? []).map((m) =>
                m.material_id === materialId
                  ? { ...m, qty_staged: g.booked, remaining: g.remaining }
                  : m,
              ),
            }
          : r,
      );
    }, materialId);
  }

  async function commit() {
    if (!batchId) return;
    setBusy(true);
    setRefusal(null);
    setMessage(null);
    try {
      const res = await post<{ booked_materials?: string[] }>("/scan/report", {
        batch_id: batchId,
        operator_id: OPERATOR,
      });
      setMessage(`Gecommit: ${(res.booked_materials ?? []).join(", ") || "niets open"}`);
    } catch (err) {
      setRefusal(err as Refusal);
    } finally {
      setBusy(false);
    }
  }

  const packsTotal = batch.data?.packs_total ?? 0;
  const huBooked = (hus.data ?? []).reduce((sum, h) => sum + (h.packs_count ?? 0), 0);
  const huLeft = Math.max(0, packsTotal - huBooked);

  return (
    <main className="mx-auto flex max-w-4xl flex-col gap-3 p-2 pb-16 sm:gap-4 sm:p-4">
      <ScreenHeader
        eyebrow="Werkvloer, de hele dienst"
        title="Afwegen, boeken, verzenden"
        subtitle="Scan een order- of batchcode; de rest van de keten opent daaronder."
        actions={[
          { href: "/orders", label: "Orders", title: "Order aanmaken of een batch starten" },
          { href: "/line", label: "Lijn L1" },
          { href: "/alarms", label: "Alarmen" },
        ]}
      />

      <form onSubmit={scan} className="tile flex flex-wrap items-end gap-3">
        <label className="flex flex-1 flex-col gap-1 text-[0.8125rem]">
          <span className="eyebrow">Order- of batchcode</span>
          <input
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder="ORD-2210 of B-1042"
            className="border border-line-strong bg-surface px-2 py-1.5 mono"
          />
        </label>
        <button
          type="submit"
          disabled={busy}
          className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
        >
          Scannen
        </button>

        <span className="flex items-center gap-2">
          {gate === "open" && <StatusPill status="ok">Gate open</StatusPill>}
          {gate === "logistics" && (
            <StatusPill status="done">Batch afgerond, alleen logistiek</StatusPill>
          )}
          {/* NEUTRAAL, niet groen en niet rood. */}
          {gate === "no-batch" && <StatusPill status="idle">Order zonder batch</StatusPill>}
          {gate === "rejected" && <StatusPill status="alarm">Geweigerd</StatusPill>}
        </span>
      </form>

      {gate === "no-batch" && (
        <p className="text-[0.8125rem] text-ink-muted">
          Deze order heeft nog geen batch.{" "}
          <CrossLink href={`/orders${result?.order_id ? `?order=${result.order_id}` : ""}`}>
            Maak er eerst een aan op het orderscherm
          </CrossLink>
          , of via <CrossLink href="/scada">de SCADA-console</CrossLink>.
        </p>
      )}

      {refusal && (
        <RefusalPanel
          refusal={refusal}
          operatorId={OPERATOR}
          onCleared={() => setRefusal(null)}
          // Bewust GEEN onRetry: een weeg- of boekhandeling opnieuw uitvoeren
          // zonder dat de operator dat vraagt kan dubbel boeken.
        />
      )}

      {message && <p className="text-[0.8125rem] text-ink-muted">{message}</p>}

      {/* Panel alleen open bij een echte gate. De kaarten worden geleegd zodra
          de gate dat niet is, anders blijft er oude context staan. */}
      {(gate === "open" || gate === "logistics") && batchId && (
        <>
          {gate === "logistics" && (
            <p className="text-[0.8125rem] text-ink-muted">
              Batch <span className="mono">{batchId}</span> is afgerond
              {batch.data?.verdict ? ` (${batch.data.verdict})` : ""}: wegen, monsters en
              productie boeken kan niet meer. Alleen pallets, inslag en verzending.
            </p>
          )}
          {gate === "open" && (
          <>
          <section className="tile flex flex-col gap-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <span className="eyebrow">
                1. Doseren &middot; batch <span className="mono">{batchId}</span>
              </span>
              <span className="flex items-center gap-2 text-[0.8125rem]">
                {batch.data && <StatusPill status="run">{batch.data.state}</StatusPill>}
                <CrossLink href={`/batches?batch=${batchId}`}>batchdetail</CrossLink>
              </span>
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-[0.8125rem]">
                Lotnummer
                <input
                  value={lot}
                  onChange={(e) => setLot(e.target.value)}
                  placeholder="van het grondstoflabel"
                  className="w-44 border border-line-strong bg-surface px-2 py-1.5 mono"
                />
              </label>
              <form onSubmit={scanLabel} className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1 text-[0.8125rem]">
                  Label scannen
                  <input
                    value={labelMaterial}
                    onChange={(e) => setLabelMaterial(e.target.value)}
                    placeholder="grondstof, bv. cocoa"
                    className="w-44 border border-line-strong bg-surface px-2 py-1.5 mono"
                  />
                </label>
                <button
                  type="submit"
                  disabled={busy || !labelMaterial.trim() || !lot.trim()}
                  className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
                >
                  Scan
                </button>
              </form>
            </div>
            {!lot.trim() && (
              <p className="text-[0.6875rem] text-ink-faint">
                Elke weging draagt het lot van de grondstof: vul eerst het lotnummer in.
              </p>
            )}
            {(result?.materials ?? []).map((m) => (
              <div
                key={m.material_id}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 border-b border-line py-[var(--density-row)] last:border-b-0 sm:grid-cols-[7rem_1fr_auto]"
              >
                <span className="mono">{m.material_id}</span>
                <span className="text-[0.8125rem] num">
                  {m.qty_staged === null || m.qty_staged === undefined
                    ? EMPTY
                    : num(m.qty_staged, 1)}{" "}
                  van {num(m.qty_target, 1)} kg
                  {m.remaining !== null && m.remaining !== undefined && (
                    <span className="ml-2 text-ink-faint">rest {num(m.remaining, 1)}</span>
                  )}
                </span>
                <button
                  type="button"
                  disabled={busy || !lot.trim()}
                  title={lot.trim() ? undefined : "Vul eerst het lotnummer van het label in"}
                  onClick={() => weigh(m.material_id, m.remaining ?? m.qty_target, true)}
                  className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase hover:border-ink-muted disabled:opacity-40"
                >
                  Totaal
                </button>
              </div>
            ))}
            <button
              type="button"
              disabled={busy}
              onClick={commit}
              className="self-start border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
            >
              Rapport-scan (commit)
            </button>
            <p className="text-[0.6875rem] text-ink-faint">
              Een tekort aan grondstof weigert hier.{" "}
              <CrossLink href="/voorraad">Voorraad ontvangen of bijvullen</CrossLink>.
            </p>
          </section>

          <section className="tile flex flex-col gap-3">
            <span className="eyebrow">2. Monsters</span>
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-[0.8125rem]">
                Type
                <select
                  value={sampleType}
                  onChange={(e) => setSampleType(e.target.value)}
                  className="border border-line-strong bg-surface px-2 py-1.5"
                >
                  {SAMPLE_TYPES.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.label}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await post("/samples", {
                      batch_id: batchId,
                      sample_type: sampleType,
                      operator_id: OPERATOR,
                    });
                    await samples.refetch();
                  }, "Monster")
                }
                className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
              >
                Monster nemen
              </button>
            </div>
            {(samples.data ?? []).length === 0 ? (
              <p className="text-[0.8125rem] text-ink-muted">Nog geen monsters voor deze batch.</p>
            ) : (
              <ul className="m-0 flex list-none flex-col p-0">
                {(samples.data ?? []).map((s) => (
                  <li
                    key={s.sample_id}
                    className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-line py-[var(--density-row)] last:border-b-0"
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <StatusPill status={sampleStatus(s.result)}>{s.result}</StatusPill>
                      <span className="text-[0.8125rem]">{s.sample_type}</span>
                      <span className="text-[0.8125rem] text-ink-muted num">
                        {s.value === null ? EMPTY : `${num(s.value, 1)} ${s.unit ?? ""}`}
                      </span>
                      <span className="text-[0.6875rem] text-ink-faint num">{shortTime(s.ts)}</span>
                    </span>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        run(
                          () => post(`/samples/${s.sample_id}/reprint-label`, {}),
                          "Label herdrukt",
                        )
                      }
                      className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase hover:border-ink-muted disabled:opacity-40"
                    >
                      Label
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="tile flex flex-col gap-3">
            <span className="eyebrow">3. Productie boeken</span>
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-[0.8125rem]">
                Pakken
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={packs}
                  onChange={(e) => setPacks(Number(e.target.value))}
                  className="w-24 border border-line-strong bg-surface px-2 py-1.5 text-right num"
                />
              </label>
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  run(async () => {
                    await post("/production", {
                      batch_id: batchId,
                      packs,
                      operator_id: OPERATOR,
                    });
                    await batch.refetch();
                  }, `${packs} pakken`)
                }
                className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
              >
                Boeken
              </button>
              <span className="text-[0.8125rem] text-ink-muted num">
                op deze batch geboekt: {num(packsTotal, 0)} pakken
              </span>
            </div>
            <p className="text-[0.6875rem] text-ink-faint">
              De vullijn boekt zelf; dit is de handmatige correctie. Zonder geboekte productie
              weigert het sluiten van de order.
            </p>
          </section>

          </>
          )}

          <section className="tile flex flex-col gap-3">
            <span className="eyebrow">4. Pallets, inslag en verzending</span>
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1 text-[0.8125rem]">
                Pakken op de pallet
                <input
                  type="number"
                  min={1}
                  step={1}
                  value={huPacks}
                  onChange={(e) => setHuPacks(Number(e.target.value))}
                  className="w-24 border border-line-strong bg-surface px-2 py-1.5 text-right num"
                />
              </label>
              <button
                type="button"
                disabled={busy || huLeft === 0}
                onClick={() =>
                  run(async () => {
                    await post("/hu", {
                      batch_id: batchId,
                      packs_count: huPacks,
                      operator_id: OPERATOR,
                    });
                    await hus.refetch();
                  }, "Pallet")
                }
                className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
              >
                Pallet maken
              </button>
              <span className="text-[0.8125rem] text-ink-muted num">
                nog te verpakken: {num(huLeft, 0)} van {num(packsTotal, 0)}
              </span>
            </div>

            {(hus.data ?? []).length === 0 ? (
              <p className="text-[0.8125rem] text-ink-muted">
                Nog geen pallets. Boek eerst productie: meer verpakken dan er geproduceerd is,
                weigert.
              </p>
            ) : (
              <ul className="m-0 flex list-none flex-col p-0">
                {(hus.data ?? []).map((h) => (
                  <li
                    key={h.hu_id}
                    className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-line py-[var(--density-row)] last:border-b-0"
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="mono">{h.hu_id}</span>
                      <span className="text-[0.8125rem] num">{num(h.packs_count, 0)} pakken</span>
                      <StatusPill status={huStatus(h.status)}>
                        {HU_LABEL[h.status] ?? h.status}
                      </StatusPill>
                      {h.location && (
                        <span className="text-[0.6875rem] text-ink-faint">{h.location}</span>
                      )}
                    </span>
                    <span className="flex gap-1.5">
                      <button
                        type="button"
                        disabled={busy || h.status !== "wrapped"}
                        onClick={() =>
                          run(async () => {
                            await post(`/hu/${h.hu_id}/putaway`, { operator_id: OPERATOR });
                            await hus.refetch();
                          }, "Inslag")
                        }
                        className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase hover:border-ink-muted disabled:opacity-40"
                      >
                        Inslaan
                      </button>
                      <button
                        type="button"
                        disabled={busy || h.status !== "awaiting_shipment"}
                        onClick={() =>
                          run(async () => {
                            await post(`/hu/${h.hu_id}/ship`, { operator_id: OPERATOR });
                            await hus.refetch();
                          }, "Verzending")
                        }
                        className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase hover:border-ink-muted disabled:opacity-40"
                      >
                        Verschepen
                      </button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <p className="text-[0.6875rem] text-ink-faint">
              De volgorde ligt vast: gewikkeld, dan koelmagazijn, dan expeditie. Een stap overslaan
              weigert, en dat is de bedoeling.
            </p>
          </section>

          <NextStep
            steps={[
              {
                href: `/batches?batch=${batchId}`,
                label: "Verdict ondertekenen",
                why: "Een afgeronde batch wacht op een handtekening.",
                done: Boolean(batch.data?.verdict),
              },
              {
                href: `/reports?type=batch&dimension=${batchId}`,
                label: "Batchrapport (EBR)",
                why: "Doses, monsters en verdict in een PDF.",
              },
              {
                href: "/orders",
                label: "Order sluiten",
                why: "Kan pas als er productie is geboekt.",
              },
            ]}
          />
        </>
      )}
    </main>
  );
}
