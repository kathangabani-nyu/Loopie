# Reliability Notes

Future folder for evals, scorers, failure classification, correction proposals, and rerun orchestration.

## Intended Files

```text
evals.py
scorers.py
failure_classifier.py
corrections.py
rerun.py
```

## Correction Types

Start with:

- `stale_memory`
- `missing_guard`
- `loop_detected`

## Required Proof

Every correction should connect:

```text
failed metric -> failure type -> proposed artifact diff -> approval -> rerun score delta
```

