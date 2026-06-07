"use client";

import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { useCallback, useEffect, useRef, useState } from "react";

import { useLoopieCockpit } from "@/hooks/use-loopie-cockpit";

import "./cockpit.css";
import "./components.css";

import {
  buildArtifactHistory,
  buildBudgetView,
  buildCorrectionView,
  buildDemoBriefView,
  buildEvalDeltaView,
  buildEventStream,
  buildFailureView,
  buildScorecard,
  buildSwarmView,
  buildTraceView,
  buildVerdictView,
  buildWeaveProofView,
  derivePhase,
  tracePassing,
} from "./adapters";
import { COMMANDS, COPY, LIVE, normalizeProviderMode, PHASE_LABEL, PHASES, providerModeLabel, SWARM_AGENTS } from "./constants";
import { useRipple } from "./motion";
import {
  CorrectionPanel,
  DemoBrief,
  EventStream,
  FailedCase,
  IdleNote,
  Panel,
  WeaveProofPanel,
} from "./panels";
import type { Phase } from "./types";
import {
  BudgetMeter,
  CausalityTrace,
  EvalDelta,
  Scorecard,
  SwarmRunTelemetry,
  TimeMachine,
  VerdictStrip,
} from "./viz";

async function post(action: string, body: Record<string, unknown> = {}) {
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

export function LoopieCockpit() {
  const { state, error, refresh, runAction, useAgentState, hasRestState, agentRunning } = useLoopieCockpit();
  const [loading, setLoading] = useState(false);
  const [wiping, setWiping] = useState(false);
  const [autopilot, setAutopilot] = useState(false);
  const apRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ripple = useRipple();

  const phase = derivePhase(state);
  const idx = PHASES.indexOf(phase);
  const live = LIVE[phase] || {};

  useEffect(() => {
    if (error) return;
    const iv = setInterval(() => {
      refresh().catch(() => {});
    }, 4000);
    return () => clearInterval(iv);
  }, [refresh, error]);

  const runActionWrapped = useCallback(
    async (action: string, body: Record<string, unknown> = {}) => {
      setLoading(true);
      try {
        await runAction(action, body);
      } catch {
        /* surfaced via hook error */
      } finally {
        setLoading(false);
      }
    },
    [runAction],
  );

  const advance = useCallback(
    (to: Phase) => {
      const cmd = COMMANDS.find((c) => c.id === to);
      if (!cmd) return;
      const body = { ...(cmd.body || {}) };
      if (cmd.action === "approve") {
        body.correction_id = state.proposedCorrections?.[0]?.id;
      }
      void runActionWrapped(cmd.action, body);
    },
    [runActionWrapped, state.proposedCorrections],
  );

  const doReset = useCallback(() => {
    setAutopilot(false);
    setWiping(true);
    void post("reset").catch(() => {});
    setTimeout(() => {
      void refresh().catch(() => {});
    }, 360);
    setTimeout(() => {
      setWiping(false);
      refresh().catch(() => {});
    }, 820);
  }, [refresh]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT") return;
      if (e.key === "r" || e.key === "R") doReset();
      else if (e.key === " ") {
        e.preventDefault();
        setAutopilot((a) => !a);
      } else {
        const c = COMMANDS.find((c) => c.key === e.key);
        if (c && c.from === phase && !loading) advance(c.id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [phase, advance, doReset, loading]);

  useEffect(() => {
    if (!autopilot || loading) {
      if (apRef.current) clearTimeout(apRef.current);
      return;
    }
    const seq: Phase[] = ["baseline", "proposal", "approved", "patched", "counterfactual"];
    if (phase === "counterfactual") {
      setAutopilot(false);
      return;
    }
    const target: Phase = phase === "idle" ? "baseline" : seq[seq.indexOf(phase) + 1];
    const delay = phase === "idle" ? 600 : 2600;
    apRef.current = setTimeout(() => advance(target), delay);
    return () => {
      if (apRef.current) clearTimeout(apRef.current);
    };
  }, [autopilot, phase, advance, loading]);

  const events = buildEventStream(state.events);
  const failure = buildFailureView(state);
  const trace = buildTraceView(state, phase);
  const correction = buildCorrectionView(state);
  const evalDelta = buildEvalDeltaView(state);
  const artifactHistory = buildArtifactHistory(state, phase);
  const budget = buildBudgetView(state);
  const verdict = buildVerdictView(state, phase);
  const scorecard = buildScorecard(state, phase);
  const swarm = buildSwarmView(state, phase, loading || agentRunning);
  const traceIsPassing = tracePassing(state, phase);
  const demoBrief = buildDemoBriefView(state, phase);
  const weaveProof = buildWeaveProofView(state);

  const swarmRunning = loading || agentRunning;
  const swarmTraced =
    Boolean(swarm?.agents.some((a) => a.lastMs > 0)) ||
    Boolean(weaveProof?.baselineUrl || weaveProof?.patchedUrl);
  const swarmState = swarmRunning ? "running" : swarmTraced ? "traced" : "configured";
  const swarmDot =
    swarmState === "traced" ? "#3fb950" : swarmState === "running" ? "#58a6ff" : "#484f58";
  const swarmTooltip = `LangGraph swarm: ${Object.keys(SWARM_AGENTS).join(" → ")}`;

  const redisReachable = state.preflight?.redis_reachable;
  const redisState =
    redisReachable === undefined ? "unknown" : redisReachable ? "live" : "offline";
  const redisLabel =
    redisState === "live" && state.preflight?.redis_json ? "live +JSON" : redisState;
  const redisDot =
    redisState === "live" ? "#3fb950" : redisState === "offline" ? "#f85149" : "#484f58";

  const weaveEnabled = state.preflight?.weave_enabled;
  const weaveHasEval = Boolean(weaveProof?.baselineUrl || weaveProof?.patchedUrl);
  const weaveState =
    weaveHasEval
      ? "eval linked"
      : weaveEnabled === undefined
        ? "unknown"
        : weaveEnabled
          ? "enabled"
          : "off";
  const weaveDot =
    weaveState === "eval linked"
      ? "#3fb950"
      : weaveState === "enabled"
        ? "#58a6ff"
        : "#484f58";
  const weaveTracesUrl = state.preflight?.weave_project_url || null;

  const providerMode = normalizeProviderMode(
    swarm?.providerMode || state.preflight?.provider_mode || state.preflight?.llm_mode,
  );
  const modeLabel = providerModeLabel(providerMode);

  return (
    <div className="loopie-cockpit-root">
      <div className="stage">
        <div className="aurora a1" />
        <div className="aurora a2" />
        <div className="grain" />
        <div className="vignette" />
      </div>

      <AnimatePresence>
        {wiping && (
          <motion.div
            className="wipe"
            key="wipe"
            initial={{ scaleX: 0, opacity: 0.95 }}
            animate={{ scaleX: 1, opacity: 0.95 }}
            exit={{ scaleX: 0, opacity: 0, transformOrigin: "right" }}
            transition={{ duration: 0.42, ease: [0.65, 0, 0.35, 1] }}
          />
        )}
      </AnimatePresence>

      <MotionConfig reducedMotion="user">
        <div className="cockpit">
          <header className="topbar">
            <div className="topbar-row">
              <div className="brand">
                <div className="mark" />
                <div>
                  <h1>LOOPIE</h1>
                  <div className="sub">{COPY.brandSub}</div>
                </div>
              </div>
              <div className="grow" />
              <div className="phase-pill">
                <span
                  className="dot"
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: 99,
                    display: "inline-block",
                    background: phase === "idle" ? "#484f58" : "#58a6ff",
                    boxShadow: "none",
                    transition: "background .5s",
                  }}
                />
                phase <b>{PHASE_LABEL[phase]}</b>
              </div>
              <div
                className="phase-pill"
                title="CopilotKit agent-state binding stays mounted alongside this cockpit"
              >
                <span
                  className="dot"
                  style={{
                    width: 7,
                    height: 7,
                    borderRadius: 99,
                    background: error ? "#f85149" : "#58a6ff",
                    boxShadow: "none",
                    display: "inline-block",
                  }}
                />
                {hasRestState ? "api" : useAgentState ? "agent" : "api"}{" "}
                <b>{error ? "offline" : "live"}</b>
              </div>
              <div className="stack-cluster">
                <div className="phase-pill" title={swarmTooltip}>
                  <span
                    className="dot"
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: 99,
                      background: swarmDot,
                      boxShadow: "none",
                      display: "inline-block",
                    }}
                  />
                  swarm <b>{swarmState}</b>
                </div>
                <div className="phase-pill" title="Redis artifact store reachability">
                  <span
                    className="dot"
                    style={{
                      width: 7,
                      height: 7,
                      borderRadius: 99,
                      background: redisDot,
                      boxShadow: "none",
                      display: "inline-block",
                    }}
                  />
                  redis <b>{redisLabel}</b>
                </div>
                {weaveTracesUrl ? (
                  <a
                    className="phase-pill phase-pill-link"
                    href={weaveTracesUrl}
                    target="_blank"
                    rel="noreferrer"
                    title="Open the live W&B Weave traces dashboard"
                  >
                    <span
                      className="dot"
                      style={{
                        width: 7,
                        height: 7,
                        borderRadius: 99,
                        background: weaveDot,
                        boxShadow: "none",
                        display: "inline-block",
                      }}
                    />
                    weave <b>{weaveState}</b>
                  </a>
                ) : (
                  <div className="phase-pill" title="W&B Weave tracing and eval links">
                    <span
                      className="dot"
                      style={{
                        width: 7,
                        height: 7,
                        borderRadius: 99,
                        background: weaveDot,
                        boxShadow: "none",
                        display: "inline-block",
                      }}
                    />
                    weave <b>{weaveState}</b>
                  </div>
                )}
              </div>
              <div className="phase-pill" title={`Provider mode: ${providerMode}`}>
                mode <b>{modeLabel}</b>
              </div>
            </div>

            {error && <div className="error-banner">{error}</div>}

            <div className="cmdbar">
              <button
                type="button"
                className="cmd ghost"
                onClick={(e) => {
                  ripple(e);
                  doReset();
                }}
                disabled={loading}
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M3 12a9 9 0 1 0 3-6.7M3 4v4h4" />
                </svg>
                Reset
                <span className="kbd">R</span>
              </button>
              <div className="div" />
              {COMMANDS.map((c) => {
                const isNext = c.from === phase;
                const done = PHASES.indexOf(c.id) <= idx;
                return (
                  <button
                    key={c.id}
                    type="button"
                    className={`cmd${isNext ? " armed" : ""}`}
                    disabled={!isNext || loading || agentRunning}
                    onClick={(e) => {
                      ripple(e);
                      advance(c.id);
                    }}
                  >
                    {done && !isNext && (
                      <svg
                        width="12"
                        height="12"
                        viewBox="0 0 24 24"
                        fill="none"
                        stroke="var(--teal)"
                        strokeWidth="3"
                      >
                        <path d="M5 13l4 4L19 7" />
                      </svg>
                    )}
                    {c.label}
                    {isNext && <span className="kbd">{c.key}</span>}
                  </button>
                );
              })}
              <div className="div" />
              <button
                type="button"
                className={`cmd${autopilot ? " armed" : ""}`}
                onClick={(e) => {
                  ripple(e);
                  setAutopilot((a) => !a);
                }}
                title="Play the whole narrative hands-free (Space)"
                disabled={!!error}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                  {autopilot ? (
                    <path d="M6 5h4v14H6zM14 5h4v14h-4z" />
                  ) : (
                    <path d="M7 5l12 7-12 7z" />
                  )}
                </svg>
                {autopilot ? "Pause" : "Autopilot"}
              </button>
            </div>
          </header>

          <DemoBrief brief={demoBrief} />

          <VerdictStrip verdict={verdict} />

          <div className="grid">
            <Panel
              title="Reliability Scorecard"
              subtitle="deterministic pass/fail proof"
              area="scorecard"
              live={!!live.scorecard}
              scroll
            >
              {scorecard ? (
                <Scorecard data={scorecard} phase={phase} />
              ) : (
                <IdleNote label="no scorecard yet" />
              )}
            </Panel>

            <Panel
              title="Audit Event Stream"
              subtitle="Redis and run events"
              area="stream"
              live={!!live.stream}
              tag={`${events.length} events`}
            >
              <EventStream events={events} />
            </Panel>

            <Panel title="Failing Case" subtitle="the refund ticket under test" area="case" live={!!live.case}>
              <FailedCase failure={failure} />
            </Panel>

            <Panel
              title="Causality Trace"
              subtitle="per-agent execution path with real latency"
              area="trace"
              live={!!live.trace}
              tag={
                trace.length
                  ? traceIsPassing
                    ? "passing"
                    : "failing"
                  : null
              }
              scroll
            >
              {trace.length ? (
                <CausalityTrace
                  key={traceIsPassing ? "patched" : "baseline"}
                  trace={trace}
                  runKey={phase}
                />
              ) : (
                <IdleNote label="no trace yet" />
              )}
            </Panel>

            <Panel
              title="Proposed Correction"
              subtitle="reviewable Redis artifact change"
              area="correction"
              live={!!live.correction}
            >
              <CorrectionPanel
                correction={correction}
                canApprove={phase === "proposal"}
                loading={loading}
                onApprove={() => advance("approved")}
              />
            </Panel>

            <Panel
              title="Score Delta"
              subtitle="same eval, two artifact states + Weave compare"
              area="delta"
              live={!!live.delta || !!weaveProof}
              scroll={false}
            >
              {evalDelta ? (
                <>
                  <EvalDelta data={evalDelta} />
                  {weaveProof ? <WeaveProofPanel proof={weaveProof} /> : null}
                </>
              ) : weaveProof ? (
                <WeaveProofPanel proof={weaveProof} />
              ) : (
                <IdleNote label="no eval yet" />
              )}
            </Panel>

            <Panel
              title="Artifact Time Machine"
              subtitle="approved runtime versions"
              area="timemachine"
              live={!!live.timemachine}
              scroll={false}
            >
              <TimeMachine history={artifactHistory} />
            </Panel>

            <Panel
              title="Swarm Run Telemetry"
              area="swarm"
              live={!!live.swarm}
              scroll
              tag={swarm ? `${swarm.agentCount} agents` : null}
            >
              {swarm ? (
                <SwarmRunTelemetry data={swarm} runKey={phase} running={loading || agentRunning} />
              ) : (
                <IdleNote label="no swarm telemetry yet" />
              )}
            </Panel>

            <Panel title="Budget Meter" subtitle="test-first cost guardrail" area="budget" live={!!live.budget} scroll={false}>
              <BudgetMeter budget={budget} />
            </Panel>
          </div>
        </div>
      </MotionConfig>
    </div>
  );
}

export { LoopieCockpit as Cockpit };
