"use client";

import { useEffect, useState } from "react";
import type { BroadcastScreen } from "@/lib/broadcast/screens";

/**
 * De carrousel van de livestream: één scherm tegelijk, schermvullend, met een
 * smalle balk boven en onder.
 *
 * Steeds maar één iframe actief, behalve tijdens een wissel. Het volgende
 * scherm laadt onzichtbaar boven het huidige en neemt het pas over na zijn
 * load plus een korte marge. Zonder die marge zou de kijker bij elke wissel
 * "3D-scène laden" zien. Daarna verdwijnt het oude iframe, zodat er nooit
 * twee scènes tegelijk tekenen op een CPU zonder GPU.
 */

/** Marge na de load-event voordat een nieuw scherm zichtbaar wordt. */
const SETTLE_MS = 2500;

function useClock() {
  const [now, setNow] = useState<string>("");
  useEffect(() => {
    const fmt = () =>
      new Date().toLocaleTimeString("en-GB", {
        timeZone: "Europe/Amsterdam",
        hour: "2-digit",
        minute: "2-digit",
      });
    setNow(fmt());
    const id = window.setInterval(() => setNow(fmt()), 10_000);
    return () => window.clearInterval(id);
  }, []);
  return now;
}

export default function ScreenCarousel({ screens }: { screens: BroadcastScreen[] }) {
  const [shown, setShown] = useState(0);
  const [loading, setLoading] = useState<number | null>(null);
  const clock = useClock();

  // Na de weergaveduur het volgende scherm laden (nog onzichtbaar).
  useEffect(() => {
    if (screens.length < 2) return;
    const id = window.setTimeout(
      () => setLoading((shown + 1) % screens.length),
      screens[shown].seconds * 1000,
    );
    return () => window.clearTimeout(id);
  }, [shown, screens]);

  // Laadt een scherm nooit (route kapot, timeout), dan na een minuut toch door:
  // een stilstaande stream is erger dan één leeg scherm.
  useEffect(() => {
    if (loading === null) return;
    const id = window.setTimeout(() => {
      setShown(loading);
      setLoading(null);
    }, 60_000);
    return () => window.clearTimeout(id);
  }, [loading]);

  if (!screens.length) {
    return <main className="grid h-screen place-items-center text-ink-muted">No screens configured</main>;
  }

  const current = screens[shown];
  const frame = (i: number, visible: boolean) => (
    <iframe
      key={`${screens[i].id}-${i}`}
      src={screens[i].path}
      title={screens[i].title}
      onLoad={
        visible
          ? undefined
          : () =>
              window.setTimeout(() => {
                setShown(i);
                setLoading(null);
              }, SETTLE_MS)
      }
      // Geen schuifbalk in beeld: wat onder de vouw valt, valt buiten de uitzending.
      scrolling="no"
      className="absolute inset-0 h-full w-full border-0"
      style={{ opacity: visible ? 1 : 0 }}
      aria-hidden={!visible}
    />
  );

  return (
    <main data-ui="mgmt" className="flex h-screen w-screen flex-col overflow-hidden bg-canvas text-ink">
      <header className="flex h-11 shrink-0 items-center justify-between border-b border-line-strong bg-surface px-5">
        <div className="flex items-baseline gap-2">
          <span className="text-[0.9375rem] font-semibold tracking-[-0.01em]">DairyWorks</span>
          <span className="text-[0.8125rem] text-ink-muted">Vla batch demo · open-source data layer</span>
        </div>
        <div className="flex items-center gap-4 text-[0.8125rem]">
          <span className="text-ink-muted">Simulated plant, live data</span>
          <span className="flex items-center gap-1.5 font-semibold">
            <i aria-hidden className="block h-2 w-2 rounded-full bg-status-run" />
            LIVE
          </span>
          <span className="num text-ink-muted">{clock}</span>
        </div>
      </header>

      <section className="relative min-h-0 flex-1">
        {/* Eén gekeyde lijst, geen twee losse plekken: dan herkent React het
            iframe dat net geladen is en hergebruikt het bij de wissel, in
            plaats van het op de eerste plek opnieuw te laden. */}
        {[shown, ...(loading !== null && loading !== shown ? [loading] : [])].map((i) => frame(i, i === shown))}
      </section>

      <footer className="flex h-7 shrink-0 items-center justify-between border-t border-line-strong bg-surface px-5 text-[0.75rem] text-ink-muted">
        <span>{current.title}</span>
        <span className="num">
          {shown + 1} / {screens.length}
        </span>
      </footer>
    </main>
  );
}
