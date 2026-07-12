"""Fast-lane tests for real per-node swarm timing and receipts."""

from memory_stores import MemoryLedger, MemoryRedis
from src.loopie.runner import run_ticket, seed_baseline, tickets_by_id

EXPECTED_RECEIPT_KEYS = {
    "triage": {"classification", "security_flag", "amount", "tier"},
    "memory_lookup": {"policy_version", "freshness", "artifact_hash"},
    "policy_check": {"applicable_rules", "approved_rules_checked", "policy_read_sets"},
    "resolution": {"tool_attempt", "policy_result", "authorization", "action"},
    "evaluator": {"scorers_passed", "scorers_total", "audit_event_id"},
}


def test_swarm_trace_has_real_duration_ms():
    redis = MemoryRedis()
    ledger = MemoryLedger()
    seed_baseline(redis=redis, ledger=ledger)
    ticket = tickets_by_id()["security_001"]
    run = run_ticket(ticket, redis=redis, ledger=ledger, mode="test")

    trace = run.get("trace") or []
    assert len(trace) >= 5
    for entry in trace:
        if entry.get("node") not in EXPECTED_RECEIPT_KEYS:
            continue
        assert isinstance(entry.get("duration_ms"), (int, float))
        assert entry["duration_ms"] >= 0


def test_swarm_trace_carries_enterprise_receipts():
    redis = MemoryRedis()
    ledger = MemoryLedger()
    seed_baseline(redis=redis, ledger=ledger)
    ticket = tickets_by_id()["security_001"]
    run = run_ticket(ticket, redis=redis, ledger=ledger, mode="test")

    by_node = {e["node"]: e for e in run.get("trace", []) if e.get("node")}
    for node, keys in EXPECTED_RECEIPT_KEYS.items():
        assert node in by_node, f"missing trace node {node}"
        receipt = by_node[node].get("receipt") or {}
        assert keys.issubset(receipt.keys()), f"{node} receipt missing keys: {keys - receipt.keys()}"

    resolution = by_node["resolution"]["receipt"]
    assert resolution["tool_attempt"] == "refund_tool"
    assert resolution["policy_result"] == "blocked"
    assert resolution["authorization"] == "denied_after_attempt"
    assert resolution.get("audit_event_id") is not None
