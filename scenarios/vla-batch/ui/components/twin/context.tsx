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
