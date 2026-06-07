"use client";

import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { ModeToggle } from "./mode-toggle";
import { useFrontendTool } from "@copilotkit/react-core/v2";
import { COPY } from "@/components/loopie-cockpit/constants";

interface ExampleLayoutProps {
  chatContent: ReactNode;
  appContent: ReactNode;
}

export function ExampleLayout({ chatContent, appContent }: ExampleLayoutProps) {
  const [mode, setMode] = useState<"chat" | "app">("app");
  const [chatCost, setChatCost] = useState(0);
  const [maxChatCost, setMaxChatCost] = useState(40);

  useEffect(() => {
    const load = () => {
      fetch("/api/loopie/state")
        .then((r) => (r.ok ? r.json() : null))
        .then((state) => {
          if (!state?.budget) return;
          setChatCost(Number(state.budget.chat_cost_usd ?? 0));
          setMaxChatCost(Number(state.budget.max_chat_cost_usd ?? 40));
        })
        .catch(() => {});
    };
    load();
    const iv = setInterval(load, 5000);
    return () => clearInterval(iv);
  }, []);

  useFrontendTool({
    name: "enableAppMode",
    description: "Enable app mode to show the Loopie reliability cockpit.",
    handler: async () => {
      setMode("app");
    },
  });

  useFrontendTool({
    name: "enableChatMode",
    description: "Enable chat mode",
    handler: async () => {
      setMode("chat");
    },
  });

  return (
    <div className="h-full min-h-0 flex flex-row">
      <ModeToggle mode={mode} onModeChange={setMode} />

      <div
        className={`max-h-full flex flex-col dark:bg-stone-950 ${
          mode === "app" ? "w-1/3 px-6 max-lg:hidden" : "flex-1 max-lg:px-4"
        }`}
      >
        <div className="shrink-0 pt-6 pl-6 pb-2 max-lg:pl-6 max-lg:pt-2.5 max-lg:pb-0 flex gap-2 items-center align-center flex-wrap">
          <div className="flex gap-1.5 items-center">
            <span className="font-extrabold text-2xl pb-1.5 max-lg:pb-0">Loopie Copilot</span>
            <img src="/copilotkit-logo-mark.svg" alt="CopilotKit" className="h-6 opacity-70" />
          </div>
          <span className="text-xs text-stone-500">powered by CopilotKit</span>
          <span className="text-xs font-mono rounded-full border border-stone-700 px-2 py-0.5 text-stone-300">
            live · {COPY.chatModel} · ${chatCost.toFixed(2)} / ${maxChatCost.toFixed(0)}
          </span>
        </div>
        <div className="flex-1 min-h-0 overflow-y-auto">{chatContent}</div>
      </div>

      <div
        className={`h-full min-w-0 overflow-auto ${
          mode === "app"
            ? "w-2/3 max-lg:w-full border-l border-[var(--border)] max-lg:border-l-0"
            : "w-0 border-l-0"
        }`}
      >
        <div className="w-full min-w-0 h-full">{appContent}</div>
      </div>
    </div>
  );
}
