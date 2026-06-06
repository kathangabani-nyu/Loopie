"""Deterministic correction propose/apply."""

from __future__ import annotations

import uuid
from typing import Any

from src.loopie.stores.ledger import Ledger
from src.loopie.stores.redis_store import RedisStore


SECURITY_GUARD = {
    "rule": "security_flag_blocks_refund",
    "condition": "security_flag == true",
    "required_action": "escalate_security",
}

STALE_MEMORY_FIX = {
    "key": "policy:refund_window",
    "value": "Refunds are allowed within 30 days unless enterprise override exists.",
    "version": 2,
}

PLANNER_LOOP_FIX = {
    "key": "max_transitions",
    "value": 4,
}


def propose(failure_category: str, *, case_id: str) -> dict[str, Any]:
    if failure_category in {"bad_tool_authority", "missing_guard"}:
        return {
            "id": f"corr_{uuid.uuid4().hex[:8]}",
            "type": "routing_rule",
            "case_id": case_id,
            "category": failure_category,
            "proposal": SECURITY_GUARD,
            "summary": "Add routing guard blocking refund_tool when security_flag is true.",
        }
    if failure_category == "stale_memory":
        return {
            "id": f"corr_{uuid.uuid4().hex[:8]}",
            "type": "memory_update",
            "case_id": case_id,
            "category": failure_category,
            "proposal": STALE_MEMORY_FIX,
            "summary": "Update stale refund window memory from 45 to 30 days.",
        }
    if failure_category == "looping_plan":
        return {
            "id": f"corr_{uuid.uuid4().hex[:8]}",
            "type": "config_update",
            "case_id": case_id,
            "category": failure_category,
            "proposal": PLANNER_LOOP_FIX,
            "summary": "Tighten max transitions to stop planner-policy loop.",
        }
    return {
        "id": f"corr_{uuid.uuid4().hex[:8]}",
        "type": "manual_review",
        "case_id": case_id,
        "category": failure_category,
        "proposal": {},
        "summary": "Manual review required.",
    }


def apply(
    correction: dict[str, Any],
    *,
    redis: RedisStore,
    ledger: Ledger,
) -> dict[str, Any]:
    ctype = correction.get("type")
    proposal = correction.get("proposal", {})
    artifact_key = ""
    version = 1

    if ctype == "routing_rule":
        rules = redis.get_routing_rules()
        rules = [r for r in rules if r.get("rule") != proposal.get("rule")]
        rules.append(proposal)
        redis.set_routing_rules(rules)
        artifact_key = "routing:rules"
        history = ledger.artifact_history(artifact_key)
        version = max((row["version"] for row in history), default=0) + 1
        ledger.append_artifact_version(
            artifact_key=artifact_key,
            version=version,
            value={"rules": rules},
            source_case=correction.get("case_id"),
            correction_id=correction.get("id"),
        )
        redis.xadd("corrections", {"event": "applied_routing_rule", "correction": correction})

    elif ctype == "memory_update":
        redis.set_memory(proposal["key"], proposal["value"], version=proposal["version"])
        artifact_key = f"memory:{proposal['key']}"
        history = ledger.artifact_history(artifact_key)
        version = max((row["version"] for row in history), default=0) + 1
        ledger.append_artifact_version(
            artifact_key=artifact_key,
            version=version,
            value=proposal,
            source_case=correction.get("case_id"),
            correction_id=correction.get("id"),
        )
        redis.xadd("corrections", {"event": "applied_memory_patch", "correction": correction})

    elif ctype == "config_update":
        redis.set_config(proposal["key"], proposal["value"])
        artifact_key = f"config:{proposal['key']}"
        history = ledger.artifact_history(artifact_key)
        version = max((row["version"] for row in history), default=0) + 1
        ledger.append_artifact_version(
            artifact_key=artifact_key,
            version=version,
            value=proposal,
            source_case=correction.get("case_id"),
            correction_id=correction.get("id"),
        )
        redis.xadd("corrections", {"event": "applied_config_patch", "correction": correction})

    ledger.record_audit("correction_applied", {"correction": correction, "artifact_key": artifact_key})
    return {"artifact_key": artifact_key, "version": version, "correction_id": correction.get("id")}
