import { NextRequest, NextResponse } from "next/server";

const LOOPIE_API = process.env.LOOPIE_API_BASE || "http://localhost:8001";

async function proxy(path: string, init?: RequestInit) {
  const res = await fetch(`${LOOPIE_API}${path}`, init);
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ action: string }> }) {
  const { action } = await params;
  const body = await req.json().catch(() => ({}));

  switch (action) {
    case "reset":
      return proxy("/reset", { method: "POST" });
    case "seed":
      return proxy("/seed", { method: "POST" });
    case "baseline":
      return proxy("/run/baseline", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    case "propose":
      return proxy("/corrections/propose", { method: "POST" });
    case "approve":
      return proxy("/corrections/approve", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    case "patched":
      return proxy("/run/patched", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    case "counterfactual":
      return proxy("/counterfactual", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    default:
      return NextResponse.json({ error: "unknown action" }, { status: 404 });
  }
}

export async function GET() {
  return proxy("/state");
}
