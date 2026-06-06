# loopie-code-review

Review Loopie code before merging or demoing.

## Review checklist

### Secrets and credentials
- No API keys, tokens, or passwords hardcoded anywhere.
- `.env.example` exists and `.env` is in `.gitignore`.
- No internal URLs, personal emails, or account IDs in committed code.

### Fake improvement
- Scorers must measure real task success. Check for scorers that always return a high score, ignore edge cases, or are trivially gameable.
- Eval improvements must be traced to a specific pipeline change — not a prompt tweak with no mechanism.

### LLM-only scoring
- Every LLM-graded scorer must have a deterministic fallback or sanity check.
- If removing the LLM call would make the scorer meaningless, it needs a deterministic complement.

### Flaky demo paths
- Any code path shown in the demo must be deterministic given the same Redis state.
- No `random`, `uuid`, or timestamp-dependent logic in the critical path without seeding.
- No network calls in the demo path that aren't retried or cached.

### Missing deterministic tests
- Every scorer must have at least one unit test with a fixed input and expected output.
- The build invariant (baseline fails → correction → rerun passes) must be covered by a test, not just documented.
