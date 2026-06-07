import { useConfigureSuggestions } from "@copilotkit/react-core/v2";

export const useExampleSuggestions = () => {
  useConfigureSuggestions({
    suggestions: [
      {
        title: "Run baseline",
        message: "Run the baseline on the primary case (security_001).",
      },
      {
        title: "Why did it fail?",
        message: "Why did the primary case fail on the baseline run? Cite scorer results.",
      },
      {
        title: "Propose a fix",
        message: "Propose a structured correction for the current failure.",
      },
      {
        title: "Artifact diff",
        message: "Walk me through the artifact diff for the proposed correction.",
      },
      {
        title: "Counterfactual replay",
        message: "Run the counterfactual replay suite on the primary case.",
      },
    ],
    available: "always",
  });
};
