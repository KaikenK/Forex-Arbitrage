"use client";

import { useArbexStore } from '@/lib/store';
import { fallbackInstrument, InstrumentInfo } from '@/lib/instruments';

/** The selected pair (or `symbol`) and its display conventions from the backend registry. */
export function useInstrument(symbol?: string): InstrumentInfo & { symbol: string } {
  const selected = useArbexStore((state) => state.selectedSymbol);
  const sym = symbol ?? selected;
  const info = useArbexStore((state) => state.feedStatus?.instruments?.[sym]);
  return { symbol: sym, ...(info ?? fallbackInstrument(sym)) };
}
