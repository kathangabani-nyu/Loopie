# North Star: Industrial-Grade Loopie

This document captures the **ambitious end-state** for Loopie after the current hero-slice is proven (`security_001` + neighbors with honest `live == oracle` differential checks). Nothing here is in scope for the hero demo — it is the planning backbone for post-hackathon work.

## Full-agentic decisions

Today the deterministic `decide.py` oracle still decides most cases; live LLM decisions are whitelisted to hero + neighbors only.

**North star:**

- LLMs decide **every** graded case. Retire the oracle as the runtime decider; keep `decide.py` purely as the **differential oracle / CI guardrail**.
- Both layers become fully LLM-driven: the worker swarm **and** the Loopie supervisory loop.
- `live == oracle` becomes a **leaderboard metric** (Weave Evals → Compare → Leaderboards), not a hidden tautology.

## Multi-provider gateway

`llm.py` is already the single model gateway. Extend it to route across **OpenAI + Cursor** models:

- Provider selection per node/role (narration vs decision vs supervisory).
- Per-provider cost ledger and budget caps (~$100 Cursor credits framing).
- Failover when a provider errors or exceeds budget.
- Replay cache keyed by `provider` so OpenAI and Cursor completions never cross-contaminate.

## Redis — best features

Beyond current k/v + Streams:

| Feature | Use in Loopie |
|---------|----------------|
| **RedisVL / vector search** | Semantic memory retrieval for policy and case history |
| **Semantic LLM cache** | LangCache-style near-duplicate completion reuse |
| **RedisJSON** | Rich artifact documents with partial updates |
| **TimeSeries** | Cost and score trend panels in the cockpit |
| **Pub/sub** | Live cockpit event fan-out |
| **Versioned keys** | Time Machine rollback without Postgres-only custody |

### Deployment preflights (checks, not rewrites)

- **Redis Cluster supports DB 0 only** — `loopie:` prefix is the real isolation boundary; detect cluster mode and fall back to DB 0.
- **RedisVL / RedisJSON / vector search** require Redis Stack/Search — preflight modules before enabling semantic panels.
- **Streams stay a UI feed** (XADD + recent reads). Evidence custody lives in Postgres `artifact_versions`. Consumer groups (XREADGROUP/XACK) are **out of scope** unless streams become a durable work-queue substrate.

## Weave — best features

- **Evaluations + Compare + Leaderboards** for baseline vs patched and `live == oracle` over time.
- **Online / guardrail scorers** on production-like traffic.
- **Datasets from mined failures** — auto-append regressions from Weave traces.
- **Trace-linked evidence custody** — deep-link cockpit panels to Weave call ids.
- **Cost dashboards** tied to per-run and per-provider ledgers.

## CopilotKit — best features

- **CoAgents shared-state cockpit** — Run Baseline, Approve Correction, Rerun + Compare as first-class UI actions.
- **Generative UI** for failure, correction diff, and Time Machine panels.
- **Human-in-the-loop approval** as a first-class interrupt (in-app primary surface — scores the CopilotKit prize axis).
- Frontend actions drive the supervisory loop without external setup.

## Channels & integrations (Slack, etc.)

Slack is a **separate surface** from CopilotKit (Slack Bolt + Events API onto the LangGraph backend, not "Slack via CopilotKit").

On-thesis use:

- Post a correction proposal (diff + blast radius) to Slack; take **Approve / Reject** there.
- Failure alerting for regressions.

Deferred because it needs OAuth, a public URL, and signing secrets (live-demo risk). In-app CopilotKit HITL delivers the same "human approves" beat first. Slack becomes "also pages/approves in Slack" on the roadmap.

## HITL in LangGraph (future)

If approval moves into LangGraph interrupts:

- Requires a **checkpointer** + stable `thread_id`.
- Interrupts resume the node from the start — keep **non-idempotent writes after approval**.

The current pipeline already satisfies durable approval via discrete `propose → approve → apply` steps with idempotent `_commit_artifact` (task #1).

## Phased path

```text
hero-slice (live == oracle on whitelist)
  → full-agentic single provider
  → multi-provider (OpenAI + Cursor)
  → Redis / Weave feature depth
  → enterprise (RBAC, tenancy, CI gate from free-enterprise-roadmap.md)
```
