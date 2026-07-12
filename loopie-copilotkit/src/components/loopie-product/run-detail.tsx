"use client";

import { useEffect } from "react";

import { useResource } from "./use-resource";

export function RunDetail({ runId }: { runId: string }) {
  const resource = useResource<Record<string, unknown> | null>(`runs/${runId}`, null);
  const { data, error, loading, refresh } = resource;
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

  if (loading) return <div className="lp-empty">Loading…</div>;
  if (error || !data) return <div className="lp-error">{error ?? "Run not found"}</div>;
  const decision = data.decision as Record<string, unknown> | null;
  return <>
    <header className="lp-header"><div><h1>Run evidence</h1><p className="lp-mono">{runId}</p></div><span className="lp-pill" data-status={String(data.status)}>{String(data.status)}</span></header>
    <div className="lp-grid">
      <div className="lp-card"><div className="lp-card-label">Mode</div><div className="lp-card-value">{String(data.mode)}</div></div>
      <div className="lp-card"><div className="lp-card-label">Action</div><div className="lp-card-value">{String(decision?.action ?? "—")}</div></div>
      <div className="lp-card"><div className="lp-card-label">Manifest</div><div className="lp-mono">{String(data.manifest_id)}</div></div>
      <div className="lp-card"><div className="lp-card-label">Fallback</div><div className="lp-card-value">{String(decision?.fallback_used ?? false)}</div></div>
    </div>
    <section className="lp-section lp-card"><h2 className="lp-section-title">Correctness layers</h2><pre className="lp-mono">{JSON.stringify(decision?.correctness ?? {}, null, 2)}</pre></section>
    <section className="lp-section lp-card"><h2 className="lp-section-title">Authoritative read set</h2><pre className="lp-mono">{JSON.stringify(decision?.read_set ?? [], null, 2)}</pre></section>
    <section className="lp-section lp-card"><h2 className="lp-section-title">Trace receipts</h2><pre className="lp-mono">{JSON.stringify(decision?.trace ?? [], null, 2)}</pre></section>
  </>;
}
