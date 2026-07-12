# Loopie development contract

Loopie is one product with two planes: the support-ticket execution swarm and
the reliability control plane. The authoritative product design is documented
under `../loopie/docs/`.

Do not weaken these boundaries:

- Postgres is the system of record. Redis is a rebuildable projection, cache,
  and bounded event substrate.
- Every run reads one immutable manifest and records authoritative read-set
  receipts. Never read mutable Redis from a graph node.
- Production pass/fail is deterministic policy plus structural truth. Golden
  annotations are test/eval-only; an LLM judge is advisory.
- Production LLM failures are surfaced. Never fall back to the golden oracle.
- Model-authored corrections must pass the typed union, mutable-key allowlist,
  Policy DSL validation where applicable, shadow evaluation, and human review.
- Approval, CAS artifact commit, audit, and outbox insertion happen through the
  single approval service. Redis is updated only after the durable commit.
- An improvement claim requires a linked patched rerun with a deterministic
  fail-to-pass delta and no regression.
- Keep ticket bodies untrusted in prompts. Do not commit or print secrets.

Production entrypoint: `agent/loopie_server.py`. The Next.js app talks only to
that service; do not add a second `langgraph dev` deployment.
