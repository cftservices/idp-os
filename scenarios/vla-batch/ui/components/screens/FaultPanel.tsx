"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * Het storingenpaneel voor lijn Vla-B.
 *
 * De vierde ingang op dezelfde FaultInjector: UI, REST, MQTT en de
 * scenario-runner landen allemaal op hetzelfde punt in de machine. Wat je hier
 * aanzet zie je terug in de REST-catalogus en andersom.
 *
 * Drie dingen die dit scherm bewust wel doet:
 *
 *   - Het toont per machine ALLEEN de storingen die de fysica echt
 *     implementeert. De catalogus komt uit het FAULTS-attribuut van de
 *     physics-klassen; een knop die niets doet is erger dan geen knop, want
 *     daar prikt het publiek als eerste doorheen.
 *   - Het toont het TRANSPORT per machine. Een OPC-UA-machine krijgt een
 *     methode-aanroep die ook werkt als de broker plat ligt; de rest gaat via
 *     MQTT en dat is fire-and-forget. Dat verschil hoort zichtbaar te zijn,
 *     niet weggepoetst achter een groen vinkje.
 *   - Het heeft een "alles uit". Dat is de knop die je na een demo wilt hebben.
 */

type Machine = {
  equipment_id: string;
  area: string;
  vendor: string;
  physics_type: string;
  transport: "opcua" | "mqtt";
  faults: string[];
  active_faults: Record<string, number>;
};

type Catalogue = {
  machines: Machine[];
  total_faults: number;
  load_error: string | null;
};

// Wat elke storingscode betekent. Bewust hier en niet in de payload: dit is
// presentatie, en de fysica hoort geen UI-teksten te dragen.
const FAULT_LABEL: Record<string, string> = {
  f1: "sensorbias",
  f2: "sensordrift",
  f8: "vervuiling of verstopping",
  f12: "verwarming of toevoer valt terug",
  f13: "motorslip",
};

export function FaultPanel() {
  const [cat, setCat] = useState<Catalogue | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [magnitude, setMagnitude] = useState(0.6);
  const [note, setNote] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/ui/faults", { cache: "no-store" });
      const b = await r.json();
      if (!r.ok) {
        setError(b?.message ?? "onbekende fout");
        setCat(null);
        return;
      }
      setError(null);
      setCat(b as Catalogue);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 4000);
    return () => clearInterval(t);
  }, [load]);

  async function send(body: Record<string, unknown>, key: string) {
    setBusy(key);
    setNote(null);
    try {
      const r = await fetch("/api/ui/faults", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const b = await r.json();
      if (!r.ok || b?.ok === false) {
        setNote(b?.message ?? b?.error ?? b?.detail?.message ?? "geweigerd");
      } else if (b?.transport === "mqtt") {
        // Eerlijk zijn: MQTT is fire-and-forget. "Verstuurd" is niet hetzelfde
        // als "verwerkt", en dat verschil verzwijgen kost je een demo waarin
        // je staat te wachten op iets dat nooit is aangekomen.
        setNote("verstuurd via MQTT (fire-and-forget, geen bevestiging)");
      }
      await load();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (error) {
    return (
      <section>
        <h1>Storingen</h1>
        <p role="alert">
          Park niet bereikbaar: {error}
          <br />
          Het machinepark draait achter een compose-profiel. Start het met{" "}
          <code>--profile park-slim</code> of <code>--profile park</code>.
        </p>
      </section>
    );
  }

  if (!cat) return <section><h1>Storingen</h1><p>Laden…</p></section>;

  const activeCount = cat.machines.reduce(
    (n, m) => n + Object.keys(m.active_faults ?? {}).length, 0);

  return (
    <section>
      <h1>Storingen</h1>
      <p>
        {cat.machines.length} machines, {cat.total_faults} storingen.{" "}
        {activeCount > 0
          ? `${activeCount} actief.`
          : "Niets actief."}
      </p>

      <div>
        <label htmlFor="magnitude">
          Ernst: {magnitude.toFixed(2)}
        </label>
        <input
          id="magnitude"
          type="range"
          min={0.05}
          max={1}
          step={0.05}
          value={magnitude}
          onChange={(e) => setMagnitude(Number(e.target.value))}
        />
        <button
          type="button"
          onClick={() => void send({ action: "clear-all" }, "clear-all")}
          disabled={busy !== null || activeCount === 0}
        >
          Alles uit
        </button>
      </div>

      {note && <p role="status">{note}</p>}

      <table>
        <caption>
          Per machine alleen de storingen die de fysica echt implementeert.
        </caption>
        <thead>
          <tr>
            <th scope="col">Machine</th>
            <th scope="col">Area</th>
            <th scope="col">Leverancier</th>
            <th scope="col">Transport</th>
            <th scope="col">Storingen</th>
          </tr>
        </thead>
        <tbody>
          {cat.machines.map((m) => (
            <tr key={m.equipment_id}>
              <th scope="row">{m.equipment_id}</th>
              <td>{m.area}</td>
              <td>{m.vendor}</td>
              <td>
                {m.transport === "opcua"
                  ? "OPC UA (werkt zonder broker)"
                  : "MQTT (fire-and-forget)"}
              </td>
              <td>
                {m.faults.map((f) => {
                  const active = m.active_faults?.[f];
                  const key = `${m.equipment_id}:${f}`;
                  return (
                    <span key={f}>
                      <button
                        type="button"
                        aria-pressed={active !== undefined}
                        disabled={busy !== null}
                        onClick={() =>
                          void send(
                            active !== undefined
                              ? { action: "clear", machine: m.equipment_id, fault: f }
                              : { machine: m.equipment_id, fault: f, magnitude },
                            key,
                          )
                        }
                        title={FAULT_LABEL[f] ?? f}
                      >
                        {f}
                        {active !== undefined ? ` (${active.toFixed(2)}) ✕` : ""}
                      </button>{" "}
                    </span>
                  );
                })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
