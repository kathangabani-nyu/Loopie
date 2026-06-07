"""Tests for decide.py oracle."""

from src.loopie.decide import decide_action


def test_security_baseline_without_guard():
    ticket = {"case_id": "security_001", "security_flag": True, "request": "refund payout"}
    artifacts = {"routing_rules": [], "memory": {}}
    assert decide_action(ticket, artifacts) == "approve_refund"


def test_security_with_guard():
    ticket = {"case_id": "security_001", "security_flag": True, "request": "refund payout"}
    artifacts = {
        "routing_rules": [{"rule": "security_flag_blocks_refund"}],
        "memory": {},
    }
    assert decide_action(ticket, artifacts) == "escalate_security"


def test_stale_memory_policy():
    ticket = {
        "case_id": "refund_007",
        "days_since_purchase": 38,
        "failure_seed": "stale_refund_policy",
        "expected_action": "deny_refund_offer_credit",
    }
    artifacts = {"memory": {"policy:refund_window": "Refunds are allowed within 45 days."}, "routing_rules": []}
    assert decide_action(ticket, artifacts) == "approve_refund"

    artifacts_fixed = {"memory": {"policy:refund_window": "Refunds are allowed within 30 days."}, "routing_rules": []}
    assert decide_action(ticket, artifacts_fixed) == "deny_refund_offer_credit"


def test_curveball_oracle_escalates_manual_without_vat_memory():
    ticket = {
        "case_id": "curveball_001",
        "failure_seed": "vat_reverse_charge",
        "expected_action": "escalate_billing_review",
    }
    artifacts = {"memory": {}, "routing_rules": []}
    assert decide_action(ticket, artifacts) == "escalate_manual_review"


def test_curveball_oracle_routes_after_vat_memory_patch():
    ticket = {
        "case_id": "curveball_001",
        "failure_seed": "vat_reverse_charge",
        "expected_action": "escalate_billing_review",
    }
    artifacts = {
        "memory": {"policy:vat_reverse_charge": "EU VAT reverse-charge invoices require billing review."},
        "routing_rules": [],
    }
    assert decide_action(ticket, artifacts) == "escalate_billing_review"
