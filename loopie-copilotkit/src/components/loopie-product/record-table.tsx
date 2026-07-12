"use client";

import Link from "next/link";

function display(value: unknown): string {
  if (value == null) return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function RecordTable({ rows, columns, linkPrefixes = {} }: { rows: Record<string, unknown>[]; columns: string[]; linkPrefixes?: Record<string, string> }) {
  if (!rows.length) return <div className="lp-empty">No records yet.</div>;
  return (
    <div className="lp-table-wrap">
      <table className="lp-table">
        <thead><tr>{columns.map(column => <th key={column}>{column.replaceAll("_", " ")}</th>)}</tr></thead>
        <tbody>{rows.map((row, index) => (
          <tr key={String(row.id ?? index)}>{columns.map(column => {
            const value = row[column];
            const status = column === "status" ? String(value) : undefined;
            return <td key={column} className={column.endsWith("id") ? "lp-mono" : undefined}>
              {status ? <span className="lp-pill" data-status={status}>{status}</span> : linkPrefixes[column] && value != null ? <Link href={`${linkPrefixes[column]}${String(value)}`}>{display(value)}</Link> : display(value)}
            </td>;
          })}</tr>
        ))}</tbody>
      </table>
    </div>
  );
}
