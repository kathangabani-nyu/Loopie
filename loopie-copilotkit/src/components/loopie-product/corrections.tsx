"use client";

import { useState } from "react";

import { RecordTable } from "./record-table";
import { useResource } from "./use-resource";

export function Corrections() {
  const resource = useResource<Record<string, unknown>[]>("corrections", []);
  const [message, setMessage] = useState<string | null>(null);
  async function approve(id: string) {
    const response = await fetch(`/api/loopie/v1/corrections/${id}/approve`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor: "owner", channel: "ui" }),
    });
    const payload = await response.json();
    setMessage(response.ok ? `Applied ${id}; patched rerun ${payload.patched_run?.run_id ?? "not required"} queued.` : payload.detail ?? payload.error);
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
    <RecordTable rows={resource.data} columns={["id", "status", "kind", "failure_case", "base_artifact_version", "shadow_passed", "created_at"]} />
    <section className="lp-section"><h2 className="lp-section-title">Ready for approval</h2>
      <div className="lp-grid">{resource.data.filter(row => row.status === "proposed" && row.shadow_passed).map(row => (
        <div className="lp-card" key={String(row.id)}><div className="lp-mono">{String(row.id)}</div><p>{String(row.category ?? row.kind)}</p><div style={{display: "flex", gap: 8}}><button className="lp-button" onClick={() => void approve(String(row.id))}>Approve, apply, and rerun</button><button className="lp-button" onClick={() => void reject(String(row.id))}>Reject</button></div></div>
      ))}</div>
    </section>
  </>;
}
