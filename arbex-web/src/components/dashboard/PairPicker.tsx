"use client";

import React, { useEffect } from 'react';
import { SYMBOL_KEY, useArbexStore } from '@/lib/store';
import { fallbackInstrument, shortPair } from '@/lib/instruments';

// Stable fallback: a fresh [] from a selector re-renders forever.
const NONE: string[] = [];

/**
 * Header control: which currency pair the price, venue and depth panels show.
 * Only rendered when the backend runs more than one pair (ARBEX_SYMBOLS).
 */
export function PairPicker() {
  const symbols = useArbexStore((state) => state.feedStatus?.symbols ?? NONE);
  const instruments = useArbexStore((state) => state.feedStatus?.instruments);
  const unsupported = useArbexStore((state) => state.feedStatus?.unsupported_symbols ?? NONE);
  const selected = useArbexStore((state) => state.selectedSymbol);
  const setSelected = useArbexStore((state) => state.setSelectedSymbol);

  // Restore the last choice once the feed's pairs are known.
  useEffect(() => {
    let stored: string | null = null;
    try { stored = window.localStorage.getItem(SYMBOL_KEY); } catch {}
    if (stored && symbols.includes(stored) && stored !== selected) setSelected(stored);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbols.join(',')]);

  if (symbols.length < 2) return null;

  return (
    <div className="flex items-center gap-2">
      <div role="radiogroup" aria-label="Currency pair" className="flex rounded border border-[#2a2f45] overflow-hidden">
        {symbols.map((sym) => {
          const active = sym === selected;
          const info = instruments?.[sym] ?? fallbackInstrument(sym);
          return (
            <button
              key={sym}
              type="button"
              role="radio"
              aria-checked={active}
              title={info.display}
              onClick={() => setSelected(sym)}
              className={[
                'text-[10px] px-2 py-0.5 font-mono font-semibold tracking-wide transition-colors cursor-pointer',
                active ? 'bg-blue-600 text-white' : 'text-slate-400 hover:bg-[#1e2235]',
              ].join(' ')}
            >
              {shortPair(info)}
            </button>
          );
        })}
      </div>
      {unsupported.length > 0 && (
        <span className="text-[10px] text-amber-400 font-mono" title="Requested in ARBEX_SYMBOLS but this feed has no data for them">
          not in this feed: {unsupported.join(', ')}
        </span>
      )}
    </div>
  );
}
