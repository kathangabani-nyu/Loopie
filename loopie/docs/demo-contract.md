# Demo Contract

The demo should prove that Loopie improves an agent swarm through a closed loop.

## Required Invariant

```text
A fixed eval case fails in baseline.
A Weave trace shows why.
A Redis artifact changes.
A human approves the correction in the UI.
The same eval case reruns.
The score improves.
The before/after comparison is visible.
```

## Three-Button UI Flow

The frontend should eventually support the full demo through three obvious actions:

```text
1. Run Baseline
2. Approve Correction
3. Rerun + Compare
```

## Main Story Ticket

Start with stale refund memory:

```text
Baseline retrieves a 45-day refund policy.
The customer bought an annual plan 38 days ago.
The swarm wrongly approves the refund.
Loopie proposes a corrected Redis memory artifact.
The human approves the correction.
The rerun uses the 30-day policy and denies the refund while offering credit.
```

## Cut Line

If time gets tight, keep only:

```text
one failing ticket
one Redis memory correction
one Weave trace
one approval action
one patched rerun
one before/after score comparison
```

