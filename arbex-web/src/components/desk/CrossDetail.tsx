"use client";

import React from "react";
import { useArbexStore } from "@/lib/store";
import { spreadUnit } from "@/lib/instruments";
import { parseSourceId } from "@/lib/sources";
import { DIRECTION_LABEL, detectorScore, riskOf } from "@/lib/semantic";
import { Chip, EmptyState, Panel, Stat, Waterfall, type WaterfallStep } from "@/components/terminal/primitives";

const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const ACTIVITY_SATURATION = 5;     // the live window's activity_factor = min(1, items / 5)

function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 border-b border-line px-3 py-3 last:border-b-0">
      <div className="flex items-center justify-between gap-2">
        <span className="label-caps">{title}</span>
        {aside}
      </div>
      {children}
    </div>
  );
}

/** "Why this cross": the detector's score, what news risk takes off it, and why direction does not. */
export function CrossDetail() {
  const ranked = useArbexStore((s) => s.rankedOpportunities);
  const selectedId = useArbexStore((s) => s.selectedOpportunityId);
  const opp = ranked.find((o) => o.id === selectedId) ?? ranked[0];

  if (!opp) {
    return (
      <Panel title="Why this cross" className="h-full">
        <EmptyState title="Select a cross"
          detail="The breakdown shows the detector's confidence, what news risk takes off it, and why direction is not used." />
      </Panel>
    );
  }

  const o = opp.opportunity;
  const pair = o.symbols[0] ?? "";
  const risk = riskOf(opp);
  const det = detectorScore(opp);
  const shadow = opp.shadow;
  const stale = shadow?.stale_caution;
  const afterRisk = clamp01(det.value - risk.penalty);
  const result = typeof shadow?.adjusted_confidence_score === "number"
    ? shadow.adjusted_confidence_score : clamp01(afterRisk - (stale ?? 0));
  const steps: WaterfallStep[] = [
    { label: det.rebuilt ? "Detector score (rebuilt)" : "Detector score", value: det.value, tone: "ink",
      note: det.rebuilt ? "Rebuilt from the displayed score; exact once the shadow block is deployed" : undefined },
    { label: "News risk channel", value: afterRisk, delta: -risk.penalty, note: risk.reason },
    ...(stale !== undefined
      ? [{ label: "Stale-quote caution", value: clamp01(afterRisk - stale), delta: -stale,
           note: `older leg ${shadow?.older_leg_age_ms ?? "—"} ms at detection` }]
      : []),
    { label: "News-as-risk confidence", value: result, tone: "sealed" as const },
  ];

  const window = opp.news_bias;
  const items = window?.item_count ?? 0;
  const dirInputs = (opp.semantic_channels?.direction?.inputs ?? {}) as { label?: string; relative_bias?: number; confidence?: number };
  const ciLabel = shadow?.window_direction_currency_index ?? dirInputs.label ?? null;
  const v1 = shadow?.window_direction_v1 ?? window?.direction ?? null;

  return (
    <Panel title="Why this cross" className="h-full"
      actions={<Chip tone="sealed" title="Recorded only after 18 Nov 00:00 IST (C42 switch); shown live as the C43 shadow">shadow</Chip>}>
      <div className="flex items-end justify-between gap-3 border-b border-line px-3 py-3">
        <div className="flex min-w-0 flex-col">
          <span className="num text-[18px] font-semibold text-ink">{pair}</span>
          <span className="num truncate text-[11px] text-ink-3">
            buy {parseSourceId(o.buy_source).group} · sell {parseSourceId(o.sell_source).group} · {opp.persistence_class}
          </span>
        </div>
        <Stat label="Edge" value={`+${o.estimated_profit_pips.toFixed(2)}`} unit={spreadUnit(pair).toLowerCase()} tone="up"
          className="items-end text-right" />
      </div>

      <Section title="Confidence"
        aside={<span className="num text-[11px] text-ink-3">0 ─ 1 scale</span>}>
        <Waterfall steps={steps} />
        <p className="text-[11px] leading-relaxed text-ink-3">
          The detector&apos;s score is what is recorded until 18 Nov. News lowers it as a risk, never as a call on
          direction. {stale === undefined && "Stale-quote caution appears after the next backend deploy."}
        </p>
      </Section>

      <Section title="Why the risk penalty"
        aside={risk.available ? <Chip tone={risk.penalty > 0 ? "risk" : "muted"}>−{risk.penalty.toFixed(3)}</Chip> : <Chip>unavailable</Chip>}>
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Expected move" value={risk.expectedMoveBps?.toFixed(1) ?? "—"} unit="bps" />
          <Stat label="vs normal" value={risk.riskRatio ? `${risk.riskRatio.toFixed(1)}×` : "warming up"}
            tone={risk.riskRatio && risk.riskRatio > 1.5 ? "risk" : "ink"} />
          <Stat label="Release" value={risk.proximity ?? "none near"} tone={risk.proximity ? "risk" : "muted"} />
        </div>
      </Section>

      <Section title="News window · 30 min">
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Headlines" value={items} hint={`${window?.status ?? "—"}`} />
          <Stat label="Activity" value={Math.min(1, items / ACTIVITY_SATURATION).toFixed(2)}
            hint="min(1, items / 5)" tone={items >= ACTIVITY_SATURATION ? "risk" : "ink"} />
          <Stat label="FX-relevant" value={shadow?.window_filtered?.item_count ?? "—"}
            hint={shadow?.window_filtered ? `${shadow.window_filtered.fx_filter_dropped ?? 0} dropped` : "after deploy"} />
        </div>
      </Section>

      <Section title="Direction" aside={<Chip tone="muted">not applied</Chip>}>
        <div className="grid grid-cols-2 gap-3">
          <Stat label="Currency index" value={ciLabel ? DIRECTION_LABEL[ciLabel] ?? ciLabel : "—"}
            hint={typeof dirInputs.relative_bias === "number" ? `relative bias ${dirInputs.relative_bias.toFixed(2)}` : undefined} />
          <Stat label="V1 window" value={v1 ? DIRECTION_LABEL[v1] ?? v1 : "—"} tone="muted" hint="kept for the record" />
        </div>
        <p className="text-[11px] leading-relaxed text-ink-3">
          No direction method beat chance on held-out data (H6 not supported; RQ2-E2E at chance), so direction never
          moves the score.
        </p>
      </Section>
    </Panel>
  );
}
