"use client";

import { createContext, useContext } from "react";
import type { TwinPalette } from "@/lib/twin/palette";
import type { VisualState } from "@/lib/twin/derive";

export type TwinCtx = {
  palette: TwinPalette;
  selected: string | null;
  hovered: string | null;
  select: (id: string | null) => void;
  hover: (id: string | null) => void;
  reducedMotion: boolean;
  /** Objecten die nu een zwevend label krijgen (labelBudget). */
  labelled: Set<string>;
  /** Procesregel per object-id, bv. "92,4 °C". */
  meta: Record<string, string>;
  /**
   * Livestream (?broadcast=1): geen HTML in de 3D-scène. Elk drei-<Html> wordt
   * elk frame opnieuw in de DOM geplaatst, en op een Pi 5 zat de hoofdthread
   * van Chromium daardoor op 100% (bankproef 20-11-2026: 5,6 fps).
   */
  broadcast: boolean;
};

export const TwinContext = createContext<TwinCtx | null>(null);

export function useTwin(): TwinCtx {
  const v = useContext(TwinContext);
  if (!v) throw new Error("useTwin buiten TwinContext");
  return v;
}

export function statusColor(p: TwinPalette, s: VisualState): string {
  switch (s) {
    case "run":
      return p["status-run"];
    case "warn":
      return p["status-warn"];
    case "alarm":
      return p["status-alarm"];
    case "stale":
      return p["status-stale"];
    default:
      return p["status-idle"];
  }
}
