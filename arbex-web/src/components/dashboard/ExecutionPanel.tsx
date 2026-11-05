"use client";

import React from 'react';
import { useArbexStore } from '@/lib/store';
import { spreadUnit } from '@/lib/instruments';
import { parseSourceId } from '@/lib/sources';
import { Chip, EmptyState, Stat } from '@/components/terminal/primitives';

const VERDICT = {
  viable: { tone: 'up', label: 'viable' },
  risky: { tone: 'risk', label: 'high execution risk' },
  unknown: { tone: 'muted', label: 'unknown' },
} as const;

/** Backend reasons carry emoji markers; keep the text, drop the pictographs. */
const plain = (text: string) => text.replace(/\p{Extended_Pictographic}|️/gu, '').trim();

/** Simulated execution of the top-ranked cross: no order is ever sent. */
export function ExecutionPanel() {
  const top = useArbexStore((state) => (state.rankedOpportunities.length > 0 ? state.rankedOpportunities[0] : null));

  if (!top) return <EmptyState title="No execution target" detail="The top-ranked cross appears here." />;

  const { opportunity: o, execution_verdict, duration_ms, execution_reasons } = top;
  const verdict = VERDICT[execution_verdict] ?? VERDICT.unknown;
  const pair = o.symbols[0] ?? '';
  const d = pair.endsWith('JPY') ? 3 : pair.endsWith('INR') ? 4 : 5;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <span className="num text-[13px] font-semibold text-ink">{pair}</span>
        <Chip tone={verdict.tone}>{verdict.label}</Chip>
      </div>
      <div className="grid grid-cols-4 gap-3">
        <Stat label="Edge" value={o.estimated_profit_pips.toFixed(2)} unit={spreadUnit(pair).toLowerCase()} tone="up" />
        <Stat label="Confidence" value={(Number(o.confidence_score) * 100).toFixed(1)} unit="%" tone="info" />
        <Stat label="Latency risk" value={o.latency_risk_ms} unit="ms" />
        <Stat label="Seen for" value={duration_ms} unit="ms" tone={duration_ms > 2500 ? 'up' : 'ink'} />
      </div>
      <div className="grid grid-cols-2 gap-3 border-t border-line pt-2">
        <div className="flex flex-col">
          <span className="label-caps">Buy · {parseSourceId(o.buy_source).group}</span>
          <span className="num text-[12px] text-ink">{o.buy_price.toFixed(d)}</span>
        </div>
        <div className="flex flex-col items-end">
          <span className="label-caps">Sell · {parseSourceId(o.sell_source).group}</span>
          <span className="num text-[12px] text-ink">{o.sell_price.toFixed(d)}</span>
        </div>
      </div>
      {execution_reasons && execution_reasons.length > 0 && (
        <ul className="flex flex-col gap-0.5 border-t border-line pt-2 text-[11px] text-ink-3">
          {execution_reasons.slice(0, 4).map((reason, i) => <li key={i} className="truncate" title={plain(reason)}>{plain(reason)}</li>)}
        </ul>
      )}
    </div>
  );
}
