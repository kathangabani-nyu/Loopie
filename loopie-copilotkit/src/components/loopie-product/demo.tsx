"use client";

import Link from "next/link";
import { useState } from "react";

import { RecordTable } from "./record-table";
import { useResource } from "./use-resource";

export function GoldenDemo() {
  const runs = useResource<Record<string, unknown>[]>("runs?limit=50", []);
  const failures = useResource<Record<string, unknown>[]>("failures?limit=50", []);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function start() {
    setBusy(true); setMessage(null);
    const response = await fetch("/api/loopie/v1/demo/start", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({confirm: "RESET_DEMO"}),
    });
    const payload = await response.json();
    setMessage(response.ok ? `Baseline queued: ${payload.run_id}` : payload.detail ?? payload.error ?? "Demo start failed");
    await Promise.all([runs.refresh(), failures.refresh()]);
    setBusy(false);
  }

  async function propose(id: string) {
    setBusy(true); setMessage("Generating and shadow-testing the correction. This can take up to two minutes; keep this page open.");
    const response = await fetch(`/api/loopie/v1/failures/${id}/corrections`, {method: "POST"});
    const payload = await response.json();
    setMessage(response.ok ? `Correction ${payload.id} passed shadow evaluation and is ready for review.` : payload.detail ?? payload.error ?? "Correction generation failed");
    await failures.refresh();
    setBusy(false);
  }

  const demoRuns = runs.data.filter(row => row.kind === "golden" || row.kind === "patched");
  const openFailure = failures.data.find(row => row.external_id === "security_001" && row.status === "open");
  return <>
    <header className="lp-header"><div><h1>Golden Demo</h1><p>Real failing baseline → Redis correction → human approval → identical rerun → deterministic improvement.</p></div><div className="lp-live">Weave required</div></header>
    <section className="lp-card"><p>Starting resets Loopie run/evidence state and restores the known missing-guard baseline. Seeded tickets and golden annotations remain intact.</p><button className="lp-button" disabled={busy} onClick={() => void start()}>{busy ? "Working…" : "Reset and run security_001 baseline"}</button>{message ? <p>{message}</p> : null}</section>
    {openFailure ? <section className="lp-card"><h2 className="lp-section-title">Baseline failure ready</h2><p>{String(openFailure.category)} / {String(openFailure.layer)}</p><button className="lp-button" disabled={busy} onClick={() => void propose(String(openFailure.id))}>Generate and shadow-test correction</button></section> : null}
    <section className="lp-section"><h2 className="lp-section-title">Demo runs</h2><RecordTable rows={demoRuns} columns={["id", "status", "kind", "parent_run_id", "correction_id", "created_at"]} linkPrefixes={{id: "/runs/", parent_run_id: "/runs/"}} /></section>
    <section className="lp-card"><h2 className="lp-section-title">Human approval boundary</h2><p>Review the artifact diff and shadow result before applying it.</p><Link href="/corrections">Open corrections →</Link></section>
  </>;
}
