import type { Metadata } from "next";
import "./globals.css";
import { AppShell } from "@/components/AppShell";

export const metadata: Metadata = {
  title: "Relay | Resilient LLM Gateway Dashboard",
  description:
    "Live control dashboard, failover observability, and chaos testing for the Relay LLM Gateway.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-background text-gray-100 antialiased">
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}

