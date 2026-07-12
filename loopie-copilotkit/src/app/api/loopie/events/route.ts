import { NextRequest, NextResponse } from "next/server";

import { fetchLoopieApi, guardOwnerRequest } from "@/lib/server-security";

export async function GET(request: NextRequest) {
  const denied = await guardOwnerRequest(request, { scope: "loopie-events" });
  if (denied) return denied;
  try {
    const upstream = await fetchLoopieApi("/api/v1/events", {
      headers: {
        Accept: "text/event-stream",
        ...(request.headers.get("last-event-id")
          ? { "Last-Event-ID": request.headers.get("last-event-id") as string }
          : {}),
      },
      cache: "no-store",
    });
    if (!upstream.ok || !upstream.body) {
      return NextResponse.json({ error: "Loopie event stream unavailable" }, { status: upstream.status });
    }
    return new Response(upstream.body, {
      status: 200,
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
      },
    });
  } catch {
    return NextResponse.json({ error: "Loopie API unavailable" }, { status: 503 });
  }
}
