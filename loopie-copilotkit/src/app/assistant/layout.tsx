"use client";

import { CopilotKit } from "@copilotkit/react-core/v2";

export default function AssistantLayout({ children }: { children: React.ReactNode }) {
  return (
    <CopilotKit
      runtimeUrl="/api/copilotkit"
      agent="loopie_control"
      showDevConsole={false}
      useSingleEndpoint={false}
    >
      {children}
    </CopilotKit>
  );
}
