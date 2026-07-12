"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { RecordTable } from "./record-table";
import { useResource } from "./use-resource";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function list(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function Corrections() {
  const resource = useResource<Record<string, unknown>[]>("corrections", []);
  const [message, setMessage] = useState<string | null>(null);
  const [patchedRun, setPatchedRun] = useState<string | null>(null);
  const [reviewed, setReviewed] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (resource.data.length > 0) return;
    const timer = window.setInterval(() => void resource.refresh(), 3_000);
    return () => window.clearInterval(timer);
  }, [resource.data.length, resource.refresh]);
  async function approve(id: string) {
    const response = await fetch(`/api/loopie/v1/corrections/${id}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: "owner", channel: "ui" }),
    });
    const payload = await response.json();
    const runId = payload.patched_run?.run_id ? String(payload.patched_run.run_id) : null;
    setPatchedRun(response.ok ? runId : null);
    setMessage(response.ok ? `Applied ${id}; patched rerun ${runId ?? "not required"} queued.` : payload.detail ?? payload.error);
    setReviewed(current => ({ ...current, [id]: false }));
    await resource.refresh();
  }
  async function reject(id: string) {
    const response = await fetch(`/api/loopie/v1/corrections/${id}/reject`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: "owner", channel: "ui" }),
    });
    const payload = await response.json();
    setMessage(response.ok ? `Rejected ${id}; artifacts were not changed.` : payload.detail ?? payload.error);
    await resource.refresh();
  }
  return <>
    <header className="lp-header"><div><h1>Corrections</h1><p>Only passing shadow proposals can cross the human approval boundary.</p></div><div className="lp-live">Live updates</div></header>
    {message ? <div className="lp-card">{message}</div> : null}
    {patchedRun ? <div className="lp-card"><Link href={`/runs/${patchedRun}`}>Open patched run →</Link></div> : null}
    <RecordTable rows={resource.data} columns={["id", "status", "kind", "failure_case", "base_artifact_version", "shadow_passed", "created_at"]} />
    <section className="lp-section"><h2 className="lp-section-title">Ready for approval</h2>
      <div className="lp-review-list">{resource.data.filter(row => row.status === "proposed" && row.shadow_passed).map(row => {
        const id = String(row.id);
        const payload = record(row.payload);
        const proposal = record(row.proposal);
        const blastRadius = record(row.blast_radius);
        const diff = record(list(row.diff)[0]);
        const ticketIds = list(blastRadius.ticket_ids);
        return <article className="lp-card lp-review-card" key={id}>
          <div className="lp-review-heading">
            <div><div className="lp-card-label">Proposed correction</div><h3>{String(payload.summary ?? row.category ?? row.kind)}</h3><div className="lp-mono">{id}</div></div>
            <span className="lp-pill" data-status="succeeded">Shadow passed</span>
          </div>

          <div className="lp-review-facts">
            <div><div className="lp-card-label">Failure</div><strong>{String(row.failure_case)}</strong><div>{String(row.category)}</div><Link href={`/failures/${String(row.failure_id)}`}>Inspect baseline evidence →</Link></div>
            <div><div className="lp-card-label">Durable artifact</div><strong>{String(payload.artifact_key ?? blastRadius.artifact_key)}</strong><div>Base version {String(row.base_artifact_version)}</div></div>
            <div><div className="lp-card-label">Blast radius</div><strong>{ticketIds.length} affected ticket{ticketIds.length === 1 ? "" : "s"}</strong><div>{String(blastRadius.source ?? "shadow evaluation")}</div></div>
            <div><div className="lp-card-label">Shadow evaluation</div><strong>No regression detected</strong><div className="lp-mono">{String(row.shadow_eval_run_id)}</div></div>
          </div>

          <section className="lp-review-rule">
            <div className="lp-card-label">Behavior being introduced</div>
            <p><strong>When</strong> <code>{String(proposal.condition ?? "the rule matches")}</code>, require <code>{String(proposal.required_action ?? proposal.rule ?? "the proposed action")}</code>.</p>
          </section>

          <div className="lp-diff-grid">
            <div><div className="lp-card-label">Before</div><pre className="lp-mono lp-diff-before">{JSON.stringify(diff.before ?? null, null, 2)}</pre></div>
            <div><div className="lp-card-label">After</div><pre className="lp-mono lp-diff-after">{JSON.stringify(diff.after ?? payload.value ?? null, null, 2)}</pre></div>
          </div>

          <label className="lp-review-confirm">
            <input type="checkbox" checked={Boolean(reviewed[id])} onChange={event => setReviewed(current => ({ ...current, [id]: event.target.checked }))} />
            I reviewed the failure evidence, artifact diff, blast radius, and passing shadow result.
          </label>
          <div className="lp-review-actions">
            <button className="lp-button" disabled={!reviewed[id]} onClick={() => void approve(id)}>Approve, apply, and rerun</button>
            <button className="lp-button lp-button-secondary" onClick={() => void reject(id)}>Reject without changing artifacts</button>
          </div>
        </article>;
      })}</div>
    </section>
  </>;
}
