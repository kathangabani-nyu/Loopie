"use client";

import { useEffect } from "react";

import { useResource } from "./use-resource";

export function RunDetail({ runId }: { runId: string }) {
  const resource = useResource<Record<string, unknown> | null>(`runs/${runId}`, null);
  const { data, error, loading, waking, refresh } = resource;
  const active = data?.status === "queued" || data?.status === "running";

  useEffect(() => {
    if (!active) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh();
      if (!cancelled) timer = setTimeout(poll, 2_000);
    };
    timer = setTimeout(poll, 2_000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [active, refresh]);

  if (loading) return <div className="lp-empty">{waking ? "Free backend is waking; this can take about one minute…" : "Loading run evidence…"}</div>;
  if (error || !data) return <div className="lp-error">{error ?? "Run not found"}</div>;
  const decision = data.decision as Record<string, unknown> | null;
  const ticket = data.ticket_snapshot as Record<string, unknown> | null;
  const facts = (ticket?.facts ?? {}) as Record<string, unknown>;
  const evidenceStatus = String(data.evidence_status ?? decision?.evidence_status ?? "incomplete");
  const evaluationStatus = String(data.evaluation_status ?? "pending");
  const weaveUrl = typeof data.weave_url === "string" ? data.weave_url : null;
  const toolReceipts = (decision?.tool_receipts ?? []) as unknown[];
  return <>
    <header className="lp-header"><div><h1>Run evidence</h1><p className="lp-mono">{runId}</p></div><div style={{display: "flex", gap: 8, flexWrap: "wrap"}}><span className="lp-pill" data-status={String(data.status)}>execution {String(data.status)}</span><span className="lp-pill" data-status={evaluationStatus === "passed" ? "succeeded" : evaluationStatus}>checks {evaluationStatus}</span><span className="lp-pill" data-status={evidenceStatus === "complete" ? "succeeded" : "running"}>evidence {evidenceStatus}</span></div></header>
    <div className="lp-grid">
      <div className="lp-card"><div className="lp-card-label">Mode</div><div className="lp-card-value">{String(data.mode)}</div></div>
      <div className="lp-card"><div className="lp-card-label">Action</div><div className="lp-card-value">{String(decision?.action ?? "—")}</div></div>
      <div className="lp-card"><div className="lp-card-label">Manifest</div><div className="lp-mono">{String(data.manifest_id)}</div></div>
      <div className="lp-card"><div className="lp-card-label">Fallback</div><div className="lp-card-value">{String(decision?.fallback_used ?? false)}</div></div>
      <div className="lp-card"><div className="lp-card-label">Audit receipt</div><div className="lp-card-value">{String(data.audit_event_id ?? decision?.audit_event_id ?? "missing")}</div></div>
      <div className="lp-card"><div className="lp-card-label">Tokens / cost</div><div className="lp-card-value">{String(data.total_tokens ?? 0)} / ${Number(data.estimated_cost ?? 0).toFixed(6)}</div></div>
      <div className="lp-card"><div className="lp-card-label">Weave proof</div><div>{weaveUrl ? <a href={weaveUrl} target="_blank" rel="noreferrer">Open trace ↗</a> : "Incomplete — no trace link"}</div></div>
    </div>
    <section className="lp-section lp-card"><h2 className="lp-section-title">Pinned ticket facts</h2><div className="lp-grid"><div><div className="lp-card-label">Customer tier</div>{String(facts.customer_tier ?? "—")}</div><div><div className="lp-card-label">Purchase age</div>{String(facts.days_since_purchase ?? "—")} days</div><div><div className="lp-card-label">Amount</div>{facts.amount_minor == null ? "Missing" : `${String(facts.currency ?? "USD")} ${(Number(facts.amount_minor) / 100).toLocaleString(undefined, {minimumFractionDigits: 2})}`} ({String(facts.amount_source ?? "missing")})</div><div><div className="lp-card-label">Security flag</div>{String(facts.security_flag ?? false)}</div></div></section>
    <section className="lp-section lp-card"><h2 className="lp-section-title">Tool execution</h2><pre className="lp-mono">{JSON.stringify(toolReceipts, null, 2)}</pre></section>
    <details className="lp-section lp-card" open><summary className="lp-section-title">Correctness layers</summary><pre className="lp-mono">{JSON.stringify(decision?.correctness ?? {}, null, 2)}</pre></details>
    <details className="lp-section lp-card"><summary className="lp-section-title">Authoritative read set</summary><pre className="lp-mono">{JSON.stringify(decision?.read_set ?? [], null, 2)}</pre></details>
    <details className="lp-section lp-card"><summary className="lp-section-title">Raw trace receipts</summary><pre className="lp-mono">{JSON.stringify(decision?.trace ?? [], null, 2)}</pre></details>
  </>;
}
