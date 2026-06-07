"use client";

import { z } from "zod";
import { useCallback, useEffect, useMemo, useState } from "react";

import { CorrectionPanel } from "@/components/loopie-cockpit/panels";
import { buildCorrectionView } from "@/components/loopie-cockpit/adapters";
import type { LoopieState } from "@/components/loopie-cockpit/types";

import { useAgent, useHumanInTheLoop } from "@copilotkit/react-core/v2";

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
  return data;
}

export function useLoopieCockpit(options: UseLoopieCockpitOptions = {}) {
  const { preferAgentState = true } = options;
  const { agent } = useAgent();
  const [restState, setRestState] = useState<LoopieState>({});
  const [error, setError] = useState<string | null>(null);
  const [useAgentState, setUseAgentState] = useState(preferAgentState);

  const agentState = (agent.state || {}) as LoopieState;
  const hasAgentState = Boolean(agentState.runs || agentState.currentFailure || agentState.proposedCorrections?.length);

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
  }, []);

  useEffect(() => {
    if (useAgentState && hasAgentState) return;
    refresh().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Failed to load Loopie state");
    });
  }, [refresh, useAgentState, hasAgentState]);

  useEffect(() => {
    if (!useAgentState || hasAgentState) {
      setError(null);
    }
  }, [useAgentState, hasAgentState]);

  useEffect(() => {
    if (!preferAgentState) return;
    if (hasAgentState) setUseAgentState(true);
  }, [preferAgentState, hasAgentState]);

  const state = useMemo<LoopieState>(() => {
    if (useAgentState && hasAgentState) return agentState;
    return restState;
  }, [useAgentState, hasAgentState, agentState, restState]);

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
        if (!useAgentState || !hasAgentState) {
          await refresh();
        }
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Action failed");
        throw err;
      }
    },
    [refresh, state.proposedCorrections, useAgentState, hasAgentState],
  );

  return {
    state,
    error,
    refresh,
    runAction,
    useAgentState,
    setUseAgentState,
    agentRunning: agent.isRunning,
  };
}
