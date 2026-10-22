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

/** Productielijnen: L1 (Hal 1, chocolade) en L2 (Hal 2, de smaken). */
export const LINE_IDS = ["L1", "L2"] as const;
export type LineId = (typeof LINE_IDS)[number];

export function asLineId(v: string | null | undefined): LineId {
  return (LINE_IDS as readonly string[]).includes(v ?? "") ? (v as LineId) : "L1";
}

/** L2 heeft een eigen root, zodat de L1-historian er niets van merkt. */
export function rootOf(line: LineId = "L1"): string {
  return line === "L1" ? ROOT : `${ROOT}-${line}`;
}

/** Equipment-id op een lijn: cook-unit-01 wordt cook-unit-02 op L2. */
export function equipmentOn(equipment: string, line: LineId = "L1"): string {
  return line === "L1" ? equipment : equipment.replace(/-01$/, `-0${line.slice(1)}`);
}

export function statusTopic(area: string, equipment: string, tag: string, line: LineId = "L1"): string {
  return `${rootOf(line)}/${area}/${equipmentOn(equipment, line)}/Status/${tag}`;
}

export function batchTopic(tag: string, line: LineId = "L1"): string {
  return `${rootOf(line)}/Batch/Status/${tag}`;
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
