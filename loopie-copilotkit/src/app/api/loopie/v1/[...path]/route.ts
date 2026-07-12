import { NextRequest, NextResponse } from "next/server";

import { fetchLoopieApi, guardOwnerRequest } from "@/lib/server-security";

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const mutation = request.method !== "GET";
  const denied = await guardOwnerRequest(request, {
    mutation,
    scope: `loopie-v1-${request.method.toLowerCase()}`,
  });
  if (denied) return denied;

  const { path } = await context.params;
  const query = request.nextUrl.search;
  const headers = new Headers();
  const idempotencyKey = request.headers.get("idempotency-key");
  if (idempotencyKey) headers.set("Idempotency-Key", idempotencyKey);
  if (mutation) headers.set("Content-Type", "application/json");
  const body = mutation ? await request.text() : undefined;
  try {
    const upstream = await fetchLoopieApi(`/api/v1/${path.join("/")}${query}`, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
    });
    const responseHeaders = new Headers({ "Content-Type": "application/json" });
    const location = upstream.headers.get("location");
    if (location) responseHeaders.set("Location", location);
    return new NextResponse(await upstream.text(), {
      status: upstream.status,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json({ error: "Loopie API unavailable" }, { status: 503 });
  }
}

export const GET = proxy;
export const POST = proxy;
