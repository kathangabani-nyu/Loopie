import { CopilotRuntime, createCopilotEndpoint, InMemoryAgentRunner } from "@copilotkit/runtime/v2";
import { HttpAgent } from "@ag-ui/client";
import { handle } from "hono/vercel";

import { guardOwnerRequest } from "@/lib/server-security";

const loopieApiBase = process.env.LOOPIE_API_BASE || "http://localhost:8001";
const serviceToken = process.env.LOOPIE_API_TOKEN;

function makeControlAgent() {
  return new HttpAgent({
    agentId: "loopie_control",
    url: `${loopieApiBase}/api/copilotkit/agent/loopie_control`,
    headers: serviceToken ? { Authorization: `Bearer ${serviceToken}` } : {},
  });
}

const runtime = new CopilotRuntime({
  agents: { loopie_control: makeControlAgent() },
  // LangGraph/Postgres owns graph durability. The runner only bridges this
  // authenticated request to the AG-UI agent and holds no product evidence.
  runner: new InMemoryAgentRunner(),
});

const endpoint = handle(createCopilotEndpoint({ runtime, basePath: "/api/copilotkit" }));

async function guarded(request: Request) {
  const mutation = request.method !== "GET";
  const denied = await guardOwnerRequest(request, { mutation, scope: "copilotkit" });
  return denied || endpoint(request);
}

export const GET = guarded;
export const POST = guarded;
export const PATCH = guarded;
export const DELETE = guarded;
