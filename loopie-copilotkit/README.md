# Loopie

Loopie is a support-ticket runtime and its reliability control plane. It runs
refund, billing, and security tickets through a LangGraph swarm, evaluates the
result against deterministic policy and structural rules, and proposes guarded
artifact corrections that require human approval.

The product invariant is:

```text
baseline fails -> trace explains why -> candidate artifact differs ->
shadow evaluation passes -> human approves -> durable artifact commits ->
same ticket reruns -> deterministic score improves without regressions
```

## Architecture

```text
Browser -> Next.js/Auth.js -> authenticated proxy -> FastAPI
                                                   |-- REST /api/v1
                                                   |-- native AG-UI control agent
                                                   |-- durable PG lease worker
                                                   |-- Redis Stream SSE

Postgres = system of record, jobs, manifests, read sets, corrections, audit
Redis    = artifact projection, bounded events, LLM cache
Weave    = traces and comparisons, never the only evidence store
```

Each run executes from one immutable manifest. Nodes never read mutable Redis
mid-run. Corrections use CAS and a Postgres transaction before an outbox projects
the committed version to Redis.

## Local development

1. Copy `.env.example` to `.env.local` and set the owner password, Auth.js
   secret, and service token. Never commit the result.
2. Start Postgres and Redis: `npm run dev:infra`.
3. Set `POSTGRES_URL`, `REDIS_URL`, and the matching `LOOPIE_API_TOKEN`.
4. Run migrations: `cd agent && uv run alembic upgrade head`.
5. Start the app: `npm run dev`.

Use `npm run dev:stack` to start infrastructure and both application processes.
The UI is at `http://localhost:3000`; the API is at `http://localhost:8001`.

## Verification

```powershell
cd agent
uv run ruff check src tests migrations loopie_server.py loopie_dev.py loopie_graph.py loopie_control.py
uv run pytest -m "not integration and not live" -q
uv run alembic upgrade head --sql

cd ..
npm run build
npm audit --omit=dev --audit-level=high
```

Real recovery tests run with `docker compose -f docker-compose.test.yml up
--build --abort-on-container-exit --exit-code-from tests`. Live smoke tests are
opt-in and budget capped; see `.github/workflows/nightly-reliability.yml`.
