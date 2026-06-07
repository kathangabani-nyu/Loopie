import type { CommandDef, Phase } from "./types";

export const HERO_CASE_ID = "security_001";

export const COPY = {
  brandSub: "Agent Reliability Control Plane",
  deterministicMode: "deterministic / $0 pipeline",
  chatModel: "gpt-5.5",
} as const;

export const VERDICT = {
  idle: { label: "Standby", sub: "Awaiting baseline run" },
  baseline: { label: "Baseline Failing", sub: "Deterministic checks failing on the primary case" },
  proposal: { label: "Fix Proposed", sub: "Correction selected for review" },
  approved: { label: "Fix Approved - staged", sub: "Human approval locked" },
  patched: { label: "Recovered", sub: "Patched rerun shows scorer recovery" },
  counterfactualClean: {
    label: "Verified Reliable",
    sub: "Primary case recovered - regression replay stayed clean",
  },
  counterfactualDirty: {
    label: "Regression Detected",
    sub: "Counterfactual replay needs attention",
  },
} as const;

export const SWARM_AGENTS: Record<string, { name: string; role: string }> = {
  triage: { name: "Triage", role: "Classify request and security context" },
  memory_lookup: { name: "Memory Lookup", role: "Read policy version from Redis" },
  policy_check: { name: "Policy Check", role: "Evaluate routing guards" },
  resolution: { name: "Resolution", role: "Authorize tools and select action" },
  evaluator: { name: "Evaluator", role: "Grade deterministic scorers" },
};

export const PHASES: Phase[] = [
  "idle",
  "baseline",
  "proposal",
  "approved",
  "patched",
  "counterfactual",
];

export const PHASE_LABEL: Record<Phase, string> = {
  idle: "Idle",
  baseline: "Baseline",
  proposal: "Fix proposed",
  approved: "Approved",
  patched: "Patched & compared",
  counterfactual: "Counterfactual verified",
};

export const LIVE: Record<Phase, Partial<Record<string, number>>> = {
  idle: {},
  baseline: { stream: 1, case: 1, trace: 1, swarm: 1, delta: 1, scorecard: 1 },
  proposal: { stream: 1, correction: 1 },
  approved: { stream: 1, correction: 1, timemachine: 1 },
  patched: { stream: 1, trace: 1, swarm: 1, delta: 1, budget: 1, scorecard: 1 },
  counterfactual: { stream: 1, swarm: 1, delta: 1, budget: 1, scorecard: 1 },
};

export const COMMANDS: CommandDef[] = [
  {
    id: "baseline",
    label: "Run Baseline",
    from: "idle",
    key: "1",
    action: "baseline",
    body: { case_id: HERO_CASE_ID },
  },
  {
    id: "proposal",
    label: "Propose",
    from: "baseline",
    key: "2",
    action: "propose",
  },
  {
    id: "approved",
    label: "Approve",
    from: "proposal",
    key: "3",
    action: "approve",
  },
  {
    id: "patched",
    label: "Rerun + Compare",
    from: "approved",
    key: "4",
    action: "patched",
    body: { case_id: HERO_CASE_ID },
  },
  {
    id: "counterfactual",
    label: "Counterfactual Replay",
    from: "patched",
    key: "5",
    action: "counterfactual",
    body: { hero_case_id: HERO_CASE_ID },
  },
];

export const SCORE_ORDER = [
  "action_match",
  "required_policy_checked",
  "unauthorized_tool_call",
  "loop_count_under_limit",
  "tool_calls_under_budget",
  "memory_version_correct",
];

export const SCORE_SHORT: Record<string, string> = {
  action_match: "Action",
  required_policy_checked: "Policy",
  unauthorized_tool_call: "Authz",
  loop_count_under_limit: "Loops",
  tool_calls_under_budget: "Calls",
  memory_version_correct: "Memory",
};

export const CATEGORY_TITLES: Record<string, string> = {
  bad_tool_authority: "Unauthorized tool invoked under security flag",
  missing_guard: "Missing routing guard for security-flagged refund",
  stale_memory: "Stale memory served wrong policy window",
  looping_plan: "Planner loop exceeded transition budget",
  conflicting_context: "Conflicting context from memory version",
  prompt_regression: "Required policy check skipped",
  unknown_failure: "Deterministic scorer failure",
  unsafe_escalation: "Unsafe escalation path taken",
};
