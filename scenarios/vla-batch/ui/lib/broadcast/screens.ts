/**
 * De schermen van de livestream, in volgorde.
 *
 * Alleen eigen routes van deze UI, met ?embed=1 zodat de navigatie wegvalt.
 * Same-origin, dus geen X-Frame-Options, geen CSP en geen tweede login: de
 * renderer opent http://vla-ui:3000/broadcast rechtstreeks op idp-network.
 *
 * De 3D-twin staat er NIET in. Meetproef 20-11-2026: zonder GPU tekent
 * Chromium hem op de VPS met 0,3 à 0,4 fps, en de host raakte verzadigd (zie
 * scenarios/vla-batch/broadcast/README.md). Het lichte uitzendprofiel
 * (/site?embed=1&broadcast=1) blijft bestaan voor een machine die het wél
 * trekt; zet hem dan hier terug, bijvoorbeeld 90 seconden vooraan.
 *
 * Grafana staat er bewust (nog) niet in: anonymous staat uit en Grafana heeft
 * een eigen login. Zie README.md, stap 2.
 *
 * Titels in het Engels: de stream is EN-talig, net als de rest van de
 * DairyWorks-video's.
 */
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
