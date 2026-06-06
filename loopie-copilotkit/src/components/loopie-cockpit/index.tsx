"use client";

import { useCallback, useEffect, useState } from "react";

type LoopieState = {
  runs?: Record<string, unknown>;
  currentFailure?: { case_id?: string; category?: string; scores?: Record<string, boolean> } | null;
  proposedCorrections?: Array<{ id?: string; summary?: string; proposal?: Record<string, unknown> }>;
  artifactHistory?: Array<Record<string, unknown>>;
  evalDelta?: Record<string, unknown>;
  counterfactual?: { no_regression?: boolean; newly_failing?: string[] };
  events?: Array<Record<string, unknown>>;
  budget?: Record<string, unknown>;
  approvalState?: string;
};

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-lg border border-[--border] bg-[--card] p-4">
      <h3 className="text-sm font-semibold mb-2">{title}</h3>
      {children}
    </section>
  );
}

async function post(action: string, body: Record<string, unknown> = {}) {
  const res = await fetch(`/api/loopie/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return res.json();
}

export function LoopieCockpit() {
  const [state, setState] = useState<LoopieState>({});
  const [loading, setLoading] = useState(false);
  const [lastResult, setLastResult] = useState<Record<string, unknown> | null>(null);

  const refresh = useCallback(async () => {
    const res = await fetch("/api/loopie/state");
    if (res.ok) setState(await res.json());
  }, []);

  useEffect(() => {
    refresh().catch(() => undefined);
  }, [refresh]);

  const run = async (action: string, body: Record<string, unknown> = {}) => {
    setLoading(true);
    try {
      const result = await post(action, body);
      setLastResult(result);
      await refresh();
    } finally {
      setLoading(false);
    }
  };

  const failure = state.currentFailure;
  const proposal = state.proposedCorrections?.[0];

  return (
    <div className="h-full overflow-y-auto bg-[--background] p-6 space-y-4">
      <header className="flex flex-wrap gap-2">
        <button disabled={loading} className="px-3 py-1.5 rounded-md bg-blue-600 text-white text-sm" onClick={() => run("seed")}>Seed</button>
        <button disabled={loading} className="px-3 py-1.5 rounded-md bg-slate-700 text-white text-sm" onClick={() => run("baseline", { case_id: "security_001" })}>Run Baseline</button>
        <button disabled={loading} className="px-3 py-1.5 rounded-md bg-amber-600 text-white text-sm" onClick={() => run("propose")}>Propose</button>
        <button disabled={loading || !proposal?.id} className="px-3 py-1.5 rounded-md bg-green-600 text-white text-sm" onClick={() => proposal?.id && run("approve", { correction_id: proposal.id })}>Approve</button>
        <button disabled={loading} className="px-3 py-1.5 rounded-md bg-indigo-600 text-white text-sm" onClick={() => run("patched", { case_id: "security_001" })}>Rerun + Compare</button>
        <button disabled={loading} className="px-3 py-1.5 rounded-md bg-purple-600 text-white text-sm" onClick={() => run("counterfactual", { hero_case_id: "security_001" })}>Counterfactual Replay</button>
      </header>

      {lastResult && (
        <Panel title="Last Action Result">
          <pre className="text-xs bg-black/5 p-2 rounded overflow-x-auto max-h-32">{JSON.stringify(lastResult, null, 2)}</pre>
        </Panel>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
        <Panel title="Event Stream">
          <ul className="text-xs space-y-1 max-h-48 overflow-y-auto font-mono">
            {(state.events || []).slice(-20).map((evt, i) => (
              <li key={i}>{JSON.stringify(evt)}</li>
            ))}
            {!state.events?.length && <li className="text-muted-foreground">No events yet.</li>}
          </ul>
        </Panel>

        <Panel title="Failure Card">
          {failure ? (
            <div className="text-sm space-y-1">
              <p><strong>Case:</strong> {failure.case_id}</p>
              <p><strong>Genome:</strong> {failure.category}</p>
              <pre className="text-xs bg-black/5 p-2 rounded">{JSON.stringify(failure.scores, null, 2)}</pre>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Run baseline to load failure.</p>
          )}
        </Panel>

        <Panel title="Correction Diff">
          {proposal ? (
            <div className="text-sm space-y-1">
              <p>{proposal.summary}</p>
              <pre className="text-xs bg-black/5 p-2 rounded">{JSON.stringify(proposal.proposal, null, 2)}</pre>
              <p className="text-xs">Approval: {state.approvalState || "idle"}</p>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">Propose a correction after baseline failure.</p>
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
        <Panel title="Eval Delta">
          <pre className="text-xs bg-black/5 p-2 rounded">{JSON.stringify(state.evalDelta || {}, null, 2)}</pre>
        </Panel>
        <Panel title="Counterfactual / No Regression">
          <pre className="text-xs bg-black/5 p-2 rounded">{JSON.stringify(state.counterfactual || {}, null, 2)}</pre>
        </Panel>
      </div>

      <Panel title="Artifact Time Machine">
        <pre className="text-xs bg-black/5 p-2 rounded max-h-40 overflow-y-auto">{JSON.stringify(state.artifactHistory || [], null, 2)}</pre>
      </Panel>

      <Panel title="Budget Meter">
        <pre className="text-xs bg-black/5 p-2 rounded">{JSON.stringify(state.budget || {}, null, 2)}</pre>
      </Panel>
    </div>
  );
}

export { LoopieCockpit as Cockpit };
