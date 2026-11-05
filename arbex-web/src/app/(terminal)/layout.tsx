import React from "react";
import { LiveProvider } from "@/components/shell/LiveProvider";
import { TopBar } from "@/components/shell/TopBar";
import { TickerStrip } from "@/components/dashboard/TickerStrip";

/** Terminal shell for every view: one live connection, the top bar, the venue ticker. */
export default function TerminalLayout({ children }: { children: React.ReactNode }) {
  return (
    <LiveProvider>
      <div className="flex h-screen min-h-0 flex-col overflow-hidden bg-canvas text-ink">
        <TopBar />
        <TickerStrip />
        <main className="min-h-0 flex-1 overflow-hidden">{children}</main>
      </div>
    </LiveProvider>
  );
}
