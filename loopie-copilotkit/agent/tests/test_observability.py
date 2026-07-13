"""Weave trace-shape and redaction tests."""

from __future__ import annotations

from dataclasses import dataclass

from src.loopie.observability import (
    _postprocess_inputs,
    _postprocess_output,
    compact_approval_output,
    compact_episode_output,
    compact_run_output,
    compact_shadow_output,
)


@dataclass
class SecretBearingClient:
    api_key: str


def test_trace_inputs_are_compact_and_do_not_serialize_runtime_objects():
    inputs = _postprocess_inputs(
        {
            "self": SecretBearingClient(api_key="do-not-log"),
            "ticket": {
                "case_id": "security_002",
                "request": "Refund this order",
                "security_flag": True,
                "internal_note": "not needed in Weave",
            },
            "artifacts": {
                "routing_rules": [{"rule": f"rule-{index}"} for index in range(30)],
                "policy_rules": [{"rule_id": "approved-refund"}],
                "action_taxonomy": ["approve_refund", "escalate_security"],
                "large_internal_blob": "x" * 5_000,
            },
        }
    )

    assert inputs["self"] == "<SecretBearingClient>"
    assert inputs["ticket"] == {
        "case_id": "security_002",
        "request": "Refund this order",
        "security_flag": True,
    }
    assert inputs["artifacts"]["routing_rule_count"] == 30
    assert "large_internal_blob" not in inputs["artifacts"]
    assert "do-not-log" not in repr(inputs)


def test_default_trace_output_redacts_dsns_bounds_strings_and_objects():
    output = _postprocess_output(
        {
            "dsn": "postgresql://user:password@example.com/loopie",
            "openai_api_key": "do-not-log-key",
            "authorization": "Bearer do-not-log-token",
            "long": "x" * 1_000,
            "client": SecretBearingClient(api_key="do-not-log"),
        }
    )

    assert output["dsn"] == "postgresql://***@example.com/loopie"
    assert output["openai_api_key"] == "***"
    assert output["authorization"] == "***"
    assert len(output["long"]) < 700
    assert output["client"] == "<SecretBearingClient>"
    assert "password" not in repr(output)
    assert "do-not-log-key" not in repr(output)
    assert "do-not-log-token" not in repr(output)
    assert "do-not-log" not in repr(output)


def test_run_trace_keeps_decision_proof_but_drops_duplicate_raw_payloads():
    output = compact_run_output(
        {
            "run_id": "run-123",
            "case_id": "security_002",
            "phase": "patched",
            "correction_id": "corr-1",
            "action": "escalate_security",
            "oracle_action": "escalate_security",
            "mode": "test",
            "tool_calls": [{"name": "escalate_tool", "args": {}}],
            "audit_payload": {"policy_result": "allowed", "blocked_tools": []},
            "evidence_calls": [
                {
                    "name": "risk_score_lookup",
                    "iteration": 1,
                    "result_hash": "hash-1",
                    "result": {"raw": "x" * 5_000},
                }
            ],
            "artifacts_snapshot": {"large": "x" * 5_000},
            "run_manifest": {"large": "x" * 5_000},
            "artifact_hash": "manifest-1",
            "trace": [{"large": "x" * 5_000}],
        }
    )

    assert output["action"] == "escalate_security"
    assert output["phase"] == "patched"
    assert output["correction_id"] == "corr-1"
    assert output["manifest_hash"] == "manifest-1"
    assert "artifact_hash" not in output
    assert output["tool_calls"] == ["escalate_tool"]
    assert output["policy_result"] == "allowed"
    assert output["evidence_calls"] == [
        {"name": "risk_score_lookup", "iteration": 1, "result_hash": "hash-1"}
    ]
    assert "artifacts_snapshot" not in output
    assert "run_manifest" not in output
    assert "trace" not in output


def test_episode_and_shadow_outputs_are_summary_first():
    episode = compact_episode_output(
        {
            "action": "escalate_security",
            "proposed_tools": [{"name": "escalate_tool", "args": {}}],
            "evidence_calls": [
                {
                    "name": "risk_score_lookup",
                    "iteration": 1,
                    "result_hash": "hash-1",
                    "result": {"raw": "secret"},
                }
            ],
            "reason": "Security flag requires escalation.",
        }
    )
    shadow = compact_shadow_output(
        {
            "id": "shadow-1",
            "correction_id": "corr-1",
            "artifact_key": "routing:rules",
            "before_hash": "before-1",
            "after_hash": "after-1",
            "cases": [
                {
                    "case_id": "security_002",
                    "baseline_passed": False,
                    "passed": True,
                    "correctness": {"raw": "x" * 5_000},
                },
                {
                    "case_id": "refund_001",
                    "baseline_passed": True,
                    "passed": False,
                    "regressed": True,
                },
            ],
            "hero_improved": True,
            "no_regressions": False,
            "passed": False,
        }
    )

    assert episode["proposed_tools"] == ["escalate_tool"]
    assert episode["evidence_calls"] == [
        {"name": "risk_score_lookup", "iteration": 1, "result_hash": "hash-1"}
    ]
    assert shadow["case_count"] == 2
    assert shadow["evaluation_count"] == 2
    assert shadow["correction_id"] == "corr-1"
    assert shadow["before_hash"] == "before-1"
    assert shadow["after_hash"] == "after-1"
    assert shadow["regressions"] == ["refund_001"]
    assert shadow["gate_passed"] is False
    assert shadow["all_evaluations_passed"] is False
    assert "passed" not in shadow
    assert "cases" not in shadow


def test_shadow_summary_distinguishes_gate_pass_from_universal_pass() -> None:
    shadow = compact_shadow_output(
        {
            "cases": [
                {
                    "case_id": "security_001",
                    "baseline_passed": False,
                    "passed": True,
                    "regressed": False,
                },
                {
                    "case_id": "memory_001",
                    "baseline_passed": False,
                    "passed": False,
                    "regressed": False,
                },
            ],
            "hero_improved": True,
            "no_regressions": True,
            "passed": True,
        }
    )

    assert shadow["gate_passed"] is True
    assert shadow["all_evaluations_passed"] is False
    assert shadow["pre_existing_failures"] == ["memory_001"]


def test_approval_summary_keeps_human_and_artifact_proof_only() -> None:
    approval = compact_approval_output(
        {
            "approval_decision": "approved",
            "approval_channel": "hitl_chat",
            "correction_id": "corr-1",
            "artifact_key": "routing:rules",
            "version": 2,
            "before_hash": "before-1",
            "after_hash": "after-1",
            "value": {"secretly_large": "x" * 5_000},
            "projected": [{"artifact_key": "routing:rules"}],
            "patched_run": {"run_id": "run-2", "parent_run_id": "run-1"},
        }
    )

    assert approval == {
        "approved": True,
        "approval_channel": "hitl_chat",
        "correction_id": "corr-1",
        "artifact_key": "routing:rules",
        "artifact_version": 2,
        "before_hash": "before-1",
        "after_hash": "after-1",
        "no_op": False,
        "projected_count": 1,
        "patched_run_id": "run-2",
        "parent_run_id": "run-1",
    }
