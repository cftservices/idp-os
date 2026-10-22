/**
 * Het contract van de batch-engine, zoals de UI het gebruikt.
 *
 * Bron: batch-engine/vla/kpi.py en app.py. Deze typen zijn met de hand
 * bijgehouden en niet gegenereerd; wijkt de engine af, dan is dat een bug in
 * een van beide en moet hij hier zichtbaar worden.
 */

export type KpiStatus = "OK" | "WARNING" | "CRITICAL" | "UNSET";

export type KpiValue = {
  kpi_id: string;
  name: string;
  unit: string;
  iso_ref: string;
  direction: "higher_is_better" | "lower_is_better";
  formula: string;
  /** ISO 22400-2 Table 1 contextvelden. */
  timing: string;
  audience: string;
  production_methodology: string;

  value: number | null;
  status: KpiStatus;
  /** Ver onder de kritieke grens. Onderscheidt 40 % van 89 % zonder een
   *  vierde kleur te introduceren. */
  beyond_critical: boolean;
  target: number | null;
  warn: number | null;
  critical: number | null;

  window: string;
  from: string;
  to: string;

  previous_value: number | null;
  delta: number | null;
  delta_pct: number | null;
  /** null als de delta nul is of ontbreekt. */
  favourable: boolean | null;

  /** Aanwezig bij UNSET: waarom er geen getal is. Nooit een verzonnen nul. */
  reason?: string;
  detail: Record<string, unknown>;
};

export type LossItem = {
  category: string;
  label: string;
  amount: number;
  currency: string;
  /** Alleen causale verliezen tellen in het kopbedrag. */
  causal_or_resultant: "causal" | "resultant";
  caused_by: string | null;
  cause: string | null;
  /** null voor resultanten: die zitten al in hun oorzaak. */
  share_pct: number | null;
  detail: Record<string, unknown>;
};

export type Losses = {
  items: LossItem[];
  /** null als er geen kostenmodel is. Nooit 0. */
  total_causal: number | null;
  currency: string | null;
  /** Welke categorie ontbreekt en waarom. */
  omitted: Array<{ category: string; reason: string }>;
};

export type KpiSummary = {
  window: string;
  from: string;
  to: string;
  timezone: string;
  shift_hours: number;
  compare: boolean;
  kpis: KpiValue[];
  losses: Losses;
  alarm_load?: AlarmLoad;
  generated_at: string;
};

export type UiModel = {
  available: boolean;
  reason?: string;
  recipe?: {
    recipe_id: string | null;
    product_name: string | null;
    /** Batchgrootte in liter: de schaal voor elke niveaumeting. */
    basis_L: number | null;
    pack_size_L: number | null;
    cook_setpoint_C: number | null;
    hold_sec: number | null;
    cool_target_C: number | null;
    viscosity_spec_cP: { min: number; max: number } | null;
  };
  materials?: Array<{ material_id: string; name: string; uom: string; category: string }>;
  kpi_targets?: Array<Record<string, unknown>>;
  product_density_kg_L?: number | null;
  packs_per_pallet?: number | null;
  fill_limits_ml?: { T1: number; T2: number } | null;
  stale_threshold_s?: number | null;
};

/* ------------------------------------------------------------ operatorkant */

export type Alarm = {
  alarm_id: string;
  message: string;
  equipment_id: string | null;
  alarm_type: string | null;
  batch_id: string | null;
  severity: string;
  ts: string;
  acknowledged: boolean;
  priority: "high" | "medium" | "low";
  state: "open" | "acknowledged" | "shelved";
  shelved_reason?: string | null;
  shelved_until?: string | null;
};

export type AlarmLoad = {
  available: boolean;
  reason?: string;
  total?: number;
  per_hour?: number;
  per_10min?: number;
  per_day?: number;
  counts?: { high: number; medium: number; low: number };
  mix_pct?: { high: number | null; medium: number | null; low: number | null };
  mix_targets_pct?: { high: number; medium: number; low: number };
  targets?: {
    per_10min: { acceptable: number; max: number };
    per_hour: { acceptable: number; max: number };
    per_day: { acceptable: number; max: number };
  };
  flood_time_share?: number;
  flood_target?: number;
  stale_over_24h?: number;
  stale_target_per_day?: number;
  top_sources?: Array<{ source: string; count: number; share_pct: number | null }>;
};

export type TagReading = {
  value: number | string | null;
  ts: string | null;
  unit: string | null;
  quality: string;
  retained: boolean;
  age_s: number | null;
};

export type LineLive = {
  available: boolean;
  reason?: string;
  batch?: {
    batch_id: string;
    state: string;
    verdict: string | null;
    recipe_id: string;
    started_at: string | null;
    completed_at: string | null;
    phase_started_at: string | null;
    phase_nominal_sec: number | null;
  };
  doses?: Array<{
    material_id: string;
    qty_target: number;
    qty_actual: number | null;
    uom: string;
    tol_min: number | null;
    tol_max: number | null;
    in_tolerance: boolean | null;
  }>;
  dose_totals?: { target_kg: number; actual_kg: number; pct: number | null };
  filling?: {
    packs_total: number;
    packs_target: number | null;
    packs_progress_pct: number | null;
    packs_rate_per_min: number;
    rate_window_s: number;
    pallets: number | null;
    fill_limits_ml: { nominal: number; T1: number; T2: number } | null;
  };
  quality?: {
    end_viscosity_cP: number | null;
    peak_cook_temp_C: number | null;
    hold_elapsed_sec: number | null;
    spec_cP: { min: number; max: number } | null;
  };
};

/* ------------------------------------------------------ productielijnen */

export type LineId = "L1" | "L2";

export type ProductLine = {
  line_id: LineId;
  name: string;
  hall: string;
  uns_root: string;
  recipes: Array<{
    recipe_id: string;
    product_name: string;
    fg_material_id: string;
    flavour: string;
    /** Licht (0) naar donker: een stap terug vraagt een omstel-CIP. */
    rank: number;
  }>;
  /** rol -> equipment-id, bijvoorbeeld cook -> cook-unit-02. */
  equipment: Record<string, string>;
  autopilot: {
    recipe_id: string | null;
    last_action: string | null;
    changeover_cips: number | null;
  } | null;
};

export type PrepState = "gepland" | "afwegen" | "premix" | "gereed" | "verbruikt";

export type PrepSet = {
  prep_id: string;
  line: LineId;
  recipe_id: string;
  order_id: string | null;
  batch_id: string | null;
  weigh_station: string;
  premix_tank: string;
  state: PrepState;
  components: Array<{ material_id: string; qty_kg: number; uom: string }>;
  created_at: string;
  state_changed_at: string;
  history: Array<{ state: PrepState; ts: string }>;
};

export type PrepStation = {
  station_id: string;
  kind: "weigh" | "premix";
  line: LineId;
  state: "bezig" | "vrij";
  prep_id: string | null;
  recipe_id: string | null;
};
