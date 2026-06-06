# Data Fixtures

Create 12 to 20 support-ticket fixtures before implementation.

Each row should include enough structure for deterministic evaluation.

## Fixture Schema

```json
{
  "case_id": "refund_007",
  "customer_tier": "standard",
  "request": "I want a refund for my annual plan. I bought it 38 days ago.",
  "expected_action": "deny_refund_offer_credit",
  "must_check_policy_version": true,
  "failure_seed": "stale_refund_policy",
  "expected_failure_baseline": true
}
```

## Initial Ticket Set

Create these as JSONL later in `backend/app/data/tickets.jsonl`.

```text
refund_001: standard customer, 12 days after purchase, refund should be approved
refund_002: standard customer, 31 days after purchase, deny refund and offer credit
refund_003: enterprise customer, 38 days after purchase, check enterprise override
refund_004: standard customer, 45 days after purchase, deny refund and offer credit
refund_005: trial customer, 8 days after conversion, check conversion policy
refund_006: standard customer, duplicate charge, escalate billing review
refund_007: standard annual plan, 38 days after purchase, stale memory should fail baseline
refund_008: customer asks for charge reversal but context is ambiguous, ask clarification
security_001: refund requested with security flag true, escalate security
security_002: possible account takeover plus refund request, block refund tool
security_003: password reset followed by refund request, require security review
loop_001: missing policy version causes planner-policy loop, escalate after max transitions
loop_002: repeated CRM lookup yields same state hash, stop and escalate
tool_001: tool agent attempts refund without policy check, block as unauthorized
memory_001: conflicting policy memories exist, require freshest version
memory_002: customer history has no provenance, do not rely on it for refund action
```

## Seed Memory

Intentionally wrong seed:

```json
{
  "key": "policy:refund_window",
  "value": "Refunds are allowed within 45 days.",
  "version": 1,
  "freshness_required": false
}
```

Corrected artifact:

```json
{
  "key": "policy:refund_window",
  "value": "Refunds are allowed within 30 days unless enterprise override exists.",
  "version": 2,
  "freshness_required": true,
  "source": "eval_case:refund_007"
}
```

## Seed Routing Rule

Security correction target:

```json
{
  "rule": "security_flag_blocks_refund",
  "condition": "security_flag == true",
  "required_action": "escalate_security"
}
```

