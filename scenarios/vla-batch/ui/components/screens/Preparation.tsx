"use client";

import { useState } from "react";
import { CrossLink, ScreenHeader } from "@/components/nav/ScreenHeader";
import { LineBadge, LineSwitch, useLineParam } from "@/components/toolkit/LineSwitch";
import { FlavourDot, ProductTag, useRecipeIndex } from "@/components/toolkit/Product";
import { RefusalPanel } from "@/components/toolkit/RefusalPanel";
import { StatusPill, type Status } from "@/components/toolkit/StatusPill";
import { post } from "@/lib/client";
import { EMPTY, num, shortTime } from "@/lib/format";
import { useLines, usePrepOrders } from "@/lib/queries";
import type { Refusal } from "@/lib/refusal";
import type { PrepSet, PrepState } from "@/lib/types";

/**
 * Voorbereiding: de gedeelde ruimte tussen Hal 1 en Hal 2 waar per batch de
 * droge grondstoffen worden afgewogen en voorgemengd. Een batch start pas als
 * zijn set "gereed" is; dat is de poort die de autopilot respecteert.
 *
 * Dit scherm rekent niets: stations en sets komen uit GET /prep-orders.
 */

const OPERATOR = "op-01";

const COLUMNS: Array<{ state: PrepState; label: string; hint: string }> = [
  { state: "gepland", label: "Gepland", hint: "wacht op een vrij weegstation" },
  { state: "afwegen", label: "Afwegen", hint: "op het weegstation" },
  { state: "premix", label: "Premix", hint: "in de premixtank" },
  { state: "gereed", label: "Gereed", hint: "klaar voor de lijn" },
];

function stateStatus(s: PrepState): Status {
  switch (s) {
    case "gereed":
      return "ok";
    case "afwegen":
    case "premix":
      return "run";
    case "verbruikt":
      return "done";
    default:
      return "idle";
  }
}

export function Preparation() {
  const lineFilter = useLineParam(true);
  const prep = usePrepOrders(lineFilter);
  const lines = useLines();
  const recipes = useRecipeIndex();
  const [recipeId, setRecipeId] = useState("");
  const [busy, setBusy] = useState(false);
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const sets = prep.data?.sets ?? [];
  const stations = (prep.data?.stations ?? []).filter((s) => !lineFilter || s.line === lineFilter);
  const open = sets.filter((s) => s.state !== "verbruikt");
  const used = sets.filter((s) => s.state === "verbruikt").slice(0, 8);
  const choice = recipeId || lines.data?.[0]?.recipes[0]?.recipe_id || "";

  async function plan() {
    if (!choice) return;
    setBusy(true);
    setRefusal(null);
    setMessage(null);
    try {
      await post("/prep-orders", { recipe_id: choice, operator_id: OPERATOR });
      await prep.refetch();
      setMessage("Voorbereiding ingepland");
    } catch (e) {
      setRefusal(e as Refusal);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex max-w-[1440px] flex-col gap-4 p-3 pb-16 sm:gap-5 sm:p-6">
      <ScreenHeader
        eyebrow="Gedeelde ruimte, Hal 1 en Hal 2"
        title="Voorbereiding"
        subtitle="Afwegen en premixen per batch. Een batch start pas als zijn set gereed is."
        actions={[
          { href: "/scada", label: "SCADA" },
          { href: "/batches", label: "Batches" },
          { href: "/site", label: "3D Site" },
        ]}
      />

      <LineSwitch allowAll />

      {refusal && (
        <RefusalPanel refusal={refusal} operatorId={OPERATOR} onCleared={() => setRefusal(null)} />
      )}
      {message && <p className="text-[0.8125rem] text-ink-muted">{message}</p>}

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" aria-label="Stations">
        {stations.map((st) => (
          <article key={st.station_id} className="tile flex flex-col gap-2" data-station={st.station_id}>
            <div className="flex items-center justify-between gap-2">
              <span className="eyebrow">{st.kind === "weigh" ? "Weegstation" : "Premixtank"}</span>
              <LineBadge line={st.line} />
            </div>
            <span className="mono text-[0.8125rem]">{st.station_id}</span>
            <div className="flex items-center gap-2">
              <StatusPill status={st.state === "bezig" ? "run" : "idle"}>{st.state}</StatusPill>
              {st.recipe_id && <ProductTag recipeId={st.recipe_id} index={recipes} />}
            </div>
            <span className="mono text-[0.6875rem] text-ink-faint">{st.prep_id ?? EMPTY}</span>
          </article>
        ))}
        {stations.length === 0 && (
          <p className="tile text-[0.8125rem] text-ink-muted">
            Nog geen stations bekend. Draait de engine?
          </p>
        )}
      </section>

      <section className="grid items-start gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="Sets per fase">
        {COLUMNS.map((c) => {
          const items = open.filter((s) => s.state === c.state);
          return (
            <div key={c.state} className="tile flex flex-col gap-2">
              <div className="flex items-baseline justify-between">
                <h2 className="text-base font-semibold">{c.label}</h2>
                <span className="num text-[0.8125rem] text-ink-muted">{items.length}</span>
              </div>
              <span className="text-[0.6875rem] text-ink-faint">{c.hint}</span>
              {items.map((s) => (
                <PrepCard key={s.prep_id} set={s} recipes={recipes} />
              ))}
              {items.length === 0 && <span className="text-[0.8125rem] text-ink-faint">{EMPTY}</span>}
            </div>
          );
        })}
      </section>

      <section className="tile flex flex-col gap-3">
        <span className="eyebrow">Handmatig inplannen</span>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Product
            <select
              value={choice}
              onChange={(e) => setRecipeId(e.target.value)}
              className="border border-line-strong bg-surface px-2 py-1.5"
            >
              {(lines.data ?? []).map((l) => (
                <optgroup key={l.line_id} label={`${l.line_id} · ${l.hall}`}>
                  {l.recipes.map((r) => (
                    <option key={r.recipe_id} value={r.recipe_id}>
                      {r.product_name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy || !choice}
            onClick={plan}
            className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
          >
            Set inplannen
          </button>
        </div>
        <p className="text-[0.6875rem] text-ink-faint">
          De autopilot plant zelf vooruit. Handmatig inplannen is voor een losse batch of om de
          poort te demonstreren.
        </p>
      </section>

      <section className="tile table-scroll p-0">
        <table className="w-full border-collapse text-[0.8125rem]">
          <thead>
            <tr className="border-b border-line text-left">
              {["Set", "Product", "Batch", "Order", "Verbruikt"].map((h) => (
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
            {used.length === 0 && (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-ink-muted">
                  Nog geen verbruikte sets.
                </td>
              </tr>
            )}
            {used.map((s) => (
              <tr key={s.prep_id} className="border-b border-line last:border-b-0">
                <td className="px-3 py-[var(--density-row)] mono">{s.prep_id}</td>
                <td className="px-3 py-[var(--density-row)] whitespace-nowrap">
                  <ProductTag recipeId={s.recipe_id} index={recipes} showLine />
                </td>
                <td className="px-3 py-[var(--density-row)] mono">
                  {s.batch_id ? (
                    <CrossLink href={`/batches?batch=${s.batch_id}`}>{s.batch_id}</CrossLink>
                  ) : (
                    EMPTY
                  )}
                </td>
                <td className="px-3 py-[var(--density-row)] mono">
                  {s.order_id ? (
                    <CrossLink href={`/orders?order=${s.order_id}`}>{s.order_id}</CrossLink>
                  ) : (
                    EMPTY
                  )}
                </td>
                <td className="px-3 py-[var(--density-row)] num text-ink-muted">
                  {shortTime(s.state_changed_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}

function PrepCard({ set, recipes }: { set: PrepSet; recipes: ReturnType<typeof useRecipeIndex> }) {
  const r = recipes.get(set.recipe_id);
  return (
    <article className="flex flex-col gap-1 border border-line p-2" data-prep={set.prep_id}>
      <div className="flex items-center justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-[0.8125rem] font-semibold">
          <FlavourDot flavour={r?.flavour} />
          {r?.product_name ?? set.recipe_id}
        </span>
        <LineBadge line={set.line} />
      </div>
      <div className="flex flex-wrap items-center gap-x-2 text-[0.6875rem] text-ink-muted">
        <StatusPill status={stateStatus(set.state)}>{set.state}</StatusPill>
        <span className="mono">{set.prep_id}</span>
        <span className="num">sinds {shortTime(set.state_changed_at)}</span>
      </div>
      <ul className="text-[0.6875rem] text-ink-muted">
        {set.components.map((c) => (
          <li key={c.material_id} className="flex justify-between gap-2">
            <span className="mono">{c.material_id}</span>
            <span className="num">
              {num(c.qty_kg, 1)} {c.uom}
            </span>
          </li>
        ))}
      </ul>
      {set.order_id && (
        <span className="text-[0.6875rem]">
          order <CrossLink href={`/orders?order=${set.order_id}`}>{set.order_id}</CrossLink>
        </span>
      )}
    </article>
  );
}
