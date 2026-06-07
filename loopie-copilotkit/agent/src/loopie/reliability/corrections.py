"""Deterministic correction propose/apply."""

from __future__ import annotations

import json
import uuid
from typing import Any

from src.loopie.artifacts import build_artifact_proof
from src.loopie.observability import op
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

VAT_RECLASSIFICATION_FIX = {
    "key": "policy:vat_reverse_charge",
    "value": "EU VAT reverse-charge invoices require escalate_billing_review before any payout.",
    "version": 2,
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
    if failure_category == "vat_reclassification":
        return {
            "id": f"corr_{uuid.uuid4().hex[:8]}",
            "type": "memory_update",
            "case_id": case_id,
            "category": failure_category,
            "proposal": VAT_RECLASSIFICATION_FIX,
            "summary": "Add VAT reverse-charge policy memory routing to billing review.",
        }
    return {
        "id": f"corr_{uuid.uuid4().hex[:8]}",
        "type": "manual_review",
        "case_id": case_id,
        "category": failure_category,
        "proposal": {},
        "summary": "Manual review required.",
    }


def _latest_version(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not history:
        return None
    return max(history, key=lambda row: row["version"])


def _value_of(row: dict[str, Any]) -> Any:
    value = row.get("value")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _commit_artifact(
    *,
    ledger: Ledger,
    redis: RedisStore,
    artifact_key: str,
    new_value: dict[str, Any],
    correction: dict[str, Any],
    event: str,
) -> dict[str, Any]:
    """Append a new artifact version only when the value actually changed.

    Re-approving a correction whose value already matches the active version is a
    no-op: it must not mint a duplicate Time Machine entry (evidence custody must
    reflect real changes, not rehearsal noise).
    """
    history = ledger.artifact_history(artifact_key)
    latest = _latest_version(history)

    before_value = _value_of(latest) if latest is not None else None
    proof = build_artifact_proof(
        correction_id=correction.get("id"),
        before_value=before_value,
        after_value=new_value,
    )

    if latest is not None and before_value == new_value:
        redis.xadd("corrections", {"event": "correction_noop", "correction": correction})
        return {
            "artifact_key": artifact_key,
            "version": latest["version"],
            "correction_id": correction.get("id"),
            "no_op": True,
            **proof,
        }

    version = (latest["version"] if latest else 0) + 1
    ledger.append_artifact_version(
        artifact_key=artifact_key,
        version=version,
        value=new_value,
        source_case=correction.get("case_id"),
        correction_id=correction.get("id"),
    )
    redis.set_artifact_doc(
        artifact_key,
        {
            "artifact_key": artifact_key,
            "version": version,
            "value": new_value,
            "correction_id": correction.get("id"),
            "proof": proof,
        },
    )
    redis.xadd("corrections", {"event": event, "correction": correction, "artifact_proof": proof})
    return {
        "artifact_key": artifact_key,
        "version": version,
        "no_op": False,
        **proof,
    }


@op("corrections.apply")
def apply(
    correction: dict[str, Any],
    *,
    redis: RedisStore,
    ledger: Ledger,
) -> dict[str, Any]:
    ctype = correction.get("type")
    proposal = correction.get("proposal", {})
    result: dict[str, Any] = {"artifact_key": "", "version": 1, "correction_id": correction.get("id")}

    if ctype == "routing_rule":
        rules = redis.get_routing_rules()
        rules = [r for r in rules if r.get("rule") != proposal.get("rule")]
        rules.append(proposal)
        redis.set_routing_rules(rules)
        result = _commit_artifact(
            ledger=ledger,
            redis=redis,
            artifact_key="routing:rules",
            new_value={"rules": rules},
            correction=correction,
            event="applied_routing_rule",
        )

    elif ctype == "memory_update":
        redis.set_memory(proposal["key"], proposal["value"], version=proposal["version"])
        result = _commit_artifact(
            ledger=ledger,
            redis=redis,
            artifact_key=f"memory:{proposal['key']}",
            new_value=proposal,
            correction=correction,
            event="applied_memory_patch",
        )

    elif ctype == "config_update":
        redis.set_config(proposal["key"], proposal["value"])
        result = _commit_artifact(
            ledger=ledger,
            redis=redis,
            artifact_key=f"config:{proposal['key']}",
            new_value=proposal,
            correction=correction,
            event="applied_config_patch",
        )

    ledger.record_audit("correction_applied", {"correction": correction, "artifact_key": result["artifact_key"]})
    return result
