import { z } from "zod";
import { useTheme } from "@/hooks/use-theme";

import { useFrontendTool, useDefaultRenderTool } from "@copilotkit/react-core/v2";

import { ToolReasoning } from "@/components/tool-rendering";

export const useGenerativeUIExamples = () => {
  const { setTheme } = useTheme();

  const ignoredTools = [
    "render_a2ui",
    "generate_a2ui",
    "log_a2ui_event",
    "runBaseline",
    "proposeCorrection",
    "approveCorrection",
    "rerunCompare",
    "counterfactualReplay",
    "resetLoopieDemo",
  ];
  useDefaultRenderTool({
    render: ({ name, status, parameters }) => {
      if (ignoredTools.includes(name)) return <></>;
      return <ToolReasoning name={name} status={status} args={parameters} />;
    },
  });

  useFrontendTool({
    name: "toggleTheme",
    description: "Toggle the app theme between light and dark.",
    parameters: z.object({}),
    handler: async () => {
      const isDark = document.documentElement.classList.contains("dark");
      setTheme(isDark ? "light" : "dark");
    },
  });
};
