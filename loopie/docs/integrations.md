# Integration Notes

## Weave

Every meaningful backend node should become a Weave op.

Suggested op names:

```text
triage_agent
memory_lookup
policy_check
tool_call
evaluate_ticket
apply_correction
```

Use Weave Evaluation for before/after proof. Evaluation runs should use the same dataset and deterministic scorers across baseline and patched runs.

Suggested display names:

```text
baseline
patched
```

## Redis

Use Redis as the visible correction substrate.

Use Redis Streams for event logs, not Pub/Sub.

Stream names:

```text
swarm:events
corrections:events
evals:events
```

Correction-visible keys:

```text
memory:policy:refund_window:v1
memory:policy:refund_window:v2
routing:refund_tool:v1
correction:corr_001
run:baseline:001
run:patched:001
```

## CopilotKit and AG-UI

The UI should be a control cockpit, not a generic chat.

Target layout:

```text
Left: live event stream
Middle: failed case + trace summary
Right: correction diff + approve/edit/reject
Bottom: baseline vs patched eval delta
```

## OpenAI

OpenAI can power agent reasoning and optional failure summaries, but deterministic scorers and structured correction objects should carry the proof.

