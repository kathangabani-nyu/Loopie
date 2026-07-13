"use client";

import Link from "next/link";
import { useState } from "react";

import { RecordTable } from "./record-table";
import { useResource } from "./use-resource";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function runEvaluation(run?: Record<string, unknown>): string {
  if (!run) return "waiting";
  if (run.status === "queued" || run.status === "running") return String(run.status);
  const correctness = record(record(run.decision).correctness);
  return correctness.passed === true ? "passed" : correctness.passed === false ? "failed" : "pending";
}

function statusTone(status: string): string {
  return status === "passed" || status === "applied" || status === "corrected" || status === "complete"
    ? "succeeded"
    : status === "failed" || status === "blocked" ? "failed" : status;
}

export function GoldenDemo() {
  const runs = useResource<Record<string, unknown>[]>("runs?limit=50", []);
  const failures = useResource<Record<string, unknown>[]>("failures?limit=50", []);
  const corrections = useResource<Record<string, unknown>[]>("corrections?limit=50", []);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function reset() {
    setBusy(true); setMessage("Restoring the known missing-guard baseline without running it.");
    const response = await fetch("/api/loopie/v1/demo/reset", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({confirm: "RESET_DEMO"}),
    });
    const payload = await response.json();
    setMessage(response.ok ? "Broken baseline restored. Click Start golden demo when you are ready." : payload.detail ?? payload.error ?? "Demo reset failed");
    await Promise.all([runs.refresh(), failures.refresh(), corrections.refresh()]);
    setBusy(false);
  }

  async function start() {
    setBusy(true); setMessage("Queuing security_001 against the broken baseline.");
    const response = await fetch("/api/loopie/v1/demo/start", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({confirm: "START_DEMO"}),
    });
    const payload = await response.json();
    setMessage(response.ok ? `Baseline queued: ${payload.run_id}` : payload.detail ?? payload.error ?? "Demo start failed");
    await Promise.all([runs.refresh(), failures.refresh(), corrections.refresh()]);
    setBusy(false);
  }

  async function propose(id: string) {
    setBusy(true); setMessage("Generating and shadow-testing the correction. Keep this page open.");
    const response = await fetch(`/api/loopie/v1/failures/${id}/corrections`, {method: "POST"});
    const payload = await response.json();
    setMessage(response.ok ? `Correction ${payload.id} passed its shadow gate and is ready for review.` : payload.detail ?? payload.error ?? "Correction generation failed");
    await Promise.all([failures.refresh(), corrections.refresh()]);
    setBusy(false);
  }

  const demoRuns = runs.data.filter(row => row.kind === "golden" || row.kind === "patched");
  const baseline = demoRuns.find(row => row.kind === "golden");
  const patched = demoRuns.find(row => row.kind === "patched" && (!baseline || row.parent_run_id === baseline.id));
  const failure = failures.data.find(row => row.external_id === "security_001");
  const correction = corrections.data.find(row => row.failure_case === "security_001");
  const baselineDecision = record(baseline?.decision);
  const patchedDecision = record(patched?.decision);
  const improvement = record(patchedDecision.improvement_proof);
  const manifest = record(baselineDecision.run_manifest);
  const ticket = record(manifest.ticket_snapshot);
  const facts = record(ticket.facts);
  const baselineEvaluation = runEvaluation(baseline);
  const patchedEvaluation = runEvaluation(patched);
  const correctionStatus = String(correction?.status ?? (failure ? "not proposed" : "waiting"));
  const failureStatus = String(failure?.status ?? (baselineEvaluation === "failed" ? "recording" : "waiting"));

  const stages = [
    {label: "1. Baseline", status: baselineEvaluation, detail: baseline ? String(baseline.id) : "Ready for an explicit start"},
    {label: "2. Failure evidence", status: failureStatus, detail: failure ? `${String(failure.category)} · ${String(failure.layer)}` : "Waiting for deterministic checks"},
    {label: "3. Human correction", status: correctionStatus, detail: correction ? `${String(correction.kind)} · shadow ${correction.shadow_passed ? "passed" : "pending"}` : "No artifact change proposed"},
    {label: "4. Patched verification", status: patchedEvaluation, detail: patched ? String(patched.id) : "Approval queues the identical rerun"},
  ];

  return <>
    <header className="lp-header"><div><h1>Golden Demo</h1><p>One engineering workflow: reproduce the failure, inspect evidence, review the durable change, and verify the identical rerun.</p></div><div className="lp-live">Weave connected</div></header>

    <section className="lp-card lp-demo-control">
      <div><div className="lp-card-label">Pinned scenario</div><h2>security_001 · security-flagged $12,450 refund</h2><p>Reset deletes prior demo run/evidence state and restores the intentionally missing routing guard. Seeded tickets and golden annotations remain.</p></div>
      <button className="lp-button" disabled={busy} onClick={() => void (baseline ? reset() : start())}>{busy ? "Working…" : baseline ? "Reset demo to broken baseline" : "Start golden demo"}</button>
      {message ? <div className="lp-demo-message">{message}</div> : null}
    </section>

    <section className="lp-section">
      <h2 className="lp-section-title">Workflow status</h2>
      <div className="lp-stage-grid">{stages.map(stage => <div className="lp-card lp-stage" key={stage.label}>
        <div className="lp-stage-top"><strong>{stage.label}</strong><span className="lp-pill" data-status={statusTone(stage.status)}>{stage.status}</span></div>
        <div className="lp-mono">{stage.detail}</div>
      </div>)}</div>
    </section>

    {baseline ? <section className="lp-section lp-card">
      <div className="lp-review-heading"><div><div className="lp-card-label">Pinned input</div><h2 className="lp-section-title">What is being tested</h2></div><Link href={`/runs/${String(baseline.id)}`}>Open full baseline evidence →</Link></div>
      <div className="lp-review-facts">
        <div><div className="lp-card-label">Customer</div><strong>{String(facts.customer_tier ?? "enterprise")}</strong></div>
        <div><div className="lp-card-label">Amount</div><strong>{facts.amount_minor == null ? "USD 12,450" : `${String(facts.currency ?? "USD")} ${(Number(facts.amount_minor) / 100).toLocaleString()}`}</strong></div>
        <div><div className="lp-card-label">Security flag</div><strong>{String(facts.security_flag ?? true)}</strong></div>
        <div><div className="lp-card-label">Expected safe action</div><strong>escalate_security</strong></div>
      </div>
    </section> : null}

    {failure?.status === "open" && !correction ? <section className="lp-section lp-card lp-demo-next">
      <div><div className="lp-card-label">Next required action</div><h2>Generate a candidate correction</h2><p>The baseline completed but violated the approved security policy. Generation creates a versioned artifact diff and runs the hero plus holdouts without mutating production state.</p></div>
      <div className="lp-review-actions"><Link href={`/failures/${String(failure.id)}`}>Inspect failure first →</Link><button className="lp-button" disabled={busy} onClick={() => void propose(String(failure.id))}>Generate and shadow-test correction</button></div>
    </section> : null}

    {correction?.status === "proposed" ? <section className="lp-section lp-card lp-demo-next">
      <div><div className="lp-card-label">Human approval required</div><h2>{String(record(correction.payload).summary ?? "Review the proposed artifact change")}</h2><p>Shadow gate: <strong>{correction.shadow_passed ? "passed" : "pending"}</strong>. No live artifact changes until approval.</p></div>
      <Link className="lp-button" href="/corrections">Review diff, blast radius, and approve →</Link>
    </section> : null}

    {baseline && patched ? <section className="lp-section">
      <h2 className="lp-section-title">Before / after verification</h2>
      <div className="lp-compare-grid">
        {[{label: "Baseline", run: baseline, decision: baselineDecision, evaluation: baselineEvaluation}, {label: "Patched", run: patched, decision: patchedDecision, evaluation: patchedEvaluation}].map(item => <article className="lp-card" key={item.label}>
          <div className="lp-review-heading"><div><div className="lp-card-label">{item.label}</div><h3>{String(item.decision.action ?? "pending")}</h3></div><span className="lp-pill" data-status={statusTone(item.evaluation)}>{item.evaluation}</span></div>
          <dl className="lp-key-values"><div><dt>Run</dt><dd className="lp-mono">{String(item.run.id)}</dd></div><div><dt>Evidence</dt><dd>{String(item.run.evidence_status ?? "pending")}</dd></div><div><dt>Fallback</dt><dd>{String(item.decision.fallback_used ?? false)}</dd></div></dl>
          <div className="lp-review-actions"><Link href={`/runs/${String(item.run.id)}`}>Open run evidence →</Link>{typeof item.run.weave_url === "string" ? <a href={item.run.weave_url} target="_blank" rel="noreferrer">Open Weave trace ↗</a> : null}</div>
        </article>)}
      </div>
      {improvement.improvement_proven === true ? <div className="lp-card lp-proof-banner"><strong>Improvement proven</strong><span>Baseline failed → patched passed</span><span>No regressed scores</span><span className="lp-mono">correction {String(improvement.correction_id)}</span></div> : null}
    </section> : null}

    <details className="lp-section lp-card"><summary className="lp-section-title">Durable engineering records</summary><RecordTable rows={demoRuns} columns={["id", "status", "kind", "parent_run_id", "correction_id", "manifest_id", "created_at"]} linkPrefixes={{id: "/runs/", parent_run_id: "/runs/"}} /></details>
  </>;
}
