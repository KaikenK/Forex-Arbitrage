"use client";

import React from "react";
import { usePoll } from "@/hooks/usePoll";
import { FeedModeToggle } from "@/components/dashboard/FeedModeToggle";
import { Chip, type Column, DataTable, EmptyState, Panel, Stat, ago } from "@/components/terminal/primitives";

interface Leg {
  id: string; symbol: string; exchange: string; provider: string;
  bid: number | null; ask: number | null; spread_pips: number | null;
  age_ms: number | null; healthy: boolean; suppressed_reason: string | null;
}
interface Hub { running?: boolean; polls?: number; errors?: number; last_error?: string | null; last_latency_ms?: number | null }
interface LiveStatus {
  error?: string; mode?: string; legs?: Leg[]; hubs?: Record<string, Hub>; recorded_rows?: number;
  opportunities_seen?: number; skipped_feeds?: Record<string, string>;
}
interface SystemHealth {
  backend: { started_at: number; uptime_s: number; mode: string };
  recorder: { enabled: boolean; rows_written: number | null; last_quote_recv_ms: number | null;
              gap_threshold_s: number; gaps_since_start: number;
              recent_gaps: { start_ms: number; end_ms: number; seconds: number }[] };
  shadow: { enabled: boolean; dir: string; rows_written: number | null; last_row_ms: number | null };
}
interface BackendHealth {
  status?: string;
  sentiment?: { available?: boolean; ingest_active?: boolean; analysis_worker_active?: boolean;
                analysis_queue_depth?: number; tracked_pairs?: string[]; last_error?: string | null };
}
interface SentimentStatus {
  news_stream?: { available?: boolean; last_latency_ms?: number | null; last_error?: string | null; last_success_at?: string };
  runtime?: { finbert_enabled?: boolean; rag_enabled?: boolean; window_minutes?: number };
}

const fmtUptime = (s: number) => {
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m ${Math.floor(s % 60)}s`;
};
const ist = (ms: number) => new Date(ms).toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata" });

/** Health: is every part of the pipeline alive, from the venue polls to the recorder and the shadow log. */
export default function HealthView() {
  const live = usePoll<LiveStatus>("/live/status", 3_000);
  const sys = usePoll<SystemHealth>("/system/health", 5_000);
  const backend = usePoll<BackendHealth>("/health", 10_000);
  const semantic = usePoll<SentimentStatus>("/sentiment/status", 10_000);
  const legs = live.data?.legs ?? [];
  const hubs = Object.entries(live.data?.hubs ?? {});
  const skipped = Object.entries(live.data?.skipped_feeds ?? {});
  const healthy = legs.filter((l) => l.healthy).length;
  const notDeployed = sys.error === "not deployed";

  const legColumns: Column<Leg>[] = [
    { key: "venue", header: "Venue", render: (l) => <span className="num font-semibold">{l.exchange}</span> },
    { key: "pair", header: "Pair", render: (l) => <span className="num">{l.symbol}</span> },
    { key: "bid", header: "Bid", align: "right", mono: true, render: (l) => l.bid ?? "—" },
    { key: "ask", header: "Ask", align: "right", mono: true, render: (l) => l.ask ?? "—" },
    { key: "spread", header: "Spread", align: "right", mono: true,
      render: (l) => (l.spread_pips === null ? "—" : `${l.spread_pips.toFixed(1)} pips`) },
    { key: "age", header: "Quote age", align: "right", mono: true,
      render: (l) => <span className={l.age_ms !== null && l.age_ms > 5000 ? "text-risk" : "text-ink-2"}>
        {l.age_ms === null ? "—" : l.age_ms < 10_000 ? `${l.age_ms} ms` : `${Math.round(l.age_ms / 1000)} s`}</span> },
    { key: "state", header: "State",
      render: (l) => l.healthy ? <Chip tone="up">in use</Chip>
        : <Chip tone="risk" title={l.suppressed_reason ?? ""}>{(l.suppressed_reason ?? "ignored").replace(/\s*\(.*\)$/, "")}</Chip> },
  ];

  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="flex flex-col gap-px bg-line">
        <div className="grid grid-cols-2 gap-px md:grid-cols-3 xl:grid-cols-6">
          {[
            <Stat key="b" label="Backend" value={backend.data?.status ?? (backend.error ? "down" : "…")}
              tone={backend.data?.status === "healthy" ? "up" : "down"}
              hint={sys.data ? `up ${fmtUptime(sys.data.backend.uptime_s)}` : live.data?.mode ?? ""} />,
            <Stat key="l" label="Venue legs in use" value={`${healthy} / ${legs.length}`}
              tone={legs.length && healthy === legs.length ? "up" : "risk"} hint="stale or one-sided legs are ignored" />,
            <Stat key="r" label="Recorder" value={(live.data?.recorded_rows ?? 0).toLocaleString()} unit="rows"
              hint={sys.data?.recorder.last_quote_recv_ms ? `last quote ${ago(sys.data.recorder.last_quote_recv_ms)} ago` : "since backend start"} />,
            <Stat key="g" label={`Silences > ${sys.data?.recorder.gap_threshold_s ?? 5} s`}
              value={sys.data ? sys.data.recorder.gaps_since_start : "—"}
              tone={sys.data && sys.data.recorder.gaps_since_start > 0 ? "risk" : "ink"}
              hint={notDeployed ? "after the next backend deploy" : "since backend start"} />,
            <Stat key="s" label="Semantic service" value={semantic.data?.news_stream?.available ? "reachable" : semantic.error ? "unknown" : "down"}
              tone={semantic.data?.news_stream?.available ? "up" : "down"}
              hint={semantic.data?.news_stream?.last_latency_ms != null ? `${semantic.data.news_stream.last_latency_ms.toFixed(0)} ms` : ""} />,
            <Stat key="h" label="Shadow log (C43)" value={sys.data ? (sys.data.shadow.enabled ? (sys.data.shadow.rows_written ?? 0) : "off") : "—"}
              unit={sys.data?.shadow.enabled ? "rows" : undefined} tone="sealed"
              hint={notDeployed ? "after the next backend deploy" : sys.data?.shadow.last_row_ms ? `last ${ist(sys.data.shadow.last_row_ms)} IST` : ""} />,
          ].map((s, i) => <div key={i} className="bg-panel px-4 py-3">{s}</div>)}
        </div>

        <div className="grid grid-cols-1 gap-px xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <Panel title="Venue legs" scroll={false} actions={<span className="num text-[11px] text-ink-3">{live.error ?? "every 3 s"}</span>}>
            <div className="overflow-x-auto custom-scrollbar">
              <DataTable columns={legColumns} rows={legs} rowKey={(l) => l.id}
                empty={<EmptyState title={live.data?.error ?? live.error ?? "Loading legs…"} />} />
            </div>
          </Panel>
          <div className="grid gap-px">
            <Panel title="Pollers" scroll={false}>
              <div className="flex flex-col gap-2 p-3">
                {hubs.length === 0 && <EmptyState title="No hub data" />}
                {hubs.map(([name, h]) => (
                  <div key={name} className="grid grid-cols-[5rem_repeat(3,minmax(0,1fr))] items-center gap-2 text-[12px]">
                    <span className="num font-semibold text-ink">{name}</span>
                    <span className="num text-ink-2">{(h.polls ?? 0).toLocaleString()} polls</span>
                    <span className={h.errors ? "num text-risk" : "num text-ink-3"}>{h.errors ?? 0} errors</span>
                    <span className="num text-right text-ink-3">{h.last_latency_ms != null ? `${h.last_latency_ms.toFixed(0)} ms` : "—"}</span>
                  </div>
                ))}
                {skipped.length > 0 && (
                  <div className="flex flex-col gap-1 border-t border-line pt-2">
                    <span className="label-caps">Not connected</span>
                    {skipped.map(([k, v]) => <span key={k} className="text-[11px] text-ink-3"><b className="num text-ink-2">{k}</b> {v}</span>)}
                  </div>
                )}
              </div>
            </Panel>
            <Panel title="News pipeline" scroll={false}>
              <div className="grid grid-cols-3 gap-3 p-3">
                <Stat label="Ingest" value={backend.data?.sentiment?.ingest_active ? "active" : "idle"}
                  tone={backend.data?.sentiment?.ingest_active ? "up" : "muted"} />
                <Stat label="Analysis queue" value={backend.data?.sentiment?.analysis_queue_depth ?? "—"} />
                <Stat label="FinBERT" value={semantic.data?.runtime?.finbert_enabled ? "on" : "off"} />
                <div className="col-span-3 flex flex-wrap gap-1">
                  {(backend.data?.sentiment?.tracked_pairs ?? []).map((p) => <Chip key={p} tone="muted">{p}</Chip>)}
                </div>
              </div>
            </Panel>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-px xl:grid-cols-2">
          <Panel title="Recorder silences since backend start" scroll={false}>
            <div className="p-3">
              {notDeployed ? (
                <EmptyState title="Available after the next backend deploy" detail="/system/health is on the ui-updates branch." />
              ) : (sys.data?.recorder.recent_gaps ?? []).length === 0 ? (
                <EmptyState title="No silence over 5 s since the backend started" />
              ) : (
                <ul className="flex flex-col gap-1">
                  {sys.data!.recorder.recent_gaps.slice().reverse().map((g) => (
                    <li key={g.start_ms} className="num flex justify-between text-[12px] text-ink-2">
                      <span>{ist(g.start_ms)} → {ist(g.end_ms)} IST</span><span className="text-risk">{g.seconds} s</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </Panel>
          <Panel title="Controls" scroll={false} actions={<Chip tone="risk">changes the live backend</Chip>}>
            <div className="flex flex-col gap-2 p-3">
              <span className="text-[12px] text-ink-2">Data feed</span>
              <FeedModeToggle />
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
