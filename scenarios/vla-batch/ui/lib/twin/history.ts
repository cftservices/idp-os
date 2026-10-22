"use client";

import { useRef } from "react";

/**
 * Korte historie voor de minigrafiekjes. De engine heeft geen
 * historie-endpoint, dus de browser houdt zelf een ringbuffer bij vanaf het
 * moment dat de pagina open staat. Dat staat ook zo in de detailkaart.
 */

export type Sample = { t: number; v: number };

export class Ring {
  private buf: Sample[] = [];
  constructor(readonly capacity: number) {}

  push(s: Sample) {
    const last = this.buf[this.buf.length - 1];
    if (last && s.t <= last.t) return;
    this.buf.push(s);
    if (this.buf.length > this.capacity) this.buf.splice(0, this.buf.length - this.capacity);
  }

  values(): Sample[] {
    return this.buf.slice();
  }

  get size() {
    return this.buf.length;
  }
}

/** Verandering tussen het eerste en het laatste punt; null bij minder dan twee punten. */
export function delta(samples: Sample[]): number | null {
  if (samples.length < 2) return null;
  return samples[samples.length - 1].v - samples[0].v;
}

/** SVG-pad voor een sparkline in een vak van w×h. Leeg bij minder dan twee punten. */
export function sparkPath(samples: Sample[], w: number, h: number): string {
  if (samples.length < 2) return "";
  const t0 = samples[0].t;
  const t1 = samples[samples.length - 1].t;
  let lo = Infinity;
  let hi = -Infinity;
  for (const s of samples) {
    lo = Math.min(lo, s.v);
    hi = Math.max(hi, s.v);
  }
  const span = hi - lo || 1;
  const dt = t1 - t0 || 1;
  return samples
    .map((s, i) => {
      const x = ((s.t - t0) / dt) * w;
      const y = h - 1 - ((s.v - lo) / span) * (h - 2);
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
}

/**
 * Houdt per sleutel een ring bij. `record` wordt bij elke modelupdate
 * aangeroepen; null-waarden worden overgeslagen (een gat, geen nul).
 */
export function useSeries(capacity = 90) {
  const rings = useRef(new Map<string, Ring>());
  return {
    record(t: number, values: Record<string, number | null | undefined>) {
      for (const [k, v] of Object.entries(values)) {
        if (v == null || !Number.isFinite(v)) continue;
        let r = rings.current.get(k);
        if (!r) rings.current.set(k, (r = new Ring(capacity)));
        r.push({ t, v });
      }
    },
    get(k: string): Sample[] {
      return rings.current.get(k)?.values() ?? [];
    },
  };
}
