"""Fast-lane tests for real per-node swarm timing and receipts."""

from memory_stores import MemoryLedger, MemoryRedis
from src.loopie.runner import run_ticket, seed_baseline, tickets_by_id

EXPECTED_RECEIPT_KEYS = {
    "triage": {"classification", "security_flag", "amount", "tier"},
    "context": {"policy_version", "freshness", "artifact_hash", "approved_rules_loaded", "routing_rules_count"},
    "resolution": {"iterations", "evidence_calls", "proposed_tools", "action"},
    "execution": {"proposed_tools", "policy_required_tools", "authorized_tools", "blocked_tools", "executed_tools", "policy_result", "policy_read_sets", "action"},
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
    execution = by_node["execution"]["receipt"]
    assert resolution["proposed_tools"] == ["refund_tool"]
    assert execution["blocked_tools"] == ["refund_tool"]
    assert execution["executed_tools"] == []
    assert execution["policy_result"] == "blocked"
    assert execution.get("audit_event_id") is None


def test_security_guard_executes_one_escalation_and_no_refund():
    redis = MemoryRedis()
    ledger = MemoryLedger()
    seed_baseline(redis=redis, ledger=ledger)
    redis.set_routing_rules([{"rule": "security_flag_blocks_refund"}])
    run = run_ticket(tickets_by_id()["security_001"], redis=redis, ledger=ledger, mode="test")
    execution = next(entry for entry in run["trace"] if entry.get("node") == "execution")["receipt"]
    assert run["action"] == "escalate_security"
    assert execution["executed_tools"] == ["escalate_tool"]
    assert execution["blocked_tools"] == ["refund_tool"]
