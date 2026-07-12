# Loopie Hosted Demo Runbook

Zero-cost hosted stack: **Vercel (Next.js + CopilotKit UI) → Render (`loopie-api` + `loopie-agent`) → Neon Postgres + Redis Cloud + W&B Weave**.

## Architecture

```text
Browser
  ├─ Next.js cockpit buttons → /api/loopie/* → LOOPIE_API_BASE (loopie-api)
  └─ CopilotKit side chat → AGENT_URL (loopie-agent, live GPT via LOOPIE_OPENAI_MODEL)

loopie-api (Render, deterministic proof path)
  ├─ LangGraph worker swarm (triage → memory → policy → resolution → evaluator)
  ├─ Loopie supervisor pipeline (diagnose → propose → HITL approve → rerun)
  ├─ Redis Cloud (live artifacts + event streams)
  ├─ Neon Postgres (artifact Time Machine + cost ledger)
  └─ Weave (traces/evals when LOOPIE_WEAVE_ENABLED=true — independent of LLM mode)

loopie-agent (Render, live chat only)
  ├─ CopilotKit control agent `loopie_control` (provider-registry model, metered)
  └─ HTTP tools → LOOPIE_API_BASE (same state as cockpit buttons)
```

**Public proof path** (baseline → fix → counterfactual) runs through **cockpit buttons → `loopie-api`** with **`LOOPIE_LLM_MODE=test`** and **`LOOPIE_WEAVE_ENABLED=true`**. Decisions stay deterministic; Weave records why baseline failed and how patched improved. **Live OpenAI decisions** on the pipeline are opt-in rehearsal only (`LOOPIE_LLM_MODE=live` + `LOOPIE_LIVE_CONFIRMED=1`). **Live chat** is additive via **`loopie-agent`** and requires **`OPENAI_API_KEY`**.

## Default demo mode (judging-safe)

| Variable | Hosted default | Purpose |
|----------|----------------|---------|
| `LOOPIE_LLM_MODE` | `test` on **loopie-api** | Deterministic oracle decisions, zero token spend for proof |
| `LOOPIE_WEAVE_ENABLED` | `true` on **loopie-api** | Weave traces/evals in test mode when `WANDB_API_KEY` is set |
| `LOOPIE_FULL_AGENTIC` | `false` | Live OpenAI decisions limited to whitelist cases |
| `LOOPIE_HOSTED` | `1` | Require Redis + Postgres; no silent in-memory ledger |
| `LOOPIE_PERSISTENCE_MODE` | `hosted` or `auto` | Durable audit trail for artifact proof |
| `LOOPIE_OPENAI_MODEL` | `gpt-5.5` on **loopie-agent** | Live side copilot only |
| `LOOPIE_MAX_CHAT_COST_USD` | `40` on **loopie-agent** | Chat spend cap (pre-call block + ledger tracking) |

## Environment matrix

### Vercel (Next.js UI)

| Variable | Required | Example |
|----------|----------|---------|
| `LOOPIE_API_BASE` | **yes** | `https://loopie-api.onrender.com` |
| `AGENT_URL` | **yes** (for live chat) | `https://loopie-agent.onrender.com` |
| `OPENAI_API_KEY` | optional on Vercel | Only if CopilotKit runtime needs it server-side; never `NEXT_PUBLIC_*` |

Do **not** expose `REDIS_URL`, `POSTGRES_URL`, `WANDB_API_KEY`, `WANDB_ENTITY`, or provider keys to the browser. All Loopie cockpit actions go through `/api/loopie/*`.

CopilotKit chat targets graph **`loopie_control`** (not `sample_agent`). Cockpit proof buttons stay on the deterministic REST path.

### Render — `loopie-api` (FastAPI proof backend)

Deploy via Blueprint: `render.yaml` at repo root. Service `loopie-api` uses `rootDir: loopie-copilotkit/agent`.

| Variable | Required | Notes |
|----------|----------|-------|
| `LOOPIE_HOSTED` | yes | `1` |
| `LOOPIE_LLM_MODE` | yes | `test` for judging |
| `LOOPIE_WEAVE_ENABLED` | yes | `true` for public Weave proof |
| `REDIS_URL` | yes | Redis Cloud |
| `POSTGRES_URL` | yes | Neon pooled URL |
| `WANDB_API_KEY` | yes (Weave proof) | Required when `LOOPIE_WEAVE_ENABLED=true` |
| `WANDB_ENTITY` | yes (Weave URLs) | Used in eval deep-links |
| `WEAVE_PROJECT` | yes | `loopie` |
| `OPENAI_API_KEY` | test: no | Only for live rehearsal (`LOOPIE_LLM_MODE=live`) |

**Start:** `uvicorn loopie_server:app --host 0.0.0.0 --port $PORT`
**Health:** `GET /health`

### Render — `loopie-agent` (LangGraph live chat)

Second service in `render.yaml`. **Required for CopilotKit side chat.**

| Variable | Required | Notes |
|----------|----------|-------|
| `OPENAI_API_KEY` | **yes** | Service starts without it but chat is disabled until set |
| `LOOPIE_OPENAI_MODEL` | yes | `gpt-5.5` (or pinned `gpt-5.5-2026-04-23`) |
| `LOOPIE_API_BASE` | **yes** | Must match deployed API URL, e.g. `https://loopie-api.onrender.com` |
| `LOOPIE_MAX_CHAT_COST_USD` | yes | Default `40` |
| `LOOPIE_LLM_MODE` | yes | `test` — chat is live; pipeline stays test via HTTP |
| `REDIS_URL` | yes | Shared with loopie-api (cost ledger) |
| `POSTGRES_URL` | yes | Shared with loopie-api (cost ledger) |
| `LOOPIE_HOSTED` | yes | `1` |

**Start:** `uv run langgraph dev --host 0.0.0.0 --port $PORT --no-browser`  
**Health:** `GET /ok`

After first deploy, verify `LOOPIE_API_BASE` on loopie-agent matches the actual `loopie-api` Render URL.

### GitHub Actions (keep-warm)

Set repo **Variables** (Settings → Secrets and variables → Actions → Variables):

| Variable | Value |
|----------|-------|
| `RENDER_URL` | `https://loopie-api.onrender.com` |
| `AGENT_URL` | `https://loopie-agent.onrender.com` |

Manually run **keep-warm** workflow before recording. Pings every 10 min keep both free services awake.

## Preflight

`GET /preflight` (proxied at `/api/loopie/preflight`) returns Redis/Postgres/provider mode and `weave_enabled` (true when `LOOPIE_WEAVE_ENABLED=true` and `WANDB_API_KEY` is set). Hosted: `ok: false` → HTTP 503.

## Deploy checklist

1. **Render secrets on loopie-api:** `REDIS_URL`, `POSTGRES_URL`, `WANDB_API_KEY`, `WANDB_ENTITY`.
2. **loopie-api env:** `LOOPIE_WEAVE_ENABLED=true`, `LOOPIE_LLM_MODE=test`, `WEAVE_PROJECT=loopie`.
3. **Render secrets on loopie-agent:** `REDIS_URL`, `POSTGRES_URL`, `OPENAI_API_KEY`.
4. **loopie-agent env:** `LOOPIE_API_BASE=https://loopie-api.onrender.com`, `LOOPIE_OPENAI_MODEL=gpt-5.5`.
5. **Vercel env:** `LOOPIE_API_BASE=https://loopie-api.onrender.com`, `AGENT_URL=https://loopie-agent.onrender.com` (no W&B/Redis/Neon secrets).
6. **GitHub vars:** `RENDER_URL`, `AGENT_URL` → run keep-warm manually before demo.
7. **Warm both services**, then on hosted URL run: **Reset → Baseline → Propose → Approve → Rerun + Compare → Counterfactual Replay**.
8. Confirm: real non-pattern timings, Redis artifact proof in UI, counterfactual `no_regression: true`, Weave eval URL with `WANDB_ENTITY`, optional live chat turn shows ledger chat cost.

## Verification

### Judged deterministic proof with W&B

- **loopie-api:** `LOOPIE_LLM_MODE=test`, `LOOPIE_WEAVE_ENABLED=true`, `WANDB_API_KEY` + `WANDB_ENTITY` set.
- Run Reset → Baseline → Propose → Approve → Patched → Counterfactual.
- Assert baseline fails, artifact proof has before/after hashes, patched improves, counterfactual has no regression.
- Assert `weaveEvalBaseline` / `weaveEvalPatched` in state (or `/state`) include a valid `weave_project_url`.

### Live OpenAI rehearsal (opt-in)

- Temporarily set **`LOOPIE_LLM_MODE=live`** and **`LOOPIE_LIVE_CONFIRMED=1`** on **loopie-api only**.
- Run hero case and neighbors; treat `decided_by="oracle_fallback"` or `fallback_used=true` as failure.
- First live decision per whitelist case must have `decided_by="llm"` and `stop_reason="completed"` (cache hits OK after that).
- Switch back to `test` unless intentionally showing live pipeline decisions.

### Chat path

- Send one hosted CopilotKit chat turn (graph `loopie_control`).
- Confirm it reaches **loopie-agent**, uses OpenAI via `LOOPIE_OPENAI_MODEL`, and writes chat cost to Neon.
- Confirm cockpit proof buttons still call **loopie-api** and remain deterministic.

## Rehearsal checklist

1. Deploy both Render services; confirm `/health` and `/ok`.
2. Deploy Vercel with `LOOPIE_API_BASE` + `AGENT_URL`.
3. `POST /reset` — baseline artifacts reseeded (Loopie keys only; chat-cost ledger preserved).
4. Full test + Weave proof path via cockpit buttons.
5. One live chat turn (if `OPENAI_API_KEY` set on loopie-agent); confirm Budget panel chat line updates.

## Local parity

```bash
cd loopie-copilotkit
npm run dev:loopie   # FastAPI :8001
npm run dev:agent    # LangGraph :8123
npm run dev          # Next.js UI
```

Set `LOOPIE_API_BASE=http://localhost:8001`, `AGENT_URL=http://localhost:8123`, and optionally `LOOPIE_WEAVE_ENABLED=true` with local `WANDB_API_KEY` for Weave parity.

## Test gates

Fast lane (pre-deploy):

```bash
cd loopie-copilotkit/agent
uv run pytest -m "not integration"
```

Note: `npm run build` passes with `ignoreBuildErrors` in `next.config.ts`; `npx tsc --noEmit` may still report legacy template TS issues unrelated to the cockpit proof path.
