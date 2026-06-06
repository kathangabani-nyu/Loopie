import { NextResponse } from "next/server";

const LOOPIE_API = process.env.LOOPIE_API_BASE || "http://localhost:8001";

export async function GET() {
  try {
    const res = await fetch(`${LOOPIE_API}/state`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "Loopie API unavailable — start loopie_server on :8001" }, { status: 503 });
  }
}
