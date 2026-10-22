import { NextRequest } from "next/server";

/**
 * De BFF voor het storingenpaneel.
 *
 * Doet bewust weinig: doorgeven aan de batch-engine en de fout leesbaar
 * teruggeven. De catalogus, de validatie en de transportkeuze per machine zitten
 * in batch-engine/vla/park_control.py, en horen daar ook.
 *
 * Waarom dan toch deze laag? Omdat de browser de engine niet rechtstreeks kan
 * bereiken: die luistert alleen op idp-network. Alle andere schermen lopen ook
 * via /api/ui, dus een uitzondering hier zou betekenen dat je de engine publiek
 * moet maken om een knop te laten werken. Dat is een slechte ruil.
 */

export const dynamic = "force-dynamic";

const ENGINE = process.env.ENGINE_URL ?? "http://vla-batch-engine:8000";

async function proxy(path: string, init?: RequestInit) {
  try {
    const r = await fetch(`${ENGINE}/api/v1${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
      signal: AbortSignal.timeout(6000),
    });
    const text = await r.text();
    let body: unknown;
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
      body = { message: text };
    }
    return Response.json(body, { status: r.status });
  } catch (e) {
    // Engine onbereikbaar. Een nette 503 met uitleg, geen stacktrace: het
    // paneel toont dan "park niet bereikbaar" in plaats van leeg te blijven,
    // en dat scheelt tijdens een demo een hoop raden.
    return Response.json(
      {
        reason: "engine_unreachable",
        message: `batch-engine niet bereikbaar op ${ENGINE}. Draait het park?`,
        detail: e instanceof Error ? e.message : String(e),
      },
      { status: 503 },
    );
  }
}

export async function GET() {
  return proxy("/park/faults");
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({}));

  if (body?.action === "clear-all") {
    return proxy("/park/clear-all", { method: "POST" });
  }

  const machine = String(body?.machine ?? "").trim();
  const fault = String(body?.fault ?? "").trim();
  if (!machine || !fault) {
    return Response.json(
      { reason: "bad_request", message: "machine en fault zijn verplicht" },
      { status: 400 },
    );
  }

  if (body?.action === "clear") {
    return proxy(`/park/${encodeURIComponent(machine)}/fault/${encodeURIComponent(fault)}`, {
      method: "DELETE",
    });
  }

  return proxy(`/park/${encodeURIComponent(machine)}/fault`, {
    method: "POST",
    body: JSON.stringify({ fault, magnitude: Number(body?.magnitude ?? 1) }),
  });
}
