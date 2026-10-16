"use client";

import { useEffect, useState } from "react";

/**
 * Het gamepalet van de 3D site-twin, gelezen uit de --twin-*-tokens in
 * globals.css. three.js wil een kleurwaarde in JavaScript, terwijl de
 * tokenpoort hexwaarden buiten globals.css verbiedt. Deze hook is dus de
 * enige brug: de kleur zelf staat nog steeds op één plek.
 */
export const TWIN_KEYS = [
  "sky",
  "ground",
  "ground-line",
  "road",
  "road-mark",
  "hall",
  "hall-roof",
  "cold",
  "cold-roof",
  "steel",
  "steel-dark",
  "tank",
  "milk",
  "vla",
  "pipe",
  "rack",
  "pallet",
  "pack",
  "truck-cab",
  "truck-box",
  "dock",
  "silo",
  "tree",
  "accent",
  "glow",
  "status-run",
  "status-idle",
  "status-warn",
  "status-alarm",
  "status-stale",
] as const;

export type TwinKey = (typeof TWIN_KEYS)[number];
export type TwinPalette = Record<TwinKey, string>;

export function readTwinPalette(el: Element): TwinPalette {
  const css = getComputedStyle(el);
  const out = {} as TwinPalette;
  for (const k of TWIN_KEYS) {
    // Een ontbrekend token mag de scène niet zwart maken; "grey" valt op
    // zonder dat het een status suggereert.
    out[k] = css.getPropertyValue(`--twin-${k}`).trim() || "grey";
  }
  return out;
}

export function useTwinPalette(): TwinPalette | null {
  const [palette, setPalette] = useState<TwinPalette | null>(null);
  useEffect(() => {
    setPalette(readTwinPalette(document.documentElement));
  }, []);
  return palette;
}
