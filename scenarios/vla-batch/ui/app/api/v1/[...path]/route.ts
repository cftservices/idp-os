import { NextRequest } from "next/server";

/**
 * BFF-proxy naar de batch-engine. Vervangt de `location /api/` uit
 * dashboard/nginx.conf.
 *
 * Waarom een route handler en geen next.config-rewrite: dit is de plek waar de
 * engine-URL en straks de TDengine-credentials blijven, en waar een storing
 * genormaliseerd wordt. Een rewrite omzeilt beide.
 *
 * De engine is niet extern bereikbaar en draagt geen eigen auth: hij hangt op
 * idp-network en deze proxy is de enige weg naartoe, achter dezelfde
 * Traefik-basicauth als de rest van de demo.
 */

export const dynamic = "force-dynamic";

const ENGINE = process.env.ENGINE_URL ?? "http://vla-batch-engine:8000";

// nginx had proxy_read_timeout 60s. Die marge is er voor de PDF-renders van
// report/period en report/{batch_id}; een korter budget breekt die stil.
const TIMEOUT_MS = 60_000;

async function forward(req: NextRequest, path: string[]) {
  const suffix = path.map(encodeURIComponent).join("/");
  const target = `${ENGINE}/api/v1/${suffix}${req.nextUrl.search}`;

  const init: RequestInit = {
    method: req.method,
    headers: { accept: req.headers.get("accept") ?? "application/json" },
    signal: AbortSignal.timeout(TIMEOUT_MS),
  };

  if (req.method !== "GET" && req.method !== "HEAD") {
    init.body = await req.text();
    (init.headers as Record<string, string>)["content-type"] =
      req.headers.get("content-type") ?? "application/json";
  }

  try {
    const upstream = await fetch(target, init);
    // Statuscode en body ongewijzigd doorgeven: de client moet de
    // gestructureerde weigeringen (CipRequired, ScanRejected) intact zien,
    // inclusief het detail-object. Hier iets van maken zou dat platslaan.
    const headers = new Headers();
    const ct = upstream.headers.get("content-type");
    if (ct) headers.set("content-type", ct);
    const cd = upstream.headers.get("content-disposition");
    if (cd) headers.set("content-disposition", cd);
    headers.set("cache-control", "no-store");

    return new Response(upstream.body, { status: upstream.status, headers });
  } catch (e) {
    const timedOut = e instanceof Error && e.name === "TimeoutError";
    return Response.json(
      {
        detail: timedOut
          ? `de engine antwoordde niet binnen ${TIMEOUT_MS / 1000}s op ${req.method} /${suffix}`
          : `de engine is niet bereikbaar (${req.method} /${suffix})`,
      },
      { status: timedOut ? 504 : 502, headers: { "cache-control": "no-store" } },
    );
  }
}

type Ctx = { params: Promise<{ path: string[] }> };

export async function GET(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}

export async function POST(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}

export async function DELETE(req: NextRequest, ctx: Ctx) {
  return forward(req, (await ctx.params).path);
}
