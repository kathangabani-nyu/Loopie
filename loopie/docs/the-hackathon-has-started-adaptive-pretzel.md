# Loopie — Flight Recorder & Change-Control for Agent Swarms (WeaveHacks 4)

## Context

Loopie is the WeaveHacks 4 (Multi-Agent Orchestration) entry. The magic is **not** "multi-agent"
or "eval dashboard" — it is **auditable agent self-improvement with evidence custody**: Loopie
proves *why* a swarm failed, proposes the smallest durable correction, requires human approval,
mutates a **real runtime artifact**, then replays the same eval (and its neighbors) to prove the
swarm got better *without regressing*.

The invariant every doc + `AGENTS.md` enforces (anti-fake contract):

```text
baseline fails -> Weave trace shows WHY -> a Redis artifact changes ->
human approves the correction -> the SAME eval (+ neighbors) reruns -> score provably improves
```

Every improvement must map to a real artifact (Redis memory diff / routing-rule diff / versioned
prompt). Deterministic scorers carry the proof; LLM judges are optional/secondary. Never claim an
integration works until run locally.

### What is ALREADY running (do not rebuild — extend)

The `loopie-copilotkit/` app is the **CopilotKit A2UI + LangGraph template** (todo/flights demo),
fully live:

- Docker `copilotkit-intelligence` stack healthy: **Postgres** (5432, user/pw `intelligence`,
  db `intelligence_app`, `cpki` schema), **Redis** (6379), **intelligence composite** (app-api
  4201 + realtime-gateway 4401).
- **LangGraph dev** on :8123 (`agent/`, graph `sample_agent`), **Next UI** on :3000.
- CopilotKit **v2 agent-state pattern** (`useAgent()` ↔ Python `AgentState`) — the binding the
  cockpit will reuse. Deps: langchain 1.2.15, langgraph 1.1.6, copilotkit 0.1.93, Next 16.

### Locked decisions (confirmed with user)

- Framework: **LangGraph (Python)**. Build: **extend the running template in-place**.
- **Demo hero: fintech fraud-flag** — a swarm about to take a risky payout/refund action despite a
  fraud/security flag. The other two failure modes (stale memory, planner loop) still ship as the
  "and it generalizes" beat.
- Must-have "awe" upgrades on-screen: **Artifact Time Machine** + **Counterfactual Replay**.
  (Causality Ledger + Promotion Gate are stretch; the versioned-artifact store underpins both.)
- Prize emphasis: **Weave + Redis + CopilotKit**.

### Store split = the evidence-custody thesis

- **Redis** = the *live runtime substrate* the swarm reads on every run: `loopie:memory:*`,
  `loopie:routing:*`, `loopie:config:max_transitions`, `loopie:prompt:*:active`; plus **Streams**
  `loopie:events:*` for the live cockpit feed. Use a dedicated `loopie:` prefix (and logical DB 1)
  so we never collide with the intelligence stack's Redis usage.
- **Postgres** = immutable **Artifact Time Machine** + ledger: schema `loopie` in `intelligence_app`,
  tables `artifact_versions` (Time Machine), `corrections`, `eval_runs`, `eval_case_results`.
  Append-only version history is the "evidence custody" judges can scrub. (`cpki` schema untouched.)

---

## Two agent layers + deterministic oracle (the harness story)

Loopie is a **multi-agent system supervising a multi-agent system** — two genuine agentic layers.
This is deliberately designed to score on "multi-agent harness sophistication," which is our weakest
judging axis, while keeping the proof reproducible.

- **Worker swarm (real LLM agents):** heterogeneous agents — `triage`, `memory_lookup`,
  `policy_check`, `resolution`, `evaluator` — that genuinely reason, route, hand off, and **decide**
  proposed actions. They run at `temperature=0` + fixed seed for stability. Sophistication lives here.
- **Loopie supervisory loop (second agent layer):** `failure_classifier` → `correction_proposer`
  → `eval/replay_orchestrator`. This layer observes the worker swarm, diagnoses, and drives the
  correction lifecycle.

### Determinism as an ORACLE, not a cage

We keep a deterministic reference (`decide.py`) but it does **not** replace the agents:

> The LLM agents decide; their decisions are *grounded in the retrieved Redis artifact*, so at
> `temperature=0` a stale fact reliably drives the wrong action and the corrected fact reliably
> drives the right one. `decide.py` is the **golden oracle** used for mock/CI/proof-stability, and
> we assert `live-LLM-swarm == oracle` on the canonical cases (differential test).

Why this is the right reconciliation:

- The failure is still *provably caused by the artifact* (stale memory / missing guard), not by model
  randomness — the improvement stays defensible, not cherry-picked.
- Dev/CI run on the oracle (mock) at ~zero tokens; the live demo shows real agentic behavior.
- The `live == oracle` differential check is itself an impressive engineering point judges rarely see.

## Token-Safe Development Mode (build BEFORE any swarm/eval wiring)

Implement `src/loopie/llm.py` as the **only** path to a model. No node calls `ChatOpenAI` directly.

1. **`LOOPIE_LLM_MODE=mock` is the DEFAULT.** Real calls require explicit `LOOPIE_LLM_MODE=live`.
   `mock` grades via the `decide.py` oracle + returns canned narration keyed by node+fixture; zero
   network, zero cost. `live` runs the real LLM agents and is asserted equal to the oracle.
2. **Hard budgets, fail-closed:** `MAX_LLM_CALLS_PER_RUN=8`, `MAX_LLM_CALLS_PER_EVAL=40`,
   `MAX_AGENT_TRANSITIONS=6`. On breach → stop, mark `budget_guard_triggered`, record the partial run.
   (`MAX_AGENT_TRANSITIONS` is the same guard that stages/fixes the planner-loop failure mode.)
3. **No LLM inside scorers** — scorers are pure functions of the run record. Enforced by review.
4. **No LLM correction authorship** — `corrections.propose()` returns a structured object built by
   deterministic rules; the LLM may only summarize evidence for display.
5. **Replay cache** (`stores/llm_cache.py`, keyed by `model + node + fixture_id + artifact_version`):
   same inputs reuse the prior completion. Dev iteration costs nothing after the first live pass.
6. **Cost ledger** — every run writes a `loopie.cost_ledger` row: `model, prompt_tokens,
   completion_tokens, total_tokens, estimated_cost, stop_reason, mode`. Surfaced in the cockpit.
7. **Dry-run command** — `run_suite(mode="mock")` executes the full pipeline (baseline → propose →
   apply → patched → counterfactual) at **zero API cost** for CI and demo rehearsal.

A live API key is used only for (a) one cache-priming pass and (b) the final recorded demo. All
day-to-day building, evals, and hot-reloads run in `mock`.

## Datasets

Demo uses **synthetic, deterministic fixtures** (the 16 cases in `docs/data-fixtures.md`) — the
correct choice: controllable, reproducible, and they prove the invariant without noise. To avoid a
hand-waved look, shape a few fixtures after a **public** support/policy dataset (e.g. Bitext customer-
support intents) — public-data provenance only, no real PII. Real-data connectors (historical
tickets, incident logs, tool-call traces, policy version history, regression suites mined from past
agent failures) are **product roadmap, not demo scope** — never claimed as working in the pitch.

## Industry-grade honesty (what we will and will NOT claim)

The *thesis* — "change control for agent behavior with evidence custody" — is genuinely buyable by
platform / support-automation / fintech-risk / healthcare-ops / compliance teams. The *demo* is a
vertical slice. Stating this gap openly is a credibility win with judges.

**Falls out of the architecture for free (will show):** immutable audit log (Postgres append-only
`artifact_versions` + `cost_ledger`), artifact **rollback** (Time Machine is version-addressable —
add a `rollback(key, version)` that re-points Redis and records a new ledger row), PR-style human
review (the cockpit), deterministic regression proof (Counterfactual Replay).

**Roadmap, explicitly NOT in demo (will NOT claim):** RBAC / approval permissions, tenant isolation,
PII redaction, SOC2 story, CI/CD gate that blocks agent deploys, framework-agnostic adapters (wrap an
arbitrary external LangGraph/CrewAI/OpenAI-SDK agent, not only our demo swarm), live failure
alerting, production deployment path. List these on a "Roadmap" slide as where the wedge expands.

## Judging-criteria self-assessment (brutal) + what we do about it

| Criterion | Honest current standing | Plan response |
|---|---|---|
| **Impact / Utility** | Strong (~8). Real pain, named buyers. | Lead every pitch sentence with the *buyer outcome*, not architecture. Show the audit log + rollback as enterprise-real. |
| **Technical Demo** | Risky (~6). Many live parts = many failure points. | Deferred — demo-robustness hardening (preflight/reset/fallback) will be addressed later, not in this plan. |
| **Creativity** | Good not safe (~7). "Self-improving loop" is a named theme here. | Foreground the actual novelty: **approved, audited, regression-proven** correction (Counterfactual Replay + Time Machine + evidence custody). Never call it "self-improving" unqualified. |
| **Multi-agent sophistication** | Weakest (~5). A deterministic graph reads as not-really-multi-agent. | **Two genuine agent layers** (worker swarm + Loopie supervisory loop), real heterogeneous agents/handoffs, `live==oracle` differential test, and the stretch framework-agnostic adapter. |

### Stretch that directly lifts criterion 4 (do only after the hero loop is solid)

Framework-agnostic adapter: wrap the template's existing `sample_agent` (or any external LangGraph
agent) so Loopie supervises a swarm it did not author — proving Loopie is a harness *about* agent
systems, not just its own toy. This is the single highest-leverage credibility upgrade if time allows.

---

## Hero loop (fintech fraud), concretely

1. `security_001`: payout/refund requested **with `security_flag=true`**. Baseline swarm ignores
   the flag, calls `refund_tool` → scorers `unauthorized_tool_call` + `action_match` FAIL.
2. Weave trace shows the exact chain: `triage → policy_check (no guard) → resolution → refund_tool`.
3. `failure_classifier` → genome category `bad_tool_authority` / `missing_guard`.
4. `corrections.propose()` → **structured** routing-rule correction
   `{rule: security_flag_blocks_refund, condition: security_flag==true, required_action: escalate_security}`.
5. Human approves in the PR-style cockpit (diff + evidence + **blast radius** = neighbor list).
6. `apply()` writes the rule to **Redis** (live) + a new **Postgres** `artifact_versions` row
   (Time Machine) + `XADD` to `loopie:events:corrections`.
7. **Counterfactual Replay**: rerun `security_001` (now → `escalate_security`) **plus neighbors**
   (`refund_001` legit refund must STILL approve; `security_002/003` still handled) → prove the
   guard is surgical, not over-blocking. Shown as a Weave Evaluation over the suite + a no-regression
   panel.
8. Weave **Evals → Compare**: baseline vs patched.

Over-blocking is the obvious risk of any safety guard, so the counterfactual no-regression proof is
the demo's "undeniable" moment.

---

## Backend — extend `loopie-copilotkit/agent/`

Add deps to `agent/pyproject.toml`: `weave`, `redis>=5`, `psycopg[binary]`. New package `src/loopie/`:

- **`llm.py`** — the ONLY model gateway. Honors `LOOPIE_LLM_MODE` (default `mock`), enforces the
  per-run/per-eval call budgets, `temperature=0`+seed in live mode, and records `cost_ledger` rows.
  Nodes import from here; none call `ChatOpenAI` directly.
- **`stores/llm_cache.py`** — replay cache keyed by `model+node+fixture_id+artifact_version`.
- **`decide.py`** — the **deterministic golden oracle**: pure functions mapping (ticket, retrieved
  artifacts) → expected graded `action`. Used by mock mode + CI; `live` LLM-agent output is asserted
  equal to it on canonical cases (differential test). It is a reference, not a replacement for agents.
- **`state.py`** — `LoopieState` TypedDict: `ticket`, `retrieved_memory`, `routing_decision`,
  `tool_calls[]`, `transitions`, `action`, `narration`, `trace[]` (the causality chain).
- **`swarm.py`** — LangGraph `StateGraph`: `triage → memory_lookup → policy_check → resolution →
  evaluator`, conditional back-edge guarded by `loopie:config:max_transitions` (stages + fixes
  failure mode 3). Every node is a `@weave.op`. Register in `agent/langgraph.json` as graph
  `loopie_swarm` (keep `sample_agent` too).
- **`tools.py`** — simulated `refund_tool`, `escalate_tool`, `crm_lookup`. No real side effects.
- **`stores/redis_store.py`** — live substrate get/set for memory/routing/config/prompt +
  `XADD`/read helpers for `loopie:events:{swarm,corrections,evals}`.
- **`stores/ledger.py`** — psycopg; `CREATE SCHEMA IF NOT EXISTS loopie` + idempotent table
  creation on startup; writers/readers for `artifact_versions`, `cost_ledger`, eval/correction tables.
  Includes `rollback(key, version)`.
- **`reliability/scorers.py`** — deterministic scorers from `docs/scorers.md`.
- **`reliability/evals.py`** — Weave `Evaluation` over the dataset.
- **`reliability/classifier.py`** — failing-scorer signature → Failure Genome category.
- **`reliability/corrections.py`** — `propose(failure)` / `apply(correction)`.
- **`reliability/replay.py`** — Counterfactual Replay.
- **`data/`** — fixtures and seed artifacts.

**Control surface for the cockpit (CopilotKit-native):** a Loopie **control agent** (LangGraph)
whose `AgentState` holds runs, failures, corrections, artifact history, eval delta, counterfactual,
events, budget, and approval state. Tools: `seed`, `run_baseline`, `propose_corrections`,
`approve_correction`, `run_patched`, `counterfactual_replay`, `get_artifact_history`, `get_budget_status`.

---

## Frontend — replace the todo canvas (`loopie-copilotkit/src/`)

Cockpit (PR-for-agent-behavior feel), driven by `useAgent()` on the control agent:

- **Top:** Run Baseline · Propose · Approve · Rerun + Compare · Counterfactual Replay.
- **Left:** live event stream (Redis Streams via SSE).
- **Center:** failed-case card + Weave trace summary + causality chain.
- **Right:** correction diff + approve/edit/reject + blast radius.
- **Bottom:** eval delta + Artifact Time Machine + token/cost budget status.

Components: `Cockpit`, `EventStream`, `FailureCard`, `CausalityChain`, `CorrectionDiff`,
`ApprovalPanel`, `BlastRadius`, `EvalDelta`, `TimeMachine`, `BudgetMeter`, `AuditTrail`.

---

## Build order (cut line preserved)

0. **`llm.py` gateway in `mock` mode + budgets + cost ledger + deterministic `decide.py`**
1. Fixtures + seeds (`data/`); lock the dataset.
2. `loopie_swarm` StateGraph + Redis live substrate; baseline run fails `security_001`.
3. Weave ops on nodes + deterministic scorers + baseline `Evaluation`.
4. Postgres `loopie` schema + `artifact_versions` + Redis Streams event log.
5. `propose`/`approve`/`apply` for the hero → patched rerun → **Weave Compare**.
6. **Counterfactual Replay** (neighbor suite + no-regression panel).
7. Extend corrections to stale memory (`refund_007`) + planner loop (`loop_001`).
8. Cockpit UI (3 buttons → full 4-region layout + Time Machine scrubber + cost ledger).
9. **Live LLM agent path + `live==oracle` differential test**
10. *(Stretch)* framework-agnostic adapter supervising the external `sample_agent`.

**Cut order if time collapses:** RedisVL/Iris → Causality Ledger UI polish → loop + stale-memory
modes → counterfactual neighbors → cockpit polish → everything except the fintech hero loop +
Weave Compare.

**Never cut:** token guardrails, one failing ticket, Weave trace, Redis artifact diff, human approval,
same eval rerun, deterministic score improvement.

---

## Environment / setup

- **`LOOPIE_LLM_MODE=mock` by default** in all `.env` files; only the recording session sets `live`.
- Set `OPENAI_API_KEY` in `loopie-copilotkit/.env` (only needed for `live`).
- Root `.env`: `WANDB_API_KEY`, `WANDB_ENTITY`, `WEAVE_PROJECT=loopie`.
- Redis: reuse running container, `loopie:` prefix + logical DB 1.
- Postgres: reuse running container, dedicated `loopie` schema.
- Restart: `cd loopie-copilotkit && npm run dev`.

## Verification (run locally before claiming anything)

Smoke: `weave.init` logs a trace; Evaluation shows under Evals + Compare; redis-py works; psycopg
creates `loopie` schema; scorer unit tests pass.

Token-safety acceptance:

- `run_suite(mode="mock")` completes with **0 network calls / $0**.
- mock vs live produce the **same graded actions** on canonical cases.
- Tripping `MAX_AGENT_TRANSITIONS` marks `budget_guard_triggered` and stops.
- Replay cache hit makes 0 new live calls.

Full-loop acceptance:

1. `seed` → Redis has wrong v1 memory + **no** fraud guard.
2. `run_baseline` → `security_001` fails (`unauthorized_tool_call`).
3. `propose_corrections` → structured routing-guard correction.
4. Approve → Redis rule + Postgres version + Stream event + Time Machine v1→v2.
5. `run_patched` → `security_001` → `escalate_security`; Weave Compare shows delta.
6. `counterfactual_replay` → neighbors green; no-regression panel confirms surgical guard.
7. Confirm no LLM-judge result is load-bearing.

**Done = the full invariant + counterfactual no-regression runs on screen, with a live Redis key
change, a Postgres version diff, and a Weave Compare — not aggregate score improvement alone.**
