"use client";

import { useQuery } from "@tanstack/react-query";
import { get } from "@/lib/client";
import type { Refusal } from "@/lib/refusal";

/**
 * De datalaag als bron voor de twin: context per machine, kwaliteit van de
 * signalen, werkorders uit het onderhoudssysteem, betrouwbaarheid en euro's
 * per machine. Alles leest via de eigen BFF; niets praat rechtstreeks met de
 * laag eronder.
 */

export type Quality = "GOOD" | "STALE" | "BAD" | "PARTIAL" | "NO_DATA";

export type LayerNode = {
  id: string;
  kind: "source" | "store" | "consumer" | string;
  name: string;
  role?: string;
  endpoint_label?: string;
  protocol?: string;
  line?: string | null;
  topics?: number | null;
  good?: number | null;
  stale?: number | null;
  bad?: number | null;
  last_age_s?: number | null;
  status: "ok" | "warn" | "down" | "off" | string;
  detail?: string;
};

export type LayerMap = {
  generated_at: string;
  stale_threshold_s: number;
  datalayer: { name: string; convention: string; topics: number; good: number; stale: number; bad: number; roots: string[] };
  sources: LayerNode[];
  services: LayerNode[];
};

export type ContextTopic = {
  tag: string;
  topic: string;
  value: unknown;
  unit?: string | null;
  quality?: string | null;
  age_s?: number | null;
  source: "MES" | "OPC-UA";
  raw: { endpoint?: string; node_id: string } | null;
};

export type WorkOrder = {
  wo_id: string;
  equipment_id: string;
  line: string;
  kind: "breakdown" | "cbm" | string;
  cause: string | null;
  priority: string;
  status: "reported" | "assigned" | "in_progress" | "done" | string;
  reported_at: string;
  started_at: string | null;
  done_at: string | null;
  technician: { id: string; name: string; skill: string } | null;
  repair_s?: number | null;
};

export type EquipmentContext = {
  equipment_id: string;
  line: string;
  isa95_path: Array<{ level: string; id: string; name: string }>;
  topic_prefix: string;
  opcua_endpoint: string;
  topics: ContextTopic[];
  counts: Record<"GOOD" | "STALE" | "BAD", number>;
  quality: Quality;
  dropout_simulated: boolean;
  state: string | null;
  down: { since: string; cause: string; work_order_id: string | null } | null;
  open_cbm_alerts: Array<{ alert_id: string; message?: string }>;
  open_work_orders: WorkOrder[];
};

export type QualityRow = {
  equipment_id: string;
  line: string;
  counts: Record<"GOOD" | "STALE" | "BAD", number>;
  quality: Quality;
  oldest_age_s: number | null;
  dropout_simulated: boolean;
  dropout_remaining_s: number | null;
};

export type ReliabilityRow = {
  equipment_id: string;
  line: string;
  apt_h: number;
  adet_h: number;
  availability: number | null;
  failures: number;
  open_failures: number;
  mtbf_h: number | null;
  mttr_min: number | null;
  downtime_eur: number | null;
  down: EquipmentContext["down"];
};

export type LossItem = { category: string; label: string; amount: number; kind: "causal" | "resultant"; cause?: string };
export type LossRow = { equipment_id: string; line: string; total_causal: number; total_resultant: number; items: LossItem[] };
export type Losses = {
  equipment: LossRow[];
  currency: string | null;
  total_causal: number | null;
  omitted: Array<{ category: string; reason: string }>;
};

export type CauseStep = { step: string; label: string; evidence: string; equipment_id: string };
export type Cause = {
  alarm: { alarm_id: string; equipment_id: string; message?: string; ts?: string };
  chain: CauseStep[];
  verdict: string;
  focus_equipment: string;
  ai: { model: string; text: string | null; error?: string } | null;
};

export type DataOps = {
  build: { git_sha: string | null; built_at: string | null; tests_passed: number | null; pipeline: string };
  rules: Array<{ id: string; rule: string; ok: boolean; detail: string }>;
  ok: boolean;
};

export type Resources = {
  available: boolean;
  reason?: string;
  measured_age_s?: number;
  stale?: boolean;
  host?: { mem_total_mib?: number; mem_used_mib?: number; load1?: number; cpus?: number } | null;
  stack?: { containers: number; cpu_pct: number; mem_mib: number };
  containers: Array<{ name: string; cpu_pct: number | null; mem_mib: number | null; mem_pct: number | null; stack: boolean }>;
};

function poll<T>(key: readonly unknown[], path: string | null, ms: number) {
  return useQuery<T, Refusal, T>({
    queryKey: key,
    queryFn: ({ signal }) => get<T>(path as string, signal),
    enabled: !!path,
    refetchInterval: ms,
    refetchIntervalInBackground: false,
    staleTime: 0,
    retry: 1,
  });
}

export const useLayerMap = (on = true) => poll<LayerMap>(["dl", "map"], on ? "/datalayer/map" : null, 3000);
export const useQualityMap = (on = true) =>
  poll<{ equipment: QualityRow[] }>(["dl", "quality"], on ? "/datalayer/quality" : null, 3000);
export const useEquipmentContext = (eq: string | null) =>
  poll<EquipmentContext>(["dl", "ctx", eq], eq ? `/datalayer/context/${encodeURIComponent(eq)}` : null, 2500);
export const useWorkOrders = (on = true) =>
  poll<{ work_orders: WorkOrder[] }>(["cmms", "wo"], on ? "/cmms/work-orders" : null, 3000);
export const useReliability = (on = true) =>
  poll<{ equipment: ReliabilityRow[]; window_h: number }>(["cmms", "rel"], on ? "/reliability?hours=24" : null, 5000);
export const useEquipmentLosses = (on = true) => poll<Losses>(["losses", "eq"], on ? "/losses/equipment?hours=24" : null, 5000);
export const useCause = (alarmId: string | null) =>
  poll<Cause>(["rca", alarmId], alarmId ? `/alarms/${encodeURIComponent(alarmId)}/cause?ai=1` : null, 60_000);
export const useDataOps = (on = true) => poll<DataOps>(["dataops"], on ? "/dataops" : null, 5000);
export const useResources = (on = true) => poll<Resources>(["resources"], on ? "/system/resources" : null, 5000);

export const WO_WORD: Record<string, string> = {
  reported: "gemeld",
  assigned: "toegewezen",
  in_progress: "in uitvoering",
  done: "gereed",
};

export const QUALITY_WORD: Record<Quality, string> = {
  GOOD: "goed",
  STALE: "verouderd",
  BAD: "slecht",
  PARTIAL: "deels",
  NO_DATA: "geen data",
};
