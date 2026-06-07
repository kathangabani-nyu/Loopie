import { CATEGORY_TITLES, HERO_CASE_ID, SCORE_ORDER, SWARM_AGENTS, VERDICT } from "./constants";
import type {
  ArtifactVersion,
  BudgetView,
  CorrectionView,
  EvalDeltaView,
  FailureView,
  LoopieState,
  Phase,
  RunReceipt,
  ScorecardCell,
  ScorecardView,
  StreamEvent,
  SwarmView,
  TraceNode,
  VerdictView,
} from "./types";

function hasBaselineRun(state: LoopieState): boolean {
  return Object.values(state.runs || {}).some((r) => r.label === "baseline");
}

function hasPatchedRun(state: LoopieState): boolean {
  return Object.values(state.runs || {}).some((r) => r.label === "patched");
}

export function derivePhase(state: LoopieState): Phase {
  if (state.counterfactual?.results && Object.keys(state.counterfactual.results).length > 0) {
    return "counterfactual";
  }
  if (hasPatchedRun(state)) return "patched";
  if (state.approvalState === "approved") return "approved";
  if (state.proposedCorrections?.length && state.approvalState === "pending") {
    return "proposal";
  }
  if (state.currentFailure || hasBaselineRun(state)) return "baseline";
  return "idle";
}

function scorePassCount(scores?: Record<string, boolean>): number {
  if (!scores) return 0;
  return Object.values(scores).filter(Boolean).length;
}

function scoreTotal(scores?: Record<string, boolean>): number {
  return scores ? Object.keys(scores).length : SCORE_ORDER.length;
}

function truthyString(value: unknown): boolean {
  return String(value).toLowerCase() === "true";
}

function boolScoresToNumeric(scores: Record<string, boolean>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const key of SCORE_ORDER) {
    if (key in scores) out[key] = scores[key] ? 1 : 0;
  }
  for (const [k, v] of Object.entries(scores)) {
    if (!(k in out)) out[k] = v ? 1 : 0;
  }
  return out;
}

function getRunForTrace(state: LoopieState, phase: Phase): RunReceipt | undefined {
  const patched = Object.values(state.runs || {}).find((r) => r.label === "patched");
  if (phase === "patched" || phase === "counterfactual") {
    return patched?.run || state.currentFailure?.run;
  }
  return state.currentFailure?.run;
}

function inferTraceStatus(
  node: string,
  run: RunReceipt | undefined,
  scores: Record<string, boolean> | undefined,
  passed: boolean,
): TraceNode["status"] {
  if (node === "evaluator") return passed ? "ok" : "fail";
  if (!scores) return "ok";

  if (node === "memory_lookup" && scores.memory_version_correct === false) return "root";
  if (node === "policy_check" && scores.required_policy_checked === false) return "warn";
  if (node === "resolution") {
    if (scores.unauthorized_tool_call === false || scores.action_match === false) return "warn";
  }
  if (node === "decision" && scores.action_match === false) return "root";

  const action = run?.action || "";
  if (node === "resolution" && action.includes("approve_refund") && scores.unauthorized_tool_call === false) {
    return "fail";
  }
  return "ok";
}

export function buildTraceView(state: LoopieState, phase: Phase): TraceNode[] {
  const run = getRunForTrace(state, phase);
  const scores =
    phase === "patched" || phase === "counterfactual"
      ? (Object.values(state.runs || {}).find((r) => r.label === "patched")?.scores ??
        state.currentFailure?.scores)
      : state.currentFailure?.scores;

  const passed = scores ? Object.values(scores).every(Boolean) : false;
  const rawTrace = run?.trace || [];
  const narration = run?.narration || {};

  const nodes = rawTrace.filter((t) => t.node);
  if (!nodes.length) return [];

  return nodes.map((step, i) => {
    const node = String(step.node);
    const agentMeta = SWARM_AGENTS[node];
    const label = agentMeta?.name || node.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
    const detail = String(step.narration || narration[node] || step.reason || step.action || "");
    const status = inferTraceStatus(node === "decision" ? "resolution" : node, run, scores, passed);
    const receipt = step.receipt as TraceNode["receipt"];
    return {
      id: `${node}-${i}`,
      label,
      status,
      detail,
      ms: Number(step.duration_ms) || 0,
      receipt,
    };
  });
}

export function buildSwarmView(state: LoopieState, phase: Phase, running: boolean): SwarmView | null {
  const run = getRunForTrace(state, phase);
  if (!run?.trace?.length && !running) return null;

  const scores =
    phase === "patched" || phase === "counterfactual"
      ? (Object.values(state.runs || {}).find((r) => r.label === "patched")?.scores ??
        state.currentFailure?.scores)
      : state.currentFailure?.scores;
  const passed = scores ? Object.values(scores).every(Boolean) : false;
  const byNode = new Map(
    (run?.trace || [])
      .filter((t) => t.node)
      .map((t) => [String(t.node === "decision" ? "resolution" : t.node), t]),
  );

  const agents = Object.entries(SWARM_AGENTS).map(([id, meta]) => {
    const step = byNode.get(id);
    const nodeKey = id;
    return {
      id,
      name: meta.name,
      role: meta.role,
      lastMs: Number(step?.duration_ms) || 0,
      status: inferTraceStatus(nodeKey, run, scores, passed),
      receipt: step?.receipt as TraceNode["receipt"],
    };
  });

  const budget = buildBudgetView(state);
  return {
    agents,
    providerMode: state.preflight?.provider_mode || state.preflight?.llm_mode || "mock",
    budgetUsd: budget.estimated_cost_usd,
    agentCount: Object.keys(SWARM_AGENTS).length,
  };
}

export function buildFailureView(state: LoopieState): FailureView | null {
  const failure = state.currentFailure;
  if (!failure?.case_id) return null;

  const run = failure.run;
  const request =
    (run?.narration?.triage as string | undefined) ||
    `Case ${failure.case_id} / category ${failure.category || "unknown"}`;

  return {
    case_id: failure.case_id,
    category: failure.category || "unknown_failure",
    title: CATEGORY_TITLES[failure.category || ""] || "Deterministic eval failure",
    input: request,
    scores: boolScoresToNumeric(failure.scores || {}),
  };
}

function buildDiffFromProposal(
  type: string | undefined,
  proposal: Record<string, unknown>,
): CorrectionView["diff"] {
  if (type === "routing_rule") {
    return [
      { t: "ctx", l: "routing_rules: [" },
      { t: "add", l: `  ${JSON.stringify(proposal, null, 0)}` },
      { t: "ctx", l: "]" },
    ];
  }
  if (type === "memory_update") {
    return [
      { t: "ctx", l: `memory["${proposal.key}"]` },
      { t: "del", l: '  "Refunds are allowed within 45 days..."' },
      { t: "add", l: `  "${String(proposal.value)}"` },
      { t: "ctx", l: `  version: ${String(proposal.version ?? 2)}` },
    ];
  }
  if (type === "config_update") {
    return [
      { t: "ctx", l: `config["${proposal.key}"]` },
      { t: "del", l: "  6" },
      { t: "add", l: `  ${String(proposal.value)}` },
    ];
  }
  return [{ t: "ctx", l: JSON.stringify(proposal, null, 2) }];
}

function buildBlastRadius(type: string | undefined): CorrectionView["blast"] {
  if (type === "routing_rule") {
    return [
      { node: "policy_check", impact: "direct", note: "guard evaluation" },
      { node: "resolution", impact: "direct", note: "action branch" },
      { node: "memory_lookup", impact: "indirect" },
      { node: "triage", impact: "none" },
      { node: "evaluator", impact: "none" },
    ];
  }
  if (type === "memory_update") {
    return [
      { node: "memory_lookup", impact: "direct" },
      { node: "policy_check", impact: "direct" },
      { node: "resolution", impact: "indirect" },
      { node: "triage", impact: "none" },
      { node: "evaluator", impact: "none" },
    ];
  }
  return [
    { node: "resolution", impact: "direct" },
    { node: "evaluator", impact: "indirect" },
    { node: "triage", impact: "none" },
  ];
}

export function buildCorrectionView(state: LoopieState): CorrectionView | null {
  const raw = state.proposedCorrections?.[0];
  if (!raw?.id) return null;

  const type = raw.type;
  const proposal = raw.proposal || {};
  const target =
    type === "routing_rule"
      ? "routing:rules"
      : type === "memory_update"
        ? String(proposal.key || "memory")
        : type === "config_update"
          ? String(proposal.key || "config")
          : raw.category || "artifact";

  const proof = state.artifactProof;
  const proofDiff =
    proof?.diff?.map((entry) => ({
      t: "ctx" as const,
      l: JSON.stringify(entry),
    })) || [];

  return {
    id: raw.id,
    title: raw.summary || "Structured correction proposal",
    rationale: (raw as { diagnosis?: string }).diagnosis || raw.summary || "",
    confidence: type === "manual_review" ? 0.5 : 0.91,
    risk: type === "routing_rule" || type === "memory_update" ? "low" : "medium",
    target,
    artifact: target,
    diff: proofDiff.length ? proofDiff : buildDiffFromProposal(type, proposal),
    blast: buildBlastRadius(type),
    approved: state.approvalState === "approved",
    beforeHash: proof?.before_hash || undefined,
    afterHash: proof?.after_hash || undefined,
  };
}

export function buildEvalDeltaView(state: LoopieState): EvalDeltaView | null {
  const delta = state.evalDelta;
  if (!delta?.baseline_passed) return null;

  const total = scoreTotal(delta.baseline_passed);
  const baseline = scorePassCount(delta.baseline_passed);
  const patched = delta.patched_passed ? scorePassCount(delta.patched_passed) : null;

  return {
    baseline_passed: baseline,
    patched_passed: patched,
    total,
    improved: Boolean(delta.improved),
  };
}

export function buildArtifactHistory(state: LoopieState, phase: Phase): ArtifactVersion[] {
  const rows = state.artifactHistory || [];
  if (!rows.length) return [];

  return rows.map((row, i) => {
    const versionNum = Number(row.version ?? i + 1);
    const isLatest = i === rows.length - 1;
    const pending = isLatest && phase === "approved";

    let label = String(row.artifact_key || "artifact");
    if (label.startsWith("memory:")) label = `Memory / ${label.slice(7)}`;
    if (label === "routing:rules") label = "Routing rules";

    const value = row.value;
    let note = String(row.source_case || "seed");
    if (typeof value === "object" && value && "value" in (value as object)) {
      note = String((value as { value?: string }).value || note).slice(0, 80);
    }

    const passed =
      phase === "patched" || phase === "counterfactual"
        ? scorePassCount(state.evalDelta?.patched_passed || undefined)
        : scorePassCount(state.currentFailure?.scores);

    return {
      version: `v${versionNum}`,
      label,
      author: String(row.correction_id ? "loopie + you" : row.source_case || "seed"),
      ts: String(row.created_at || "").slice(0, 10) || "-",
      passed: passed || versionNum * 100,
      note,
      pending,
    };
  });
}

export function buildBudgetView(state: LoopieState): BudgetView {
  const b = state.budget || {};
  const llmCalls = Number(b.llm_calls ?? 0);
  const transitions = Number(b.transitions ?? 0);
  const cost = Number(b.estimated_cost_usd ?? 0);

  return {
    budget_usd: 1.0,
    estimated_cost_usd: cost,
    chat_cost_usd: Number(b.chat_cost_usd ?? 0),
    max_chat_cost_usd: Number(b.max_chat_cost_usd ?? 40),
    llm_calls: llmCalls,
    transitions,
    tokens: llmCalls * 3200,
    wall_clock_s: transitions * 1.4,
  };
}

function findRunScores(
  state: LoopieState,
  label: "baseline" | "patched",
): Record<string, boolean> | undefined {
  return Object.values(state.runs || {}).find((r) => r.label === label)?.scores;
}

function heroCaseId(state: LoopieState): string {
  return (
    state.evalDelta?.case_id ||
    state.currentFailure?.case_id ||
    Object.values(state.runs || {}).find((r) => r.label === "baseline")?.case_id ||
    HERO_CASE_ID
  );
}

function baselineScores(state: LoopieState): Record<string, boolean> | undefined {
  return state.evalDelta?.baseline_passed || state.currentFailure?.scores || findRunScores(state, "baseline");
}

function patchedScores(state: LoopieState): Record<string, boolean> | undefined {
  return state.evalDelta?.patched_passed || findRunScores(state, "patched");
}

function scoresForPhase(
  state: LoopieState,
  phase: Phase,
): Record<string, boolean> | undefined {
  if (phase === "patched" || phase === "counterfactual") {
    return patchedScores(state) || baselineScores(state);
  }
  return baselineScores(state);
}

function scoreCells(scores?: Record<string, boolean>): ScorecardCell[] {
  return SCORE_ORDER.map((scorer) => ({
    scorer,
    pass: scores && scorer in scores ? Boolean(scores[scorer]) : null,
  }));
}

export function buildVerdictView(state: LoopieState, phase: Phase): VerdictView {
  const budget = buildBudgetView(state);
  const base = baselineScores(state);
  const patched = patchedScores(state);
  const current = scoresForPhase(state, phase);
  const total = scoreTotal(base || patched || current);
  const baselinePassed = scorePassCount(base);
  const patchedPassed = patched ? scorePassCount(patched) : null;
  const currentPassed = current ? scorePassCount(current) : null;
  const recovered = patchedPassed == null ? null : Math.max(0, patchedPassed - baselinePassed);
  const regressions = state.counterfactual?.newly_failing
    ? state.counterfactual.newly_failing.length
    : null;

  if (phase === "idle") {
    return {
      tone: "idle",
      label: VERDICT.idle.label,
      sub: VERDICT.idle.sub,
      scorersPassed: null,
      scorersTotal: total,
      recovered: null,
      regressions,
      cost: budget.estimated_cost_usd,
      wallClock: budget.wall_clock_s,
    };
  }

  if (phase === "baseline") {
    return {
      tone: "fail",
      label: VERDICT.baseline.label,
      sub: VERDICT.baseline.sub,
      scorersPassed: currentPassed,
      scorersTotal: total,
      recovered: null,
      regressions,
      cost: budget.estimated_cost_usd,
      wallClock: budget.wall_clock_s,
    };
  }

  if (phase === "proposal" || phase === "approved") {
    const copy = phase === "proposal" ? VERDICT.proposal : VERDICT.approved;
    return {
      tone: "stage",
      label: copy.label,
      sub: copy.sub,
      scorersPassed: currentPassed,
      scorersTotal: total,
      recovered: null,
      regressions,
      cost: budget.estimated_cost_usd,
      wallClock: budget.wall_clock_s,
    };
  }

  if (phase === "counterfactual") {
    const clean = state.counterfactual?.no_regression;
    const copy = clean ? VERDICT.counterfactualClean : VERDICT.counterfactualDirty;
    return {
      tone: clean ? "good" : "fail",
      label: copy.label,
      sub: copy.sub,
      scorersPassed: patchedPassed ?? currentPassed,
      scorersTotal: total,
      recovered,
      regressions,
      cost: budget.estimated_cost_usd,
      wallClock: budget.wall_clock_s,
    };
  }

  return {
    tone: "good",
    label: VERDICT.patched.label,
    sub: VERDICT.patched.sub,
    scorersPassed: patchedPassed ?? currentPassed,
    scorersTotal: total,
    recovered,
    regressions,
    cost: budget.estimated_cost_usd,
    wallClock: budget.wall_clock_s,
  };
}

export function buildScorecard(state: LoopieState, phase: Phase): ScorecardView | null {
  const base = baselineScores(state);
  const patched = patchedScores(state);
  const activeHeroScores = phase === "patched" || phase === "counterfactual" ? patched || base : base;
  const id = heroCaseId(state);
  const rows = [];

  if (activeHeroScores) {
    rows.push({
      caseId: id,
      label: phase === "patched" || phase === "counterfactual" ? "Patch recovered" : "Primary case failed",
      isHero: true,
      cells: scoreCells(activeHeroScores),
    });
  }

  if (phase === "counterfactual" && state.counterfactual?.results) {
    for (const [caseId, result] of Object.entries(state.counterfactual.results)) {
      if (caseId === id) continue;
      rows.push({
        caseId,
        label: "Replay clean",
        isHero: false,
        cells: scoreCells(result.scores),
      });
    }
  }

  if (!rows.length) return null;

  return {
    scorers: SCORE_ORDER,
    rows,
    noRegression:
      phase === "counterfactual" && state.counterfactual
        ? Boolean(state.counterfactual.no_regression)
        : null,
  };
}

function eventLevel(event: string, fields: Record<string, unknown>): StreamEvent["level"] {
  if (fields.passed !== undefined) return truthyString(fields.passed) ? "good" : "fail";
  if (event.includes("fail") || event.includes("deny")) return "fail";
  if (event.includes("approve") || event.includes("complete") || event.includes("applied")) {
    return "good";
  }
  if (event.includes("warn") || event.includes("deny")) return "warn";
  return "info";
}

function eventNode(fields: Record<string, unknown>): string {
  if (fields.case_id) return String(fields.case_id).replace(/_\d+$/, "");
  if (fields.correction && typeof fields.correction === "object") return "loopie";
  return "system";
}

function eventMessage(event: string, fields: Record<string, unknown>): string {
  if (fields.event) event = String(fields.event);
  const caseId = fields.case_id ? ` / ${fields.case_id}` : "";
  const action = fields.action ? ` -> ${fields.action}` : "";
  const passed =
    fields.passed !== undefined ? ` / ${truthyString(fields.passed) ? "PASS" : "FAIL"}` : "";

  if (event === "run_completed") return `run completed${caseId}${action}`;
  if (event === "baseline_complete") return `baseline complete${caseId}${passed}`;
  if (event === "patched_complete") return `patched re-run complete${caseId}${passed}`;
  if (event === "seed_complete") return "baseline artifacts seeded";
  if (event.startsWith("applied_")) return `correction applied / ${event.replace("applied_", "")}`;
  if (event === "correction_noop") return "correction already active (no-op)";

  return `${event}${caseId}${passed}`;
}

export function buildEventStream(events: Array<Record<string, unknown>> | undefined): StreamEvent[] {
  if (!events?.length) return [];

  return [...events]
    .sort((a, b) => String(a.id || "").localeCompare(String(b.id || "")))
    .map((raw, seq) => {
      const eventName = String(raw.event || "info");
      const level = eventLevel(eventName, raw);
      return {
        seq,
        level,
        node: eventNode(raw),
        msg: eventMessage(eventName, raw),
      };
    });
}

export function tracePassing(state: LoopieState, phase: Phase): boolean {
  if (phase === "patched" || phase === "counterfactual") {
    const patched = Object.values(state.runs || {}).find((r) => r.label === "patched");
    const scores = patched?.scores;
    return scores ? Object.values(scores).every(Boolean) : false;
  }
  return false;
}
