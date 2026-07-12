"use client";

import { CopilotChat } from "@copilotkit/react-core/v2";

export function Assistant() {
  return <>
    <header className="lp-header"><div><h1>Reliability assistant</h1><p>Investigate evidence and prepare corrections. Artifact application still requires explicit human approval.</p></div></header>
    <div className="lp-card" style={{ height: "70vh", padding: 0, overflow: "hidden" }}>
      <CopilotChat input={{ disclaimer: () => null }} />
    </div>
  </>;
}
