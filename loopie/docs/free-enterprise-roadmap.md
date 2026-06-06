# Free-First Enterprise Roadmap

Loopie should stay free/local-first wherever possible during development, while still
pointing toward a product that real companies could eventually buy.

## What We Can Potentially Do For Free

These are the areas to prefer before spending money or depending on paid services:

- Local Docker Redis for runtime memory, routing rules, correction artifacts, and Streams.
- Local Docker Postgres for artifact history, run records, and an audit-style ledger.
- Synthetic deterministic fixtures for the demo and first regression suite.
- Deterministic scorers instead of paid LLM judges.
- Mock LLM mode for development, smoke tests, and repeated eval runs.
- Replay cache for fixture/model outputs so repeated runs do not burn tokens.
- Local LangGraph dev server for agent orchestration.
- Local CopilotKit scaffold for the cockpit UI and agent-state wiring.
- Local Redis key inspection for before/after artifact diffs.
- Local CI checks for scorer unit tests and fixed fixture regression tests.
- Hand-written policy, routing, and failure fixtures before real customer data exists.
- Open-source/self-hosted libraries where they do not weaken the demo invariant.

Anything involving OpenAI, W&B/Weave, W&B Inference, CopilotKit hosted features, Redis Cloud,
or other cloud products should be treated as "verify credits/pricing first," not assumed free.

## Token-Budget Guardrails

Loopie should not accidentally spend a lot of tokens during development because of loops,
hot reloads, broad evals, retries, or bugs.

Planned defaults:

```text
LOOPIE_LLM_MODE=mock
LOOPIE_MAX_LLM_CALLS_PER_RUN=8
LOOPIE_MAX_LLM_CALLS_PER_EVAL=40
LOOPIE_MAX_AGENT_TRANSITIONS=6
LOOPIE_MAX_ESTIMATED_COST_USD=0.25
LOOPIE_ENABLE_REPLAY_CACHE=true
LOOPIE_REQUIRE_LIVE_LLM_CONFIRMATION=true
```

Rules:

- Development should default to mock mode.
- Live model calls should require an explicit opt-in.
- Deterministic scorers must never call an LLM.
- Correction objects should be structured and deterministic; an LLM may summarize evidence but
  should not author the patch.
- Every live run should record model, token counts, estimated cost, stop reason, and whether a
  budget guard triggered.
- Eval runs should fail closed if they exceed call, transition, or cost limits.

## Company-Buyable Enterprise Primitives

The hackathon demo can be smaller, but a company-buyable Loopie needs a credible path to:

- RBAC and approval permissions for correction approval.
- Tenant isolation for artifacts, eval runs, traces, and users.
- Immutable audit log for failures, proposals, approvals, artifact writes, and reruns.
- Rollback of artifact versions.
- CI/CD integration for agent behavior changes.
- PII redaction before traces, tickets, events, or prompts enter long-term storage.
- SOC2/security story covering secrets, access control, logs, retention, and change history.
- Real eval suite management with versioned datasets and regression tracking.
- Integration with existing agent frameworks, not only the Loopie demo swarm.
- Production deployment path for backend, cockpit, workers, Redis, Postgres, and Weave logging.
- Alerting when new failures, regressions, or budget overruns appear.
- Human review workflows that feel like GitHub pull requests for agent behavior.

These primitives should strengthen the core invariant:

```text
baseline fails -> trace shows why -> artifact changes ->
human approves -> same eval reruns -> score improves
```

If an enterprise feature does not help prove or govern that chain, it should wait.

## Dataset Strategy

The current project uses synthetic fixtures, not real customer datasets.

That is acceptable for the demo. Synthetic fixed fixtures are better for proving the invariant
because they are deterministic, controllable, repeatable, and safe to show publicly. The current
docs define hand-written support-ticket cases such as `security_001`, `refund_007`, and `loop_001`.

For a real product, companies would connect Loopie to:

- Historical support tickets.
- Resolved incident logs.
- Policy and artifact version history.
- Tool-call traces.
- Internal eval cases.
- Anonymized customer workflows.
- Regression suites built from prior agent failures.

The product should treat real company data as an enterprise integration step, not a prerequisite
for the first demo.
