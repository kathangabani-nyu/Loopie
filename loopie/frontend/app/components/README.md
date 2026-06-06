# Component Notes

Future components:

```text
SwarmGraph.tsx
TraceTimeline.tsx
FailureCard.tsx
CorrectionDiff.tsx
ApprovalPanel.tsx
EvalDelta.tsx
```

## Component Roles

`SwarmGraph`

Shows which agents ran and where the failure occurred.

`TraceTimeline`

Shows live and historical events from Redis Streams and Weave trace summaries.

`FailureCard`

Shows failed case, expected action, actual action, and failed metrics.

`CorrectionDiff`

Shows Redis artifact changes before approval.

`ApprovalPanel`

Lets the human approve, edit, or reject the proposed correction.

`EvalDelta`

Shows baseline vs patched scorer results.

