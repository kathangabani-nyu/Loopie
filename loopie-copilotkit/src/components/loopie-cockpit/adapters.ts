import { CATEGORY_TITLES, HERO_CASE_ID, normalizeProviderMode, PHASES, SCORE_ORDER, SWARM_AGENTS, VERDICT } from "./constants";
import type {
  ArtifactVersion,
  BudgetView,
  CorrectionView,
  DemoBriefView,
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
  WeaveProofView,
} from "./types";

type RawCorrection = NonNullable<LoopieState["proposedCorrections"]>[number];
type RunEntry = NonNullable<LoopieState["runs"]>[string];

function runEntries(state: LoopieState): RunEntry[] {
  return Object.values(state.runs || {});
}

function findLatestRunEntry(state: LoopieState, label: "baseline" | "patched"): RunEntry | undefined {
  return [...runEntries(state)].reverse().find((r) => r.label === label);
}

function scoresFailed(scores?: Record<string, boolean>): boolean {
  return Boolean(scores && Object.values(scores).some((pass) => !pass));
}

function hasBaselineRun(state: LoopieState): boolean {
  return runEntries(state).some((r) => r.label === "baseline");
}

function hasPatchedRun(state: LoopieState): boolean {
  return runEntries(state).some((r) => r.label === "patched");
}

function hasFailedBaselineRun(state: LoopieState): boolean {
  return Boolean(state.currentFailure?.case_id) || scoresFailed(findLatestRunEntry(state, "baseline")?.scores);
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
  if (hasFailedBaselineRun(state)) return "baseline";
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

function failedScorers(scores?: Record<string, boolean>): string[] {
  return Object.entries(scores || {})
    .filter(([, pass]) => !pass)
    .map(([name]) => name);
}

function expectedActionForCategory(category: string | undefined): string | undefined {
  if (category === "bad_tool_authority" || category === "missing_guard") return "escalate_security";
  if (category === "stale_memory") return "deny_refund_offer_credit";
  if (category === "looping_plan") return "escalate_after_loop";
  if (category === "vat_reclassification") return "escalate_billing_review";
  return undefined;
}

function inferFailureCategory(caseId: string | undefined, scores?: Record<string, boolean>, run?: RunReceipt): string {
  if (scores?.memory_version_correct === false) return "stale_memory";
  if (scores?.loop_count_under_limit === false) return "looping_plan";
  if (scores?.required_policy_checked === false || scores?.unauthorized_tool_call === false) {
    return "missing_guard";
  }
  if ((caseId || "").includes("security") || (run?.action || "").includes("refund")) return "missing_guard";
  return "unknown_failure";
}

function failedBaselineAsFailure(state: LoopieState): NonNullable<LoopieState["currentFailure"]> | null {
  const baseline = findLatestRunEntry(state, "baseline");
  if (!baseline?.case_id || !scoresFailed(baseline.scores)) return null;
  const category = inferFailureCategory(baseline.case_id, baseline.scores, baseline.run);
  return {
    case_id: baseline.case_id,
    category,
    scores: baseline.scores,
    run: baseline.run,
  };
}

function exactErrorForFailure(failure: LoopieState["currentFailure"]): string {
  if (!failure) return "No failing eval has been run yet.";
  const category = failure.category || "unknown_failure";
  const action = failure.run?.action ? ` The swarm chose ${failure.run.action}.` : "";
  const scores = failedScorers(failure.scores);
  const scorerText = scores.length ? ` Failed scorer${scores.length > 1 ? "s" : ""}: ${scores.join(", ")}.` : "";

  if (category === "bad_tool_authority" || category === "missing_guard") {
    return `security_flag was true, but the refund path was still allowed.${action}${scorerText}`;
  }
  if (category === "stale_memory") {
    return `The swarm read an outdated refund-window memory and made the wrong refund decision.${action}${scorerText}`;
  }
  if (category === "looping_plan") {
    return `The planner exceeded the transition budget instead of reaching a stable refund decision.${action}${scorerText}`;
  }
  if (category === "vat_reclassification") {
    return `The refund path missed the VAT reverse-charge billing rule.${action}${scorerText}`;
  }
  return `The deterministic eval failed.${action}${scorerText}`;
}

function whyFailedForCategory(failure: LoopieState["currentFailure"]): string {
  if (!failure) return "No baseline run has executed yet.";
  const category = failure.category || "unknown_failure";
  const action = failure.run?.action || "unknown";

  if (category === "bad_tool_authority" || category === "missing_guard") {
    return `The resolution node authorized \`${action}\` while \`security_flag\` was still asserted — the policy guard never gated the tool surface, so a privileged action executed under an unverified identity.`;
  }
  if (category === "stale_memory") {
    return `Memory lookup returned a superseded Redis artifact (stale \`refund_window\` version). Policy check evaluated against the wrong facts, so resolution committed to \`${action}\` without the current guardrails.`;
  }
  if (category === "looping_plan") {
    return `The LangGraph planner cycled through triage → memory_lookup → policy_check without converging on a terminal action. Transition budget exhausted before escalation, leaving the eval in an unstable partial state.`;
  }
  if (category === "vat_reclassification") {
    return `Billing context required VAT reverse-charge handling, but the routing rules lacked a \`vat_reclassification\` guard. Resolution took the default refund path (\`${action}\`) instead of escalating to billing review.`;
  }
  return `One or more deterministic scorers rejected the swarm output (\`${action}\`). The trace shows where policy, memory, or tool authority diverged from the expected contract.`;
}

function correctionDecisionBasis(raw: RawCorrection): string {
  const category = raw?.category || "unknown_failure";
  const failing = Array.isArray((raw as { failing_scorers?: unknown }).failing_scorers)
    ? ((raw as { failing_scorers?: string[] }).failing_scorers || [])
    : [];
  const scorerText = failing.length ? ` using ${failing.join(", ")}` : "";
  const mode = String((raw as { diagnosis_mode?: string }).diagnosis_mode || "deterministic");

  if (raw?.type === "routing_rule") {
    return `${mode} classifier mapped ${category}${scorerText} to a Redis routing guard.`;
  }
  if (raw?.type === "memory_update") {
    return `${mode} classifier mapped ${category}${scorerText} to a Redis memory update.`;
  }
  if (raw?.type === "config_update") {
    return `${mode} classifier mapped ${category}${scorerText} to a versioned config artifact.`;
  }
  return `${mode} classifier mapped ${category}${scorerText} to manual review.`;
}

function getRunForTrace(state: LoopieState, phase: Phase): RunReceipt | undefined {
  const patched = findLatestRunEntry(state, "patched");
  const baseline = findLatestRunEntry(state, "baseline");
  if (phase === "patched" || phase === "counterfactual") {
    return patched?.run || state.currentFailure?.run || baseline?.run;
  }
  return state.currentFailure?.run || baseline?.run;
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
      ? (findLatestRunEntry(state, "patched")?.scores ?? baselineScores(state))
      : baselineScores(state);

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
      ? (findLatestRunEntry(state, "patched")?.scores ?? baselineScores(state))
      : baselineScores(state);
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
    providerMode: normalizeProviderMode(
      state.preflight?.provider_mode || state.preflight?.llm_mode || "test",
    ),
    budgetUsd: budget.estimated_run_cost_usd,
    agentCount: Object.keys(SWARM_AGENTS).length,
  };
}

export function buildFailureView(state: LoopieState, phase: Phase): FailureView | null {
  const failure = state.currentFailure?.case_id ? state.currentFailure : failedBaselineAsFailure(state);
  if (!failure?.case_id) return null;

  const patched = patchedScores(state);
  const resolved =
    (phase === "patched" || phase === "counterfactual") &&
    Boolean(patched && Object.values(patched).every(Boolean));

  const run = failure.run;
  const request =
    (run?.narration?.triage as string | undefined) ||
    `Case ${failure.case_id} / category ${failure.category || "unknown"}`;

  return {
    case_id: failure.case_id,
    category: failure.category || "unknown_failure",
    title: resolved ? "Baseline failure (resolved)" : CATEGORY_TITLES[failure.category || ""] || "Deterministic eval failure",
    input: request,
    scores: boolScoresToNumeric(failure.scores || {}),
    failedScorers: failedScorers(failure.scores),
    observedAction: run?.action,
    expectedAction: expectedActionForCategory(failure.category),
    exactError: exactErrorForFailure(failure),
    whyFailed: whyFailedForCategory(failure),
    resolved,
  };
}

function oneLineJson(value: unknown): string {
  return JSON.stringify(value, null, 0);
}

function prettyJsonLines(value: unknown, prefix = "  "): string[] {
  return JSON.stringify(value, null, 2)
    .split("\n")
    .map((line) => `${prefix}${line}`);
}

function formatProofDiff(entries: Array<Record<string, unknown>>): CorrectionView["diff"] {
  const lines: CorrectionView["diff"] = [];
  for (const entry of entries) {
    const path = String(entry.path || ".");
    lines.push({ t: "ctx", l: path === "." ? "artifact value" : `path: ${path}` });

    if ("before" in entry) {
      const before = entry.before;
      if (Array.isArray(before) && before.length === 0) {
        lines.push({ t: "del", l: "  [] (no guard present)" });
      } else {
        lines.push({ t: "del", l: `  ${oneLineJson(before)}` });
      }
    }

    if ("after" in entry) {
      const after = entry.after;
      if (Array.isArray(after)) {
        after.forEach((item) => lines.push({ t: "add", l: `    ${oneLineJson(item)}` }));
      } else {
        lines.push({ t: "add", l: `  ${oneLineJson(after)}` });
      }
    }

    if (Array.isArray(entry.changes)) {
      lines.push(...formatProofDiff(entry.changes as Array<Record<string, unknown>>));
    }
  }
  return lines.length ? lines : [{ t: "ctx", l: "No material artifact diff recorded." }];
}

function buildDiffFromProposal(
  type: string | undefined,
  proposal: Record<string, unknown>,
): CorrectionView["diff"] {
  if (type === "routing_rule") {
    return [
      { t: "ctx", l: "Redis artifact: routing:rules" },
      { t: "del", l: "  (no security_flag_blocks_refund guard)" },
      ...prettyJsonLines(proposal, "    ").map((l) => ({ t: "add" as const, l })),
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
  const proofDiff = proof?.diff?.length ? formatProofDiff(proof.diff) : [];

  return {
    id: raw.id,
    title: raw.summary || "Structured correction proposal",
    rationale: (raw as { diagnosis?: string }).diagnosis || raw.summary || "",
    decisionBasis: correctionDecisionBasis(raw),
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

export function buildWeaveProofView(state: LoopieState): WeaveProofView | null {
  const baseline = state.weaveEvalBaseline;
  const patched = state.weaveEvalPatched;
  const enabled = Boolean(state.preflight?.weave_enabled);
  if (!enabled && !baseline && !patched) return null;

  return {
    enabled,
    tracesUrl: state.preflight?.weave_project_url || null,
    baselineUrl: baseline?.weave_project_url || null,
    patchedUrl: patched?.weave_project_url || null,
    baselineLabel: baseline?.weave_evaluation_name || baseline?.label || "loopie_baseline_v1",
    patchedLabel: patched?.weave_evaluation_name || patched?.label || "loopie_patched_v2",
    baselineError: baseline?.weave_eval_error || null,
    patchedError: patched?.weave_eval_error || null,
    manualFallback: false,
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

function collectRuns(state: LoopieState): RunReceipt[] {
  const runs: RunReceipt[] = [];
  for (const entry of Object.values(state.runs || {})) {
    if (entry?.run) runs.push(entry.run);
  }
  for (const entry of Object.values(state.counterfactual?.results || {})) {
    if (entry?.run) runs.push(entry.run as RunReceipt);
  }
  return runs;
}

function sumTraceDurationMs(state: LoopieState): number {
  let total = 0;
  for (const run of collectRuns(state)) {
    for (const step of run.trace || []) {
      total += Number(step.duration_ms) || 0;
    }
  }
  return total;
}

function fallbackWallClockSeconds(state: LoopieState): number {
  const runs = collectRuns(state);
  if (!runs.length) return 0;
  const latestWallMs = Math.max(...runs.map((run) => Number(run.wall_clock_ms) || 0));
  if (latestWallMs > 0) return latestWallMs / 1000;
  return sumTraceDurationMs(state) / 1000;
}

export function buildBudgetView(state: LoopieState): BudgetView {
  const b = state.budget || {};
  const llmCalls = Number(b.llm_calls ?? 0);
  const transitions = Number(b.transitions ?? 0);
  const estimatedRunCost = Number(b.estimated_run_cost_usd ?? b.estimated_cost_usd ?? 0);
  const actualModelCost = Number(b.actual_model_cost_usd ?? 0);
  let wallClockS = Number(b.wall_clock_s ?? 0);
  if (wallClockS <= 0) {
    wallClockS = fallbackWallClockSeconds(state);
  }
  const nodeTimeS = Number(b.node_time_s ?? sumTraceDurationMs(state) / 1000);

  return {
    budget_usd: 1.0,
    estimated_run_cost_usd: estimatedRunCost,
    actual_model_cost_usd: actualModelCost,
    estimate_basis: String(b.estimate_basis || "wall_clock_ms + trace nodes + eval cases"),
    estimated_cost_usd: estimatedRunCost,
    chat_cost_usd: Number(b.chat_cost_usd ?? 0),
    max_chat_cost_usd: Number(b.max_chat_cost_usd ?? 40),
    llm_calls: llmCalls,
    transitions,
    tokens: llmCalls * 3200,
    wall_clock_s: wallClockS,
    node_time_s: nodeTimeS,
  };
}

function findRunScores(
  state: LoopieState,
  label: "baseline" | "patched",
): Record<string, boolean> | undefined {
  return findLatestRunEntry(state, label)?.scores;
}

function heroCaseId(state: LoopieState): string {
  return (
    state.evalDelta?.case_id ||
    state.currentFailure?.case_id ||
    findLatestRunEntry(state, "baseline")?.case_id ||
    HERO_CASE_ID
  );
}

function baselineScores(state: LoopieState): Record<string, boolean> | undefined {
  return state.currentFailure?.scores ?? findRunScores(state, "baseline") ?? state.evalDelta?.baseline_passed;
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
      cost: budget.estimated_run_cost_usd,
      actualModelCost: budget.actual_model_cost_usd,
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
      cost: budget.estimated_run_cost_usd,
      actualModelCost: budget.actual_model_cost_usd,
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
      cost: budget.estimated_run_cost_usd,
      actualModelCost: budget.actual_model_cost_usd,
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
      cost: budget.estimated_run_cost_usd,
      actualModelCost: budget.actual_model_cost_usd,
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
    cost: budget.estimated_run_cost_usd,
    actualModelCost: budget.actual_model_cost_usd,
    wallClock: budget.wall_clock_s,
  };
}

export function buildScorecard(state: LoopieState, phase: Phase): ScorecardView | null {
  if (phase === "idle") return null;

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

export function buildDemoBriefView(state: LoopieState, phase: Phase): DemoBriefView {
  const failure = buildFailureView(state, phase);
  const correction = buildCorrectionView(state);
  const improved = Boolean(state.evalDelta?.improved);
  const approved = state.approvalState === "approved";
  const hasPatched = hasPatchedRun(state);

  const stepState = (step: Phase): "todo" | "active" | "done" | "blocked" => {
    const order = PHASES.indexOf(step);
    const current = PHASES.indexOf(phase);
    if (step === "baseline" && failure) return phase === "baseline" ? "active" : "done";
    if (step === "proposal" && correction) return phase === "proposal" ? "active" : "done";
    if (step === "approved" && approved) return phase === "approved" ? "active" : "done";
    if (step === "patched" && hasPatched) return improved ? "done" : "blocked";
    if (order === current) return "active";
    return order < current ? "done" : "todo";
  };

  const presenterLine =
    phase === "idle"
      ? "Start with one seeded failure: a security-flagged refund ticket should escalate, but the swarm is missing a routing guard."
      : phase === "baseline" && failure
        ? failure.exactError
        : phase === "proposal" && correction
          ? `${correction.decisionBasis} The proposed change is review-only until approved.`
          : phase === "approved" && correction
            ? `Human approval staged ${correction.artifact}; the same eval still needs to rerun before we claim improvement.`
            : hasPatched && improved
              ? "The same eval recovered after the approved Redis artifact changed."
              : "Loopie is replaying neighboring cases to prove the fix did not over-block refunds.";

  return {
    headline: "Refund Ticket Swarm Reliability Demo",
    subhead:
      "Loopie watches a support-ticket swarm fail, shows the trace, stages a Redis correction, waits for human approval, then reruns the same eval.",
    presenterLine,
    steps: [
      { label: "Baseline fails", status: stepState("baseline") },
      { label: "Trace explains why", status: phase === "baseline" ? "active" : stepState("proposal") },
      { label: "Redis fix approved", status: stepState("approved") },
      { label: "Same eval improves", status: stepState("patched") },
    ],
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
