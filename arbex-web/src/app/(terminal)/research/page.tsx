"use client";

import React from "react";
import summary from "@/data/research_summary.json";
import { Chip, Countdown, Panel, type Tone } from "@/components/terminal/primitives";
import { EvidenceStrip } from "@/components/research/EvidenceStrip";

type Result = (typeof summary.results)[number];

function ResultCard({ r }: { r: Result }) {
  return (
    <article className="flex flex-col gap-3 bg-panel p-4">
      <header className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <span className="num text-[11px] text-ink-3">{r.id}</span>
          <h3 className="text-[14px] font-semibold leading-snug text-ink">{r.title}</h3>
        </div>
        <Chip tone={r.tone as Tone}>{r.verdict}</Chip>
      </header>
      <dl className="flex flex-col gap-1.5">
        {r.numbers.map((n) => (
          <div key={n.label} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 border-b border-line/60 pb-1.5">
            <dt className="truncate text-[12px] text-ink-2" title={n.label}>{n.label}</dt>
            <dd className="num text-right text-[13px] font-semibold text-ink">{n.value}</dd>
            {n.detail && <dd className="col-span-2 num text-[11px] text-ink-3">{n.detail}</dd>}
          </div>
        ))}
      </dl>
      {r.note && <p className="text-[12px] leading-relaxed text-ink-2">{r.note}</p>}
      <p className="mt-auto text-[11px] leading-relaxed text-ink-3">
        <span className="label-caps mr-1.5">Rule</span>{r.rule}
      </p>
    </article>
  );
}

/** Research status: committed results on the frozen historical sample, and the live tests that stay
 *  sealed until their pre-registered opening time. No live outcome is shown here. */
export default function ResearchView() {
  const generated = new Date(summary.generated_at);
  return (
    <div className="h-full overflow-y-auto custom-scrollbar">
      <div className="flex flex-col gap-px bg-line">
        <div className="flex flex-wrap items-center justify-between gap-3 bg-panel px-4 py-3">
          <div className="flex flex-col">
            <span className="text-[15px] font-semibold text-ink">{summary.thesis}</span>
            <span className="num text-[11px] text-ink-3">
              Frozen GDELT sample and committed artifacts · built {generated.toLocaleString("en-GB", { timeZone: "Asia/Kolkata" })} IST
              {summary.source_commit ? ` · ${summary.source_commit}` : ""}
            </span>
          </div>
          <EvidenceStrip />
        </div>

        <div className="grid grid-cols-1 gap-px md:grid-cols-2 xl:grid-cols-4">
          {summary.results.map((r) => <ResultCard key={r.id} r={r} />)}
          <article className="flex flex-col gap-2 bg-panel p-4">
            <span className="label-caps">Reading the verdicts</span>
            {([
              ["up", "SUPPORTED", "passed its pre-registered rule"],
              ["risk", "PARTIAL", "one part of the rule passed"],
              ["down", "NOT SUPPORTED", "failed its rule; reported as a negative result"],
              ["info", "EXPLORATORY", "descriptive, no decision weight"],
              ["sealed", "SEALED", "pre-registered; data still being collected"],
            ] as [Tone, string, string][]).map(([tone, label, text]) => (
              <div key={label} className="flex items-center gap-2 text-[12px] text-ink-2">
                <Chip tone={tone}>{label}</Chip>{text}
              </div>
            ))}
            <p className="mt-auto text-[11px] leading-relaxed text-ink-3">
              Every rule was fixed in the research tracker before its result was seen; negative results stay in.
            </p>
          </article>
        </div>

        <Panel title="Sealed until their opening time" scroll={false}
          actions={<Chip tone="sealed">no live outcome shown</Chip>}>
          <div className="grid grid-cols-1 gap-px bg-line md:grid-cols-2 xl:grid-cols-5">
            {summary.sealed.map((s) => (
              <div key={s.id} className="flex flex-col gap-2 bg-panel p-4">
                <span className="num text-[11px] text-sealed">{s.id}</span>
                <span className="text-[13px] leading-snug text-ink">{s.title}</span>
                <span className="mt-auto flex items-center gap-2">
                  <span className="label-caps">opens in</span>
                  <Countdown to={s.opens} done="open" className="text-[13px] text-sealed" />
                </span>
                <span className="num text-[11px] text-ink-3">
                  {new Date(s.opens).toLocaleString("en-GB", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" })} IST
                </span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}
