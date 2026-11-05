"use client";

/* Terminal primitives: the only building blocks the redesigned views use, so every panel,
   number and signal looks the same everywhere. Colours come from the @theme tokens. */
import React, { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

export type Tone = "up" | "down" | "risk" | "info" | "sealed" | "muted" | "ink";

const TONE_TEXT: Record<Tone, string> = {
  up: "text-up", down: "text-down", risk: "text-risk", info: "text-info",
  sealed: "text-sealed", muted: "text-ink-3", ink: "text-ink",
};
const TONE_CHIP: Record<Tone, string> = {
  up: "text-up border-up/40 bg-up/10",
  down: "text-down border-down/40 bg-down/10",
  risk: "text-risk border-risk/40 bg-risk/10",
  info: "text-info border-info/40 bg-info/10",
  sealed: "text-sealed border-sealed/40 bg-sealed/10",
  muted: "text-ink-2 border-line-strong bg-raised",
  ink: "text-ink border-line-strong bg-raised",
};

export function toneText(tone: Tone) {
  return TONE_TEXT[tone];
}

export function Panel({ title, actions, children, className, bodyClassName, scroll = true }: {
  title?: React.ReactNode; actions?: React.ReactNode; children: React.ReactNode;
  className?: string; bodyClassName?: string; scroll?: boolean;
}) {
  return (
    <section className={cn("flex min-h-0 flex-col overflow-hidden border border-line bg-panel", className)}>
      {(title || actions) && (
        <header className="flex h-8 shrink-0 items-center justify-between gap-2 border-b border-line bg-raised px-3">
          <span className="label-caps truncate">{title}</span>
          {actions && <div className="flex items-center gap-1.5">{actions}</div>}
        </header>
      )}
      <div className={cn("min-h-0 flex-1", scroll && "overflow-y-auto custom-scrollbar", bodyClassName)}>
        {children}
      </div>
    </section>
  );
}

export function Stat({ label, value, unit, tone = "ink", hint, className }: {
  label: React.ReactNode; value: React.ReactNode; unit?: string; tone?: Tone;
  hint?: React.ReactNode; className?: string;
}) {
  return (
    <div className={cn("flex min-w-0 flex-col gap-0.5", className)}>
      <span className="label-caps truncate">{label}</span>
      <span className={cn("num truncate text-[15px] font-semibold leading-tight", TONE_TEXT[tone])}>
        {value}
        {unit && <span className="ml-1 text-[11px] font-normal text-ink-3">{unit}</span>}
      </span>
      {hint && <span className="truncate text-[11px] text-ink-3">{hint}</span>}
    </div>
  );
}

export function Chip({ tone = "muted", children, title, className }: {
  tone?: Tone; children: React.ReactNode; title?: string; className?: string;
}) {
  return (
    <span title={title} className={cn(
      "inline-flex shrink-0 items-center gap-1 rounded-term border px-1.5 py-px text-2xs font-semibold uppercase tracking-wider",
      TONE_CHIP[tone], className)}>
      {children}
    </span>
  );
}

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  align?: "left" | "right" | "center";
  mono?: boolean;
  width?: string;
  render: (row: T) => React.ReactNode;
}

export function DataTable<T>({ columns, rows, rowKey, empty, onRowClick, selectedKey, className }: {
  columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string; empty?: React.ReactNode;
  onRowClick?: (row: T) => void; selectedKey?: string | null; className?: string;
}) {
  if (!rows.length) return <>{empty ?? <EmptyState title="Nothing yet" />}</>;
  const align = (a?: string) => (a === "right" ? "text-right" : a === "center" ? "text-center" : "text-left");
  return (
    <table className={cn("w-full border-collapse text-[12px]", className)}>
      <thead className="sticky top-0 z-10 bg-panel">
        <tr className="border-b border-line">
          {columns.map((c) => (
            <th key={c.key} style={{ width: c.width }} className={cn("label-caps px-2 py-1.5 font-semibold", align(c.align))}>
              {c.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => {
          const key = rowKey(row);
          return (
            <tr key={key} onClick={onRowClick ? () => onRowClick(row) : undefined}
              className={cn("border-b border-line/60 transition-colors",
                onRowClick && "cursor-pointer hover:bg-raised",
                selectedKey === key && "bg-info/10 hover:bg-info/15")}>
              {columns.map((c) => (
                <td key={c.key} className={cn("px-2 py-1.5 text-ink", align(c.align), c.mono && "num")}>
                  {c.render(row)}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export interface WaterfallStep {
  label: string;
  value: number;          // the running total after this step, on a 0..1 scale
  delta?: number;         // negative for a penalty; omitted for the start and the result
  tone?: Tone;
  note?: string;
}

/** Confidence breakdown: detector score, each penalty, the result. Bars on a 0..1 scale. */
export function Waterfall({ steps }: { steps: WaterfallStep[] }) {
  return (
    <div className="flex flex-col gap-1.5">
      {steps.map((s, i) => {
        const prev = i === 0 ? 0 : steps[i - 1].value;
        const isDelta = s.delta !== undefined;
        const left = isDelta ? Math.min(prev, s.value) : 0;
        const width = isDelta ? Math.abs(prev - s.value) : s.value;
        return (
          <div key={s.label} className="grid grid-cols-[minmax(0,9.5rem)_1fr_4.2rem] items-center gap-2">
            <span className="truncate text-[11px] text-ink-2" title={s.note}>{s.label}</span>
            <div className="relative h-3.5 rounded-term bg-inset">
              <div className={cn("absolute inset-y-0 rounded-term",
                  isDelta ? "bg-risk/70" : s.tone === "sealed" ? "bg-sealed/70" : "bg-info/70")}
                style={{ left: `${Math.max(0, left) * 100}%`, width: `${Math.max(width, 0.004) * 100}%` }} />
            </div>
            <span className={cn("num text-right text-[12px]", isDelta ? "text-risk" : TONE_TEXT[s.tone ?? "ink"])}>
              {isDelta ? `${(s.delta as number) <= 0 ? "−" : "+"}${Math.abs(s.delta as number).toFixed(3)}` : s.value.toFixed(3)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function split(ms: number) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return d > 0 ? `${d}d ${pad(h)}:${pad(m)}:${pad(sec)}` : `${pad(h)}:${pad(m)}:${pad(sec)}`;
}

/** Live countdown to a fixed moment; shows `done` once it has passed. */
export function Countdown({ to, done = "open", className }: { to: string | Date; done?: React.ReactNode; className?: string }) {
  const target = typeof to === "string" ? new Date(to).getTime() : to.getTime();
  const [now, setNow] = useState<number | null>(null);
  useEffect(() => {
    setNow(Date.now());
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  if (now === null) return <span className={cn("num", className)}>--:--:--</span>;
  return <span className={cn("num", className)}>{now >= target ? done : split(target - now)}</span>;
}

export function EmptyState({ title, detail, className }: { title: React.ReactNode; detail?: React.ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-1 px-4 py-8 text-center", className)}>
      <span className="text-[12px] text-ink-2">{title}</span>
      {detail && <span className="max-w-sm text-[11px] text-ink-3">{detail}</span>}
    </div>
  );
}

/** Relative age of a millisecond timestamp, refreshed by the caller's re-render. */
export function ago(ms: number | null | undefined, now: number = Date.now()) {
  if (!ms) return "—";
  const s = Math.max(0, (now - ms) / 1000);
  if (s < 60) return `${s.toFixed(0)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}
