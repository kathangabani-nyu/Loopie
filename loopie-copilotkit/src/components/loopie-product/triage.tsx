"use client";

import Link from "next/link";

import { useResource } from "./use-resource";
import { RecordTable } from "./record-table";

export function Triage() {
  const failures = useResource<Record<string, unknown>[]>("failures?limit=100", []);
  const judgeItems = useResource<Record<string, unknown>[]>("triage?limit=100", []);
  const openFailures = failures.data.filter(row => row.status === "open" || row.status === "proposed");
  const openJudgeItems = judgeItems.data.filter(row => row.status === "open" && !row.calibration_sample);

  async function resolve(id: string, decision: "confirm" | "reject") {
    const expectedAction = decision === "confirm" ? window.prompt("Confirmed action from the project taxonomy") : null;
    if (decision === "confirm" && !expectedAction) return;
    await fetch(`/api/loopie/v1/triage/${id}/resolve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ decision, actor: "owner", expected_action: expectedAction }),
    });
    await judgeItems.refresh();
  }

  return <>
    <header className="lp-header"><div><h1>Reliability triage</h1><p>Deterministic failures require action. Advisory judge flags remain outside authoritative pass/fail.</p></div><div className="lp-live">Live updates</div></header>

    {failures.error ? <div className="lp-error">Failure queue unavailable: {failures.error}</div> : null}
    <section className="lp-section">
      <div className="lp-review-heading"><div><div className="lp-card-label">Authoritative</div><h2 className="lp-section-title">Failure queue</h2></div><span className="lp-pill" data-status={openFailures.length ? "failed" : "succeeded"}>{openFailures.length} active</span></div>
      <RecordTable rows={failures.data} columns={["id", "status", "layer", "category", "external_id", "created_at"]} linkPrefixes={{id: "/failures/"}} />
      {openFailures.length ? <div className="lp-grid lp-section">{openFailures.map(row => (
        <div className="lp-card" key={String(row.id)}>
          <div className="lp-card-label">{String(row.layer)} failure</div>
          <h3>{String(row.external_id)}</h3>
          <p>{String(row.category).replaceAll("_", " ")}</p>
          <Link href={`/failures/${String(row.id)}`}>Inspect evidence and correction →</Link>
        </div>
      ))}</div> : null}
    </section>

    {judgeItems.error ? <div className="lp-error">Advisory judge triage unavailable: {judgeItems.error}</div> : null}
    <section className="lp-section">
      <div className="lp-review-heading"><div><div className="lp-card-label">Optional advisory layer</div><h2 className="lp-section-title">Judge flags</h2></div><span className="lp-pill" data-status={openJudgeItems.length ? "pending" : "succeeded"}>{openJudgeItems.length} open</span></div>
      <p>These records appear only when the advisory judge is enabled. Golden calibration samples are listed for audit but do not change run correctness.</p>
      <RecordTable rows={judgeItems.data} columns={["id", "status", "external_id", "confidence", "judge_verdict", "resolution", "calibration_sample", "created_at"]} />
      {openJudgeItems.length ? <div className="lp-grid lp-section">{openJudgeItems.map(row => (
        <div className="lp-card" key={String(row.id)}><div className="lp-mono">{String(row.external_id)}</div><p>{JSON.stringify(row.judge_verdict)}</p><div style={{display: "flex", gap: 8}}><button className="lp-button" onClick={() => void resolve(String(row.id), "confirm")}>Confirm</button><button className="lp-button" onClick={() => void resolve(String(row.id), "reject")}>Reject</button></div></div>
      ))}</div> : null}
    </section>
  </>;
}
