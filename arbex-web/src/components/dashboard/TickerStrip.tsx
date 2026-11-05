"use client";

import React from 'react';
import { useArbexStore } from '@/lib/store';
import { sourceLabel, sourceSymbol } from '@/lib/sources';
import { useInstrument } from '@/hooks/useInstrument';
import { cn } from '@/lib/utils';

/** Best bid / ask per venue for the selected pair, plus scan counters. */
export function TickerStrip() {
  const activePairs = useArbexStore((state) => state.activePairs);
  const totalTicksLogged = useArbexStore((state) => state.totalTicksLogged);
  const detectedRate = useArbexStore((state) => state.detectedRate);
  const { symbol, price_decimals: d } = useInstrument();
  const venues = Object.entries(activePairs).filter(([sourceId]) => sourceSymbol(sourceId) === symbol);
  const tone = (drift: number) => (drift > 0 ? 'text-up' : drift < 0 ? 'text-down' : 'text-ink');

  return (
    <div className="flex h-11 shrink-0 items-stretch justify-between overflow-hidden border-b border-line bg-inset">
      <div className="flex min-w-0 items-stretch overflow-x-auto custom-scrollbar">
        {venues.length === 0 && (
          <span className="flex items-center px-3 text-[11px] text-ink-3">Waiting for quotes…</span>
        )}
        {venues.map(([sourceId, data]) => (
          <div key={sourceId} className="flex min-w-[150px] flex-col justify-center border-r border-line px-3">
            <span className="label-caps">{sourceLabel(sourceId)}</span>
            <span className="num flex gap-3 text-[12px]">
              <span className="text-ink-3">B <span className={cn('font-medium', tone(data.drift))}>{(data.bid ?? 0).toFixed(d)}</span></span>
              <span className="text-ink-3">A <span className={cn('font-medium', tone(data.drift))}>{(data.ask ?? 0).toFixed(d)}</span></span>
            </span>
          </div>
        ))}
      </div>
      <div className="flex shrink-0 items-center gap-4 border-l border-line px-3">
        <div className="flex flex-col items-end">
          <span className="label-caps">Ticks scanned</span>
          <span className="num text-[12px] text-info">{totalTicksLogged.toLocaleString()}</span>
        </div>
        <div className="flex flex-col items-end">
          <span className="label-caps">Cross rate</span>
          <span className="num text-[12px] text-ink-2">{(detectedRate ?? 0).toFixed(2)}%</span>
        </div>
      </div>
    </div>
  );
}
