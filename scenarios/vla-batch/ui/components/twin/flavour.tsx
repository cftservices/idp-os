"use client";

import { createContext, useContext, useMemo } from "react";
import type { TwinPalette } from "@/lib/twin/palette";
import { flavourKey, type Flavour } from "@/lib/twin/lines";
import { useTwin } from "./context";

/** batch_id -> smaak, zodat een pallet de kleur van zijn product draagt. */
export const FlavourContext = createContext<Record<string, Flavour>>({});

/**
 * Palet voor de pakken van één batch: dezelfde huisstijl, alleen de vlakleur
 * (`choco`) wordt de smaak. Chocolade en onbekend houden het gewone palet,
 * zodat L1 er precies zo uitziet als altijd.
 */
export function useFlavourPalette(batchId?: string | null): TwinPalette {
  const { palette } = useTwin();
  const flavours = useContext(FlavourContext);
  const f = batchId ? flavours[batchId] : undefined;
  return useMemo(
    () => (!f || f === "chocolate" ? palette : { ...palette, choco: palette[flavourKey(f)] }),
    [palette, f],
  );
}
