"use client";

import { FormEvent, useState } from "react";

import { RecordTable } from "./record-table";
import { useResource } from "./use-resource";

export function Policies() {
  const resource = useResource<Record<string, unknown>[]>("policies", []);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  async function compile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setMessage(null);
    const values = new FormData(event.currentTarget);
    const response = await fetch("/api/loopie/v1/policies/compile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_doc_ref: values.get("source_doc_ref"),
        source_text: values.get("source_text"),
      }),
    });
    const payload = await response.json();
    setMessage(response.ok
      ? `Compiled ${payload.proposal?.rule_id ?? payload.id}; review its dry-run on Corrections.`
      : payload.detail ?? payload.error ?? `request failed (${response.status})`);
    setSaving(false);
  }

  return <>
    <header className="lp-header"><div><h1>Policies</h1><p>Compile policy prose into the closed DSL, inspect deterministic impact, then approve it as an artifact.</p></div><div className="lp-live">Live updates</div></header>
    <section className="lp-card">
      <form className="lp-form" onSubmit={compile}>
        <label>Source reference<input name="source_doc_ref" required placeholder="handbook/refunds-v3" /></label>
        <label className="wide">Policy text<textarea name="source_text" required placeholder="Security-flagged accounts must never invoke the refund tool…" /></label>
        <button className="lp-button" disabled={saving}>{saving ? "Compiling…" : "Compile candidate rule"}</button>
      </form>
      {message ? <div className="lp-card">{message}</div> : null}
    </section>
    <RecordTable rows={resource.data} columns={["rule_id", "name", "status", "version", "when", "effects"]} />
  </>;
}
