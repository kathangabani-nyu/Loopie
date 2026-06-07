# Engineering Principles

Loopie should stay ambitious, but the proof path must stay boringly reliable.
Use these principles as defaults, not as bureaucracy. Break them only when the
tradeoff is explicit and the demo invariant remains honest.

## Proof First

The core invariant is non-negotiable:

```text
baseline fails -> Weave trace shows why -> Redis artifact changes ->
human approves correction -> same eval reruns -> score improves
```

If a feature does not strengthen that chain, it is probably optional.

## Atomic Enough

When a correction changes runtime behavior, keep the evidence and the mutation
together as much as the stack allows.

- Prefer idempotent writes.
- Write enough audit data to explain every Redis artifact change.
- If a cross-store operation cannot be truly atomic, record the degraded state
  clearly and make reruns safe.

## Fail Closed

Fallbacks are allowed for demo resilience, but they must not masquerade as proof.

- Live LLM fallback to oracle should mark the run degraded.
- Weave eval failures should be visible in returned state.
- A recorded live proof should not return `ok: true` if the proof path degraded.

## Deterministic Scoring

LLMs may decide or explain. Deterministic scorers grade.

- Keep scorer functions pure and cheap.
- Do not use LLM judges for load-bearing pass/fail proof.
- Keep the oracle as a differential check, not a hidden replacement for live behavior.

## Evidence Integrity

Labels are not evidence. Runtime artifacts are evidence.

- Cache keys should include artifact content, not only version labels.
- Baseline and patched evals must read the intended artifact state.
- Run records should include compact receipts such as artifact hashes, decision
  provenance, fallback flags, and prompt/schema versions.

## Small Surfaces

Prefer modules with clear jobs: runner, gateway, stores, scorers, evals,
corrections, UI state. Do not split code just to look architectural, but avoid
letting one file own unrelated policy, storage, UI, and proof behavior.

## Flexible Ambition

Ambition is good here. Multi-provider routing, deeper Redis features, richer
Weave evals, and CopilotKit human-in-the-loop flows all fit Loopie's thesis.
Add them when they preserve the proof chain and make failure modes more visible,
not when they hide uncertainty behind polish.
