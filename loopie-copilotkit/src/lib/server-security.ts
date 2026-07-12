import { auth } from "@/auth";
import { NextResponse } from "next/server";

type Bucket = { count: number; resetAt: number };

const buckets = new Map<string, Bucket>();
const WINDOW_MS = 60_000;

function clientKey(request: Request, scope: string): string {
  const forwarded = request.headers.get("x-forwarded-for")?.split(",")[0]?.trim();
  return `${scope}:${forwarded || "owner"}`;
}

function takeRequest(key: string, limit: number): { allowed: boolean; retryAfter: number } {
  const now = Date.now();
  const current = buckets.get(key);
  if (!current || current.resetAt <= now) {
    buckets.set(key, { count: 1, resetAt: now + WINDOW_MS });
    return { allowed: true, retryAfter: 0 };
  }
  if (current.count >= limit) {
    return { allowed: false, retryAfter: Math.max(1, Math.ceil((current.resetAt - now) / 1000)) };
  }
  current.count += 1;
  return { allowed: true, retryAfter: 0 };
}

function sameOrigin(request: Request): boolean {
  const origin = request.headers.get("origin");
  if (!origin) return true;
  try {
    return new URL(origin).host === request.headers.get("host");
  } catch {
    return false;
  }
}

export async function guardOwnerRequest(
  request: Request,
  { mutation = false, scope = "loopie" }: { mutation?: boolean; scope?: string } = {},
): Promise<NextResponse | null> {
  const session = await auth();
  if (!session?.user) {
    return NextResponse.json({ error: "authentication required" }, { status: 401 });
  }
  if (mutation && !sameOrigin(request)) {
    return NextResponse.json({ error: "cross-origin mutation rejected" }, { status: 403 });
  }

  const rate = takeRequest(clientKey(request, scope), mutation ? 30 : 120);
  if (!rate.allowed) {
    return NextResponse.json(
      { error: "rate limit exceeded" },
      { status: 429, headers: { "Retry-After": String(rate.retryAfter) } },
    );
  }
  return null;
}

export async function fetchLoopieApi(path: string, init: RequestInit = {}): Promise<Response> {
  const base = process.env.LOOPIE_API_BASE || "http://localhost:8001";
  const token = process.env.LOOPIE_API_TOKEN;
  if (!token) {
    return NextResponse.json(
      { error: "LOOPIE_API_TOKEN is not configured on the web service" },
      { status: 503 },
    );
  }
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(`${base}${path}`, { ...init, headers });
}

export async function jsonFromLoopie(path: string, init: RequestInit = {}): Promise<NextResponse> {
  try {
    const response = await fetchLoopieApi(path, init);
    const data = await response.json().catch(() => ({ error: "Invalid JSON from Loopie API" }));
    return NextResponse.json(data, { status: response.status });
  } catch {
    return NextResponse.json({ error: "Loopie API unavailable" }, { status: 503 });
  }
}
