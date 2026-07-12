"use client";

import "./globals.css";
import "@copilotkit/react-core/v2/styles.css";

import { CopilotKit } from "@copilotkit/react-core/v2";
import { usePathname } from "next/navigation";
import { ThemeProvider } from "@/hooks/use-theme";

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();

  return (
    <html lang="en">
      <head>
        <title>Loopie</title>
        <link rel="icon" type="image/svg+xml" href="/loopie-mark.svg" />
      </head>
      <body className={`antialiased`}>
        <ThemeProvider>
          {pathname === "/login" ? children : (
            <CopilotKit
              runtimeUrl="/api/copilotkit"
              agent="loopie_control"
              showDevConsole={false}
              useSingleEndpoint={false}
            >
              {children}
            </CopilotKit>
          )}
        </ThemeProvider>
      </body>
    </html>
  );
}
