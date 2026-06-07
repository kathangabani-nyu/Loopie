# Loopie Hosted Demo Runbook

Zero-cost hosted stack: **Vercel (Next.js + CopilotKit UI) → Render (FastAPI + LangGraph swarm) → Neon Postgres + Redis Cloud + W&B Weave**.

## Architecture

```text
Browser
  └─ Next.js (/api/loopie/* proxies, no secrets in browser)
       └─ LOOPIE_API_BASE → Render FastAPI (loopie_server.py)
            ├─ LangGraph worker swarm (triage → memory → policy → resolution → evaluator)
            ├─ Loopie supervisor pipeline (diagnose → propose → HITL approve → rerun)
            ├─ Redis Cloud (live artifacts + event streams)
            ├─ Neon Postgres (artifact Time Machine + cost ledger)
            └─ Weave (live-mode traces/evals only)
```

CopilotKit chat (optional) uses `AGENT_URL` only if you host the sample LangGraph agent separately. The **demo proof path** runs through `loopie_server`, not the chat agent.

## Default demo mode (judging-safe)

| Variable | Hosted default | Purpose |
|----------|----------------|---------|
| `LOOPIE_LLM_MODE` | `mock` | Deterministic oracle decisions, zero token spend |
| `LOOPIE_FULL_AGENTIC` | `false` | Live OpenAI decisions limited to whitelist cases |
| `LOOPIE_HOSTED` | `1` | Require Redis + Postgres; no silent in-memory ledger |
| `LOOPIE_PERSISTENCE_MODE` | `hosted` or `auto` | Durable audit trail for artifact proof |
| Cursor provider | disabled | Enable only after smoke marker + explicit env |

Live OpenAI rehearsal is **opt-in** after N stable mock runs with `fallback_count=0`, `dishonest_live_cases=[]`, and `oracle_mismatch_cases=[]`.

## Environment matrix

### Vercel (Next.js UI)

| Variable | Required | Example |
|----------|----------|---------|
| `LOOPIE_API_BASE` | yes | `https://loopie-api.onrender.com` |
| `AGENT_URL` | optional | CopilotKit chat only |
| `OPENAI_API_KEY` | optional | Server-side CopilotKit only; never `NEXT_PUBLIC_*` |

Do **not** expose `REDIS_URL`, `POSTGRES_URL`, `WANDB_API_KEY`, or provider keys to the browser. All Loopie actions go through `/api/loopie/*`.

### Render (FastAPI backend)

Deploy via Blueprint: `render.yaml` lives at the **repo root** (required for Render to auto-discover it). It declares `rootDir: loopie-copilotkit/agent`. In Render: New → Blueprint → pick this repo → it reads `render.yaml`. Fill the four `sync: false` secrets (`REDIS_URL`, `POSTGRES_URL`, `WANDB_API_KEY`, `OPENAI_API_KEY`) in the dashboard.

| Variable | Required | Notes |
|----------|----------|-------|
| `LOOPIE_HOSTED` | yes | `1` — startup hard-fails without Redis/Postgres |
| `LOOPIE_LLM_MODE` | yes | `mock` for judging |
| `REDIS_URL` | yes | Redis Cloud connection string |
| `POSTGRES_URL` | yes | Neon pooled connection string |
| `PORT` | auto | Set by Render |
| `WANDB_API_KEY` | mock: no | Required for live Weave rehearsal |
| `WEAVE_PROJECT` | optional | Default `loopie` |
| `OPENAI_API_KEY` | mock: no | Live mode only |
| `LOOPIE_PROVIDER_CURSOR_ENABLED` | no | Bonus provider; requires smoke marker |

**Start command**

```bash
uv run uvicorn loopie_server:app --host 0.0.0.0 --port $PORT
```

**Health check:** `GET /health` (also used by free keep-warm ping services)

**Before every recording:** `POST /reset` (via UI or curl) to reseed intentionally wrong artifacts.

### Neon Postgres

Create a database and set `POSTGRES_URL`. The backend auto-creates the `loopie` schema on first connect.

### Redis Cloud

Set `REDIS_URL` with RedisJSON module when available (preflight reports `redis_json: true`). Plain Redis still works for demo artifacts.

### Weave

Mock mode: tracing disabled, zero W&B calls. Live rehearsal: set `WANDB_API_KEY` + `WEAVE_PROJECT`, switch `LOOPIE_LLM_MODE=live` only after mock suite is stable.

## Preflight

`GET /preflight` (proxied at `/api/loopie/preflight`) returns:

- `redis_reachable`, `redis_json`
- `postgres_reachable`, `persistence_mode` (`postgres` | `memory`)
- `weave_enabled`, `provider_mode`, `llm_mode`, `full_agentic`

Hosted mode: `ok: false` → HTTP 503. Startup also hard-fails if `LOOPIE_HOSTED=1` and stores are missing.

## Keep-warm (Render free tier)

Two zero-cost options (use both for a judged demo):

1. **GitHub Actions** (in-repo, no signup): `.github/workflows/keep-warm.yml` pings every 10 min. Set repo variable `RENDER_URL` (Settings → Secrets and variables → Actions → Variables) to your Render URL. Best-effort timing; auto-disabled after 60 days idle.
2. **UptimeRobot / cron-job.org** (more reliable for live judging), every 5 min:

```text
GET https://<render-service>/health
```

## Rehearsal checklist

1. Deploy Render backend; confirm `/preflight` shows `ok: true`, `persistence_mode: postgres`.
2. Deploy Vercel UI with `LOOPIE_API_BASE` pointing at Render.
3. `POST /reset` — confirm baseline artifacts reseeded.
4. Run mock demo path: Baseline → Propose → Approve → Patched → Compare.
5. Confirm Weave quiet in mock (`weave_enabled: false`).
6. Optional live pass: flip `LOOPIE_LLM_MODE=live` only after fast-lane + integration suites pass.

## Local parity

```bash
cd loopie-copilotkit
npm run dev:loopie   # FastAPI :8001
npm run dev          # Next.js UI
```

Local dev uses in-memory ledger fallback unless Postgres is reachable. Set `LOOPIE_HOSTED=1` locally to rehearse hosted strictness.

## Test gates

Fast lane (pre-deploy):

```bash
cd loopie-copilotkit/agent
uv run pytest -m "not integration"
```

Full proof lane (includes integration / hosted checks):

```bash
uv run pytest
```

Hosted + LangGraph coverage lives in `test_pipeline.py` integration tests only — no separate micro-tests.
