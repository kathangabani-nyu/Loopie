"""Correction lifecycle: shadow proof, CAS commit, then Redis projection."""

from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Any

from src.loopie.artifacts import build_artifact_proof
from src.loopie.llm import DECISION_PROMPT_VERSION, DECISION_SCHEMA_VERSION
from src.loopie.manifests import ArtifactSnapshot, RunManifest, build_run_manifest
from src.loopie.observability import compact_shadow_output, op
from src.loopie.reliability.scorers import score_layers
from src.loopie.runner import run_ticket
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
PLANNER_LOOP_FIX = {"key": "max_transitions", "value": 4}
VAT_RECLASSIFICATION_FIX = {
    "key": "policy:vat_reverse_charge",
    "value": "EU VAT reverse-charge invoices require escalate_billing_review before any payout.",
    "version": 2,
}


def propose(failure_category: str, *, case_id: str) -> dict[str, Any]:
    """Golden/test fixture proposal table; production uses correction_gen."""
    common = {
        "id": f"corr_{uuid.uuid4().hex[:8]}",
        "case_id": case_id,
        "category": failure_category,
        "proposed_by": "test_fixture",
    }
    if failure_category in {"bad_tool_authority", "missing_guard"}:
        return {
            **common,
            "type": "routing_rule",
            "proposal": SECURITY_GUARD,
            "summary": "Add routing guard blocking refund_tool when security_flag is true.",
        }
    if failure_category == "stale_memory":
        return {
            **common,
            "type": "memory_update",
            "proposal": STALE_MEMORY_FIX,
            "summary": "Update stale refund window memory from 45 to 30 days.",
        }
    if failure_category == "looping_plan":
        return {
            **common,
            "type": "config_update",
            "proposal": PLANNER_LOOP_FIX,
            "summary": "Tighten max transitions to stop planner-policy loop.",
        }
    if failure_category == "vat_reclassification":
        return {
            **common,
            "type": "memory_update",
            "proposal": VAT_RECLASSIFICATION_FIX,
            "summary": "Add VAT reverse-charge policy memory routing to billing review.",
        }
    return {
        **common,
        "type": "manual_review",
        "proposal": {},
        "summary": "Manual review required.",
    }


def _latest(history: list[dict[str, Any]]) -> dict[str, Any] | None:
    return max(history, key=lambda row: int(row["version"])) if history else None


def _value(row: dict[str, Any] | None) -> Any:
    if row is None:
        return None
    value = row.get("value")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _candidate(correction: dict[str, Any], ledger: Ledger) -> tuple[str, int, Any, Any]:
    proposal = correction.get("proposal") or {}
    correction_type = correction.get("type")
    if correction_type == "policy_rule":
        artifact_key = "policy:rules"
        latest = _latest(ledger.artifact_history(artifact_key))
        before = _value(latest) or {"rules": []}
        proposed_rule = dict(proposal)
        proposed_rule["status"] = "approved"
        from src.loopie.policy.dsl import parse_policy_rule

        prior_rule = next(
            (
                rule
                for rule in before.get("rules", [])
                if rule.get("rule_id") == proposed_rule.get("rule_id")
            ),
            None,
        )
        if prior_rule:
            proposed_rule["version"] = int(prior_rule.get("version", 0)) + 1
        validated = parse_policy_rule(proposed_rule).model_dump(mode="json")
        rules = [
            rule
            for rule in before.get("rules", [])
            if rule.get("rule_id") != validated["rule_id"]
        ]
        rules.append(validated)
        candidate = {"rules": rules}
    elif correction_type == "routing_rule":
        artifact_key = "routing:rules"
        latest = _latest(ledger.artifact_history(artifact_key))
        before = _value(latest) or {"rules": []}
        rules = [rule for rule in before.get("rules", []) if rule.get("rule") != proposal.get("rule")]
        rules.append(proposal)
        candidate = {"rules": rules}
    elif correction_type == "memory_update":
        artifact_key = f"memory:{proposal['key']}"
        latest = _latest(ledger.artifact_history(artifact_key))
        before = _value(latest)
        candidate = {
            **proposal,
            "version": int(latest["version"]) + 1 if latest else int(proposal.get("version", 1)),
        }
    elif correction_type == "config_update":
        artifact_key = f"config:{proposal['key']}"
        latest = _latest(ledger.artifact_history(artifact_key))
        before = _value(latest)
        candidate = proposal
    else:
        raise ValueError(f"correction type {correction_type!r} cannot be applied")
    return artifact_key, int(latest["version"]) if latest else 0, before, candidate


def prepare_correction(
    correction: dict[str, Any],
    *,
    ledger: Ledger,
    shadow_passed: bool,
    shadow_eval_run_id: str | None,
    shadow_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_key, base_version, before, candidate = _candidate(correction, ledger)
    proof = build_artifact_proof(
        correction_id=correction["id"],
        before_value=before,
        after_value=candidate,
    )
    prepared = {
        **correction,
        "status": "proposed" if shadow_passed else "shadow_failed",
        "artifact_key": artifact_key,
        "base_artifact_version": base_version,
        "candidate_value": candidate,
        "shadow_passed": shadow_passed,
        "shadow_eval_run_id": shadow_eval_run_id,
        "shadow_result": shadow_result,
        **proof,
    }
    ledger.register_correction(
        prepared,
        artifact_key=artifact_key,
        base_artifact_version=base_version,
        shadow_passed=shadow_passed,
        shadow_eval_run_id=shadow_eval_run_id,
    )
    return prepared


def _shadow_value(artifact_key: str, candidate: Any) -> Any:
    if artifact_key == "routing:rules":
        return candidate["rules"]
    if artifact_key == "policy:rules":
        return candidate["rules"]
    if artifact_key.startswith("memory:"):
        return {"value": candidate["value"], "version": candidate["version"]}
    if artifact_key.startswith("config:"):
        return candidate["value"]
    return candidate


def _candidate_manifest(manifest: RunManifest, artifact_key: str, candidate: Any) -> RunManifest:
    snapshots = []
    found = False
    for snapshot in manifest.artifacts:
        if snapshot.key == artifact_key:
            snapshots.append(
                ArtifactSnapshot.capture(
                    artifact_key,
                    _shadow_value(artifact_key, candidate),
                    version=f"shadow:{uuid.uuid4().hex[:12]}",
                )
            )
            found = True
        else:
            snapshots.append(snapshot)
    if not found:
        snapshots.append(
            ArtifactSnapshot.capture(
                artifact_key,
                _shadow_value(artifact_key, candidate),
                version=f"shadow:{uuid.uuid4().hex[:12]}",
            )
        )
    return replace(manifest, id=str(uuid.uuid4()), artifacts=tuple(snapshots))


def _shadow_display_name(call: Any) -> str:
    inputs = getattr(call, "inputs", {}) or {}
    correction = inputs.get("correction") or {}
    return f"Shadow gate · {correction.get('case_id', 'correction')}"


@op(
    "golden_demo.shadow_gate",
    call_display_name=_shadow_display_name,
    postprocess_output=compact_shadow_output,
    kind="agent",
)
def shadow_evaluate_correction(
    correction: dict[str, Any],
    *,
    tickets: dict[str, dict[str, Any]],
    redis: RedisStore,
    ledger: Ledger,
    mode: str = "test",
    samples: int | None = None,
) -> dict[str, Any]:
    """Evaluate candidate artifacts without writing them to the live Redis projection.

    The gate is regression, not universal pass. Several golden tickets are
    deliberately still-failing at baseline pending their own, separate
    correction (e.g. a stale-memory fix has nothing to do with a routing-rule
    correction). Demanding every ticket pass under an unrelated correction's
    shadow makes the holdout permanently unsatisfiable. Instead: the hero case
    must flip fail -> pass, and no ticket that already passed at baseline may
    flip to failing under the candidate artifacts.
    """
    artifact_key, _, before, candidate = _candidate(correction, ledger)
    artifact_proof = build_artifact_proof(
        correction_id=correction["id"],
        before_value=before,
        after_value=candidate,
    )
    hero_case_id = correction["case_id"]
    hero = tickets[hero_case_id]
    case_ids = [hero_case_id, *hero.get("neighbors", [])]
    case_ids.extend(case_id for case_id in ("refund_001", "refund_002", "refund_003") if case_id in tickets)
    case_ids.extend(tickets)
    case_ids = list(dict.fromkeys(case_ids))[:30]
    samples = samples or (3 if mode == "live" else 1)
    results: list[dict[str, Any]] = []
    for case_id in case_ids:
        ticket = tickets[case_id]
        baseline_manifest = build_run_manifest(
            redis,
            ticket,
            prompt_version=DECISION_PROMPT_VERSION,
            schema_version=DECISION_SCHEMA_VERSION,
            model_version="shadow-baseline",
        )
        baseline_run = run_ticket(
            ticket,
            redis=redis,
            ledger=ledger,
            mode=mode,
            artifact_version=f"{baseline_manifest.content_hash[:12]}:baseline",
            phase="shadow_baseline",
            correction_id=correction["id"],
            manifest=baseline_manifest,
        )
        baseline_passed = bool(score_layers(baseline_run, ticket)["passed"])
        for sample in range(samples):
            manifest = build_run_manifest(
                redis,
                ticket,
                prompt_version=DECISION_PROMPT_VERSION,
                schema_version=DECISION_SCHEMA_VERSION,
                model_version="shadow",
            )
            shadow_manifest = _candidate_manifest(manifest, artifact_key, candidate)
            run = run_ticket(
                ticket,
                redis=redis,
                ledger=ledger,
                mode=mode,
                artifact_version=f"{shadow_manifest.content_hash[:12]}:{sample}",
                phase="shadow_candidate",
                correction_id=correction["id"],
                manifest=shadow_manifest,
            )
            correctness = score_layers(run, ticket)
            shadow_passed = bool(correctness["passed"])
            results.append(
                {
                    "case_id": case_id,
                    "sample": sample + 1,
                    "baseline_passed": baseline_passed,
                    "passed": shadow_passed,
                    "regressed": baseline_passed and not shadow_passed,
                    "correctness": correctness,
                }
            )
    hero_results = [r for r in results if r["case_id"] == hero_case_id]
    hero_improved = all(r["passed"] for r in hero_results)
    no_regressions = not any(r["regressed"] for r in results)
    return {
        "id": f"shadow_{uuid.uuid4().hex[:12]}",
        "artifact_key": artifact_key,
        **artifact_proof,
        "cases": results,
        "hero_improved": hero_improved,
        "no_regressions": no_regressions,
        "passed": hero_improved and no_regressions,
        "mode": mode,
        "samples_per_case": samples,
    }


def project_pending_outbox(*, ledger: Ledger, redis: RedisStore, limit: int = 100) -> list[dict[str, Any]]:
    projected: list[dict[str, Any]] = []
    for row in ledger.pending_outbox(limit=limit):
        artifact_key = str(row["artifact_key"])
        value = row["value"]
        if isinstance(value, str):
            value = json.loads(value)
        if artifact_key == "routing:rules":
            redis.set_routing_rules(list(value["rules"]))
        elif artifact_key == "policy:rules":
            redis.set_policy_rules(list(value["rules"]))
        elif artifact_key.startswith("memory:"):
            redis.set_memory(value["key"], value["value"], version=int(value["version"]))
        elif artifact_key.startswith("config:"):
            config_key = artifact_key.removeprefix("config:")
            if isinstance(value, dict) and "key" in value and "value" in value:
                config_key = str(value["key"])
                config_value = value["value"]
            else:
                # Migration 20260712_0003 briefly emitted a raw config value.
                # Accept that durable row so startup reconciliation can self-heal.
                config_value = value
                value = {"key": config_key, "value": config_value}
            redis.set_config(config_key, config_value)
        else:
            raise ValueError(f"unsupported artifact projection key {artifact_key}")
        redis.set_artifact_doc(
            artifact_key,
            {
                "artifact_key": artifact_key,
                "version": int(row["version"]),
                "value": value,
                "correction_id": row.get("correction_id"),
            },
        )
        ledger.mark_outbox_projected(str(row["id"]))
        projected.append({"outbox_id": str(row["id"]), "artifact_key": artifact_key})
    return projected


@op("corrections.apply", kind="tool")
def apply(
    correction: dict[str, Any],
    *,
    redis: RedisStore,
    ledger: Ledger,
) -> dict[str, Any]:
    if "base_artifact_version" not in correction:
        raise ValueError("correction must be prepared and shadow-evaluated before approval")
    committed = ledger.commit_correction(correction["id"])
    proof = build_artifact_proof(
        correction_id=correction["id"],
        before_value=committed.get("before_value"),
        after_value=correction["candidate_value"],
    )
    projected = project_pending_outbox(ledger=ledger, redis=redis)
    redis.xadd(
        "corrections",
        {"event": "artifact_projected", "correction_id": correction["id"], "proof": proof},
    )
    ledger.record_audit(
        "correction_applied",
        {"correction_id": correction["id"], "artifact_key": committed["artifact_key"]},
    )
    return {**committed, **proof, "projected": projected}
