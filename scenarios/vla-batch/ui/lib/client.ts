"use client";

import { stripApiPrefix, toRefusal, type Refusal, type RefusalAction } from "./refusal";

/**
 * Browser-kant van de datalaag. Praat uitsluitend met de eigen BFF onder
 * /api/v1/* ; nooit rechtstreeks met de engine, nooit met TDengine.
 */

const BASE = "/api/v1";

export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { signal, cache: "no-store" });
  if (!res.ok) throw await toRefusal(res, "GET", path);
  return (await res.json()) as T;
}

/** Eigen BFF-routes buiten de engine-proxy om (modelprojectie, tijdreeksen). */
export async function getUi<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`/api/ui${path}`, { signal, cache: "no-store" });
  if (!res.ok) throw await toRefusal(res, "GET", `/ui${path}`);
  return (await res.json()) as T;
}

export async function post<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) throw await toRefusal(res, "POST", path);
  return (await res.json()) as T;
}

export async function del<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { method: "DELETE" });
  if (!res.ok) throw await toRefusal(res, "DELETE", path);
  return (await res.json()) as T;
}

/**
 * Voert de actie uit die een weigering zelf voorstelt (vandaag: "Run CIP op X").
 * Het pad uit de server bevat al het /api/v1-prefix, dus dat gaat er eerst af.
 */
export async function runRefusalAction(
  action: RefusalAction,
  operatorId: string | null,
): Promise<void> {
  await post(stripApiPrefix(action.path), { operator_id: operatorId });
}

export type { Refusal, RefusalAction };
