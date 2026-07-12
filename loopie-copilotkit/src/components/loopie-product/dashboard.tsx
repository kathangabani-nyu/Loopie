"use client";

import { RecordTable } from "./record-table";
import { useResource } from "./use-resource";

export function Dashboard() {
  const tickets = useResource<Record<string, unknown>[]>("tickets?limit=100", []);
  const runs = useResource<Record<string, unknown>[]>("runs?limit=100", []);
  const failures = useResource<Record<string, unknown>[]>("failures?limit=100", []);
  const triage = useResource<Record<string, unknown>[]>("triage?limit=100", []);
  const running = runs.data.filter(run => run.status === "running" || run.status === "queued").length;
  const passed = runs.data.filter(run => run.status === "succeeded").length;
  return <>
    <header className="lp-header"><div><h1>Reliability overview</h1><p>Support-ticket decisions, deterministic failures, and human-approved artifact changes from one evidence ledger.</p></div><div className="lp-live">Live updates</div></header>
    <div className="lp-grid">
      <div className="lp-card"><div className="lp-card-label">Tickets</div><div className="lp-card-value">{tickets.data.length}</div></div>
      <div className="lp-card"><div className="lp-card-label">Queued / running</div><div className="lp-card-value">{running}</div></div>
      <div className="lp-card"><div className="lp-card-label">Completed</div><div className="lp-card-value">{passed}</div></div>
      <div className="lp-card"><div className="lp-card-label">Open failures</div><div className="lp-card-value">{failures.data.length}</div></div>
    </div>
    <section className="lp-section"><h2 className="lp-section-title">Recent runs</h2><RecordTable rows={runs.data.slice(0, 8)} columns={["id", "status", "mode", "kind", "ticket_id", "created_at"]} /></section>
    <section className="lp-section"><h2 className="lp-section-title">Failure queue</h2><RecordTable rows={failures.data.slice(0, 8)} columns={["id", "status", "layer", "category", "external_id", "created_at"]} /></section>
    {triage.error ? <div className="lp-error">Judge triage unavailable: {triage.error}</div> : null}
  </>;
}
