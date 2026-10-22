"use client";

import { useQuery } from "@tanstack/react-query";
import { Gauge } from "@/components/toolkit/Gauge";
import { Mimic, MimicLegend, type MimicUnit } from "@/components/toolkit/Mimic";
import { StatusPill } from "@/components/toolkit/StatusPill";
import { getUi } from "@/lib/client";
import { num } from "@/lib/format";
import { POLL, useEquipmentHealth, useUiModel } from "@/lib/queries";

/**
 * Het Level 2-procesbeeld: de installatie met live waarden, plus de meters.
 *
 * Staat boven de bediening op /scada, want dat is waar een operator hem zoekt:
 * eerst zien wat het proces doet, dan pas ingrijpen.
 *
 * Alle waarden komen uit /api/ui/line, dezelfde bron als de functionele strook
 * op /line. Dit scherm REKENT NIET, op een ding na: het vulniveau als fractie,
 * en de schaal daarvoor is basis_L uit het recept. Dat is geen verzonnen getal
 * maar de batchgrootte; het staat er ook zo bij.
 */

type Step = {
  id: string;
  name: string;
  equipment: string;
  primary: { label: string; unit: string; value: number | null };
  secondary: Array<{ label: string; unit: string; value: number | null; stale: boolean }>;
  spec: { min: number; max: number } | null;
  tone: "neutral" | "warn" | "alarm";
  stale: boolean;
};

type LinePayload = {
  engine_reachable: boolean;
  steps: Step[];
  line: {
    batch?: { batch_id: string; state: string; verdict: string | null };
    filling?: { packs_total: number; packs_target: number | null; pallets: number | null };
    dose_totals?: { target_kg: number; actual_kg: number; pct: number | null };
  } | null;
};

type Health = { equipment_id: string; state: string };

/**
 * Welke fase welke procesdelen laat stromen. Dit is procesvolgorde en geen
 * presentatie: het recept noemt hetzelfde pad (process_path).
 */
const ACTIEF: Record<string, string[]> = {
  DOSING: ["mixing"],
  COOKING: ["mixing", "cooking"],
  COOLING: ["cooking", "cooling"],
  FILLING: ["cooling", "filling"],
};

export function ProcessView({ lineId = "L1" }: { lineId?: "L1" | "L2" }) {
  const line = useQuery<LinePayload>({
    queryKey: ["ui", "line", lineId],
    queryFn: ({ signal }) => getUi<LinePayload>(`/line?line=${lineId}`, signal),
    refetchInterval: POLL.live,
    refetchIntervalInBackground: false,
    staleTime: 0,
    retry: 1,
  });
  const health = useEquipmentHealth<Health[]>();
  const model = useUiModel();

  const steps = line.data?.steps ?? [];
  const batch = line.data?.line?.batch;
  const phase = batch?.state ?? null;
  const stateOf = new Map((health.data ?? []).map((h) => [h.equipment_id, h.state]));

  const basisL = model.data?.recipe?.basis_L ?? null;
  const spec = model.data?.recipe?.viscosity_spec_cP ?? null;
  const cookSetpoint = model.data?.recipe?.cook_setpoint_C ?? null;
  const coolTarget = model.data?.recipe?.cool_target_C ?? null;

  const packsTarget = line.data?.line?.filling?.packs_target ?? null;
  const packsTotal = line.data?.line?.filling?.packs_total ?? 0;

  const actief = phase ? (ACTIEF[phase] ?? []) : [];
  const sec = (s: Step, label: string) =>
    s.secondary.find((x) => x.label === label)?.value ?? null;

  const units: MimicUnit[] = steps.map((s) => {
    // Vulniveau alleen waar er ECHT een niveaumeting is. De koeler en de
    // vuller hebben er geen; die krijgen dus geen halfvolle tank te zien.
    let fill: number | null = null;
    if ((s.id === "receiving" || s.id === "mixing") && basisL && s.primary.value !== null) {
      fill = s.primary.value / basisL;
    } else if (s.id === "filling" && packsTarget && packsTarget > 0) {
      fill = packsTotal / packsTarget;
    }

    return {
      id: s.id,
      equipment: s.equipment,
      name: s.name,
      label: s.primary.label,
      value: s.primary.value,
      unit: s.primary.unit,
      fill,
      state: stateOf.get(s.equipment) ?? "Idle",
      tone: s.tone,
      stale: s.stale,
      active: actief.includes(s.id),
    };
  });

  const koken = steps.find((s) => s.id === "cooking");
  const mengen = steps.find((s) => s.id === "mixing");
  const koelen = steps.find((s) => s.id === "cooling");
  const ontvangst = steps.find((s) => s.id === "receiving");

  const offline = line.isError || line.data?.engine_reachable === false;

  return (
    <section className="tile flex flex-col gap-3" aria-label="Procesbeeld">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <span className="eyebrow">Procesbeeld, level 2</span>
          <h2 className="text-base font-semibold">Vla-lijn {lineId}</h2>
        </div>
        <span className="flex items-center gap-2 text-[0.8125rem]">
          {offline ? (
            <StatusPill status="alarm">Engine offline</StatusPill>
          ) : batch ? (
            <>
              <StatusPill status="run">{batch.state}</StatusPill>
              <span className="mono text-ink-muted">{batch.batch_id}</span>
            </>
          ) : (
            <StatusPill status="idle">Geen actieve batch</StatusPill>
          )}
        </span>
      </div>

      {offline ? (
        <p className="text-[0.8125rem] text-ink-muted">
          Geen verbinding met de engine, dus het procesbeeld zou de laatst bekende stand tonen
          alsof die actueel is. Dat is erger dan niets tonen.
        </p>
      ) : (
        <>
          <Mimic
            units={units}
            pallets={line.data?.line?.filling?.pallets ?? 0}
            batchId={batch?.batch_id ?? null}
            phase={phase}
          />

          {/* De meters. Alleen waar een schaal een BETEKENIS heeft: een naald
              zonder verantwoord bereik is decoratie, en decoratie die eruitziet
              als een meting is erger dan geen meter. */}
          <div className="flex flex-wrap items-start justify-around gap-4 border-t border-line pt-3">
            <Gauge
              label="Viscositeit"
              value={koken?.primary.value ?? null}
              unit="cP"
              min={0}
              // Schaal rond de specband, niet rond de meting: anders verspringt
              // de hele meter zodra de waarde beweegt.
              // Zonder specband is er geen te verantwoorden bovengrens voor
              // viscositeit; dan toont de meter dat, in plaats van 400 te verzinnen.
              max={spec ? Math.round(spec.max * 1.25) : null}
              spec={spec}
              tone={koken?.tone ?? "neutral"}
              stale={koken?.stale ?? false}
            />
            <Gauge
              label="Kooktemperatuur"
              value={koken ? sec(koken, "Temperatuur") : null}
              unit="°C"
              min={0}
              // Afgeleid van het kook-setpoint uit het recept, net als de
              // schaal die de BFF aan de koelstap meegeeft. Hiervoor stond hier
              // 110 zonder herkomst.
              max={cookSetpoint ? Math.ceil(cookSetpoint * 1.15) : null}
              setpoint={cookSetpoint}
              stale={koken?.stale ?? false}
            />
            <Gauge
              label="Koeltemperatuur"
              value={koelen?.primary.value ?? null}
              unit="°C"
              min={0}
              max={cookSetpoint ? Math.ceil(cookSetpoint * 1.15) : null}
              setpoint={coolTarget}
              stale={koelen?.stale ?? false}
            />
            <Gauge
              label="Roerder"
              value={mengen ? sec(mengen, "Roerder") : null}
              unit="tpm"
              min={0}
              // Instrumentbereik van de toerenteller, geen procesnorm. Het
              // staat als getal op de schaal, dus de lezer ziet waar hij aan
              // toe is; dat is iets anders dan een stille terugval.
              max={120}
              stale={mengen?.stale ?? false}
            />
            <Gauge
              label="Tankniveau"
              value={mengen?.primary.value ?? null}
              unit="L"
              min={0}
              max={basisL}
              stale={mengen?.stale ?? false}
            />
            <Gauge
              label="Ontvangst"
              value={ontvangst?.primary.value ?? null}
              unit="L"
              min={0}
              max={basisL ?? 5000}
              stale={ontvangst?.stale ?? false}
            />
          </div>

          <div className="flex flex-wrap items-center gap-x-6 gap-y-1 border-t border-line pt-3 text-[0.8125rem] text-ink-muted">
            <span>
              Doseren{" "}
              <span className="font-semibold text-ink num">
                {num(line.data?.line?.dose_totals?.actual_kg ?? 0, 0)} /{" "}
                {num(line.data?.line?.dose_totals?.target_kg ?? 0, 0)} kg
              </span>
            </span>
            <span>
              Pakken{" "}
              <span className="font-semibold text-ink num">
                {num(packsTotal, 0)}
                {packsTarget ? ` / ${num(packsTarget, 0)}` : ""}
              </span>
            </span>
            {basisL !== null && (
              <span className="text-[0.6875rem] text-ink-faint num">
                niveauschaal: batchgrootte {num(basisL, 0)} L uit het recept
              </span>
            )}
          </div>

          <MimicLegend />
        </>
      )}
    </section>
  );
}
