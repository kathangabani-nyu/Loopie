# loopie-build

Build and iterate on the Loopie evaluation pipeline.

## Invariant

Every build must satisfy: **baseline fails -> Redis correction applied -> rerun passes -> Weave trace logged and comparable.**

Never ship a change that only improves scores without this full cycle completing end-to-end. If a build step breaks the invariant, stop and debug before continuing.

## Workflow

1. Run the baseline eval on the current branch and confirm it produces at least one failing case.
2. Apply or adjust the Redis correction layer.
3. Rerun the same eval and verify the previously failing cases now pass.
4. Open Weave and confirm the new trace is logged alongside the baseline trace.
5. Use Weave's comparison view to diff the two runs: check scores, latency, and token usage.

## What to avoid

- Do not declare a build "done" based on aggregate score improvement alone.
- Do not skip the Weave comparison step. It is the authoritative record.
- Do not add LLM-only scoring that has no deterministic fallback.

