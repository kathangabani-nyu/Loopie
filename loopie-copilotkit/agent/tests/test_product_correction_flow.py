from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.loopie.jobs import MemoryJobStore
from src.loopie.product_repository import MemoryProductRepository
from src.loopie.reliability.correction_gen import (
    validate_generated_correction,
    validate_generated_correction_wire,
)
from src.loopie.reliability.corrections import prepare_correction, propose
from src.loopie.runner import seed_baseline
from src.loopie.services.approvals import ApprovalService
from src.loopie.services.runs import RunService
from src.loopie.stores.redis_store import RedisStore

from memory_stores import MemoryLedger, MemoryRedis


def _run(coro):
    return asyncio.run(coro)


async def _parts():
    redis = MemoryRedis()
    ledger = MemoryLedger()
    seed_baseline(redis=redis, ledger=ledger)
    repository = MemoryProductRepository()
    jobs = MemoryJobStore()
    runs = RunService(repository=repository, jobs=jobs, redis=redis, ledger=ledger)
    ticket = await repository.create_ticket(
        external_id="novel-security-ticket",
        subject="Security hold",
        body="Refund requested while account is security flagged.",
        channel="api",
        customer_ref="customer-1",
        facts={
            "security_flag": True,
            "days_since_purchase": 2,
            "customer_tier": "standard",
            "amount_minor": None,
            "currency": "USD",
            "amount_source": "missing",
        },
        metadata={},
        tags=["security"],
    )
    baseline = await runs.queue_ticket_run(
        ticket_id=ticket["id"],
        mode="test",
        kind="ticket",
        idempotency_key="baseline",
    )
    failure_id = "00000000-0000-0000-0000-000000000099"
    repository.failures[failure_id] = {
        "id": failure_id,
        "project_id": ticket["project_id"],
        "run_id": baseline["run"]["id"],
        "ticket_id": ticket["id"],
        "category": "missing_guard",
        "layer": "policy",
        "diagnosis": {},
        "status": "open",
        "created_at": datetime.now(UTC),
    }
    return redis, ledger, repository, jobs, runs, ticket, baseline, failure_id


def test_approval_service_applies_projects_and_queues_linked_patched_run() -> None:
    async def scenario():
        redis, ledger, repository, _, runs, _, baseline, failure_id = await _parts()
        correction = propose("missing_guard", case_id="novel-security-ticket")
        correction["failure_id"] = failure_id
        prepared = prepare_correction(
            correction,
            ledger=ledger,
            shadow_passed=True,
            shadow_eval_run_id="shadow-pass",
        )
        # Postgres stores the diff and payload, not the convenience proof keys.
        # Mirror that durable row shape so approval must reconstruct the proof.
        ledger._memory_corrections[prepared["id"]].pop("before_hash", None)
        ledger._memory_corrections[prepared["id"]].pop("after_hash", None)
        service = ApprovalService(ledger=ledger, redis=redis, runs=runs)
        result = await service.approve(
            prepared["id"], actor="owner", channel="hitl_chat", note="approved in test"
        )

        assert result["approval_decision"] == "approved"
        assert result["approval_channel"] == "hitl_chat"
        assert result["before_hash"] == prepared["before_hash"]
        assert result["after_hash"] == prepared["after_hash"]
        assert result["patched_run"]["parent_run_id"] == baseline["run"]["id"]
        patched = await repository.get_run(result["patched_run"]["run_id"])
        assert patched["kind"] == "patched"
        assert patched["correction_id"] == prepared["id"]
        baseline_manifest = await repository.get_run_manifest(baseline["run"]["manifest_id"])
        patched_manifest = await repository.get_run_manifest(patched["manifest_id"])
        assert baseline_manifest is not None and patched_manifest is not None
        assert patched_manifest.id != baseline_manifest.id
        assert patched_manifest.ticket_snapshot == baseline_manifest.ticket_snapshot
        assert patched_manifest.ticket_content_hash == baseline_manifest.ticket_content_hash
        assert patched_manifest.evaluation_snapshot == baseline_manifest.evaluation_snapshot
        assert patched_manifest.scorer_version == baseline_manifest.scorer_version
        assert redis.get_routing_rules() == [prepared["proposal"]]
        assert ledger._memory_approvals[-1]["channel"] == "hitl_chat"

        repository.runs[baseline["run"]["id"]]["decision"] = {
            "correctness": {
                "policy": {"passed": False},
                "structural": {"passed": True, "scores": {"action_in_taxonomy": True}},
                "golden": None,
                "passed": False,
            }
        }
        patched_result = {
            "read_set": [],
            "audit_payload": {"action": "escalate_security"},
            "cost_events": [],
            "correctness": {
                "policy": {"passed": True},
                "structural": {"passed": True, "scores": {"action_in_taxonomy": True}},
                "golden": None,
                "passed": True,
            },
        }
        await repository.finish_run(patched["id"], patched_result)
        assert patched_result["improvement_proof"]["improvement_proven"] is True
        assert repository.failures[failure_id]["status"] == "corrected"

    _run(scenario())


def test_rejection_records_decision_without_mutating_artifacts() -> None:
    async def scenario():
        redis, ledger, _, _, runs, _, _, failure_id = await _parts()
        correction = propose("missing_guard", case_id="novel-security-ticket")
        correction["failure_id"] = failure_id
        prepared = prepare_correction(
            correction,
            ledger=ledger,
            shadow_passed=True,
            shadow_eval_run_id="shadow-pass",
        )
        service = ApprovalService(ledger=ledger, redis=redis, runs=runs)
        result = await service.reject(prepared["id"], actor="owner", channel="ui")
        assert result["status"] == "rejected"
        assert redis.get_routing_rules() == []
        assert ledger.get_correction(prepared["id"])["status"] == "rejected"

    _run(scenario())


def test_failed_shadow_is_a_terminal_gate_result_not_an_approval_proposal() -> None:
    redis = MemoryRedis()
    ledger = MemoryLedger()
    seed_baseline(redis=redis, ledger=ledger)
    correction = propose("missing_guard", case_id="security-shadow-fail")

    prepared = prepare_correction(
        correction,
        ledger=ledger,
        shadow_passed=False,
        shadow_eval_run_id="shadow-fail",
    )

    assert prepared["status"] == "shadow_failed"
    stored = ledger.get_correction(prepared["id"])
    assert stored is not None
    assert stored["status"] == "shadow_failed"
    assert stored["shadow_passed"] is False


def test_generated_correction_union_rejects_unsafe_policy_paths() -> None:
    with pytest.raises(ValidationError):
        validate_generated_correction(
            {
                "rationale": "A proposed rule is needed for this failure.",
                "correction": {
                    "kind": "policy_rule",
                    "summary": "Block the unsafe action deterministically.",
                    "rule": {
                        "schema_version": "1",
                        "rule_id": "unsafe_generated_rule",
                        "version": 1,
                        "status": "proposed",
                        "name": "Unsafe generated rule",
                        "when": {
                            "kind": "predicate",
                            "path": "system.secrets",
                            "operator": "exists",
                            "value": True,
                        },
                        "effects": [
                            {
                                "kind": "escalate_to",
                                "action": "escalate_security",
                                "message": "Escalate this ticket.",
                            }
                        ],
                    },
                },
            }
        )


def test_generated_correction_wire_decodes_into_validated_policy_union() -> None:
    generated = validate_generated_correction_wire(
        {
            "kind": "policy_rule",
            "summary": "Escalate security-flagged refund requests.",
            "rationale": "Security-sensitive refunds require deterministic escalation.",
            "policy_rule_json": json.dumps(
                {
                    "schema_version": "1",
                    "rule_id": "generated_security_escalation",
                    "version": 1,
                    "name": "Generated security escalation",
                    "status": "proposed",
                    "when": {
                        "kind": "predicate",
                        "path": "ticket.security_flag",
                        "operator": "eq",
                        "value": True,
                    },
                    "effects": [
                        {
                            "kind": "escalate_to",
                            "action": "escalate_security",
                            "message": "Escalate this security-sensitive refund.",
                        }
                    ],
                }
            ),
            "memory_key": "",
            "memory_value": "",
            "config_key": "",
            "config_value": 0,
        }
    )

    assert generated.correction.kind == "policy_rule"
    assert generated.correction.rule.when.path == "ticket.security_flag"


def test_generated_correction_wire_rejects_unsafe_decoded_policy() -> None:
    with pytest.raises(ValidationError):
        validate_generated_correction_wire(
            {
                "kind": "policy_rule",
                "summary": "Reject an unsafe generated policy path.",
                "rationale": "Decoded rules must still pass the internal Policy DSL.",
                "policy_rule_json": json.dumps(
                    {
                        "rule_id": "unsafe_wire_rule",
                        "version": 1,
                        "name": "Unsafe wire rule",
                        "status": "proposed",
                        "when": {
                            "kind": "predicate",
                            "path": "system.secrets",
                            "operator": "exists",
                            "value": True,
                        },
                        "effects": [
                            {
                                "kind": "escalate_to",
                                "action": "escalate_security",
                                "message": "Escalate this ticket.",
                            }
                        ],
                    }
                ),
                "memory_key": "",
                "memory_value": "",
                "config_key": "",
                "config_value": 0,
            }
        )


def test_generated_correction_wire_rejects_cross_kind_fields() -> None:
    with pytest.raises(ValueError, match="another correction kind"):
        validate_generated_correction_wire(
            {
                "kind": "memory_update",
                "summary": "Store a durable policy-scoped correction.",
                "rationale": "The approved memory should guide later decisions.",
                "policy_rule_json": "{}",
                "memory_key": "policy:refund:security",
                "memory_value": "Escalate security-flagged refund requests.",
                "config_key": "",
                "config_value": 0,
            }
        )


def test_redis_product_stream_resumes_after_last_event_id() -> None:
    redis = MemoryRedis()
    first = redis.xadd("product", {"event": "run.queued", "data": {"run_id": "one"}})
    redis.xadd("product", {"event": "run.finished", "data": {"run_id": "one"}})
    rows = redis.xread("product", last_id=first)
    assert [row["event"] for row in rows] == ["run.finished"]


def test_redis_stream_serializes_nested_uuid_values() -> None:
    class CapturingRedisClient:
        def __init__(self) -> None:
            self.payload: dict[str, str] = {}

        def xadd(
            self,
            _key: str,
            payload: dict[str, str],
            *,
            maxlen: int,
            approximate: bool,
        ) -> str:
            assert maxlen == 2_000
            assert approximate is True
            self.payload = payload
            return "1-0"

    client = CapturingRedisClient()
    redis = RedisStore.__new__(RedisStore)
    redis._prefix = "loopie:test:"
    redis._client = client

    event_id = redis.xadd(
        "product",
        {
            "event": "correction.approved",
            "data": {"patched_run": {"run_id": UUID(int=1)}},
        },
    )

    assert event_id == "1-0"
    assert json.loads(client.payload["data"]) == {
        "patched_run": {"run_id": "00000000-0000-0000-0000-000000000001"}
    }
