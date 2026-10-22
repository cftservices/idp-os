"use client";

import type { DlTab } from "./datalayer";

/**
 * Tourmodus voor de video-reeks "grip op je fabriek via de datalaag".
 * Elke aflevering is een rij stappen; een stap opent de juiste lens en
 * vliegt naar het juiste object. Starten met /site?tour=ep1 … ep4.
 */

export type TourStep = { title: string; text: string; tab?: DlTab; select?: string | null };
export type Tour = { id: string; title: string; steps: TourStep[] };

export const TOURS: Record<string, Tour> = {
  ep1: {
    id: "ep1",
    title: "Afl. 1 · Eén plek voor al je fabrieksdata",
    steps: [
      {
        title: "Losse bronnen",
        text: "Twee PLC's, een MES en een magazijn. Elk spreekt zijn eigen taal. De datalaag brengt ze samen.",
        tab: "bronnen",
        select: null,
      },
      {
        title: "Van tag naar context",
        text: "Schakel tussen ruw uit de PLC en met context: hetzelfde getal, maar nu weet je welke machine, welke lijn en welke eenheid.",
        tab: "bronnen",
        select: "equipment:cook-unit-01",
      },
      {
        title: "Tweede lijn, zelfde structuur",
        text: "Lijn 2 hangt in dezelfde ISA-95-context. Een nieuwe lijn aansluiten is configureren, geen project.",
        tab: "bronnen",
        select: "equipment:cook-unit-02",
      },
      {
        title: "Iedereen leest dezelfde laag",
        text: "Historian, dashboards, lokale AI, onderhoud en deze twin: allemaal afnemers van één bron van waarheid.",
        tab: "bronnen",
        select: null,
      },
    ],
  },
  ep2: {
    id: "ep2",
    title: "Afl. 2 · Van storing naar werkorder",
    steps: [
      {
        title: "Een machine valt uit",
        text: "Kies een machine en druk op Storing melden. De lijn stopt en wacht op de reparatie.",
        tab: "bronnen",
        select: "equipment:filler-01",
      },
      {
        title: "De werkorder ontstaat vanzelf",
        text: "Het onderhoudssysteem krijgt de storing met de ISA-95-context erbij. Geen overtypen, geen telefoontje.",
        tab: "onderhoud",
        select: "equipment:filler-01",
      },
      {
        title: "Status terug in de laag",
        text: "Toegewezen, in uitvoering, gereed: de status van de werkorder staat bij de machine. MTBF en MTTR rollen eruit.",
        tab: "onderhoud",
        select: null,
      },
    ],
  },
  ep3: {
    id: "ep3",
    title: "Afl. 3 · Wat kost het in euro's",
    steps: [
      {
        title: "Verlies per machine",
        text: "Stilstand, overdosering, afkeur en herbewerking, vertaald naar euro's per machine. Alleen oorzakelijk verlies telt op.",
        tab: "euros",
        select: null,
      },
      {
        title: "Van symptoom naar oorzaak",
        text: "Open een machine met een alert en vraag de oorzaak. De keten van signalen wijst de machine aan waar het begon.",
        tab: "euros",
        select: "equipment:cook-unit-01",
      },
    ],
  },
  ep4: {
    id: "ep4",
    title: "Afl. 4 · Kun je je data vertrouwen",
    steps: [
      {
        title: "Datakwaliteit als regel",
        text: "Elke regel wordt continu gecontroleerd. Laat een signaal wegvallen en zie hem rood worden, terwijl de fabriek gewoon doordraait.",
        tab: "dataops",
        select: null,
      },
      {
        title: "Wat het kost aan server",
        text: "De hele stack op één VPS, open source, zonder licentie per tag. Dit is de meter.",
        tab: "dataops",
        select: null,
      },
    ],
  },
};

export function TourBar({
  tour,
  step,
  onStep,
  onClose,
}: {
  tour: Tour;
  step: number;
  onStep: (n: number) => void;
  onClose: () => void;
}) {
  const s = tour.steps[step];
  const last = step >= tour.steps.length - 1;
  return (
    <section className="wt-tour wt-glass" aria-label={tour.title} data-twin-tour={tour.id}>
      <p className="wt-eyebrow">
        {tour.title} · stap {step + 1}/{tour.steps.length}
      </p>
      <h2 className="wt-detail-title">{s.title}</h2>
      <p className="wt-tour-text">{s.text}</p>
      <div className="wt-dl-buttons">
        <button type="button" className="wt-chip-btn" disabled={step === 0} onClick={() => onStep(step - 1)}>
          ← Vorige
        </button>
        {last ? (
          <button type="button" className="wt-chip-btn" onClick={onClose}>
            Klaar
          </button>
        ) : (
          <button type="button" className="wt-chip-btn" aria-pressed="true" onClick={() => onStep(step + 1)}>
            Volgende →
          </button>
        )}
        <button type="button" className="wt-chip-btn" onClick={onClose}>
          Stop tour
        </button>
      </div>
    </section>
  );
}
