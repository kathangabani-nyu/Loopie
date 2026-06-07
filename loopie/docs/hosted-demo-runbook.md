# Loopie Hosted Demo Runbook

Zero-cost hosted stack: **Vercel (Next.js + CopilotKit UI) → Render (`loopie-api` + `loopie-agent`) → Neon Postgres + Redis Cloud + W&B Weave**.

## Architecture

```text
Browser
  ├─ Next.js cockpit buttons → /api/loopie/* → LOOPIE_API_BASE (loopie-api)
  └─ CopilotKit side chat → AGENT_URL (loopie-agent, live GPT-5.5)

loopie-api (Render, mock pipeline — $0 proof path)
  ├─ LangGraph worker swarm (triage → memory → policy → resolution → evaluator)
  ├─ Loopie supervisor pipeline (diagnose → propose → HITL approve → rerun)
  ├─ Redis Cloud (live artifacts + event streams)
  ├─ Neon Postgres (artifact Time Machine + cost ledger)
  └─ Weave (live-mode traces/evals only)

loopie-agent (Render, live chat only)
  ├─ CopilotKit control agent (gpt-5.5, metered)
  └─ HTTP tools → LOOPIE_API_BASE (same state as cockpit buttons)
```

**Demo proof path** (baseline → fix → counterfactual) runs through **cockpit buttons → `loopie-api`** (`LOOPIE_LLM_MODE=mock`). **Live chat** is additive via **`loopie-agent`** and requires **`OPENAI_API_KEY`**.

## Default demo mode (judging-safe)

| Variable | Hosted default | Purpose |
|----------|----------------|---------|
| `LOOPIE_LLM_MODE` | `mock` on **loopie-api** | Deterministic oracle decisions, zero token spend for proof |
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

Do **not** expose `REDIS_URL`, `POSTGRES_URL`, `WANDB_API_KEY`, or provider keys to the browser. All Loopie cockpit actions go through `/api/loopie/*`.

### Render — `loopie-api` (FastAPI proof backend)

Deploy via Blueprint: `render.yaml` at repo root. Service `loopie-api` uses `rootDir: loopie-copilotkit/agent`.

| Variable | Required | Notes |
|----------|----------|-------|
| `LOOPIE_HOSTED` | yes | `1` |
| `LOOPIE_LLM_MODE` | yes | `mock` for judging |
| `REDIS_URL` | yes | Redis Cloud |
| `POSTGRES_URL` | yes | Neon pooled URL |
| `WANDB_API_KEY` | mock: no | Live Weave rehearsal |
| `OPENAI_API_KEY` | mock: no | Not used when `LOOPIE_LLM_MODE=mock` |

**Start:** `uv run uvicorn loopie_server:app --host 0.0.0.0 --port $PORT`  
**Health:** `GET /health`

### Render — `loopie-agent` (LangGraph live chat)

Second service in `render.yaml`. **Required for CopilotKit side chat.**

| Variable | Required | Notes |
|----------|----------|-------|
| `OPENAI_API_KEY` | **yes** | Service starts without it but chat is disabled until set |
| `LOOPIE_OPENAI_MODEL` | yes | `gpt-5.5` (or pinned `gpt-5.5-2026-04-23`) |
| `LOOPIE_API_BASE` | **yes** | Must match deployed API URL, e.g. `https://loopie-api.onrender.com` |
| `LOOPIE_MAX_CHAT_COST_USD` | yes | Default `40` |
| `LOOPIE_LLM_MODE` | yes | `mock` — chat is live; pipeline stays mock via HTTP |
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

`GET /preflight` (proxied at `/api/loopie/preflight`) returns Redis/Postgres/provider mode. Hosted: `ok: false` → HTTP 503.

## Deploy checklist

1. **Render secrets (both services):** `REDIS_URL`, `POSTGRES_URL`, `WANDB_API_KEY`, `OPENAI_API_KEY` (required on **loopie-agent**).
2. **loopie-agent env:** `LOOPIE_API_BASE=https://loopie-api.onrender.com`, `LOOPIE_OPENAI_MODEL=gpt-5.5`.
3. **Vercel env:** `LOOPIE_API_BASE=https://loopie-api.onrender.com`, `AGENT_URL=https://loopie-agent.onrender.com`.
4. **GitHub vars:** `RENDER_URL`, `AGENT_URL` → run keep-warm manually before demo.
5. **Warm both services**, then on hosted URL run: **Reset → Baseline → Propose → Approve → Rerun + Compare → Counterfactual Replay**.
6. Confirm: real non-pattern timings, Redis artifact proof in UI, counterfactual `no_regression: true`, optional live chat turn shows ledger chat cost.

## Rehearsal checklist

1. Deploy both Render services; confirm `/health` and `/ok`.
2. Deploy Vercel with `LOOPIE_API_BASE` + `AGENT_URL`.
3. `POST /reset` — baseline artifacts reseeded.
4. Full mock proof path via cockpit buttons.
5. One live chat turn (if `OPENAI_API_KEY` set on loopie-agent); confirm Budget panel chat line updates.

## Local parity

```bash
cd loopie-copilotkit
npm run dev:loopie   # FastAPI :8001
npm run dev:agent    # LangGraph :8123
npm run dev          # Next.js UI
```

Set `LOOPIE_API_BASE=http://localhost:8001` and `AGENT_URL=http://localhost:8123` locally.

## Test gates

Fast lane (pre-deploy):

```bash
cd loopie-copilotkit/agent
uv run pytest -m "not integration"
```

Note: `npm run build` passes with `ignoreBuildErrors` in `next.config.ts`; `npx tsc --noEmit` may still report legacy template TS issues unrelated to the cockpit proof path.
