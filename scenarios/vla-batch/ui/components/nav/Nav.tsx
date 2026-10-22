"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAlarms } from "@/lib/queries";

/**
 * De globale navigatie.
 *
 * Tot nu toe had deze UI er GEEN. Elf routes, nul links in het root-layout: je
 * kwam er alleen door de URL te typen. CIP stond keurig op /equipment en was in
 * de praktijk onvindbaar. Dat is de duurste soort bug, want geen enkele test
 * ziet hem: elk scherm werkte prima, alleen kwam niemand er.
 *
 * Twee groepen, niet een platte lijst. De schermen zijn ontworpen in twee
 * families met eigen dichtheid en chrome (zie de route-groups (ops) en (mgmt)),
 * en dat onderscheid hoort in het menu terug te komen. Een operator die de
 * vullijn draait heeft niets te zoeken in het report centre.
 *
 * De balk zelf staat bewust in de ops-familie: geen accentkleur, geen radius,
 * geen schaduw. Hij hangt ook boven operatorschermen en mag daar geen
 * decoratieve kleur binnensmokkelen.
 */

type Item = { href: string; label: string; hint: string };

const OPS: Item[] = [
  { href: "/line", label: "Lijn L1", hint: "Live procesbeeld, vijf processtappen" },
  { href: "/alarms", label: "Alarmen", hint: "Bevestigen en parkeren" },
  { href: "/shopfloor", label: "Werkvloer", hint: "Scannen, wegen, boeken, verzenden" },
  { href: "/scada", label: "SCADA", hint: "Setpoints, batches, simulatie" },
  { href: "/storingen", label: "Storingen", hint: "Storingen inschieten op lijn Vla-B" },
];

const MGMT: Item[] = [
  { href: "/", label: "Plant", hint: "Ligt de dienst op plan" },
  { href: "/management", label: "Management", hint: "KPI's en verliesblok" },
  { href: "/site", label: "3D Site", hint: "Hele site in één beeld, klik voor status" },
  { href: "/orders", label: "Orders", hint: "Aanmaken, batchen, sluiten" },
  { href: "/batches", label: "Batches", hint: "Verloop en verdict" },
  { href: "/equipment", label: "Equipment", hint: "CIP, draaiuren, OEE" },
  { href: "/voorraad", label: "Voorraad", hint: "Ontvangen en bijvullen" },
  { href: "/reports", label: "Rapporten", hint: "PDF per periode, batch, procesdeel" },
  { href: "/analyse", label: "Analyse", hint: "Trends in Grafana" },
];

const ALL = [...OPS, ...MGMT];

function isActive(pathname: string, href: string) {
  return href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`);
}

/** Openstaande alarmen, met de hoogste prioriteit erbij: dat is de reden om te schakelen. */
function useAlarmBadge() {
  const alarms = useAlarms();
  const open = (alarms.data ?? []).filter((a) => a.state === "open");
  return {
    count: open.length,
    high: open.some((a) => a.priority === "high"),
  };
}

function Badge({ count, high }: { count: number; high: boolean }) {
  if (count === 0) return null;
  return (
    <span
      aria-label={`${count} openstaande alarmen`}
      className={`num min-w-[1.25rem] px-1 text-center text-[0.6875rem] leading-[1.25rem] font-bold ${
        high ? "bg-status-alarm text-canvas" : "border border-line-strong text-ink-muted"
      }`}
    >
      {count}
    </span>
  );
}

function Group({
  title,
  items,
  pathname,
  badge,
  onNavigate,
}: {
  title: string;
  items: Item[];
  pathname: string;
  badge: { count: number; high: boolean };
  onNavigate?: () => void;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="eyebrow px-2.5 pt-3 pb-1">{title}</span>
      {items.map((item) => {
        const active = isActive(pathname, item.href);
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            title={item.hint}
            className={`flex items-center justify-between gap-2 border-l-[3px] px-2.5 py-2 text-[0.8125rem] ${
              active
                ? "border-l-ink bg-surface-sunken font-semibold text-ink"
                : "border-l-transparent text-ink-muted hover:bg-surface-sunken hover:text-ink"
            }`}
          >
            <span className="truncate">{item.label}</span>
            {item.href === "/alarms" && <Badge {...badge} />}
          </Link>
        );
      })}
    </div>
  );
}

export function Nav() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const badge = useAlarmBadge();

  // Een routewissel sluit de la. Zonder dit blijft hij openstaan over het
  // scherm dat je net gekozen hebt.
  useEffect(() => setOpen(false), [pathname]);

  const current = ALL.find((i) => isActive(pathname, i.href));

  const panel = (onNavigate?: () => void) => (
    <>
      <div className="flex items-baseline gap-2 border-b border-line px-2.5 pb-3">
        <span className="text-[0.9375rem] font-semibold tracking-[-0.01em]">DairyWorks</span>
        <span className="text-[0.6875rem] text-ink-faint">Vla</span>
      </div>
      <Group
        title="Operator"
        items={OPS}
        pathname={pathname}
        badge={badge}
        onNavigate={onNavigate}
      />
      <Group
        title="Management"
        items={MGMT}
        pathname={pathname}
        badge={badge}
        onNavigate={onNavigate}
      />
    </>
  );

  return (
    <div data-ui="ops" className="contents">
      {/* Mobiel: een smalle balk met de hamburger en waar je bent. */}
      <header className="sticky top-0 z-30 flex items-center gap-2 border-b border-line-strong bg-surface px-2 py-1.5 lg:hidden">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-expanded={open}
          aria-controls="hoofdmenu"
          className="flex items-center gap-2 px-2 py-1.5 text-[0.8125rem] font-semibold"
        >
          <span aria-hidden className="flex flex-col gap-[3px]">
            <i className="block h-[2px] w-4 bg-ink" />
            <i className="block h-[2px] w-4 bg-ink" />
            <i className="block h-[2px] w-4 bg-ink" />
          </span>
          Menu
        </button>
        <span className="min-w-0 flex-1 truncate text-[0.8125rem] text-ink-muted">
          {current?.label ?? "DairyWorks"}
        </span>
        <Link href="/alarms" className="flex items-center gap-1.5 px-2 py-1.5 text-[0.8125rem]">
          Alarmen
          <Badge {...badge} />
        </Link>
      </header>

      {/* Mobiel: de la zelf. */}
      {open && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Menu sluiten"
            onClick={() => setOpen(false)}
            className="absolute inset-0 bg-ink opacity-40"
          />
          <nav
            id="hoofdmenu"
            aria-label="Hoofdmenu"
            className="absolute inset-y-0 left-0 flex w-60 max-w-[80vw] flex-col gap-0.5 overflow-y-auto border-r border-line-strong bg-surface py-3"
          >
            {panel(() => setOpen(false))}
          </nav>
        </div>
      )}

      {/* Desktop: vaste kolom. */}
      <nav
        aria-label="Hoofdmenu"
        className="sticky top-0 hidden h-screen w-52 shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-line-strong bg-surface py-3 lg:flex"
      >
        {panel()}
      </nav>
    </div>
  );
}
