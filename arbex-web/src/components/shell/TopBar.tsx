"use client";

import React from "react";
import { useNow } from "@/hooks/useNow";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useArbexStore } from "@/lib/store";
import { PairPicker } from "@/components/dashboard/PairPicker";
import { Chip, Countdown } from "@/components/terminal/primitives";
import { SEALED_UNTIL } from "@/lib/schedule";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/", label: "Desk" },
  { href: "/risk", label: "Risk" },
  { href: "/research", label: "Research" },
  { href: "/health", label: "Health" },
];

function Clock() {
  const now = useNow();
  const fmt = (tz: string) =>
    now ? new Date(now).toLocaleTimeString("en-GB", { timeZone: tz, hour12: false }) : "--:--:--";
  return (
    <div className="hidden items-center gap-3 lg:flex">
      <span className="num text-[11px] text-ink-2"><span className="label-caps mr-1">IST</span>{fmt("Asia/Kolkata")}</span>
      <span className="num text-[11px] text-ink-3"><span className="label-caps mr-1">UTC</span>{fmt("UTC")}</span>
    </div>
  );
}

export function TopBar() {
  const pathname = usePathname();
  const isConnected = useArbexStore((s) => s.isConnected);
  const mode = useArbexStore((s) => s.feedStatus?.mode ?? null);
  const modeTone = mode === "live" ? "up" : mode === "replay" ? "info" : "muted";

  return (
    <header className="flex h-11 shrink-0 items-center justify-between gap-3 border-b border-line bg-panel px-3">
      <div className="flex min-w-0 items-center gap-4">
        <Link href="/" className="flex items-center gap-2">
          <span className="grid h-5 w-5 place-items-center rounded-term bg-info/15 text-info">
            <svg viewBox="0 0 24 24" className="h-3.5 w-3.5 fill-current" aria-hidden>
              <path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" />
            </svg>
          </span>
          <span className="flex flex-col leading-none">
            <span className="text-[13px] font-semibold tracking-[0.18em] text-ink">ARBEX</span>
            <span className="hidden text-2xs text-ink-3 xl:block">FX arbitrage · news as risk</span>
          </span>
        </Link>
        <nav className="flex min-w-0 items-center overflow-x-auto" aria-label="Views">
          {TABS.map((t) => {
            const active = t.href === "/" ? pathname === "/" : pathname.startsWith(t.href);
            return (
              <Link key={t.href} href={t.href}
                className={cn("relative px-3 py-3 text-[12px] font-medium transition-colors",
                  active ? "text-ink" : "text-ink-3 hover:text-ink-2")}>
                {t.label}
                {active && <span className="absolute inset-x-2 bottom-0 h-0.5 bg-info" />}
              </Link>
            );
          })}
        </nav>
        <div className="hidden md:block"><PairPicker /></div>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <Chip tone="sealed" className="hidden md:inline-flex" title="RQ5 extension, C40, the prospective test and the shadow analysis open, and the Phase 2 switches go on, at 18 Nov 00:00 IST">
          <span className="hidden xl:inline">Live tests sealed ·</span> <Countdown to={SEALED_UNTIL} done="open" />
        </Chip>
        <Clock />
        <Chip tone={modeTone} className="hidden sm:inline-flex" title="Feed mode (switch it on the Health tab)">{mode ?? "—"}</Chip>
        <span className="flex items-center gap-1.5 text-[11px] text-ink-2" title={isConnected ? "Live stream connected" : "Stream offline"}>
          <span className={cn("h-2 w-2 rounded-full", isConnected ? "bg-up shadow-[0_0_6px] shadow-up/60" : "bg-down")} />
          <span className="num hidden sm:inline">{isConnected ? "STREAM" : "OFFLINE"}</span>
        </span>
      </div>
    </header>
  );
}
