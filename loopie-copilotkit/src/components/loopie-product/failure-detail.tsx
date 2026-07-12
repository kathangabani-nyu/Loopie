"use client";

import Link from "next/link";
import { useState } from "react";

import { useResource } from "./use-resource";

export function FailureDetail({ failureId }: { failureId: string }) {
  const resource = useResource<Record<string, unknown> | null>(`failures/${failureId}`, null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function propose() {
    setBusy(true); setMessage(null);
    const response = await fetch(`/api/loopie/v1/failures/${failureId}/corrections`, {method: "POST"});
    const payload = await response.json();
    setMessage(response.ok ? `Correction ${payload.id} is ready for human review.` : payload.detail ?? payload.error ?? "Correction generation failed");
    await resource.refresh(); setBusy(false);
  }
  if (resource.loading) return <div className="lp-empty">{resource.waking ? "Free backend is waking; this can take about one minute…" : "Loading failure…"}</div>;
  if (resource.error || !resource.data) return <div className="lp-error">{resource.error ?? "Failure not found"}</div>;
  const row = resource.data;
  return <>
    <header className="lp-header"><div><h1>Failure evidence</h1><p className="lp-mono">{failureId}</p></div><span className="lp-pill" data-status={String(row.status)}>{String(row.status)}</span></header>
    <div className="lp-grid"><div className="lp-card"><div className="lp-card-label">Layer</div><div className="lp-card-value">{String(row.layer)}</div></div><div className="lp-card"><div className="lp-card-label">Category</div><div className="lp-card-value">{String(row.category)}</div></div><div className="lp-card"><div className="lp-card-label">Run</div><Link className="lp-mono" href={`/runs/${String(row.run_id)}`}>{String(row.run_id)}</Link></div></div>
    <section className="lp-card"><button className="lp-button" disabled={busy || row.status !== "open"} onClick={() => void propose()}>{busy ? "Generating…" : "Generate and shadow-test correction"}</button>{message ? <p>{message}</p> : null}{message ? <Link href="/corrections">Open human review →</Link> : null}</section>
    <details className="lp-card" open><summary className="lp-section-title">Diagnosis</summary><pre className="lp-mono">{JSON.stringify(row.diagnosis ?? row.scores ?? {}, null, 2)}</pre></details>
  </>;
}
