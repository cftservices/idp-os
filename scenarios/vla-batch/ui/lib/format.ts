/**
 * Opmaakregels uit hmi-style-guide.md §7.
 *
 * Dit is NADRUKKELIJK alleen presentatie. Er wordt hier niets uitgerekend: de
 * waarden komen kant-en-klaar uit kpi/summary. Een afwijkende formule in de UI
 * is per contract een bug.
 */

const NL = "nl-NL";

/** Maximaal een decimaal. Reken in volle precisie, rond af bij het renderen. */
export function num(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toLocaleString(NL, {
    minimumFractionDigits: 0,
    maximumFractionDigits: decimals,
  });
}

/** Compact voor krappe kaartjes: 41,3k en 3,33 mln. Onder de 10.000 gewoon num(). */
export function compact(v: number | null | undefined, decimals = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  const a = Math.abs(v);
  if (a >= 1e6) return `${num(v / 1e6, a >= 1e7 ? 1 : 2)} mln`;
  if (a >= 1e4) return `${num(v / 1e3, 1)}k`;
  return num(v, decimals);
}

/** Bedragen: nul decimalen, duizendscheiding. */
export function money(v: number | null | undefined, currency = "EUR"): string {
  if (v === null || v === undefined) return "-";
  return v.toLocaleString(NL, {
    style: "currency",
    currency,
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

/**
 * Aantal decimalen per eenheid. Een viscositeit tonen als 154,7382 cP
 * suggereert een nauwkeurigheid die de sensor niet heeft.
 */
const DECIMALS: Record<string, number> = {
  "%": 1,
  "packs/h": 0,
  cP: 0,
  "°C": 1,
  L: 0,
  kg: 1,
  ml: 1,
  h: 1,
  "": 2,
};

export function value(v: number | null | undefined, unit = ""): string {
  return num(v, DECIMALS[unit] ?? 1);
}

/** Een leeg getal is een streepje. Nooit 0, nooit een verzonnen nul. */
export const EMPTY = "-";

export function shortTime(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return EMPTY;
  return d.toLocaleTimeString(NL, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function shortDateTime(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return EMPTY;
  return d.toLocaleString(NL, {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** "wk 31 tegen wk 30", "dag tegen gisteren". Puur een label. */
export function windowLabel(window: string, from: string): string {
  const d = new Date(from);
  if (Number.isNaN(d.getTime())) return window;
  switch (window) {
    case "week": {
      const wk = isoWeek(d);
      return `wk ${wk} tegen wk ${wk === 1 ? 52 : wk - 1}`;
    }
    case "day":
      return `${d.toLocaleDateString(NL, { day: "2-digit", month: "short" })} tegen de dag ervoor`;
    case "month":
      return `${d.toLocaleDateString(NL, { month: "long" })} tegen de maand ervoor`;
    case "shift":
      return "dienst tegen de vorige dienst";
    default:
      return window;
  }
}

function isoWeek(d: Date): number {
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  const day = t.getUTCDay() || 7;
  t.setUTCDate(t.getUTCDate() + 4 - day);
  const start = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  return Math.ceil(((t.getTime() - start.getTime()) / 86400000 + 1) / 7);
}

/** Leeftijd van een meting in leesbare vorm, voor de stale-melding. */
export function age(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return EMPTY;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return EMPTY;
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  return `${Math.floor(s / 3600)} u`;
}
