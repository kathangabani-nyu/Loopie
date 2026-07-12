"use client";

import { FormEvent, useState } from "react";

import { RecordTable } from "./record-table";
import { useResource } from "./use-resource";

export function Tickets() {
  const resource = useResource<Record<string, unknown>[]>("tickets?limit=200", []);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [importing, setImporting] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setSaving(true); setError(null);
    const values = new FormData(event.currentTarget);
    const externalId = String(values.get("external_id"));
    const amountText = String(values.get("amount") ?? "").trim();
    const amountMinor = amountText === "" ? null : Math.round(Number(amountText) * 100);
    const response = await fetch("/api/loopie/v1/tickets", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        external_id: externalId,
        subject: values.get("subject"),
        body: values.get("body"),
        channel: "ui",
        customer_tier: values.get("customer_tier") || "standard",
        days_since_purchase: Number(values.get("days_since_purchase") || 0),
        security_flag: values.get("security_flag") === "on",
        amount_minor: amountMinor,
        currency: String(values.get("currency") || "USD").toUpperCase(),
        amount_source: amountMinor == null ? "missing" : "explicit",
        metadata: {},
        tags: ["support"], auto_evaluate: true,
      }),
    });
    if (!response.ok) setError((await response.json()).error ?? `request failed (${response.status})`);
    else { event.currentTarget.reset(); await resource.refresh(); }
    setSaving(false);
  }
  async function importDocument(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setImporting(true); setError(null);
    const input = event.currentTarget.elements.namedItem("document") as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) { setError("Choose a .csv or .jsonl file."); setImporting(false); return; }
    const format = file.name.toLowerCase().endsWith(".csv") ? "csv" : "jsonl";
    const response = await fetch("/api/loopie/v1/tickets/import", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ format, content: await file.text() }),
    });
    if (!response.ok) setError((await response.json()).detail ?? `request failed (${response.status})`);
    else { event.currentTarget.reset(); await resource.refresh(); }
    setImporting(false);
  }
  return <>
    <header className="lp-header"><div><h1>Tickets</h1><p>Ingest a real support ticket. It is queued immediately against a pinned artifact manifest.</p></div><div className="lp-live">Live updates</div></header>
    <section className="lp-card">
      <form className="lp-form" onSubmit={submit}>
        <label>External ID<input name="external_id" required defaultValue={`ticket-${Date.now()}`} /></label>
        <label>Subject<input name="subject" required placeholder="Refund request" /></label>
        <label className="wide">Message<textarea name="body" required placeholder="Customer message…" /></label>
        <label>Customer tier<select name="customer_tier" defaultValue="standard"><option value="standard">standard</option><option value="enterprise">enterprise</option><option value="trial">trial</option></select></label>
        <label>Days since purchase<input name="days_since_purchase" type="number" min="0" defaultValue="0" /></label>
        <label>Amount<input name="amount" type="number" min="0" step="0.01" placeholder="12450.00" /></label>
        <label>Currency<input name="currency" defaultValue="USD" maxLength={3} /></label>
        <label><span>Security flag</span><input name="security_flag" type="checkbox" /></label>
        <button className="lp-button" disabled={saving}>{saving ? "Queuing…" : "Import and evaluate"}</button>
      </form>
      {error ? <div className="lp-error">{error}</div> : null}
    </section>
    <section className="lp-card">
      <form className="lp-form" onSubmit={importDocument}>
        <label className="wide">Bulk import (.csv or .jsonl)<input name="document" type="file" accept=".csv,.jsonl,application/x-ndjson,text/csv" required /></label>
        <button className="lp-button" disabled={importing}>{importing ? "Importing…" : "Import up to 500 tickets"}</button>
      </form>
    </section>
    <section className="lp-section"><RecordTable rows={resource.data} columns={["external_id", "subject", "channel", "version", "tags", "created_at"]} /></section>
  </>;
}
