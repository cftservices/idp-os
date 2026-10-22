import { readFile } from "node:fs/promises";
import { STEPS, asLineId, equipmentOn, statusTopic } from "@/lib/domain/uns";

/**
 * De L1-payload, stap-georienteerd.
 *
 * Voegt drie bronnen samen zodat de browser er maar een hoeft te kennen:
 *   - GET /line/live      batch, doseringen, vulling, kwaliteit
 *   - GET /tags?verbose=1 waarde, ts, quality en leeftijd per topic
 *   - factory-model.json  specbanden, drempels, vulgrenzen
 *
 * En het belangrijkste: hier wordt bepaald of een waarde VEROUDERD is. Dat kan
 * alleen hier, want het vergt de koppeling tussen topic en processtap, en die
 * kennis blijft server-side (lib/domain/uns.ts draagt `server-only`).
 *
 * Zonder dit toont een tag die minutenlang stilligt zijn laatste waarde alsof
 * die actueel is. Bij een poll van 1,5 seconde is dat de meest waarschijnlijke
 * faalmodus van de hele demo.
 */

export const dynamic = "force-dynamic";

const ENGINE = process.env.ENGINE_URL ?? "http://vla-batch-engine:8000";
const MODEL_PATH = process.env.FACTORY_MODEL ?? "/model/isa95-vla.json";
const DEFAULT_STALE_S = 90;

type Reading = {
  value: number | string | null;
  ts: string | null;
  unit: string | null;
  quality: string;
  retained: boolean;
  age_s: number | null;
};

async function engine<T>(path: string): Promise<T | null> {
  try {
    const r = await fetch(`${ENGINE}/api/v1${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(10_000),
    });
    return r.ok ? ((await r.json()) as T) : null;
  } catch {
    return null;
  }
}

function toNumber(v: unknown): number | null {
  const n = typeof v === "string" ? Number(v) : typeof v === "number" ? v : NaN;
  return Number.isFinite(n) ? n : null;
}

export async function GET(req: Request) {
  const lineId = asLineId(new URL(req.url).searchParams.get("line"));
  const [live, tags, loaded] = await Promise.all([
    engine<Record<string, unknown>>(`/line/live?line=${lineId}`),
    engine<Record<string, Reading>>("/tags?verbose=1"),
    readFile(MODEL_PATH, "utf-8")
      .then((t) => ({ ok: true, model: JSON.parse(t) as Record<string, unknown> }))
      .catch(() => ({ ok: false, model: {} as Record<string, unknown> })),
  ]);

  const model = loaded.model;
  // Een stille terugval op de default is precies wat we elders verbieden: het
  // scherm zou dan een specband tonen die nergens vandaan komt, of juist geen
  // band zonder te zeggen waarom. Melden dus, en de UI beslist wat hij toont.
  const modelAvailable = loaded.ok;
  const staleAfter = Number(model.stale_threshold_s ?? DEFAULT_STALE_S);
  const staleFromModel = modelAvailable && model.stale_threshold_s !== undefined;
  const recipe = ((model.recipes as Array<Record<string, unknown>>) ?? [{}])[0] ?? {};
  const viscSpec = recipe.viscosity_spec_cP as { min: number; max: number } | undefined;
  const basisL = toNumber(recipe.basis_L);
  const cookSetpoint = toNumber(recipe.cook_setpoint_C);
  const packsTarget = toNumber(
    (live?.filling as Record<string, unknown> | undefined)?.packs_target,
  );
  const batchState =
    ((live?.batch as Record<string, unknown> | undefined)?.state as string | undefined) ?? null;

  /**
   * De schaal van een meting, met vermelding waar hij vandaan komt.
   *
   * Zonder dit viel de balk op /line terug op 0 tot 100. Bij een tankniveau van
   * 5000 L stond de marker dan vastgepind rechts en las de as "0 ... 100": een
   * meter die iets anders beweert dan het getal ernaast. Een verzonnen schaal
   * is erger dan geen schaal, dus zonder bron blijft dit null en toont de UI
   * dat er geen schaal bekend is.
   */
  function scaleFor(id: string): { min: number; max: number; source: string } | null {
    switch (id) {
      case "receiving":
      case "mixing":
        return basisL ? { min: 0, max: basisL, source: `batchgrootte ${basisL} L uit het recept` } : null;
      case "cooling":
        return cookSetpoint
          ? { min: 0, max: Math.ceil(cookSetpoint * 1.15), source: "kook-setpoint uit het recept" }
          : null;
      case "filling":
        return packsTarget ? { min: 0, max: packsTarget, source: "pakkendoel van de order" } : null;
      default:
        // Koken heeft een tweezijdige spec; die levert zijn schaal al.
        return null;
    }
  }

  /**
   * Waarden die per definitie NOOIT veranderen, en daarom nooit op de bus komen.
   *
   * De OPC-UA-koppeling publiceert bij verandering. Een kook-setpoint van 88 en
   * een pakgrootte van 1,0 veranderen nooit, dus die zijn na de eerste
   * verbinding onzichtbaar en stonden op elk scherm als een streepje. Ze
   * periodiek opnieuw laten publiceren zou werken, maar dat is precies wat de
   * historian ooit 5,3 miljoen nutteloze rijen bezorgde voor een tag.
   *
   * Ze staan al in het recept, en dat is ook hun ware aard: een setpoint is
   * CONFIGURATIE, geen meting. Dus halen we ze daar op en zeggen erbij dat het
   * configuratie is. Doen alsof het een meting is, zou een leugen zijn over de
   * herkomst.
   */
  const CONFIG_VALUE: Record<string, number | null> = {
    "cooking/Setpoint": toNumber(recipe.cook_setpoint_C),
    "cooling/Doel": toNumber(recipe.cool_target_C),
    "filling/Pakgrootte": toNumber(recipe.pack_size_L),
  };

  const reading = (area: string, eq: string, tag: string): Reading | null =>
    tags?.[statusTopic(area, eq, tag, lineId)] ?? null;

  /**
   * De jongste leeftijd over alle tags van een unit: de hartslag van de bron.
   *
   * De bus publiceert alleen bij verandering. De leeftijd van een losse tag zegt
   * dus hoe lang de WAARDE niet veranderde, niet of de BRON nog leeft. Een vol
   * tankniveau tijdens het koken of een vast setpoint werd daardoor na 90 s
   * "verouderd" terwijl dezelfde unit elke seconde state en draaiuren stuurde.
   */
  const heartbeat = new Map<string, number>();
  for (const [topic, r] of Object.entries(tags ?? {})) {
    if (r.retained || r.age_s === null) continue;
    const unit = topic.split("/Status/")[0];
    heartbeat.set(unit, Math.min(heartbeat.get(unit) ?? Infinity, r.age_s));
  }

  /**
   * Verouderd volgens drie onafhankelijke signalen. Een waarde hoeft er maar
   * een te raken: quality die niet GOOD is, alleen ooit een retained bericht
   * gezien (dan is er nooit iets live geweest), of een unit die als geheel
   * langer dan de drempel zwijgt.
   */
  function isStale(r: Reading | null, area: string, eq: string): boolean {
    if (!r) return false;
    if (r.retained) return true;
    if (r.quality && r.quality !== "GOOD" && r.quality !== "UNKNOWN") return true;
    if (r.age_s === null || r.age_s <= staleAfter) return false;
    const unitAge = heartbeat.get(statusTopic(area, eq, "", lineId).split("/Status/")[0]);
    return unitAge === undefined || unitAge > staleAfter;
  }

  const steps = STEPS.map((s) => {
    const primary = reading(s.area, s.equipment, s.primary.tag);
    const secondary = s.secondary.map((t) => {
      const live = toNumber(reading(s.area, s.equipment, t.tag)?.value);
      const config = live === null ? (CONFIG_VALUE[`${s.id}/${t.label}`] ?? null) : null;
      return {
        label: t.label,
        unit: t.unit,
        value: live ?? config,
        // Configuratie is niet verouderd: hij hoort niet te veranderen. Hem als
        // stale tonen zou een storing suggereren die er niet is.
        stale: config !== null ? false : isStale(reading(s.area, s.equipment, t.tag), s.area, s.equipment),
        /** true als deze waarde uit het recept komt en niet van de bus. */
        from_config: config !== null,
      };
    });

    const stale = isStale(primary, s.area, s.equipment) || secondary.some((x) => x.stale);
    const value = toNumber(primary?.value);

    // Alleen de kookstap heeft een tweezijdige specificatie. De rest toont een
    // waarde met context, maar zonder verzonnen band.
    const spec = s.id === "cooking" ? (viscSpec ?? null) : null;
    let tone: "neutral" | "warn" | "alarm" = "neutral";
    let distance: { label: string; value: number; unit: string } | null = null;

    /**
     * Viscositeit is pas na de hold een oordeel waard.
     *
     * Tijdens het koken bouwt hij zich op vanaf vrijwel nul, dus hem meteen
     * tegen de ondergrens afrekenen levert een KRITIEK dat bij elke normale
     * batch verschijnt en na een paar minuten vanzelf verdwijnt. Dat is een
     * vals alarm, en vals alarm is precies wat ISA-18.2 bestrijdt: een
     * operator die leert dat rood vanzelf overgaat, kijkt straks ook weg bij
     * echt rood. De engine oordeelt zelf ook pas op end_viscosity_cP.
     */
    const pending =
      s.id === "cooking" && !["COOLING", "FILLING", "COMPLETE"].includes(batchState ?? "")
        ? "bouwt nog op, wordt beoordeeld na de hold"
        : null;

    if (spec && value !== null && !pending) {
      if (value < spec.min) {
        tone = "alarm";
        distance = { label: "onder ondergrens", value: spec.min - value, unit: s.primary.unit };
      } else if (value > spec.max) {
        tone = "alarm";
        distance = { label: "boven bovengrens", value: value - spec.max, unit: s.primary.unit };
      } else {
        const toLow = value - spec.min;
        const toHigh = spec.max - value;
        const near = Math.min(toLow, toHigh) < (spec.max - spec.min) * 0.2;
        tone = near ? "warn" : "neutral";
        distance =
          toLow <= toHigh
            ? { label: "naar ondergrens", value: toLow, unit: s.primary.unit }
            : { label: "naar bovengrens", value: toHigh, unit: s.primary.unit };
      }
    }

    return {
      id: s.id,
      name: s.name,
      equipment: equipmentOn(s.equipment, lineId),
      primary: {
        label: s.primary.label,
        unit: s.primary.unit,
        value,
        ts: primary?.ts ?? null,
        age_s: primary?.age_s ?? null,
        quality: primary?.quality ?? "UNKNOWN",
      },
      secondary,
      spec,
      scale: scaleFor(s.id),
      /** Gevuld als de spec bewust nog NIET wordt toegepast, met de reden. */
      spec_pending: pending,
      distance,
      tone,
      stale,
    };
  });

  return Response.json(
    {
      line_id: lineId,
      available: Boolean(live?.available) || Boolean(tags),
      engine_reachable: live !== null,
      bus_has_tags: Boolean(tags && Object.keys(tags).length),
      model_available: modelAvailable,
      model_path: modelAvailable ? undefined : MODEL_PATH,
      stale_threshold_s: staleAfter,
      stale_threshold_from_model: staleFromModel,
      steps,
      line: live ?? null,
      fill_limits_ml: model.fill_limits_ml ?? null,
    },
    { headers: { "cache-control": "no-store" } },
  );
}
