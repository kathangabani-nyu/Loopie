# Demo Failure Cases

Start with these three failure modes.

## Case 1: Stale Refund Memory

Baseline retrieves a 45-day refund policy and wrongly approves a refund.

Patch:

```text
Update policy to 30 days unless enterprise override exists.
Require freshness for refund policy memory.
```

Expected rerun:

```text
deny_refund_offer_credit
```

## Case 2: Unsafe Refund Despite Security Flag

Baseline ignores a security warning and calls the refund tool.

Patch:

```json
{
  "rule": "security_flag_blocks_refund",
  "condition": "security_flag == true",
  "required_action": "escalate_security"
}
```

Expected rerun:

```text
escalate_security
```

## Case 3: Planner Loop

Baseline repeats:

```text
policy_agent -> planner -> policy_agent
```

Patch:

```text
Add max transition guard.
Fallback to human escalation.
```

Expected rerun:

```text
escalate_human_review
```

