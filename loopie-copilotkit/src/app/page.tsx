"use client";

import { useState } from "react";

import { ExampleLayout } from "@/components/example-layout";
import { LoopieCockpit } from "@/components/loopie-cockpit";
import { useGenerativeUIExamples, useExampleSuggestions } from "@/hooks";

import {
  CopilotChat,
  CopilotChatConfigurationProvider,
} from "@copilotkit/react-core/v2";

export default function HomePage() {
  useGenerativeUIExamples();
  useExampleSuggestions();

  const [threadId] = useState<string | undefined>(undefined);

  return (
    <div className="h-full min-h-0 flex flex-col">
      <CopilotChatConfigurationProvider agentId="default" threadId={threadId}>
        <ExampleLayout
          chatContent={
            <CopilotChat
              attachments={{ enabled: true }}
              input={{ disclaimer: () => null, className: "pb-6" }}
            />
          }
          appContent={<LoopieCockpit />}
        />
      </CopilotChatConfigurationProvider>
    </div>
  );
}
