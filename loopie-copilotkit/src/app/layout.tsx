import "./globals.css";
import "@copilotkit/react-core/v2/styles.css";

import { ThemeProvider } from "@/hooks/use-theme";

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <head>
        <title>Loopie</title>
        <link rel="icon" type="image/svg+xml" href="/loopie-mark.svg" />
      </head>
      <body className={`antialiased`}>
        <ThemeProvider>
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
