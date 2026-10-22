import "server-only";

/**
 * Koppelingskennis: welke UNS-topic hoort bij welke processtap.
 *
 * `server-only` is hier geen formaliteit. De browser hoort geen enkel topic te
 * kennen; zou hij dat wel doen, dan zit de UNS-structuur in de client en is elke
 * wijziging aan de namespace meteen een frontendwijziging. De BFF vertaalt
 * topics naar stappen, en de browser ziet alleen stappen.
 *
 * Topicconventie (vastgelegd in de v2-README):
 *   DairyWorks/Vla/{Area}/{Equipment}/Status/{tag}
 *   DairyWorks/Vla/Batch/Status/{batch_id|state|active_recipe}
 */

const ROOT = "DairyWorks/Vla";

export function statusTopic(area: string, equipment: string, tag: string): string {
  return `${ROOT}/${area}/${equipment}/Status/${tag}`;
}

export function batchTopic(tag: string): string {
  return `${ROOT}/Batch/Status/${tag}`;
}

export type StepId = "receiving" | "mixing" | "cooking" | "cooling" | "filling";

/**
 * De vijf processtappen als functionele strook. Bewust functioneel geordend en
 * niet als P&ID: het enige experimentele bewijs in de onderzoeksronde (n=18,
 * SAGAT) wijst uit dat een functioneel overzicht bij identieke data
 * significant betere situational awareness geeft dan een schematisch beeld.
 */
export const STEPS: Array<{
  id: StepId;
  name: string;
  area: string;
  equipment: string;
  /** De kernmeting van deze stap, die met band en marker wordt getoond. */
  primary: { tag: string; label: string; unit: string };
  /** Aanvullende waarden, als kerngetal onder de meting. */
  secondary: Array<{ tag: string; label: string; unit: string }>;
}> = [
  {
    id: "receiving",
    name: "Ontvangst",
    area: "Receiving",
    equipment: "receiving-tank-01",
    primary: { tag: "level_L", label: "Niveau", unit: "L" },
    secondary: [
      { tag: "temp_C", label: "Temperatuur", unit: "°C" },
      { tag: "fat_setpoint_pct", label: "Vetgehalte", unit: "%" },
    ],
  },
  {
    id: "mixing",
    name: "Doseren en mengen",
    area: "Mixing",
    equipment: "process-tank-01",
    primary: { tag: "level_L", label: "Tankniveau", unit: "L" },
    secondary: [
      { tag: "agitator_rpm", label: "Roerder", unit: "tpm" },
      { tag: "temp_C", label: "Temperatuur", unit: "°C" },
    ],
  },
  {
    id: "cooking",
    name: "Koken",
    area: "Cook",
    equipment: "cook-unit-01",
    // De kwaliteitsstap. Viscositeit is de meting die de HOLD-beslissing draagt.
    primary: { tag: "viscosity_cP", label: "Viscositeit", unit: "cP" },
    secondary: [
      { tag: "temp_C", label: "Temperatuur", unit: "°C" },
      { tag: "setpoint_C", label: "Setpoint", unit: "°C" },
      { tag: "hold_elapsed_sec", label: "Hold", unit: "s" },
    ],
  },
  {
    id: "cooling",
    name: "Koelen",
    area: "Cooling",
    equipment: "cooler-01",
    primary: { tag: "temp_C", label: "Temperatuur", unit: "°C" },
    secondary: [{ tag: "target_C", label: "Doel", unit: "°C" }],
  },
  {
    id: "filling",
    name: "Vullen",
    area: "Filling",
    equipment: "filler-01",
    primary: { tag: "packs_total", label: "Pakken", unit: "packs" },
    secondary: [
      { tag: "reject_count", label: "Afgekeurd", unit: "" },
      { tag: "pack_size_L", label: "Pakgrootte", unit: "L" },
    ],
  },
];
