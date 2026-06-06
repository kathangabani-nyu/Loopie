# Scorers

The winning proof should not depend only on LLM-judge vibes.

Use deterministic scorers first:

```text
action_match
required_policy_checked
unauthorized_tool_call
loop_count_under_limit
tool_calls_under_budget
memory_version_correct
```

## Optional LLM Judge

An LLM judge can be added later for response quality, tone, or explanation clarity.

It should not be the primary correctness proof.

## Baseline vs Patched

The same scorers must run before and after the correction.

Expected visible result:

```text
baseline action_match: fails for refund_007
patched action_match: passes for refund_007
baseline memory_version_correct: false
patched memory_version_correct: true
```

