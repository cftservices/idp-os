"use client";

import { useState } from "react";
import { NextStep } from "@/components/nav/NextStep";
import { CrossLink, ScreenHeader } from "@/components/nav/ScreenHeader";
import { RefusalPanel } from "@/components/toolkit/RefusalPanel";
import { StatusPill } from "@/components/toolkit/StatusPill";
import { post } from "@/lib/client";
import { num } from "@/lib/format";
import { useInventory } from "@/lib/queries";
import type { Refusal } from "@/lib/refusal";

/**
 * Voorraad: goederenontvangst en bijvullen.
 *
 * Verbruik was de enige voorraadmutatie die de demo kende. Er ging dus wel iets
 * af bij elke dosering, maar er kwam nooit iets bij, en na een handvol batches
 * stond een grondstof negatief. Vanaf dat moment weigert de werkvloer te
 * doseren en loopt de hele demo vast op iets dat niets met het proces te maken
 * heeft. Dit is de weg terug omhoog.
 *
 * Bijvullen vult tot TWEE keer de bestelgrens, niet tot precies de grens: op de
 * grens gaan staan betekent dat de eerstvolgende dosering hem meteen weer
 * onderschrijdt.
 */

type Material = {
  material_id: string;
  name: string;
  uom: string;
  category?: string;
  stock_qty: number;
  reorder_level: number;
  below_reorder: boolean;
  stock_pct: number | null;
  is_finished_good: boolean;
  consumed_total: number;
  batches_count: number;
  produced_total: number;
  produced_L: number;
};

type InventoryPayload = {
  materials: Material[];
  produced_packs: number;
  produced_L: number;
};

const OPERATOR = "op-01";

/** Tot twee keer de bestelgrens; zie de kopnoot. */
function topUpQty(m: Material): number {
  if (!m.reorder_level) return 0;
  // Een negatieve voorraad is een boekfout, geen vraag naar levering: tel hem
  // als nul, anders boekt "alles bijvullen" tientallen miljoenen kg als ontvangst.
  return Math.max(0, Math.ceil(m.reorder_level * 2 - Math.max(0, m.stock_qty)));
}

export function Inventory() {
  const inventory = useInventory<InventoryPayload>();
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [qty, setQty] = useState<Record<string, number>>({});

  async function receive(m: Material, amount: number) {
    if (amount <= 0) return;
    setBusy(true);
    setRefusal(null);
    setMessage(null);
    try {
      await post(`/materials/${m.material_id}/receipt`, {
        qty: amount,
        lot_no: `L-${m.material_id.toUpperCase()}`,
        operator_id: OPERATOR,
      });
      await inventory.refetch();
      setMessage(`${num(amount, 1)} ${m.uom} ${m.name} ontvangen`);
    } catch (err) {
      setRefusal(err as Refusal);
    } finally {
      setBusy(false);
    }
  }

  /** Alles onder de bestelgrens in een keer aanvullen. */
  async function topUpAll() {
    const low = (inventory.data?.materials ?? []).filter((m) => !m.is_finished_good && m.below_reorder);
    if (low.length === 0) {
      setMessage("Niets onder de bestelgrens.");
      return;
    }
    setBusy(true);
    setRefusal(null);
    setMessage(null);
    try {
      // Sequentieel: de engine boekt per materiaal en een parallelle vloed
      // maakt de foutmelding onleesbaar als er een weigert.
      for (const m of low) {
        await post(`/materials/${m.material_id}/receipt`, {
          qty: topUpQty(m),
          lot_no: `L-${m.material_id.toUpperCase()}`,
          operator_id: OPERATOR,
        });
      }
      await inventory.refetch();
      setMessage(`${low.length} materialen bijgevuld.`);
    } catch (err) {
      setRefusal(err as Refusal);
    } finally {
      setBusy(false);
    }
  }

  const materials = inventory.data?.materials ?? [];
  const raw = materials.filter((m) => !m.is_finished_good);
  const low = raw.filter((m) => m.below_reorder);

  return (
    <main className="mx-auto flex max-w-[1440px] flex-col gap-4 p-3 pb-16 sm:gap-5 sm:p-6">
      <ScreenHeader
        eyebrow="Grondstoffen en gereed product"
        title="Voorraad"
        subtitle="Ontvangen, bijvullen en zien wat de doseringen hebben opgemaakt."
        actions={[
          { href: "/shopfloor", label: "Werkvloer", title: "Daar wordt de voorraad verbruikt" },
          { href: "/batches", label: "Batches" },
          { href: "/", label: "Plant overview" },
        ]}
      >
        <button
          type="button"
          disabled={busy || low.length === 0}
          onClick={topUpAll}
          className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
        >
          Alles bijvullen{low.length > 0 ? ` (${low.length})` : ""}
        </button>
      </ScreenHeader>

      {refusal && (
        <RefusalPanel refusal={refusal} operatorId={OPERATOR} onCleared={() => setRefusal(null)} />
      )}
      {message && <p className="text-[0.8125rem] text-ink-muted">{message}</p>}

      {low.length > 0 && (
        <p className="text-[0.8125rem] text-ink-muted">
          {low.length === 1 ? "Een materiaal staat" : `${low.length} materialen staan`} onder de
          bestelgrens. Doseren weigert zodra de voorraad niet meer toereikend is, en dat is op de{" "}
          <CrossLink href="/shopfloor">werkvloer</CrossLink> niet uit te leggen zonder deze pagina.
        </p>
      )}

      <section className="tile table-scroll p-0">
        <table className="w-full border-collapse text-[0.8125rem]">
          <caption className="sr-only">Voorraad per materiaal</caption>
          <thead>
            <tr className="border-b border-line text-left">
              {["Materiaal", "Voorraad", "Bestelgrens", "Verbruikt", "Batches", "Ontvangen"].map(
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
            {raw.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-4 text-ink-muted">
                  Geen materialen. Draait de engine?
                </td>
              </tr>
            )}
            {raw.map((m) => (
              <tr key={m.material_id} className="border-b border-line last:border-b-0">
                <td className="px-3 py-[var(--density-row)]">
                  <span className="flex flex-col">
                    <span className="mono">{m.material_id}</span>
                    <span className="text-[0.6875rem] text-ink-faint">{m.name}</span>
                  </span>
                </td>
                <td className="px-3 py-[var(--density-row)]">
                  <span className="flex items-center gap-2">
                    <span className="num font-semibold">
                      {num(m.stock_qty, 1)} {m.uom}
                    </span>
                    {/* Kleur is niet de enige drager: het label zegt het ook. */}
                    {m.below_reorder && <StatusPill status="warn">onder grens</StatusPill>}
                  </span>
                </td>
                <td className="px-3 py-[var(--density-row)] num text-ink-muted">
                  {m.reorder_level ? `${num(m.reorder_level, 1)} ${m.uom}` : "geen"}
                </td>
                <td className="px-3 py-[var(--density-row)] num text-ink-muted">
                  {num(m.consumed_total, 1)} {m.uom}
                </td>
                <td className="px-3 py-[var(--density-row)] num text-ink-muted">
                  {m.batches_count}
                </td>
                <td className="px-3 py-[var(--density-row)]">
                  <span className="flex items-center justify-end gap-1.5">
                    <input
                      type="number"
                      min={0}
                      step={1}
                      aria-label={`Te ontvangen hoeveelheid ${m.name}`}
                      value={qty[m.material_id] ?? topUpQty(m)}
                      onChange={(e) =>
                        setQty({ ...qty, [m.material_id]: Number(e.target.value) })
                      }
                      className="w-20 border border-line-strong bg-surface px-2 py-1 text-right num"
                    />
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => receive(m, qty[m.material_id] ?? topUpQty(m))}
                      className="border border-line-strong px-2 py-1 text-[0.6875rem] font-semibold tracking-[0.04em] uppercase hover:border-ink-muted disabled:opacity-40"
                    >
                      Ontvangen
                    </button>
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="tile flex flex-wrap items-center gap-x-6 gap-y-2">
        <span className="eyebrow">Gereed product</span>
        <span className="text-[0.8125rem]">
          <span className="font-semibold num">{num(inventory.data?.produced_packs ?? 0, 0)}</span>{" "}
          pakken
        </span>
        <span className="text-[0.8125rem]">
          <span className="font-semibold num">{num(inventory.data?.produced_L ?? 0, 0)}</span> L
        </span>
        <CrossLink href="/shopfloor">Verpakken en verzenden</CrossLink>
      </section>

      <NextStep
        steps={[
          {
            href: "/orders",
            label: "Order aanmaken",
            why: "Voorraad op peil, dus er kan gepland worden.",
          },
          {
            href: "/shopfloor",
            label: "Doseren op de werkvloer",
            why: "Daar wordt deze voorraad verbruikt.",
          },
          {
            href: "/reports?type=period&days=7",
            label: "Periodrapport",
            why: "Verbruik en verlies over de week.",
          },
        ]}
      />
    </main>
  );
}
