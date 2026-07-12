# Rebuild Correctness Contract

Loopie is one product with two planes:

- The execution plane runs the support-ticket LangGraph swarm.
- The control plane evaluates evidence, proposes corrections, gates approval, and proves change.

Version 1 is limited to refund, billing, and security tickets. It does not claim to supervise an external support agent.

## Failure truth sources

A production run is failed only by an approved machine-readable policy violation or a structural invariant. A golden run can additionally fail against a human-maintained golden annotation. An LLM judge may open a triage item but cannot fail a run or authorize a correction.

| Layer | Truth source | Runtime effect |
|---|---|---|
| Policy | Human-approved Policy DSL artifact | Deterministic pass or fail |
| Structural | Code-defined bounds and authorization invariants | Deterministic pass or fail |
| Golden | Separate `golden_annotations` record | Test/eval-only pass or fail |
| Judge | Calibrated model verdict against policy context | Advisory triage only |
| Outcome | Human triage, reopen, reversal, escalation | Candidate golden annotation |

Production tickets never carry `expected_action` or `failure_seed`. Those fields belong only to golden annotations and are never included in a live decision prompt or run manifest.

## Policy artifact contract

Policy prose is authoring input, not runtime truth. An LLM may compile prose into a proposed Policy DSL rule, but the rule must:

1. Parse through the closed, versioned schema.
2. Reference only allowlisted fact roots.
3. Pass deterministic unit and dry-run evaluation.
4. Show a structured diff and blast radius.
5. Receive human approval before it becomes active.

The v1 DSL supports boolean composition, equality, ordered comparisons, membership, containment, and existence. It deliberately excludes arbitrary code, regex, arithmetic, network access, and dynamic field creation.

## Execution evidence contract

Each run owns an immutable manifest containing the ticket version, exact artifact contents and hashes, prompt/schema/model/tool versions, and code revision. All graph nodes read facts through this manifest. Direct reads from mutable Redis during a run are forbidden.

Every fact/artifact access records a read-set receipt. Blast radius is the intersection of a correction's changed artifact keys and historical run read-sets; it is never inferred from LLM prose.

## Improvement contract

The core proof remains:

```text
baseline fails -> trace shows why -> candidate artifact differs ->
shadow evaluation passes -> human approves -> durable artifact commits ->
same ticket reruns against a pinned patched manifest -> deterministic score improves
```

A correction is not an improvement merely because an LLM response changed. It must flip a deterministic policy, structural, or golden scorer without regressing the holdout set.
