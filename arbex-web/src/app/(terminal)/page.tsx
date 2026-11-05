"use client";

import React from 'react';
import { useArbexStore } from '@/lib/store';
import { SessionChartGrid } from '@/components/dashboard/SessionChartGrid';
import { ArbitrageFeed } from '@/components/dashboard/ArbitrageFeed';
import { OrderbookFeed } from '@/components/dashboard/OrderbookFeed';
import { GlobalRanking } from '@/components/dashboard/GlobalRanking';
import { ExecutionPanel } from '@/components/dashboard/ExecutionPanel';
import { Chip, Panel } from '@/components/terminal/primitives';
import { useInstrument } from '@/hooks/useInstrument';
import { parseSourceId, sourceSymbol } from '@/lib/sources';

/** Desk: the venues, the crosses the detector finds, and how they rank. */
export default function Desk() {
  const feedMode = useArbexStore((s) => s.feedStatus?.mode ?? null);
  const feedEpoch = useArbexStore((s) => s.feedEpoch);
  const sources = useArbexStore((s) => s.feedStatus?.sources);
  const venueMode = feedMode === 'live' || feedMode === 'replay';
  const instrument = useInstrument();
  const venues = Array.from(new Set((sources ?? [])
    .filter((id) => sourceSymbol(id) === instrument.symbol)
    .map((id) => parseSourceId(id).group))).sort();

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-px overflow-y-auto bg-line lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,0.95fr)] lg:overflow-hidden">
      <div className="grid min-h-0 grid-rows-[minmax(18rem,1.3fr)_minmax(14rem,1fr)] gap-px">
        <Panel title={venueMode ? 'Venue mid prices' : 'Market sessions'}
          actions={<Chip tone="muted">{instrument.display} · {venues.join(' / ') || '—'}</Chip>}
          bodyClassName="p-2">
          {/* remount on every feed switch so no line carries over from the previous feed */}
          <SessionChartGrid key={`${feedEpoch}-${instrument.symbol}`} venueMode={venueMode} />
        </Panel>
        <Panel title="Depth of market" bodyClassName="p-2"><OrderbookFeed /></Panel>
      </div>
      <Panel title="Live crosses" bodyClassName="p-2"><ArbitrageFeed /></Panel>
      <div className="grid min-h-0 grid-rows-[minmax(12rem,1fr)_auto] gap-px">
        <Panel title="Ranking" bodyClassName="p-2"><GlobalRanking /></Panel>
        <Panel title="Execution (simulated)" scroll={false} bodyClassName="p-2"><ExecutionPanel /></Panel>
      </div>
    </div>
  );
}
