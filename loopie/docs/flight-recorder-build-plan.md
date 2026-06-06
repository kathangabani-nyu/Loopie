# Loopie - Flight Recorder and Change-Control for Agent Swarms

## Context

Loopie is the WeaveHacks 4 multi-agent orchestration entry. The magic is not
"multi-agent" or "eval dashboard"; it is auditable agent self-improvement with
evidence custody.

Loopie proves why a swarm failed, proposes the smallest durable correction,
requires human approval, mutates a real runtime artifact, then replays the same
eval and nearby cases to prove the swarm got better without regressing.

Core invariant:

```text
baseline fails -> Weave trace shows why -> Redis artifact changes ->
human approves correction -> same eval plus neighbors rerun -> score improves
```

Every improvement must map to a real artifact:

- Redis memory diff.
- Redis routing-rule diff.
- Versioned prompt optimization artifact.

Deterministic scorers carry the proof. LLM judges are optional and never
load-bearing.

## Current Base

Extend the running `loopie-copilotkit/` template in place:

- Docker `copilotkit-intelligence` stack: Redis, Postgres, and Intelligence composite.
- LangGraph dev server on `localhost:8123`.
- Next UI on `localhost:3000`.
- CopilotKit v2 agent-state pattern for the cockpit.

Do not claim Weave, Redis, CopilotKit, AG-UI, or live model integration works
until the specific Loopie path has been run locally.

## Locked Product Decisions

- Framework: LangGraph in Python.
- Demo hero: fintech fraud/security flag.
- Core correction: add a routing guard that blocks risky payout/refund tool calls
  when a security flag is present.
- Expansion modes: stale memory and planner loop.
- On-screen awe moments: Artifact Time Machine and Counterfactual Replay.
- Prize emphasis: Weave, Redis, and CopilotKit.

## Store Split

Redis is the live runtime substrate the swarm reads on every run:

- `loopie:memory:*`
- `loopie:routing:*`
- `loopie:config:max_transitions`
- `loopie:prompt:*:active`
- `loopie:events:*` for Redis Streams

Use a dedicated `loopie:` prefix and logical DB 1 to avoid collisions with the
CopilotKit Intelligence stack.

Postgres is the immutable Artifact Time Machine and evidence ledger:

- Schema: `loopie`
- Tables: `artifact_versions`, `corrections`, `eval_runs`, `eval_case_results`,
  `approval_events`, `audit_events`, `budget_events`
- Keep the CopilotKit `cpki` schema untouched.

## Hero Loop

1. `security_001`: payout/refund requested with `security_flag=true`.
2. Baseline swarm ignores the flag and calls `refund_tool`.
3. Deterministic scorers fail `unauthorized_tool_call` and `action_match`.
4. Weave trace shows `triage -> policy_check (no guard) -> resolution -> refund_tool`.
5. Failure classifier maps this to `bad_tool_authority` / `missing_guard`.
6. Correction proposal is structured:

```json
{
  "rule": "security_flag_blocks_refund",
  "condition": "security_flag == true",
  "required_action": "escalate_security"
}
```

7. Human approves in the PR-style cockpit.
8. `apply()` writes the routing rule to Redis, writes a new Postgres artifact
   version, and emits a Redis Stream event.
9. Patched rerun sends `security_001` to `escalate_security`.
10. Counterfactual Replay reruns neighbor cases to prove the guard is surgical:
    `refund_001` still approves, `security_002/003` still escalate or block.
11. Weave Evals Compare shows baseline vs patched.

## Token-Budget Guardrails

Loopie must not accidentally spend a lot of tokens during development because of
agent loops, hot reloads, broad evals, retries, or bugs.

Default environment values:

```text
LOOPIE_LLM_MODE=mock
LOOPIE_REQUIRE_LIVE_LLM_CONFIRMATION=true
LOOPIE_MAX_LLM_CALLS_PER_RUN=8
LOOPIE_MAX_LLM_CALLS_PER_EVAL=40
LOOPIE_MAX_AGENT_TRANSITIONS=6
LOOPIE_MAX_EVAL_CASES_PER_DEV_RUN=6
LOOPIE_MAX_ESTIMATED_COST_USD=0.25
LOOPIE_ENABLE_REPLAY_CACHE=true
```

Implementation requirements:

- Mock mode is the default for development, smoke tests, and repeated evals.
- Live model calls require explicit opt-in.
- Deterministic scorers never call an LLM.
- Correction objects are deterministic; an LLM may summarize evidence but must
  not author the correction object.
- Every run records model, prompt tokens, completion tokens, total tokens,
  estimated cost, stop reason, and budget-guard status.
- Eval runs fail closed if call, transition, case-count, or cost limits are
  exceeded.
- Repeated fixture plus artifact-version combinations should use replay cache
  during development.

## Enterprise Change-Control Primitives

These are required for Loopie to become company-buyable after the demo is
polished:

- RBAC and approval permissions for correction approval.
- Tenant isolation for artifacts, traces, evals, runs, events, and users.
- Immutable audit log for failure, proposal, approval, artifact write, rollback,
  rerun, and promotion events.
- Rollback of artifact versions.
- CI/CD integration for agent behavior changes.
- PII redaction before traces, tickets, prompts, tool payloads, or events enter
  durable storage.
- SOC2/security story for secrets, access control, retention, logging, and
  change history.
- Real eval suite management with dataset versions, owners, expected actions,
  regression tracking, and promotion status.
- Integration boundary for existing agent frameworks, not only the Loopie demo
  swarm.
- Production deployment path for backend, cockpit, workers, Redis, Postgres,
  Weave logging, and secrets.
- Alerting when new failures, regressions, budget overruns, or unsafe tool calls
  appear.
- Human review workflows that feel like GitHub pull requests for agent behavior:
  diff, evidence, blast radius, approval, rollback, and promotion state.

These primitives must strengthen the core invariant. If a feature does not help
prove or govern the correction loop, defer it.

## Backend Plan

Extend `loopie-copilotkit/agent/`.

Add Python dependencies:

- `weave`
- `redis>=5`
- `psycopg[binary]`

Create a new `src/loopie/` package:

- `state.py`: `LoopieState` with ticket, retrieved memory, routing decision,
  tool calls, transitions, action, trace, tenant id, run id, and budget metadata.
- `swarm.py`: LangGraph `StateGraph` with `triage -> memory_lookup ->
  policy_check -> resolution -> evaluator`; every node is a `@weave.op`.
- `tools.py`: simulated `refund_tool`, `escalate_tool`, and `crm_lookup`; no
  real side effects.
- `stores/redis_store.py`: live memory, routing, config, prompt, and Stream
  helpers.
- `stores/ledger.py`: Postgres schema/table setup plus artifact, eval,
  correction, approval, audit, and budget writers.
- `reliability/scorers.py`: deterministic scorers from `docs/scorers.md`.
- `reliability/evals.py`: Weave Evaluation suite for baseline, patched, and
  counterfactual runs.
- `reliability/classifier.py`: failing scorer signature to Failure Genome
  category.
- `reliability/corrections.py`: deterministic propose/apply flow.
- `reliability/replay.py`: Counterfactual Replay and no-regression checks.
- `reliability/budget.py`: LLM call, transition, eval-size, estimated-cost, and
  replay-cache guardrails.
- `security/redaction.py`: first-pass PII redaction for durable traces/events.
- `data/`: synthetic fixtures and expected actions.

Register `loopie_swarm` in `agent/langgraph.json` while keeping `sample_agent`
available during migration.

## Control Agent

Build a CopilotKit-native Loopie control agent whose state contains:

```text
runs
currentFailure
proposedCorrections
artifactHistory
evalDelta
counterfactual
events
budget
approvalState
tenantContext
```

Tools:

- `seed`
- `run_baseline`
- `propose_corrections`
- `approve_correction(id)`
- `rollback_artifact(key, version)`
- `run_patched`
- `counterfactual_replay`
- `get_artifact_history(key)`
- `get_budget_status(run_id)`

Cockpit buttons call these through the CopilotKit agent-state pattern. The live
event feed additionally tails Redis Streams over SSE.

## Frontend Plan

Replace the todo canvas in `loopie-copilotkit/src/` with a cockpit.

Layout:

- Top: Run Baseline, Propose, Approve, Rerun + Compare, Counterfactual Replay.
- Left: live event stream from Redis Streams.
- Center: failed-case card, Weave trace summary, and causality chain.
- Right: correction diff, approve/edit/reject, rollback, and blast radius.
- Bottom: eval delta, Artifact Time Machine, token/cost budget status, and
  no-regression panel.

Components:

- `Cockpit`
- `EventStream`
- `FailureCard`
- `CausalityChain`
- `CorrectionDiff`
- `ApprovalPanel`
- `BlastRadius`
- `EvalDelta`
- `TimeMachine`
- `BudgetMeter`
- `AuditTrail`

## Dataset Strategy

The demo uses synthetic fixtures, not real customer datasets. This is
intentional: synthetic fixed fixtures are deterministic, controllable,
repeatable, and safe to show publicly.

Initial fixture families:

- Fraud/security routing failures.
- Stale policy memory failures.
- Planner-loop failures.
- Unauthorized tool-call failures.
- Conflicting memory/provenance failures.

For real companies, Loopie should connect to:

- Historical support tickets.
- Resolved incident logs.
- Policy and artifact version history.
- Tool-call traces.
- Internal eval cases.
- Anonymized customer workflows.
- Regression suites built from prior agent failures.

Real data is an enterprise integration step, not a prerequisite for the demo.

## Build Order

1. Fixtures, expected actions, seed artifacts, and mock LLM responses.
2. Token-budget guardrails and replay cache.
3. `loopie_swarm` StateGraph with Redis live substrate.
4. Baseline run that fails `security_001` for the right reason.
5. Weave ops and deterministic scorer Evaluation.
6. Postgres `loopie` schema, Artifact Time Machine, audit events, and Redis
   Streams.
7. Propose/approve/apply for the routing guard.
8. Patched rerun and Weave Compare.
9. Counterfactual Replay with neighbor no-regression panel.
10. Enterprise primitives v0: approval permissions, tenant-scoped keys, PII
    redaction, rollback, and audit trail.
11. Extend correction modes to stale memory and planner loop.
12. Cockpit UI and 60-second demo clip.

## Cut Order

If time collapses, cut in this order:

```text
RedisVL/Iris
external framework adapters
SOC2 polish docs
CI/CD integration
advanced RBAC
Causality Ledger UI polish
planner-loop mode
stale-memory mode
counterfactual neighbor breadth
cockpit polish
```

Never cut:

```text
token-budget guardrails
one failing ticket
Weave trace
Redis artifact diff
human approval
same eval rerun
deterministic score improvement
```

## Environment

Required planned variables:

```text
OPENAI_API_KEY=
WANDB_API_KEY=
WANDB_ENTITY=
WEAVE_PROJECT=loopie
REDIS_URL=redis://localhost:6379/1
POSTGRES_URL=postgresql://intelligence:intelligence@localhost:5432/intelligence_app
LOOPIE_LLM_MODE=mock
LOOPIE_REQUIRE_LIVE_LLM_CONFIRMATION=true
LOOPIE_MAX_LLM_CALLS_PER_RUN=8
LOOPIE_MAX_LLM_CALLS_PER_EVAL=40
LOOPIE_MAX_AGENT_TRANSITIONS=6
LOOPIE_MAX_EVAL_CASES_PER_DEV_RUN=6
LOOPIE_MAX_ESTIMATED_COST_USD=0.25
LOOPIE_ENABLE_REPLAY_CACHE=true
REDIS_CLOUD_DATABASE=database-MQ2UM6U3
REDIS_CLOUD_HOST=market-earth-voice-34724.db.redis.io
REDIS_CLOUD_PORT=12392
REDIS_CLOUD_USER=default
REDIS_CLOUD_PASSWORD=
REDIS_CLOUD_TLS=false
```

Do not print or commit `.env` files. Confirm exact available model IDs at build
time before using live calls.

Redis Cloud is optional for sponsor/demo proof. Local Redis DB 1 remains the
default development `REDIS_URL`; switch to Redis Cloud only for an explicit cloud
smoke test or hosted demo.

## Verification

Smoke checks:

- `weave.init` logs a visible trace.
- Weave Evaluation appears under Evals and Compare opens on two runs.
- Redis `set`, `get`, and `xadd` work on DB 1 with `loopie:` prefix.
- Postgres creates the `loopie` schema and artifact ledger tables.
- Mock LLM mode completes with zero live model calls.
- Budget guard fails closed when call, transition, or cost limits are exceeded.
- Scorer unit tests pass.
- PII redaction removes obvious email, phone, and account-token patterns before
  durable storage.

Full-loop acceptance:

1. `seed` writes baseline artifacts with no fraud guard.
2. `run_baseline` fails `security_001` via `unauthorized_tool_call`.
3. Weave shows the refund tool call despite `security_flag=true`.
4. `propose_corrections` creates the structured routing guard.
5. Approval writes Redis rule, Postgres artifact version, audit event, and Stream
   event.
6. `run_patched` sends `security_001` to `escalate_security`.
7. Counterfactual Replay keeps neighbor cases green.
8. Weave Compare shows baseline vs patched improvement.
9. Budget ledger shows token/cost usage and confirms no budget guard was bypassed.
10. Time Machine shows the artifact diff and rollback target.

Done means the full invariant and counterfactual no-regression proof run on
screen with a live Redis key change, Postgres version diff, Weave Compare,
approval trail, and token-budget proof.
