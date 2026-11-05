"use client";

import React from "react";
import { useArbexStore, type RankedOpportunity } from "@/lib/store";
import { usePoll } from "@/hooks/usePoll";
import { DIRECTION_LABEL, type EventRisk, riskOf, riskPenaltyNow } from "@/lib/semantic";
import { SentimentWorkbench } from "@/components/dashboard/SentimentWorkbench";
import { Chip, type Column, Countdown, DataTable, EmptyState, Panel, ago } from "@/components/terminal/primitives";
import { EvidenceStrip } from "@/components/research/EvidenceStrip";
import { cn } from "@/lib/utils";

interface PairIndex {
  relative_bias: number | null;
  label: string;
  confidence: number;
  event_risk?: EventRisk;
}
interface CurrencyIndex {
  bias: number | null;
  label: string;
  confidence: number;
  headline_count?: number;
  newest_age_hours?: number | null;
  status?: string;
}
interface CurrencyBiasPayload {
  as_of?: string;
  status?: string;
  pairs?: Record<string, PairIndex>;
  currencies?: Record<string, CurrencyIndex>;
}

type PairRow = { pair: string } & PairIndex;

function RatioBar({ ratio }: { ratio: number | null | undefined }) {
  const r = typeof ratio === "number" ? ratio : null;
  const pct = r === null ? 0 : Math.min(1, r / 4) * 100;
  return (
    <div className="flex items-center gap-2">
      <div className="relative h-1.5 w-20 rounded-term bg-inset">
        <div className="absolute inset-y-0 left-1/4 w-px bg-line-strong" title="normal (1x)" />
        <div className={cn("absolute inset-y-0 left-0 rounded-term", r !== null && r > 1.5 ? "bg-risk" : "bg-info")}
          style={{ width: `${pct}%` }} />
      </div>
      <span className="num text-[12px]">{r === null ? "—" : `${r.toFixed(1)}×`}</span>
    </div>
  );
}

function releaseAt(asOf: string | undefined, minutes: number | undefined) {
  if (!asOf || typeof minutes !== "number") return null;
  return new Date(new Date(asOf).getTime() + minutes * 60_000);
}

/** Risk: news tells the engine when to be careful, not which way. */
export default function RiskView() {
  const index = usePoll<CurrencyBiasPayload>("/sentiment/currency-bias", 30_000);
  const crosses = useArbexStore((s) => s.rankedOpportunities);
  const asOf = index.data?.as_of;
  const pairs: PairRow[] = Object.entries(index.data?.pairs ?? {}).map(([pair, v]) => ({ pair, ...v }));
  const currencies = Object.entries(index.data?.currencies ?? {});

  const pairColumns: Column<PairRow>[] = [
    { key: "pair", header: "Pair", render: (r) => <span className="num font-semibold">{r.pair}</span> },
    { key: "move", header: "Expected move", align: "right", mono: true,
      render: (r) => <>{r.event_risk?.expected_move_bps?.toFixed(1) ?? "—"} <span className="text-ink-3">bps</span></> },
    { key: "base", header: "Normal", align: "right", mono: true,
      render: (r) => <span className="text-ink-2">{r.event_risk?.baseline_move_bps?.toFixed(1) ?? "—"}</span> },
    { key: "ratio", header: "vs normal", render: (r) => <RatioBar ratio={r.event_risk?.risk_ratio} /> },
    { key: "pen", header: "Penalty now", align: "right", mono: true,
      render: (r) => {
        const p = riskPenaltyNow(r.event_risk);
        return <span className={p > 0 ? "text-risk" : "text-ink-3"}>{p > 0 ? `−${p.toFixed(3)}` : "0"}</span>;
      } },
    { key: "next", header: "Next high-impact release",
      render: (r) => {
        const next = r.event_risk?.next_release;
        const at = releaseAt(asOf, next?.minutes);
        return next && at ? (
          <span className="flex min-w-0 items-center gap-2">
            <Countdown to={at} done="now" className="text-[12px] text-risk" />
            <span className="truncate text-[11px] text-ink-2" title={next.event}>{next.event}</span>
          </span>
        ) : <span className="text-ink-3">none this week</span>;
      } },
    { key: "dir", header: "Currency index",
      render: (r) => (
        <span className="flex items-center gap-1.5">
          <span className="text-ink-2">{DIRECTION_LABEL[r.label] ?? r.label}</span>
          <Chip tone="muted" title="Reported, never applied">not applied</Chip>
        </span>
      ) },
  ];

  const crossColumns: Column<RankedOpportunity>[] = [
    { key: "pair", header: "Pair", render: (o) => <span className="num font-semibold">{o.opportunity.symbols[0]}</span> },
    { key: "edge", header: "Edge", align: "right", mono: true,
      render: (o) => <span className="text-up">+{o.opportunity.estimated_profit_pips.toFixed(2)}</span> },
    { key: "risk", header: "Risk penalty", align: "right", mono: true,
      render: (o) => {
        const r = riskOf(o);
        return r.available ? <span className={r.penalty > 0 ? "text-risk" : "text-ink-3"}>{r.penalty > 0 ? `−${r.penalty.toFixed(3)}` : "0"}</span> : "—";
      } },
    { key: "why", header: "Why", render: (o) => <span className="truncate text-[11px] text-ink-2">{riskOf(o).reason || "—"}</span> },
    { key: "age", header: "Seen", align: "right", mono: true, render: (o) => <span className="text-ink-3">{ago(o.last_seen_ms)}</span> },
  ];

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="flex flex-col gap-px bg-line">
        <div className="flex flex-wrap items-center justify-between gap-3 bg-panel px-4 py-3">
          <div className="flex flex-col">
            <span className="text-[15px] font-semibold text-ink">News tells the engine when to be careful, not which way.</span>
            <span className="text-[12px] text-ink-3">
              Each cross loses confidence when news makes a big move more likely. Direction is reported and never used.
            </span>
          </div>
          <EvidenceStrip />
        </div>

        <div className="grid grid-cols-1 gap-px xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Panel title="Risk by pair" scroll={false}
            actions={<span className="num text-[11px] text-ink-3">{index.error ?? (asOf ? `as of ${new Date(asOf).toLocaleTimeString("en-GB")}` : "loading")}</span>}>
            <div className="overflow-x-auto custom-scrollbar">
              <DataTable columns={pairColumns} rows={pairs} rowKey={(r) => r.pair}
                empty={<EmptyState title={index.error ? `Currency index ${index.error}` : "Loading the currency index…"} />} />
            </div>
          </Panel>
          <Panel title="Currency news index" scroll={false}>
            <div className="flex flex-col gap-2 p-3">
              {currencies.length === 0 && <EmptyState title="No currency data yet" />}
              {currencies.map(([code, c]) => {
                const b = typeof c.bias === "number" ? c.bias : 0;
                const w = Math.min(1, Math.abs(b) / 3) * 50;
                return (
                  <div key={code} className="grid grid-cols-[2.5rem_1fr_3.5rem] items-center gap-2">
                    <span className="num text-[12px] font-semibold text-ink">{code}</span>
                    <div className="relative h-2 rounded-term bg-inset" title={`${c.headline_count ?? 0} headlines`}>
                      <div className="absolute inset-y-0 left-1/2 w-px bg-line-strong" />
                      <div className={cn("absolute inset-y-0 rounded-term", b >= 0 ? "bg-info/70" : "bg-ink-3/70")}
                        style={b >= 0 ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }} />
                    </div>
                    <span className="num text-right text-[11px] text-ink-2">{typeof c.bias === "number" ? c.bias.toFixed(2) : "—"}</span>
                  </div>
                );
              })}
              <p className="pt-1 text-[11px] leading-relaxed text-ink-3">
                Strength of each currency&apos;s news flow (z-scored). Shown for context: pair direction from it is
                recorded in the shadow log, not applied.
              </p>
            </div>
          </Panel>
        </div>

        <div className="grid grid-cols-1 gap-px xl:grid-cols-2">
          <Panel title="Recent crosses and their risk penalty" className="max-h-[28rem]">
            <DataTable columns={crossColumns} rows={crosses} rowKey={(o) => o.id}
              empty={<EmptyState title="No crosses yet" />} />
          </Panel>
          <Panel title="Sentiment workbench" className="max-h-[28rem]" bodyClassName="p-2">
            <SentimentWorkbench />
          </Panel>
        </div>
      </div>
    </div>
  );
}
