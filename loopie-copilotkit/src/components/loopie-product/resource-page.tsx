"use client";

import { RecordTable } from "./record-table";
import { useResource } from "./use-resource";

export function ResourcePage({ title, description, path, columns }: { title: string; description: string; path: string; columns: string[] }) {
  const { data, error, loading } = useResource<Record<string, unknown>[]>(path, []);
  return <>
    <header className="lp-header"><div><h1>{title}</h1><p>{description}</p></div><div className="lp-live">Live updates</div></header>
    {error ? <div className="lp-error">{error}</div> : loading ? <div className="lp-empty">Loading…</div> : <RecordTable rows={data} columns={columns} />}
  </>;
}
