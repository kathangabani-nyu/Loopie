"use client";

import { z } from "zod";
import { useCallback, useEffect, useMemo, useState } from "react";

import { CorrectionPanel } from "@/components/loopie-cockpit/panels";
import { buildCorrectionView } from "@/components/loopie-cockpit/adapters";
import type { LoopieState } from "@/components/loopie-cockpit/types";

import { useAgent, useFrontendTool, useHumanInTheLoop } from "@copilotkit/react-core/v2";

type UseLoopieCockpitOptions = {
  /** When true, REST polling is disabled and agent.state is the source of truth. */
  preferAgentState?: boolean;
};

async function postLoopie(action: string, body: Record<string, unknown> = {}) {
  const res = await fetch(`/api/loopie/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ error: "Invalid JSON response" }));
  if (!res.ok) {
    throw new Error(typeof data.error === "string" ? data.error : `Request failed (${res.status})`);
  }
  if (typeof data.error === "string") {
    throw new Error(data.error);
  }
  return data;
}

export function useLoopieCockpit(options: UseLoopieCockpitOptions = {}) {
  const { preferAgentState = false } = options;
  const { agent } = useAgent();
  const [restState, setRestState] = useState<LoopieState>({});
  const [error, setError] = useState<string | null>(null);
  const [useAgentState, setUseAgentState] = useState(preferAgentState);

  const agentState = (agent.state || {}) as LoopieState;
  const hasAgentState = Boolean(
    agentState.runs ||
      agentState.currentFailure ||
      agentState.proposedCorrections?.length ||
      agentState.artifactProof ||
      agentState.evalDelta?.case_id,
  );
  const hasRestState = Boolean(
    restState.runs ||
      restState.currentFailure ||
      restState.proposedCorrections?.length ||
      restState.events?.length ||
      restState.preflight,
  );

  const refresh = useCallback(async () => {
    const res = await fetch("/api/loopie/state");
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(
        typeof data.error === "string"
          ? data.error
          : "Loopie API unavailable - run `npm run dev:loopie` on port 8001.",
      );
    }
    setRestState(await res.json());
    setError(null);
  }, []);

  useEffect(() => {
    refresh().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load Loopie state");
    });
  }, [refresh]);

  useEffect(() => {
    if (!preferAgentState) return;
    if (hasAgentState && !hasRestState) setUseAgentState(true);
  }, [preferAgentState, hasAgentState, hasRestState]);

  const state = useMemo<LoopieState>(() => {
    // Cockpit buttons mutate loopie-api; REST export_state is authoritative for the proof path.
    // CopilotKit agent sync may only stream a subset (e.g. events) and must not shadow REST.
    if (hasRestState) {
      return {
        ...agentState,
        ...restState,
        budget: { ...agentState.budget, ...restState.budget },
        preflight: restState.preflight || agentState.preflight,
      };
    }
    if (useAgentState && hasAgentState) return agentState;
    return restState;
  }, [useAgentState, hasAgentState, hasRestState, agentState, restState]);

  useHumanInTheLoop({
    name: "approveLoopieCorrection",
    description:
      "Human approval interrupt for a proposed Loopie correction. Shows artifact diff and blast radius before apply.",
    parameters: z.object({
      correction_id: z.string(),
      summary: z.string().optional(),
    }),
    render: ({ respond, status, args }) => {
      const correction = buildCorrectionView({
        ...state,
        proposedCorrections: state.proposedCorrections?.length
          ? state.proposedCorrections
          : [{ id: args.correction_id, summary: args.summary || "Pending correction" }],
        artifactProof: state.artifactProof,
      });
      const proof = state.artifactProof;

      return (
        <div className="hitl-shell">
          <div className="hitl-title">Approve correction?</div>
          {proof && (
            <div className="hitl-proof">
              <div>
                <b>before</b> {proof.before_hash}
              </div>
              <div>
                <b>after</b> {proof.after_hash}
              </div>
              <pre className="hitl-diff">{JSON.stringify(proof.diff, null, 2)}</pre>
            </div>
          )}
          <CorrectionPanel
            correction={correction}
            canApprove={status === "executing"}
            loading={status === "inProgress"}
            onApprove={() => respond?.({ approved: true, correction_id: args.correction_id })}
          />
          <div className="hitl-actions">
            <button type="button" onClick={() => respond?.({ approved: true, correction_id: args.correction_id })}>
              Approve
            </button>
            <button type="button" onClick={() => respond?.({ approved: false })}>
              Reject
            </button>
          </div>
        </div>
      );
    },
  });

  const runAction = useCallback(
    async (action: string, body: Record<string, unknown> = {}) => {
      setError(null);
      try {
        if (action === "approve" && !body.correction_id) {
          const correctionId = state.proposedCorrections?.[0]?.id;
          if (!correctionId) throw new Error("No correction to approve");
          body = { correction_id: correctionId };
        }
        await postLoopie(action, body);
        setUseAgentState(false);
        await refresh();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Action failed");
        throw err;
      }
    },
    [refresh, state.proposedCorrections],
  );

  const cockpitTool = useCallback(
    async (action: string, body: Record<string, unknown> = {}) => {
      await runAction(action, body);
      return { ok: true, action };
    },
    [runAction],
  );

  useFrontendTool({
    name: "runBaseline",
    description: "Run the deterministic baseline eval on the primary case.",
    parameters: z.object({ case_id: z.string().optional() }),
    handler: async ({ case_id }) => cockpitTool("baseline", { case_id: case_id || "security_001" }),
  });

  useFrontendTool({
    name: "proposeCorrection",
    description: "Propose a structured correction for the current failure.",
    parameters: z.object({}),
    handler: async () => cockpitTool("propose"),
  });

  useFrontendTool({
    name: "approveCorrection",
    description: "Approve the pending Loopie correction.",
    parameters: z.object({ correction_id: z.string().optional() }),
    handler: async ({ correction_id }) =>
      cockpitTool("approve", { correction_id: correction_id || state.proposedCorrections?.[0]?.id }),
  });

  useFrontendTool({
    name: "rerunCompare",
    description: "Rerun the patched eval and compare scores.",
    parameters: z.object({ case_id: z.string().optional() }),
    handler: async ({ case_id }) => cockpitTool("patched", { case_id: case_id || "security_001" }),
  });

  useFrontendTool({
    name: "counterfactualReplay",
    description: "Run counterfactual replay to verify no regression.",
    parameters: z.object({ hero_case_id: z.string().optional() }),
    handler: async ({ hero_case_id }) =>
      cockpitTool("counterfactual", { hero_case_id: hero_case_id || "security_001" }),
  });

  useFrontendTool({
    name: "resetLoopieDemo",
    description: "Reset the Loopie demo to a clean seeded state.",
    parameters: z.object({}),
    handler: async () => cockpitTool("reset"),
  });

  return {
    state,
    error,
    refresh,
    runAction,
    useAgentState,
    hasRestState,
    setUseAgentState,
    agentRunning: agent.isRunning,
  };
}
