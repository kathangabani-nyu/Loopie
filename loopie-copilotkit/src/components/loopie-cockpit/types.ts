export type Phase =
  | "idle"
  | "baseline"
  | "proposal"
  | "approved"
  | "patched"
  | "counterfactual";

export type RunReceipt = {
  action?: string;
  artifact_hash?: string;
  case_id?: string;
  decided_by?: string;
  fallback_used?: boolean;
  decision_schema_version?: string;
  prompt_version?: string;
  trace?: Array<Record<string, unknown>>;
  narration?: Record<string, string>;
  budget?: Record<string, unknown>;
  transitions?: number;
  tool_calls?: Array<{ name?: string }>;
};

export type LoopieState = {
  runs?: Record<string, { label?: string; case_id?: string; scores?: Record<string, boolean>; run?: RunReceipt }>;
  currentFailure?: {
    case_id?: string;
    category?: string;
    scores?: Record<string, boolean>;
    run?: RunReceipt;
  } | null;
  proposedCorrections?: Array<{
    id?: string;
    summary?: string;
    type?: string;
    category?: string;
    case_id?: string;
    proposal?: Record<string, unknown>;
  }>;
  artifactHistory?: Array<Record<string, unknown>>;
  artifactProof?: {
    correction_id?: string;
    before_hash?: string | null;
    after_hash?: string;
    diff?: Array<Record<string, unknown>>;
    artifact_key?: string;
    version?: number;
  } | null;
  evalDelta?: {
    case_id?: string;
    baseline_passed?: Record<string, boolean>;
    patched_passed?: Record<string, boolean> | null;
    improved?: boolean;
  };
  counterfactual?: {
    no_regression?: boolean;
    newly_failing?: string[];
    results?: Record<string, { passed?: boolean; scores?: Record<string, boolean> }>;
  };
  events?: Array<Record<string, unknown>>;
  budget?: Record<string, unknown>;
  approvalState?: string;
  preflight?: {
    ok?: boolean;
    hosted?: boolean;
    redis_reachable?: boolean;
    redis_json?: boolean;
    postgres_reachable?: boolean;
    persistence_mode?: string;
    weave_enabled?: boolean;
    provider_mode?: string;
    llm_mode?: string;
    full_agentic?: boolean;
  };
};

export type StreamEvent = {
  seq: number;
  level: "info" | "warn" | "fail" | "good";
  node: string;
  msg: string;
};

export type TraceReceipt = Record<string, unknown>;

export type TraceNode = {
  id: string;
  label: string;
  status: "ok" | "root" | "warn" | "fail";
  detail: string;
  ms: number;
  receipt?: TraceReceipt;
};

export type SwarmAgentView = {
  id: string;
  name: string;
  role: string;
  lastMs: number;
  status: TraceNode["status"];
  receipt?: TraceReceipt;
};

export type SwarmView = {
  agents: SwarmAgentView[];
  providerMode: string;
  budgetUsd: number;
  agentCount: number;
};

export type DiffLine = { t: "add" | "del" | "ctx"; l: string };

export type CorrectionView = {
  id: string;
  title: string;
  rationale: string;
  decisionBasis: string;
  confidence: number;
  risk: string;
  target: string;
  artifact: string;
  diff: DiffLine[];
  blast: Array<{ node: string; impact: "direct" | "indirect" | "none"; note?: string }>;
  approved: boolean;
  beforeHash?: string;
  afterHash?: string;
};

export type FailureView = {
  case_id: string;
  category: string;
  title: string;
  input: string;
  scores: Record<string, number>;
  failedScorers: string[];
  observedAction?: string;
  expectedAction?: string;
  exactError: string;
};

export type DemoStep = {
  label: string;
  status: "todo" | "active" | "done" | "blocked";
};

export type DemoBriefView = {
  headline: string;
  subhead: string;
  presenterLine: string;
  steps: DemoStep[];
};

export type EvalDeltaView = {
  baseline_passed: number;
  patched_passed: number | null;
  total: number;
  improved: boolean;
};

export type ArtifactVersion = {
  version: string;
  label: string;
  author: string;
  ts: string;
  passed: number;
  note: string;
  pending?: boolean;
};

export type BudgetView = {
  budget_usd: number;
  estimated_cost_usd: number;
  chat_cost_usd: number;
  max_chat_cost_usd: number;
  llm_calls: number;
  transitions: number;
  tokens: number;
  wall_clock_s: number;
};

export type VerdictView = {
  tone: "idle" | "fail" | "stage" | "good";
  label: string;
  sub: string;
  scorersPassed: number | null;
  scorersTotal: number;
  recovered: number | null;
  regressions: number | null;
  cost: number;
  wallClock: number;
};

export type ScorecardCell = {
  scorer: string;
  pass: boolean | null;
};

export type ScorecardRow = {
  caseId: string;
  label: string;
  isHero: boolean;
  cells: ScorecardCell[];
};

export type ScorecardView = {
  scorers: string[];
  rows: ScorecardRow[];
  noRegression: boolean | null;
};

export type CommandDef = {
  id: Phase;
  label: string;
  from: Phase;
  key: string;
  action: string;
  body?: Record<string, unknown>;
};
