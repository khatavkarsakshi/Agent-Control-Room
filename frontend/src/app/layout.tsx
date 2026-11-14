import type { Metadata, Viewport } from "next";

import "./globals.css";

import { ApiStatus } from "@/components/ApiStatus";
import { LogoIcon } from "@/components/icons";
import { Sidebar } from "@/components/Sidebar";

export const metadata: Metadata = {
  title: {
    default: "Agent Control Room",
    template: "%s · Agent Control Room",
  },
  description:
    "Observability and control plane for AI agents: runs, traces, tokens and cost per step.",
  applicationName: "Agent Control Room",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#070b12",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <div className="min-h-screen lg:grid lg:grid-cols-[248px_minmax(0,1fr)]">
          <Sidebar />

          <div className="flex min-w-0 flex-col">
            <header className="sticky top-0 z-30 flex h-16 items-center justify-between gap-4 border-b border-white/5 bg-ink-950/80 px-4 backdrop-blur-md lg:px-8">
              <div className="flex items-center gap-2 lg:hidden">
                <LogoIcon className="h-5 w-5 text-accent" />
                <span className="text-sm font-semibold text-slate-100">Agent Control Room</span>
              </div>
              <p className="hidden text-xs text-slate-500 lg:block">
                Every LLM call, tool invocation and data access — with tokens and cost.
              </p>
              <ApiStatus />
            </header>

            <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-8 lg:px-8">{children}</main>

            <footer className="border-t border-white/5 px-4 py-4 text-[11px] text-slate-600 lg:px-8">
              Agent Control Room · Phase 3 dashboard · Costs are estimates based on provider
              list prices.
            </footer>
          </div>
        </div>
      </body>
    </html>
  );
}
