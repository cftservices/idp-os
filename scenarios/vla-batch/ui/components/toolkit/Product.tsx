"use client";

import { useMemo } from "react";
import { useLines } from "@/lib/queries";

/**
 * Productnamen en smaakkleur komen uit GET /lines, dus uit het recepten-
 * register van de engine. De UI kent zelf geen receptenlijst.
 * De kleur is een CSS-variabele (--twin-flavour-*), geen hex in TSX.
 */

export type RecipeInfo = {
  recipe_id: string;
  product_name: string;
  flavour: string;
  line_id: string;
};

export function useRecipeIndex(): Map<string, RecipeInfo> {
  const lines = useLines();
  return useMemo(() => {
    const m = new Map<string, RecipeInfo>();
    for (const l of lines.data ?? []) {
      for (const r of l.recipes) {
        m.set(r.recipe_id, {
          recipe_id: r.recipe_id,
          product_name: r.product_name,
          flavour: r.flavour,
          line_id: l.line_id,
        });
      }
    }
    return m;
  }, [lines.data]);
}

export function FlavourDot({ flavour }: { flavour?: string | null }) {
  if (!flavour) return null;
  return (
    <i
      aria-hidden
      className="inline-block h-2.5 w-2.5 shrink-0 border border-line-strong"
      style={{ background: `var(--twin-flavour-${flavour === "cocoa" ? "chocolate" : flavour})` }}
    />
  );
}

/** Product met smaakkleur en lijn; valt terug op de recipe_id. */
export function ProductTag({
  recipeId,
  index,
  showLine = false,
}: {
  recipeId?: string | null;
  index: Map<string, RecipeInfo>;
  showLine?: boolean;
}) {
  if (!recipeId) return null;
  const r = index.get(recipeId);
  return (
    <span className="inline-flex items-center gap-1.5">
      <FlavourDot flavour={r?.flavour} />
      <span>{r?.product_name ?? recipeId}</span>
      {showLine && r && <span className="mono text-[0.6875rem] text-ink-faint">{r.line_id}</span>}
    </span>
  );
}
