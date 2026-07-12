"use client";

import { useResource } from "./use-resource";
import { RecordTable } from "./record-table";

export function Triage() {
  const resource = useResource<Record<string, unknown>[]>("triage", []);
  async function resolve(id: string, decision: "confirm" | "reject") {
    const expectedAction = decision === "confirm" ? window.prompt("Confirmed action from the project taxonomy") : null;
    if (decision === "confirm" && !expectedAction) return;
    await fetch(`/api/loopie/v1/triage/${id}/resolve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, actor: "owner", expected_action: expectedAction }),
    });
    await resource.refresh();
  }
  return <>
    <header className="lp-header"><div><h1>Judge triage</h1><p>Advisory flags remain outside pass/fail until a human resolves them.</p></div><div className="lp-live">Live updates</div></header>
    <RecordTable rows={resource.data} columns={["id", "status", "external_id", "confidence", "judge_verdict", "resolution", "created_at"]} />
    <section className="lp-section"><div className="lp-grid">{resource.data.filter(row => row.status === "open" && !row.calibration_sample).map(row => (
      <div className="lp-card" key={String(row.id)}><div className="lp-mono">{String(row.external_id)}</div><p>{JSON.stringify(row.judge_verdict)}</p><div style={{display: "flex", gap: 8}}><button className="lp-button" onClick={() => void resolve(String(row.id), "confirm")}>Confirm</button><button className="lp-button" onClick={() => void resolve(String(row.id), "reject")}>Reject</button></div></div>
    ))}</div></section>
  </>;
}
