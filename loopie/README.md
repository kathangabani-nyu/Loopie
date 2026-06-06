# Loopie

Loopie: Reliability CI for agent swarms.

Loopie catches when agent swarms mess up, turns the failure into a persistent correction, reruns the same eval, and proves the swarm got better.

Implementation lives in [`loopie-copilotkit/`](../loopie-copilotkit/). Build plan:
[`docs/the-hackathon-has-started-adaptive-pretzel.md`](docs/the-hackathon-has-started-adaptive-pretzel.md).

## Demo Contract

The product should build toward this invariant:

```text
A fixed eval case fails in baseline.
A Weave trace shows why.
A Redis artifact changes.
A human approves the correction in the UI.
The same eval case reruns.
The score improves.
The before/after comparison is visible.
```

If a feature does not support that chain, deprioritize it.

## Project Shape

```text
loopie/
  README.md
  docs/
  backend/
    app/
      swarm/
      reliability/
      stores/
      data/
  frontend/
    app/
      components/
```

Expected future package names should stay boring:

```text
loopie/
loopie-server/
loopie-ui/
```

## Product Boundary

Loopie is not just observability. The wedge is closed-loop correction:

```text
trace -> diagnose -> persist correction -> approve -> rerun -> compare
```

The correction engine should be structured and inspectable, not a magical patch agent.

## First Build Target

Preserve this minimal path if the project gets messy:

```text
FastAPI backend
Redis seeded memory
One failing ticket
Weave trace
One correction diff
Rerun succeeds
Frontend shows before/after
```

