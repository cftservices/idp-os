"use client";

import { useState } from "react";
import { CrossLink, ScreenHeader } from "@/components/nav/ScreenHeader";
import { ProcessView } from "@/components/screens/ProcessView";
import { RefusalPanel } from "@/components/toolkit/RefusalPanel";
import { StatusPill } from "@/components/toolkit/StatusPill";
import { post } from "@/lib/client";
import { useLines, useOrders, useUiModel } from "@/lib/queries";
import { LINE_LABEL, LineSwitch, useLineParam } from "@/components/toolkit/LineSwitch";
import type { Refusal } from "@/lib/refusal";

/**
 * Scherm 4, de SCADA-console, met scherm 7 (admin) erin gevouwen als
 * simulatieblok.
 *
 * Waarom invouwen: van de drie blokken op scherm 7 stonden er twee al hier,
 * inclusief dezelfde fault-dropdown en magnitude-slider. Twee plekken met
 * dezelfde fault-lijst is een divergentierisico: de lijst wordt op twee
 * plaatsen onderhouden en loopt uiteen.
 *
 * De werkvloerflow is er juist UIT gehaald naar /shopfloor. De operator die
 * afweegt zet nooit een kook-setpoint.
 *
 * Hier zit ook de enige plek met automatisch opnieuw proberen: een batch
 * aanmaken bij een order wordt na een geslaagde CIP herhaald. Bij het sluiten
 * of aanmaken van een order gebeurt dat bewust niet.
 */

const OPERATOR = "op-01";

type Order = { order_id: string; status: string; recipe_id?: string; line?: string };

export function Scada() {
  const orders = useOrders<Order[]>();
  const model = useUiModel();
  const lineId = useLineParam();
  const lines = useLines();
  const lineRecipes = lines.data?.find((l) => l.line_id === lineId)?.recipes ?? [];
  const [pickedRecipe, setPickedRecipe] = useState("");
  const looseRecipe = lineRecipes.some((r) => r.recipe_id === pickedRecipe)
    ? pickedRecipe
    : lineRecipes[0]?.recipe_id ?? (lineId === "L1" ? "chocolate-vla-1L" : "");
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const [retry, setRetry] = useState<(() => Promise<void>) | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [setpoint, setSetpoint] = useState({ target: "cook", value: 88 });
  const [fault, setFault] = useState({ fault_id: "cook_undertemp", magnitude: 0.6 });
  const [orderId, setOrderId] = useState("");
  const [plannedL, setPlannedL] = useState(1000);

  const recipe = model.data?.recipe;

  async function run(fn: () => Promise<unknown>, label: string, retryable?: () => Promise<void>) {
    setBusy(true);
    setRefusal(null);
    setMessage(null);
    try {
      await fn();
      setMessage(`${label} geaccepteerd`);
    } catch (e) {
      setRefusal(e as Refusal);
      setRetry(() => retryable ?? null);
    } finally {
      setBusy(false);
    }
  }

  // Elk commando gaat naar de gekozen lijn; de engine routeert op params.line.
  const cmd = (body: { equipment_id: string; cmd: string; params: Record<string, unknown> }) =>
    post("/admin/command", { ...body, params: { ...body.params, line: lineId } });

  async function orderBatch() {
    if (!orderId) return;
    await post(`/orders/${orderId}/batches`, { operator_id: OPERATOR });
    await orders.refetch();
  }

  async function looseBatch() {
    await post("/batches", {
      recipe_id: looseRecipe,
      planned_L: plannedL,
    });
  }

  return (
    <main className="mx-auto flex max-w-[1400px] flex-col gap-3 p-2 pb-16 sm:gap-4 sm:p-4">
      <ScreenHeader
        eyebrow="SCADA-console"
        title="Bediening"
        subtitle={
          <>
            Operator <span className="mono">{OPERATOR}</span> &middot; {LINE_LABEL[lineId]}
          </>
        }
        actions={[
          { href: `/line?line=${lineId}`, label: `Lijn ${lineId}`, title: "Live procesbeeld" },
          { href: "/voorbereiding", label: "Voorbereiding" },
          { href: "/orders", label: "Orders" },
          { href: "/batches", label: "Batches" },
          { href: "/equipment", label: "CIP" },
        ]}
      />

      {refusal && (
        <RefusalPanel
          refusal={refusal}
          operatorId={OPERATOR}
          onCleared={() => setRefusal(null)}
          // Alleen hier opnieuw proberen. Een batch aanmaken is idempotent
          // genoeg om na een CIP te herhalen; een order sluiten niet.
          onRetry={retry ?? undefined}
        />
      )}
      {message && <p className="text-[0.8125rem] text-ink-muted">{message}</p>}

      {/* Eerst zien wat het proces doet, dan pas ingrijpen. Een console zonder
          procesbeeld is blind bedienen, en dat was precies wat hier ontbrak. */}
      <LineSwitch />

      <ProcessView lineId={lineId} />

      <section className="tile flex flex-col gap-3">
        <span className="eyebrow">Lijnbesturing</span>
        <div className="flex flex-wrap gap-2">
          {(["start", "stop"] as const).map((c) => (
            <button
              key={c}
              type="button"
              disabled={busy}
              onClick={() =>
                run(() => cmd({ equipment_id: "Batch", cmd: c, params: {} }), c, undefined)
              }
              className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
            >
              {c === "start" ? "Start" : "Stop"}
            </button>
          ))}
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(
                () =>
                  cmd({
                    equipment_id: "Batch",
                    cmd: "sample",
                    params: { sample_type: "viscosity" },
                  }),
                "monster",
              )
            }
            className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
          >
            Monster nemen
          </button>
        </div>
      </section>

      <section className="tile flex flex-col gap-3">
        <span className="eyebrow">Setpoint</span>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Doel
            <select
              value={setpoint.target}
              onChange={(e) => setSetpoint({ ...setpoint, target: e.target.value })}
              className="border border-line-strong bg-surface px-2 py-1"
            >
              <option value="cook">kooktemperatuur</option>
              <option value="cool">koeldoel</option>
              <option value="rpm">roerdertoerental</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Waarde
            <input
              type="number"
              value={setpoint.value}
              onChange={(e) => setSetpoint({ ...setpoint, value: Number(e.target.value) })}
              className="w-24 border border-line-strong bg-surface px-2 py-1 text-right num"
            />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(
                () =>
                  cmd({
                    equipment_id: "Batch",
                    cmd: "setpoint",
                    params: { target: setpoint.target, value: setpoint.value },
                  }),
                "setpoint",
              )
            }
            className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
          >
            Zetten
          </button>
          {recipe && (
            <span className="text-[0.8125rem] text-ink-muted num">
              recept: kook {recipe.cook_setpoint_C} &deg;C, hold {recipe.hold_sec} s, koel{" "}
              {recipe.cool_target_C} &deg;C
            </span>
          )}
        </div>
      </section>

      <section className="tile flex flex-col gap-3">
        <span className="eyebrow">Batch bij een order</span>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Order
            <select
              value={orderId}
              onChange={(e) => setOrderId(e.target.value)}
              className="border border-line-strong bg-surface px-2 py-1"
            >
              <option value="">kies een order</option>
              {(orders.data ?? [])
                .filter((o) => o.status !== "DONE")
                .filter((o) => !o.recipe_id || !lineRecipes.length || lineRecipes.some((r) => r.recipe_id === o.recipe_id))
                .map((o) => (
                  <option key={o.order_id} value={o.order_id}>
                    {o.order_id}
                  </option>
                ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy || !orderId}
            onClick={() => run(orderBatch, "batch", orderBatch)}
            className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
          >
            Batch aanmaken
          </button>
          <span className="text-[0.8125rem] text-ink-muted">
            Wordt na een geslaagde CIP automatisch opnieuw geprobeerd.
          </span>
        </div>
        <p className="text-[0.6875rem] text-ink-faint">
          Nog geen order? <CrossLink href="/orders">Maak er een aan</CrossLink>. Zonder order telt
          een batch niet mee voor OTIF en planrealisatie.
        </p>
      </section>

      {/* Losse batch: buiten elke order om. Bestond in de oude applicatie en is
          nodig om te proefdraaien of een storing na te spelen zonder de
          orderadministratie te vervuilen. */}
      <section className="tile flex flex-col gap-3">
        <span className="eyebrow">Losse batch, zonder order</span>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Recept
            <select
              value={looseRecipe}
              onChange={(e) => setPickedRecipe(e.target.value)}
              className="border border-line-strong bg-surface px-2 py-1.5"
            >
              {lineRecipes.length === 0 && <option value={looseRecipe}>{looseRecipe}</option>}
              {lineRecipes.map((r) => (
                <option key={r.recipe_id} value={r.recipe_id}>
                  {r.product_name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Volume
            <span className="flex items-center gap-1.5">
              <input
                type="number"
                min={100}
                step={100}
                value={plannedL}
                onChange={(e) => setPlannedL(Number(e.target.value))}
                className="w-24 border border-line-strong bg-surface px-2 py-1.5 text-right num"
              />
              <span className="text-ink-muted">L</span>
            </span>
          </label>
          <button
            type="button"
            disabled={busy || !looseRecipe}
            onClick={() => run(looseBatch, "losse batch", looseBatch)}
            className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
          >
            Batch aanmaken
          </button>
        </div>
        <p className="text-[0.6875rem] text-ink-faint">
          Telt niet mee voor OTIF: er is geen leverdatum om tegen af te zetten. Gebruik dit om te
          proefdraaien, niet om te produceren.
        </p>
      </section>

      {/* Scherm 7 als blok, niet als route. */}
      <section className="tile flex flex-col gap-3 border-l-[3px] border-l-status-warn">
        <div className="flex items-center gap-2">
          <span className="eyebrow">Simulatie</span>
          <StatusPill status="warn">Alleen voor de demo</StatusPill>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Storing
            <select
              value={fault.fault_id}
              onChange={(e) => setFault({ ...fault, fault_id: e.target.value })}
              className="border border-line-strong bg-surface px-2 py-1"
            >
              <option value="cook_undertemp">te laag koken</option>
              <option value="dose_off">doseerafwijking</option>
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[0.8125rem]">
            Sterkte
            <input
              type="range"
              min={0}
              max={1}
              step={0.1}
              value={fault.magnitude}
              onChange={(e) => setFault({ ...fault, magnitude: Number(e.target.value) })}
            />
          </label>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(
                () => cmd({ equipment_id: "Batch", cmd: "fault", params: fault }),
                "storing",
              )
            }
            className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
          >
            Injecteren
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() =>
              run(() => cmd({ equipment_id: "Batch", cmd: "clear", params: {} }), "herstel")
            }
            className="border border-line-strong px-3 py-1.5 text-[0.8125rem] font-semibold hover:border-ink-muted disabled:opacity-40"
          >
            Opheffen
          </button>
        </div>
        <p className="text-[0.8125rem] text-ink-muted">
          Te laag koken geeft te lage viscositeit, buiten spec, en dus HOLD plus meetbare afkeur.
          Gebruik dit om te oefenen met een afwijking voordat hij zich echt voordoet.
        </p>
      </section>
    </main>
  );
}
