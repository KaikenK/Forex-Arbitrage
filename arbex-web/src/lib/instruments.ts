// Per-pair display conventions. The backend's registry (backend/core/instruments.py)
// is the source of truth and is sent in /feeds/mode as `instruments`; the
// fallback here only covers the moment before that first arrives.

export interface InstrumentInfo {
  display: string;          // "USD/INR", "JPY/INR (per 100)"
  pip_size: number;         // 0.01 for INR and JPY pairs (INR: one paisa), else 0.0001
  price_decimals: number;
  tick_size: number;        // exchange futures tick where listed
  quote_unit: number;
  exchange_listed: boolean;
}

export function fallbackInstrument(symbol: string): InstrumentInfo {
  const quote = symbol.slice(3);
  const hundredth = quote === 'INR' || quote === 'JPY';
  return {
    display: `${symbol.slice(0, 3)}/${quote}`,
    pip_size: hundredth ? 0.01 : 0.0001,
    price_decimals: quote === 'INR' ? 4 : quote === 'JPY' ? 3 : 5,
    tick_size: quote === 'INR' ? 0.0025 : hundredth ? 0.01 : 0.0001,
    quote_unit: 1,
    exchange_listed: false,
  };
}

/** Spread unit as the market quotes it: paise for INR pairs, pips otherwise. */
export function spreadUnit(symbol: string): 'PAISE' | 'PIPS' {
  return symbol.endsWith('INR') ? 'PAISE' : 'PIPS';
}

/** "USD/INR" without the "(per 100)" note, for compact labels. */
export function shortPair(info: InstrumentInfo): string {
  return info.display.replace(/ \(per \d+\)$/, '');
}
