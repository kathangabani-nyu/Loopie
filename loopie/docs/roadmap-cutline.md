# Roadmap and Cut Line

## Build Order

1. Create fixture dataset and seed artifacts.
2. Implement one deterministic support swarm baseline.
3. Add Weave ops and deterministic scorers.
4. Add Redis memory and Streams event log.
5. Add correction proposal and approval flow.
6. Rerun same eval and compare baseline vs patched.
7. Add cockpit UI.

## Cut First

If scope gets messy, cut in this order:

```text
GEPA/DSPy
semantic cache
vector search
multiple domains
multiple failure modes
fancy graph animation
LLM-based failure summarizer
```

## Preserve Always

```text
FastAPI backend
Redis seeded memory
One failing ticket
Weave trace
One correction diff
Rerun succeeds
Frontend shows before/after
```

