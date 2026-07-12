from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import uuid

import pytest

from src.loopie.reliability.corrections import (
    prepare_correction,
    project_pending_outbox,
    propose,
)
from src.loopie.runner import seed_baseline

from memory_stores import MemoryLedger, MemoryRedis


def _stores():
    redis = MemoryRedis()
    ledger = MemoryLedger()
    seed_baseline(redis=redis, ledger=ledger)
    return redis, ledger


def _prepared(ledger: MemoryLedger):
    return prepare_correction(
        propose("missing_guard", case_id="security_001"),
        ledger=ledger,
        shadow_passed=True,
        shadow_eval_run_id="shadow-pass",
    )


def test_committed_artifact_survives_projection_crash_and_reconciles() -> None:
    redis, ledger = _stores()
    correction = _prepared(ledger)

    committed = ledger.commit_correction(correction["id"])
    assert committed["version"] == 2
    assert redis.get_routing_rules() == []
    assert len(ledger.pending_outbox()) == 1

    projected = project_pending_outbox(ledger=ledger, redis=redis)
    assert projected[0]["artifact_key"] == "routing:rules"
    assert redis.get_routing_rules() == [correction["proposal"]]
    assert ledger.pending_outbox() == []


def test_raw_config_outbox_from_v3_self_heals_during_projection() -> None:
    redis, ledger = _stores()
    taxonomy = ["approve_refund", "escalate_security"]
    ledger._memory_outbox.append(
        {
            "id": str(uuid.uuid4()),
            "project_id": "00000000-0000-0000-0000-000000000001",
            "correction_id": None,
            "artifact_key": "config:action_taxonomy",
            "version": 2,
            "value": taxonomy,
            "projected_at": None,
        }
    )

    projected = project_pending_outbox(ledger=ledger, redis=redis)

    assert projected[0]["artifact_key"] == "config:action_taxonomy"
    assert json.loads(redis.get_config("action_taxonomy") or "[]") == taxonomy
    assert redis.get_artifact_doc("config:action_taxonomy")["value"] == {
        "key": "action_taxonomy",
        "value": taxonomy,
    }
    assert ledger.pending_outbox() == []


def test_cas_rejects_stale_correction_without_touching_redis() -> None:
    redis, ledger = _stores()
    correction = _prepared(ledger)
    ledger.append_artifact_version(
        artifact_key="routing:rules",
        version=2,
        value={"rules": [{"rule": "newer_change"}]},
        source_case="concurrent",
    )

    with pytest.raises(ValueError, match="CAS conflict"):
        ledger.commit_correction(correction["id"])
    assert redis.get_routing_rules() == []
    assert ledger.pending_outbox() == []


def test_concurrent_approval_is_idempotent_and_mints_one_version() -> None:
    _, ledger = _stores()
    correction = _prepared(ledger)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: ledger.commit_correction(correction["id"]), range(2)))

    history = ledger.artifact_history("routing:rules")
    assert [row["version"] for row in history] == [1, 2]
    assert sum(not result["no_op"] for result in results) == 1
    assert len(ledger.pending_outbox()) == 1
