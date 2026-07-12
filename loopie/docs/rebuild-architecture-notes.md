# Loopie Rebuild Architecture Notes

This is the running decision and inspiration log for the demo-to-product rebuild. It records what changed from the v2 plan and why.

## Baseline

- Baseline tracked revision: `0a86bd72778cdb5541cb624a739d30db91b0cc46`.
- The existing Next production build passed only because type validation was disabled. Direct `tsc --noEmit` found four template-era errors.
- The Python fast lane inherited external Weave settings from the developer environment and could become unexpectedly slow. Tests now force memory persistence and disable Weave unless a test opts in.
- No non-example `.env` file is tracked. The committed Redis URI contains infrastructure identifiers but no password.
- Docker Desktop and local Redis/Postgres CLIs were unavailable during the initial audit, so real-store recovery remains an explicit integration gate rather than an assumed success.
- Browser verification proved the Auth.js boundary end to end: an unauthenticated root request redirects to `/login`, credentials establish a session, and the protected cockpit loads. This is local evidence only; hosted cookie/origin behavior remains a deployment check.

## Non-negotiable boundaries

- Postgres is the system of record. Redis is a rebuildable low-latency projection and event/cache substrate.
- A run reads one immutable manifest. Mutable Redis cannot be consulted mid-run.
- LLM judges are advisory. Deterministic policy/structural/golden scorers own pass/fail.
- Every mutating correction uses human approval, CAS against its base version, a Postgres transaction, and an outbox projection.
- Test fixtures and golden annotations are physically separated from production ticket records.

## Inspirations and resulting choices

### LangGraph durable execution and fault tolerance

Sources:

- [LangGraph persistence](https://docs.langchain.com/oss/javascript/langgraph/persistence)
- [LangGraph fault tolerance](https://docs.langchain.com/oss/python/langgraph/fault-tolerance)
- [LangGraph functional API determinism and idempotency](https://docs.langchain.com/oss/javascript/langgraph/functional-api)

Choice: retain the v2 Postgres `jobs` table for admission, leasing, retries, API status, and scheduling, but use a persistent LangGraph Postgres checkpointer for node-level state. The job row must not duplicate graph state. Non-deterministic calls and side effects are checkpointed tasks/nodes with idempotency keys.

Deviation from v2: the v2 plan proposed a custom job runner as the whole recovery story. That is insufficiently granular and redundant with LangGraph's native checkpoint model. The hybrid boundary is:

```text
jobs table = should this run execute, who owns it, and what is its status?
LangGraph checkpoints = which node results are durable and where can execution resume?
run manifest/read sets = exactly what evidence and artifacts the run used
```

### OpenAI agent evaluation direction

Sources:

- [OpenAI AgentKit and Evals announcement, including the 2026 wind-down notice](https://openai.com/index/introducing-agentkit/)
- [OpenAI Evals research](https://evals.openai.com/)

Choice: datasets, trace grading, and human annotations remain useful patterns, but Loopie's correctness ledger stays code-owned and provider-neutral. OpenAI's 2026 product wind-down is direct evidence against coupling core evidence custody to one hosted eval product.

### Model selection and cost custody

Sources:

- [OpenAI current model guide](https://developers.openai.com/api/docs/models)
- [GPT-5.6 Luna model](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [GPT-4o mini model and price card](https://developers.openai.com/api/docs/models/gpt-4o-mini)

Choice: default high-volume ticket decisions to the current cost-sensitive `gpt-5.6-luna` tier, with an independently configurable `gpt-5.6-terra` advisory judge. Costs use separate input/output rates from a dated, code-owned price card. An unknown model fails price validation instead of silently applying the old flat `$2/M` approximation. The model and price-card version are evidence fields, not UI copy.

### W&B Weave

Choice: initialize Weave before LLM calls, retain explicit `op` boundaries around custom agent/control functions, and store trace/evaluation URLs on durable run records. Weave is observability and comparison evidence, not the sole system of record and not a runtime dependency in the fast test lane.

### Redis modeling and operations

Choice:

- Project-scoped keys use `loopie:{project_id}:...`.
- Artifact projections use JSON where available and strings as a compatible fallback.
- Streams are a bounded event feed, not the job queue or evidence ledger.
- One pooled client serves ordinary commands; blocking `XREAD` uses a dedicated connection/client.
- Production requires TLS, a dedicated ACL user, and no dangerous/admin commands for application credentials.
- Bulk projection uses pipelining; cleanup uses `SCAN`, never `KEYS`.

## Design deviations

| Date | Plan area | Decision | Reason |
|---|---|---|---|
| 2026-07-11 | Durable jobs | Add LangGraph Postgres checkpointing beneath the PG lease queue | Avoid duplicate workflow-state machinery and resume at node boundaries |
| 2026-07-11 | Frontend cleanup | Fix deferred template types now, but do not purge the files yet | Make CI honest while preserving a reviewable regression baseline |
| 2026-07-11 | Policy truth | Implement a closed Policy DSL before live correction generation | Generated JSON shape alone does not give deterministic semantics |
| 2026-07-11 | CopilotKit consolidation | Use the native `ag_ui_langgraph` FastAPI endpoint and an async Postgres LangGraph checkpointer; keep the second service fallback enabled until a real-store integration run passes | The CopilotKit legacy FastAPI adapter is incompatible with its AG-UI agent, and AG-UI correctly refuses graphs without checkpointing |
| 2026-07-11 | Job leases | Add a unique lease token as a fencing token on every claim | Worker names alone cannot stop an expired worker from committing after the same name reclaims a job |
| 2026-07-11 | Run isolation | Materialize Redis memory, routing, compiled policies, budgets, and action taxonomy into an immutable manifest before enqueue | Recording intended inputs is not isolation; the worker must execute the exact bundle accepted by the API |
| 2026-07-11 | Idempotent run admission | Take a project/idempotency advisory transaction lock before inserting an immutable manifest, run, and job | A plain upsert could leave orphan immutable manifests during concurrent retries |
| 2026-07-11 | Production decisions | Remove the partial `LIVE_DECISION_CASES` gate and all oracle fallbacks from live mode | A provider or budget failure is a failed run, not permission to fabricate a production decision from fixture logic |
| 2026-07-11 | Tool authority | Evaluate the same approved Policy DSL bundle for refund authorization and correctness scoring | A descriptive routing dictionary cannot be the only enforcement boundary |
| 2026-07-11 | LLM cache | Put production replay-cache values in Redis with TTL while keeping an explicit in-memory fake for tests | Process-local cache state is lost on restart and diverges across replicas |
| 2026-07-11 | Correction writes | Commit approval, CAS-checked artifact version, and outbox row in one PG transaction; project to Redis afterward | Redis-first writes created an unrecoverable split-brain window |
| 2026-07-11 | Hosted topology | Collapse Render to one paid FastAPI service and delete the second LangGraph web service | Native AG-UI, the PG checkpointer, and the durable worker now share one lifecycle; the duplicate service added cost and split ownership |
| 2026-07-11 | Production correction authorship | Replace the production call to the four-case fixture table with an enum-constrained LLM correction union | Model output is untrusted authoring input; typed policy/memory/config variants, Policy DSL parsing, mutable-key allowlists, taxonomy checks, shadow evaluation, and human review form the trust boundary |
| 2026-07-11 | Improvement custody | Make one ApprovalService own approve/reject, outbox projection, and admission of the linked patched rerun | Applying an artifact is not evidence of improvement; the product now records parent/correction links and a deterministic score delta with regression detection |
| 2026-07-11 | Product events | Use one bounded Redis Stream and a dedicated blocking Redis client for SSE | Postgres polling made the event endpoint a hidden query loop; a separate blocking pool prevents XREAD consumers from starving artifact/cache traffic |
| 2026-07-11 | Deployment runtime | Build the single FastAPI service from `agent/uv.lock` in Docker and run migrations with Render `preDeployCommand` | The old Docker path still launched `langgraph dev`; Render's current Blueprint contract supports repo-relative `dockerfilePath`, `dockerContext`, and pre-deploy migrations |
| 2026-07-11 | Redis TLS in hosted mode | Add `LOOPIE_ALLOW_INSECURE_REDIS` as an explicit, off-by-default opt-out for the `rediss://` requirement in `redis_store.py` | Verified against Redis's own docs that TLS is not available on the free Redis Cloud Essentials tier at all (paid-only) — the hard requirement made free-tier Redis Cloud unusable for hosted mode. The owner made an informed choice to accept unencrypted Redis traffic on the free tier rather than switch providers (Upstash offers free TLS) or pay. Kept the default secure and the override loud and named, matching `LOOPIE_LIVE_CONFIRMED` / `LOOPIE_ENABLE_ADMIN_RESET` — never a silent downgrade |
| 2026-07-11 | Render plan | Revert `render.yaml`'s `loopie-api` service from `plan: starter` ($7/mo) back to `plan: free` | Contradicts the earlier "Hosted topology" deviation above, which paid specifically to avoid free-tier cold starts. Owner chose $0/mo cost over always-on availability for a personal single-user deployment; the tradeoff is a ~30-60s wake-up delay on the first request after ~15 minutes idle. If demoing live to someone else matters more than cost later, revert this row, not that one — the keep-warm hack was deliberately removed, so nothing currently mitigates the cold start |

## Failure-seeking findings during implementation

- Alembic offline compilation treated JSON `:value` fragments inside raw SQL as bind parameters and emitted corrupted seed JSON with `NULL` values. Migration JSON colons are now escaped for SQLAlchemy `TextClause`, and a regression test compiles representative numeric/boolean JSON.
- The golden oracle checked the generic `days > 30` rule before the enterprise override, so `refund_003` incorrectly denied instead of checking the enterprise override. The mandatory shadow holdout caught this; the enterprise branch now precedes the generic denial.
- A concurrent run upsert initially inserted a new immutable manifest before discovering an existing idempotent run. Admission now serializes the scope before any manifest insert.
- Explicit memory-mode tests were still attempting failed Postgres connections before falling back. The ledger now skips network access in explicit memory mode; durable modes must surface store failure.
- **(2026-07-11, continuation session)** `TestClient(app, lifespan="off")` in `test_api_security.py` does not exist on the pinned Starlette (1.0.0): `TestClient.__init__` has no `lifespan` kwarg on this version. The real fix is not a shim — this Starlette only runs an app's lifespan when `TestClient` is entered as a context manager (`with TestClient(app) as client`); a plain, non-context-managed instance never touches startup/shutdown at all. Rewrote the four tests to construct `TestClient(app)` directly (no `with`), which both fixes the `TypeError` and is the correct way to keep the manually-injected `StubRuntime` from being clobbered by the real lifespan's Postgres-backed `build_runtime()`.
- **The oracle branch-ordering bug class (see `refund_003` above) was not fully fixed.** Auditing all 17 golden tickets against `decide_action` at baseline (excluding the four intentionally-still-broken hero narratives — `security_001`, `refund_007`, `loop_001`, `curveball_001`, and the newly-discovered fifth, `security_002`, which is guard-dependent the same way `security_001` is) surfaced three more standing mismatches: `refund_006` (day 5, swallowed by the generic `days <= 14` approve bucket), `tool_001` (day 14, same bucket), and `memory_001` (day 33, swallowed by the generic `days > 30` deny bucket). None of these three has a `failure_seed` or a dedicated correction in the canned table — they were simply never exercised by any prior assertion (the old demo suite only ever checked the four canonical hero cases). Fixed by moving all case-specific branches in `reliability/oracle.py` before the generic day-window heuristics, and removed two dead/unreachable `case_id == "security_002"/"security_003"` branches at the bottom of the function (both tickets have `security_flag=True` and are always resolved inside the `if security_flag:` block above).
- **`shadow_evaluate_correction` was demanding universal pass, not absence of regression.** It swept all 17 tickets and required every one to pass under a correction's shadow manifest, including tickets whose brokenness is intentional and unrelated to the correction under evaluation (e.g. `refund_007`'s stale-memory failure and `curveball_001`'s missing-VAT-memory failure have nothing to do with a routing-rule correction touching `routing:rules`, and can only be fixed by their own, separately-proposed corrections). This made every correction's shadow gate permanently unsatisfiable the moment more than one hero narrative existed, which is exactly the `ledger.py` "correction must be proposed with a passing shadow evaluation" failure this session hit. Fixed by scoring every swept ticket against baseline (current, unpatched artifacts) as well as the shadow candidate, and gating on two conditions instead of one: `hero_improved` (the correction's own case must flip fail -> pass) and `no_regressions` (no ticket that already passed at baseline may flip to failing) — matching the documented Improvement contract exactly. `shadow_evaluate_correction`'s result now carries `hero_improved` / `no_regressions` fields alongside per-case `baseline_passed` / `regressed`, so a correction can no longer be blocked by a wholly unrelated ticket's pre-existing, separately-owned failure.

## Open architecture audits

- Decide whether a separate worker process is eventually required once concurrency exceeds the single-user target. For v1, an in-process worker with leases is intentional.
- ~~Verify the already-working in-process CopilotKit AG-UI endpoint against real Postgres checkpoints before deleting the second service fallback.~~ **Done 2026-07-11**, see below — checkpoint resume across a fresh pool/graph instance is proven against real Postgres.
- Confirm the exact Redis Cloud ACL/TLS capabilities before deployment; do not infer them from a URL. Still open — needs the real Redis Cloud instance, not a local container.
- ~~Add crash tests proving job lease recovery, checkpoint resume, outbox reconciliation, and correction CAS behavior against real Postgres/Redis.~~ **Done 2026-07-11**, `tests/test_real_store_crash_recovery.py` (5 tests, `@pytest.mark.integration`, skips itself when `POSTGRES_URL`/`REDIS_URL` are unset or unreachable — safe in CI without real infra, runs locally against `docker run postgres:16-alpine` / `redis:7-alpine`).
- `artifact_history()` still queries `loopie.artifact_versions` without a `project_id` filter (SELECT, not a write — harmless with one default project, but inconsistent with the rest of the ledger once a second project exists). Low priority; flagging so it isn't forgotten.
- `POST /admin/reset` now truncates jobs, runs, manifests, read sets, failures, triage, corrections, approvals, and outbox evidence before reseeding artifacts. It preserves projects, tickets, golden annotations, and compiled policy source rows.

## Completion-audit corrections (2026-07-11)

The first architecture audit found that green fast tests hid four product gaps:

- `pytest.ini` deselected every integration test by default, and one explicit integration test could call a real provider with a fake key and hang. The test is now fully stubbed, CI has a Postgres/Redis service lane, and the nightly workflow runs the golden contract plus a credential-required live honesty smoke.
- The product API still called the four canned fixture corrections and labeled them `test_fixture`. Production now calls `reliability/correction_gen.py`; the canned table remains reachable only for the golden/test lane.
- Approval committed an artifact but did not rerun the failed ticket. The unified approval service now projects the durable outbox, snapshots the resulting artifact bundle, queues an idempotent `patched` run linked by `parent_run_id` and `correction_id`, and records whether deterministic scores flipped without regression.
- SSE was a one-second Postgres polling loop. It now blocks on a bounded project-scoped Redis Stream, resumes from `Last-Event-ID`, and the browser shares one EventSource across resource hooks.

Fresh databases now receive authoritative v1 artifact versions plus outbox rows in migration `20260711_0002`; startup reconciliation builds Redis from Postgres instead of assuming an admin reset seeded process state. The stale `langgraph dev`, todo/A2UI, second-agent fallback, keep-warm, and alternate Docker paths were removed.

Deployment field selection was verified against the current [Render Blueprint YAML reference](https://render.com/docs/blueprint-spec) and [Docker deployment guide](https://render.com/docs/docker). An existing native-runtime Render service may require a one-time dashboard recreation because Render does not allow changing a service's runtime in place; fresh Blueprint creation uses the checked-in Docker contract.

## Real-store verification session (2026-07-11)

First time this codebase's tests, migrations, or app code touched a real Postgres/Redis instead of the in-memory doubles (`tests/memory_stores.py`) or offline SQL compilation. Ran via throwaway `docker run postgres:16-alpine` / `redis:7-alpine` containers. Findings:

- **`append_artifact_version` was silently writing to the in-memory fallback in real hosted mode, always.** The migration (`20260711_0001`) added `project_id UUID NOT NULL` to `loopie.artifact_versions` with a unique index on `(project_id, artifact_key, version)`, replacing the old bare `(artifact_key, version)` uniqueness. `append_artifact_version`'s INSERT was never updated to match: it omitted `project_id` entirely and targeted the now-nonexistent `(artifact_key, version)` conflict arbiter. Every call raised a NOT NULL violation, caught by a bare `except Exception:` and silently redirected to `self._memory_rows` — functionally invisible (no crash, no log) until something else queried real Postgres directly. That something is `commit_correction()`'s CAS check, which always saw `current_version = 0` for every artifact, meaning **every correction commit in real hosted mode would have failed CAS or, worse, silently permitted an incorrect version-0 base** — this would have made the entire correction-approval flow non-functional the first time it ran against a real database, and nothing in the existing test suite could have caught it because no test had ever exercised this path against real Postgres. Fixed: `append_artifact_version` now takes `project_id` (default `DEFAULT_PROJECT_ID`), includes it in the INSERT and the `ON CONFLICT` target, and — matching the "kill silent fallback" pattern used everywhere else in `ledger.py` — re-raises instead of falling back to memory when `self._postgres_ok` or `requires_durable_stores` is true. This is the single most important reason to keep a real-store integration lane: an in-memory-only test suite cannot see a bug that only exists in the seam between application code and the real schema.
- Windows-only: async psycopg refuses to run under the default `ProactorEventLoop` (`Psycopg cannot use the 'ProactorEventLoop' to run in async mode`). This would also break `uv run uvicorn loopie_server:app` on a Windows dev machine, not just tests — production (Render, Linux) is unaffected. Fixed with `src/loopie/winloop.py::ensure_selector_event_loop_policy()`, called at the top of `loopie_server.py` (before any event loop exists) and in `tests/conftest.py`.
- The Alembic *online* migration path (`alembic upgrade head`, as opposed to the offline `--sql` compile CI actually runs) required `psycopg2`, which this project does not depend on — `engine_from_config` defaults the bare `postgresql://` scheme to the psycopg2 SQLAlchemy dialect. Fixed in `migrations/env.py` by forcing the `postgresql+psycopg://` dialect so Alembic uses the same psycopg3 driver the app already depends on. Worth noting: **CI's "Compile migrations" step only proves migrations produce syntactically valid SQL offline — it has never proven they actually run against a live database.** This session was the first time `alembic upgrade head` (online) executed for real, and it caught nothing wrong with the migration itself, but the gap in coverage is real.
- `psycopg_pool.AsyncConnectionPool` open and close must happen on the *same* asyncio event loop — a fixture that calls `asyncio.run()` once to open and a second, separate `asyncio.run()` to close will orphan the pool's background worker tasks (bound to the first, now-destroyed loop) and raise `CancelledError` from `pool.close()`. Not an app bug, a test-authoring trap; the crash-recovery tests now open/use/close the pool inside one `asyncio.run()` call each.

All 97 backend tests (fast lane + integration, `mode=test`) pass against real Postgres + Redis; ruff, the offline migration compile, `tsc --noEmit`, and `next build` are all clean as of this session.

## Final production-readiness audit (2026-07-11)

- Added LLM-assisted policy compilation into the closed Policy DSL. Compiler output is parsed as untrusted input, forced back to `proposed`, checked against the project action taxonomy, deterministically replayed over recent durable runs, and registered as a normal human-reviewed `policy_rule` correction. Compiler tests cover status downgrading and rejection of actions outside the taxonomy.
- Removed the remaining internal `Ledger.rollback()` direct-write method. Any future time-machine restore must be represented as an inverse correction and pass the same shadow, approval, CAS, outbox, and audit path; there is no privileged artifact-write escape hatch.
- Hardened the default Docker Compose stack as deterministic test mode. It no longer inherits live-provider mode or provider credentials from a developer `.env`; explicit live work uses the development entrypoint or hosted deployment contract.
- Removed plain `uv run` from production startup and pre-deploy commands. Plain `uv run` re-synced the development group into the otherwise `--no-dev` runtime image at container boot; production now invokes the locked virtualenv's `alembic` and `uvicorn` executables directly.
- Render's free tier does not support Blueprint `preDeployCommand`. The Docker entrypoint now applies Alembic migrations before starting Uvicorn, preserving fresh-database bootstrap without a paid-tier hook.
- The Blueprint now provisions a free Render Postgres 16 database and injects its private `connectionString` as `POSTGRES_URL`. A `sync: false` placeholder was insufficient because Render ignores those placeholders when updating an existing Blueprint.
- Vercel compiled the Next app but its output packager rejected the self-hosting-only `output: standalone` and manual tracing-root layout (`ENOENT .next/package.json`). The frontend now uses Vercel's native Next.js output; Docker applies only to the FastAPI backend.
- The free Render Redis path exposes a non-TLS `redis://` connection, so the Blueprint sets the code-owned `LOOPIE_ALLOW_INSECURE_REDIS=1` opt-out explicitly. Replace it with a `rediss://` Redis Cloud URL and remove the opt-out before treating the deployment as security-hardened production.
- Rebuilt the integration stack from empty volumes. Alembic applied `20260711_0001` then `20260711_0002`; all 9 real-store tests passed against Postgres 16 and Redis 7.
- Built the production runtime stage from `uv.lock`, booted the complete Compose product from empty volumes, passed health and authenticated metadata checks, then ingested and completed a durable test-mode ticket run with a manifest (`approve_refund`).
- Current local verification after the final changes: Ruff clean; 95 fast tests passed with 11 deselected (9 integration and 2 live); 9 integration tests passed from empty real-store volumes; Next production build clean across 15 routes; production npm audit has 5 low findings and no high or critical findings; `git diff --check` clean.

External-state gates remain intentionally separate from code readiness: a live OpenAI + Weave honesty run, Redis Cloud ACL/TLS validation, and a fresh hosted deployment require the real project credentials and infrastructure. No local test result is presented as proof of those external systems.

## Independent verification pass (2026-07-11, later)

Re-audited the codebase against the second architecture review's five "critical" findings to confirm they were genuinely closed rather than merely worked on. All five verified resolved in code, not just intent:

1. **Canned corrections in production** — closed. `services/corrections.py:propose_for_failure` routes `mode != "test"` failures through `reliability/correction_gen.py:generate_correction` (real structured-output LLM, closed typed union, DSL validation at the trust boundary, taxonomy enforcement, `CorrectionGenerationUnavailable` instead of any fixture fallback). The canned `propose()` table is reachable only on the golden/test lane.
2. **Loop not closed** — closed. `services/approvals.py:approve` commits + projects the outbox, then enqueues a linked `kind="patched"` run carrying `parent_run_id` and `correction_id`; run completion records the deterministic fail→pass delta.
3. **No rejection / channel misattribution** — closed. `ApprovalService.reject` exists; `v1.py` `ReviewBody.channel` is `Literal["hitl_chat","rest","ui"]` (default `rest`) and `control_agent.py:106` now stamps `hitl_chat`, so chat approvals are no longer recorded as `ui`.
4. **SSE was Postgres polling** — closed. `v1.py:/events` blocks on `redis.xread` over a bounded (`maxlen=2_000`) project-scoped `product` stream via a dedicated `_blocking_client` (so blocking reads can't starve the shared pool), resumable by `Last-Event-ID`.
5. **Live test hang + CI hid the integration lane** — closed. The offending test was refactored from a full-`run_suite` drive (`test_run_suite_live_fails_when_whitelist_case_used_fallback`) to a direct honesty-gate assertion (`test_proof_gaps.py:test_live_honesty_gate_fails_when_any_case_used_oracle_fallback`, fabricated `oracle_fallback` run, no live pipeline). `.env` autoloading moved out of the server module into the dev-only `loopie_dev.py` entrypoint. CI (`.github/workflows/ci.yml`) gained a `durable-integration` job with real Postgres 16 + Redis 7 service containers running `integration and not live`, and `nightly-reliability.yml` runs the golden regression contract plus a credential-gated budget-capped live honesty smoke.

**Golden-run join** (the item the prior session stopped mid-fix on) landed: `services/runs.py:100-125` fetches the separate `golden_annotation` for `kind="golden"` runs and, in `test` mode only, merges the expected fields into the agent input before scoring — the production data model now actually exercises its own golden layer. Covered by `test_product_runtime.py` (asserts `correctness["golden"]["passed"]` and `failure["layer"] == "golden"`). Migration chain is now `20260711_0001 → 0002` (0002 seeds authoritative v1 artifact versions + outbox rows so a fresh DB reconciles Redis from Postgres rather than relying on an admin-reset seed).

Full green this pass, all against real Postgres 16 + Redis 7 containers: ruff clean; `integration and not live` = 9 passed; full `not live` suite = 102 passed; `tsc --noEmit` clean; `next build` clean (all 15 product routes present). No source changes were needed — this was a verification pass; the rebuild holds against its own plan.
