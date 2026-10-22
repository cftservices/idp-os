"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

/**
 * Lijnkeuze via `?line=` in de URL, zodat een link naar /line?line=L2 gewoon
 * werkt en de keuze een refresh overleeft. Pagina's die dit gebruiken staan
 * binnen een <Suspense> (eis van useSearchParams).
 */

export type LineChoice = "L1" | "L2";
export type LineFilter = LineChoice | "";

export const LINE_LABEL: Record<LineChoice, string> = {
  L1: "L1 · Hal 1, chocolade",
  L2: "L2 · Hal 2, smaken",
};

/** `allowAll` geeft "" (alle lijnen) als standaard in plaats van L1. */
export function useLineParam(allowAll: true): LineFilter;
export function useLineParam(allowAll?: false): LineChoice;
export function useLineParam(allowAll = false): LineFilter {
  const v = useSearchParams().get("line");
  if (v === "L1" || v === "L2") return v;
  return allowAll ? "" : "L1";
}

/** Lijn van een equipment-id: alles op -02 hoort bij L2. */
export function lineOfEquipment(id: string): LineChoice {
  return /-02$/.test(id) ? "L2" : "L1";
}

export function LineSwitch({ allowAll = false }: { allowAll?: boolean }) {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();
  const current = params.get("line") ?? (allowAll ? "" : "L1");
  const options: Array<{ v: LineFilter; label: string }> = [
    ...(allowAll ? [{ v: "" as const, label: "Alle lijnen" }] : []),
    { v: "L1", label: LINE_LABEL.L1 },
    { v: "L2", label: LINE_LABEL.L2 },
  ];

  function pick(v: LineFilter) {
    const next = new URLSearchParams(params.toString());
    if (v) next.set("line", v);
    else next.delete("line");
    const qs = next.toString();
    router.replace(qs ? `${path}?${qs}` : path, { scroll: false });
  }

  return (
    <div className="flex border border-line-strong" role="radiogroup" aria-label="Productielijn">
      {options.map((o) => {
        const on = current === o.v;
        return (
          <button
            key={o.v || "alle"}
            type="button"
            role="radio"
            aria-checked={on}
            onClick={() => pick(o.v)}
            className={`border-r border-line px-3 py-1.5 last:border-r-0 text-[0.75rem] font-semibold ${
              on ? "bg-ink text-canvas" : "text-ink-muted hover:text-ink"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** Kleine lijnbadge voor tabellen en panelen. */
export function LineBadge({ line }: { line?: string | null }) {
  if (!line) return null;
  return (
    <span className="mono inline-block border border-line-strong px-1.5 text-[0.6875rem] font-semibold">
      {line}
    </span>
  );
}
