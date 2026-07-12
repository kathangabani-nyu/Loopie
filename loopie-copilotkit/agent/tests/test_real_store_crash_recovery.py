"""Crash/recovery proofs against real Postgres + Redis.

These are the four scenarios the rebuild notes flagged as still open:
job lease recovery, correction CAS conflict, outbox reconciliation after a
simulated crash, and LangGraph checkpoint resume. All of them are meaningless
against the in-memory test doubles — they only prove anything when they hold
under real, restart-surviving storage. The whole module is skipped when
POSTGRES_URL / REDIS_URL aren't reachable, so it is safe in CI without real
infra and runs locally against `docker run postgres:16-alpine` /
`redis:7-alpine` containers.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from psycopg_pool import AsyncConnectionPool

from src.loopie.config import get_settings
from src.loopie.jobs import PostgresJobStore
from src.loopie.reliability.corrections import project_pending_outbox
from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore

pytestmark = pytest.mark.integration

POSTGRES_URL = os.getenv("POSTGRES_URL")
REDIS_URL = os.getenv("REDIS_URL")


def _real_stores_reachable() -> bool:
    if not POSTGRES_URL or not REDIS_URL:
        return False
    try:
        ledger = Ledger.connect(url=POSTGRES_URL, strict=True)
        if not ledger.ping():
            return False
        redis = RedisStore(url=REDIS_URL)
        return bool(redis.ping())
    except Exception:
        return False


requires_real_stores = pytest.mark.skipif(
    not _real_stores_reachable(),
    reason="POSTGRES_URL/REDIS_URL not reachable — start real Postgres+Redis to run this module",
)


@pytest.fixture(autouse=True)
def _durable_settings(monkeypatch):
    # Deliberately NOT LOOPIE_HOSTED=1: that flag also enforces Redis TLS
    # (rediss://), which is a separate, already-covered invariant. These
    # tests point Ledger.connect(strict=True) and RedisStore(url=...) at
    # real local containers directly and only care that durable writes
    # actually happen and survive a simulated crash.
    monkeypatch.setenv("LOOPIE_PERSISTENCE_MODE", "hosted")
    monkeypatch.setenv("LOOPIE_LLM_MODE", "test")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def ledger() -> Ledger:
    ledger = Ledger.connect(url=POSTGRES_URL, strict=True)
    ledger.reset()
    return ledger


@pytest.fixture
def redis() -> RedisStore:
    store = RedisStore(url=REDIS_URL)
    store.flush_loopie_keys()
    return store


@pytest.fixture(autouse=True)
def _clear_jobs_table():
    # Direct SQL, not the async pool: PostgresJobStore.claim() picks the
    # oldest eligible row, so leftover 'queued'/'running' jobs from a prior
    # test run (or a prior interrupted run of this same file) make the "the
    # job I just enqueued is the one claimed" assertions flaky/wrong. Job
    # tests own this table exclusively; other fixtures own their own tables.
    ledger = Ledger.connect(url=POSTGRES_URL, strict=True)
    with ledger._connect() as conn:
        conn.execute("TRUNCATE TABLE loopie.jobs")
        conn.commit()
    yield


async def _open_job_pool() -> AsyncConnectionPool:
    pool = AsyncConnectionPool(conninfo=POSTGRES_URL, min_size=1, max_size=3, open=False)
    await pool.open(wait=True, timeout=10)
    return pool


@requires_real_stores
def test_job_lease_expiry_lets_a_second_worker_reclaim_with_fencing():
    """A stale worker whose lease expired must not be able to commit after reclaim."""

    async def _run():
        # Pool open/use/close must share one event loop — psycopg_pool's
        # background workers are bound to the loop that opened them, and a
        # separate asyncio.run() for teardown tears down a *different* loop.
        pool = await _open_job_pool()
        store = PostgresJobStore(pool)
        job = await store.enqueue(
            job_type="run_ticket",
            payload={"ticket_id": "security_001"},
            idempotency_key=f"lease-test-{uuid.uuid4().hex[:8]}",
        )

        # Worker A claims with a lease so short it is already expired by the
        # time we look for a second claimant.
        claimed_a = await store.claim(worker_id="worker-a", lease_seconds=0)
        assert claimed_a is not None
        assert claimed_a.id == job.id
        stale_lease_token = claimed_a.lease_token

        await asyncio.sleep(0.05)

        # Worker B reclaims the same job because A's lease has expired.
        claimed_b = await store.claim(worker_id="worker-b", lease_seconds=30)
        assert claimed_b is not None
        assert claimed_b.id == job.id
        assert claimed_b.lease_token != stale_lease_token

        # The stale worker's fencing token must be rejected now that B owns the lease.
        stale_completed = await store.complete(job_id=job.id, lease_token=stale_lease_token)
        assert stale_completed is False

        # The current owner can still complete cleanly.
        current_completed = await store.complete(job_id=job.id, lease_token=claimed_b.lease_token)
        assert current_completed is True

        await pool.close(timeout=5)

    asyncio.run(_run())


@requires_real_stores
def test_job_heartbeat_is_fenced_after_reclaim():
    """A worker that lost its lease cannot resurrect it via a stray heartbeat."""

    async def _run():
        pool = await _open_job_pool()
        store = PostgresJobStore(pool)
        job = await store.enqueue(
            job_type="run_ticket",
            payload={"ticket_id": "security_001"},
            idempotency_key=f"heartbeat-test-{uuid.uuid4().hex[:8]}",
        )
        claimed_a = await store.claim(worker_id="worker-a", lease_seconds=0)
        await asyncio.sleep(0.05)
        claimed_b = await store.claim(worker_id="worker-b", lease_seconds=30)
        assert claimed_b is not None

        renewed = await store.heartbeat(
            job_id=job.id, lease_token=claimed_a.lease_token, lease_seconds=30
        )
        assert renewed is False

        await pool.close(timeout=5)

    asyncio.run(_run())


@requires_real_stores
def test_correction_cas_rejects_stale_base_version(ledger: Ledger, redis: RedisStore):
    """A correction proposed against v1 must not apply once the artifact has moved to v2."""
    ledger.append_artifact_version(
        artifact_key="routing:rules", version=1, value={"rules": []}, source_case="seed"
    )

    correction = {
        "id": f"corr-{uuid.uuid4().hex[:8]}",
        "case_id": "security_001",
        "category": "missing_guard",
        "proposal": {"summary": "add security guard"},
        "type": "routing_rule",
        "diff": [],
        "blast_radius": {},
        "candidate_value": {"rules": [{"rule": "security_flag_blocks_refund"}]},
    }
    ledger.register_correction(
        correction,
        artifact_key="routing:rules",
        base_artifact_version=1,
        shadow_passed=True,
        shadow_eval_run_id="shadow-test",
    )

    # Someone else's correction lands first and moves the artifact to v2 out
    # from under this proposal.
    ledger.append_artifact_version(
        artifact_key="routing:rules",
        version=2,
        value={"rules": [{"rule": "some_other_rule"}]},
        source_case="concurrent",
    )

    with pytest.raises(ValueError, match="CAS conflict"):
        ledger.commit_correction(correction["id"])

    # No projection should have happened for the rejected correction.
    projected = project_pending_outbox(ledger=ledger, redis=redis)
    assert projected == []


@requires_real_stores
def test_outbox_reconciles_after_simulated_crash(ledger: Ledger, redis: RedisStore):
    """A commit that lands in Postgres but never reaches Redis must self-heal on restart."""
    ledger.append_artifact_version(
        artifact_key="routing:rules", version=1, value={"rules": []}, source_case="seed"
    )
    correction = {
        "id": f"corr-{uuid.uuid4().hex[:8]}",
        "case_id": "security_001",
        "category": "missing_guard",
        "proposal": {"summary": "add security guard"},
        "type": "routing_rule",
        "diff": [],
        "blast_radius": {},
        "candidate_value": {"rules": [{"rule": "security_flag_blocks_refund"}]},
    }
    ledger.register_correction(
        correction,
        artifact_key="routing:rules",
        base_artifact_version=1,
        shadow_passed=True,
        shadow_eval_run_id="shadow-test",
    )

    committed = ledger.commit_correction(correction["id"])
    assert committed["no_op"] is False

    # Simulate a crash between the durable commit and the Redis projection:
    # the outbox row exists, but Redis has not been touched yet.
    assert redis.get_routing_rules() == []
    pending = ledger.pending_outbox()
    assert any(row["artifact_key"] == "routing:rules" for row in pending)

    # Startup reconciliation runs project_pending_outbox again — this must be
    # idempotent and must heal the projection without a second approval.
    projected_first = project_pending_outbox(ledger=ledger, redis=redis)
    assert any(item["artifact_key"] == "routing:rules" for item in projected_first)
    assert redis.get_routing_rules() == [{"rule": "security_flag_blocks_refund"}]

    # A second reconciliation pass (e.g. a retried restart) must be a no-op —
    # the row is already marked projected.
    projected_second = project_pending_outbox(ledger=ledger, redis=redis)
    assert projected_second == []


@requires_real_stores
def test_langgraph_checkpoint_survives_a_fresh_pool_and_graph_instance():
    """State written by one graph/pool instance must be readable by a brand-new one.

    This is the "process restarted" proof for the in-process AG-UI mount: the
    checkpointer, not the Python process, must own durability.
    """
    from langchain_core.runnables import RunnableConfig
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row

    from src.loopie.control_agent import build_unconfigured_chat_graph

    thread_id = f"crash-test-{uuid.uuid4().hex[:8]}"

    async def _write_with_first_instance():
        pool = AsyncConnectionPool(
            conninfo=POSTGRES_URL,
            min_size=1,
            max_size=3,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await pool.open(wait=True, timeout=10)
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()
        graph = build_unconfigured_chat_graph("crash-recovery-test", checkpointer=checkpointer)
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        await graph.ainvoke({"messages": [{"role": "user", "content": "hello"}]}, config=config)
        await pool.close(timeout=5)

    async def _resume_with_second_instance():
        pool = AsyncConnectionPool(
            conninfo=POSTGRES_URL,
            min_size=1,
            max_size=3,
            open=False,
            kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        )
        await pool.open(wait=True, timeout=10)
        checkpointer = AsyncPostgresSaver(pool)
        graph = build_unconfigured_chat_graph("crash-recovery-test", checkpointer=checkpointer)
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        state = await graph.aget_state(config)
        await pool.close(timeout=5)
        return state

    asyncio.run(_write_with_first_instance())
    state = asyncio.run(_resume_with_second_instance())

    assert state is not None
    assert state.values.get("messages"), "checkpointed messages did not survive a fresh pool/graph"
    assert state.config["configurable"]["thread_id"] == thread_id
