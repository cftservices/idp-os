/**
 * De schermen van de livestream, in volgorde.
 *
 * Alleen eigen routes van deze UI, met ?embed=1 zodat de navigatie wegvalt.
 * Same-origin, dus geen X-Frame-Options, geen CSP en geen tweede login: de
 * renderer opent http://vla-ui:3000/broadcast rechtstreeks op idp-network.
 *
 * De 3D-twin staat er standaard NIET in. Meetproef 20-11-2026: zonder GPU
 * tekent Chromium hem op de VPS met 0,3 à 0,4 fps, en de host raakte
 * verzadigd (zie scenarios/vla-batch/broadcast/README.md). Een machine met een
 * GPU opent /broadcast?twin=1; dan komt TWIN_SCREEN vooraan. Een Pi 5 haalt
 * daarmee 15 fps (bankproef 2, 20-11-2026).
 *
 * Grafana: anonymous staat uit, dus alleen publiek gedeelde dashboards (grafanaScreens), met de
 * token van de renderer. Zie broadcast/README.md.
 *
 * Titels in het Engels: de stream is EN-talig, net als de rest van de
 * DairyWorks-video's.
 */
import { STORY_SECONDS } from "@/lib/twin/story";

export type BroadcastScreen = {
  id: string;
  title: string;
  /** Pad binnen deze UI, inclusief ?embed=1. */
  path: string;
  /** Weergaveduur in seconden. */
  seconds: number;
};

export const BROADCAST_SCREENS: BroadcastScreen[] = [
  { id: "plant", title: "Plant overview", path: "/?embed=1", seconds: 45 },
  { id: "line", title: "Live process: lines L1 and L2", path: "/line?embed=1", seconds: 45 },
  { id: "management", title: "Management KPIs and losses", path: "/management?embed=1", seconds: 45 },
  { id: "equipment", title: "Equipment: CIP, running hours, OEE", path: "/equipment?embed=1", seconds: 45 },
];

/**
 * De 3D-twin in het lichte uitzendprofiel, in story-modus: de camera loopt de fabriek langs met
 * Engelse uitleg per stop (lib/twin/story.ts, ongeveer 6 minuten). Alleen met /broadcast?twin=1.
 */
export const TWIN_SCREEN: BroadcastScreen = {
  id: "twin",
  title: "Plant tour: from raw milk to the customer, live",
  path: "/site?embed=1&broadcast=1&story=1",
  // + de laadtijd van de iframe, anders valt het einde van de laatste stop weg.
  seconds: STORY_SECONDS + 5,
};

/**
 * Publiek gedeelde Grafana-dashboards (grafana/stream_board.py, grafana/pi_board.py). Alleen deze
 * uids; de token komt van de renderer (?gf=uid:token,...), niet uit git. De Pi bereikt Grafana via
 * zijn tunnel op localhost:3001; dat is het enige adres dat we toestaan.
 */
const GRAFANA_LOCAL = "http://localhost:3001/grafana";
const GRAFANA_SCREENS: Record<string, { title: string; seconds: number }> = {
  "stream-factory": { title: "Grafana: the historian behind the twin (cooking, quality, OEE)", seconds: 45 },
  "pi5-health": { title: "Grafana: the Raspberry Pi 5 that renders this stream", seconds: 30 },
};

/** "stream-factory:abc…,pi5-health:def…" → screens; onbekende uids en rare tokens vallen weg. */
export function grafanaScreens(gf: string | undefined): Record<string, BroadcastScreen> {
  const out: Record<string, BroadcastScreen> = {};
  for (const part of (gf ?? "").split(",")) {
    const [uid, token] = part.split(":");
    const meta = GRAFANA_SCREENS[uid];
    if (!meta || !/^[a-f0-9]{32}$/.test(token ?? "")) continue;
    out[uid] = { id: `grafana-${uid}`, title: meta.title, path: `${GRAFANA_LOCAL}/public-dashboards/${token}`, seconds: meta.seconds };
  }
  return out;
}

/**
 * fps (10 t/m 30) gaat door naar de twin als framecap; zonder fps houdt die zijn standaard van 20.
 * Met twin alleen Engelstalige schermen: rondleiding, Grafana-proces, Grafana-Pi (ongeveer 7 minuten).
 * De vier 2D-schermen van vla-ui zijn Nederlands en blijven daarom alleen in de 2D-variant.
 */
export function broadcastScreens({ twin, fps, gf }: { twin: boolean; fps?: number; gf?: string }): BroadcastScreen[] {
  if (!twin) return BROADCAST_SCREENS;
  const path = fps ? `${TWIN_SCREEN.path}&fps=${fps}` : TWIN_SCREEN.path;
  const g = grafanaScreens(gf);
  return [
    { ...TWIN_SCREEN, path },
    ...(g["stream-factory"] ? [g["stream-factory"]] : []),
    ...(g["pi5-health"] ? [g["pi5-health"]] : []),
  ];
}
