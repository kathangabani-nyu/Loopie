# Architecture Notes

Loopie should be a closed-loop reliability system for a support-ticket agent swarm.

## Core Flow

```text
Ticket fixture
  -> baseline swarm run
  -> Weave-traced agent/tool events
  -> deterministic scorers
  -> failure classifier
  -> structured correction proposal
  -> Redis artifact write after approval
  -> patched rerun
  -> before/after eval comparison
```

## Backend Intent

Future backend shape:

```text
backend/
  pyproject.toml
  app/
    main.py
    config.py
    models.py
    swarm/
      graph.py
      agents.py
      tools.py
    reliability/
      evals.py
      scorers.py
      failure_classifier.py
      corrections.py
      rerun.py
    stores/
      redis_client.py
      memory_store.py
      routing_store.py
      stream_store.py
    data/
      tickets.jsonl
      expected.json
      seed_memory.json
      seed_routing_rules.json
```

This scaffold does not create those implementation files yet.

## Frontend Intent

Future frontend shape:

```text
frontend/
  package.json
  app/
    page.tsx
    components/
      SwarmGraph.tsx
      TraceTimeline.tsx
      FailureCard.tsx
      CorrectionDiff.tsx
      ApprovalPanel.tsx
      EvalDelta.tsx
```

This scaffold does not create those implementation files yet.

## Correction Engine Policy

The correction engine should be deterministic:

```python
if failure_type == "stale_memory":
    propose_memory_update(...)
elif failure_type == "missing_guard":
    propose_routing_rule(...)
elif failure_type == "loop_detected":
    propose_max_transition_guard(...)
```

The LLM can summarize evidence, but the actual correction object should be structured and inspectable.

