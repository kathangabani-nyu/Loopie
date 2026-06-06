# loopie-debug-eval

Debug failing evaluation cases in the Loopie pipeline.

## Process

Work through these layers in order. Stop at the first layer that explains the failure:

1. **Failing cases**: read the raw eval output. What inputs are failing? What did the model return vs. what was expected?
2. **Scorers**: are the scorers themselves correct? Check for off-by-one errors, wrong field references, or regex mismatch.
3. **Weave traces**: open the trace for the failing run. Look at the full prompt sent to the model and the raw completion.
4. **Redis state**: check whether the relevant correction key exists, has the right value, and is being read at the right point in the pipeline.
5. **Propose a fix**: identify the smallest deterministic change that makes the failing case pass without breaking passing cases.

## Rules

- The fix must be deterministic. If the proposed fix is "tweak the prompt and hope," that is not a fix. Keep debugging.
- Do not modify test fixtures to make tests pass. Fix the pipeline.
- After applying a fix, rerun the full eval suite, not just the failing case.
- Document the root cause in a comment or TODO.md entry.

