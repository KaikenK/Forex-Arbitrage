"use client";

import React, { useEffect, useState } from 'react';

const API_BASE_URL = process.env.NEXT_PUBLIC_ARBEX_API_BASE_URL || 'http://127.0.0.1:8000';
const REFRESH_MS = 30_000;
const CURRENCIES = ['USD', 'EUR', 'GBP', 'JPY', 'CHF', 'INR'];

type CurrencyRow = {
  bias: number | null;
  label: string;
  confidence: number;
  headline_count: number;
  status: string;
  top_headlines: string[];
};

type PairRow = {
  relative_bias: number | null;
  label: string;
  confidence: number;
  event_risk: { expected_move_bps: number; release_proximity: string | null };
};

type CurrencyBiasResponse = {
  enabled: boolean;
  status?: string;
  applied_to_ranking?: boolean;
  as_of?: string;
  currencies?: Record<string, CurrencyRow>;
  pairs?: Record<string, PairRow>;
  horizon?: { label: string | null };
  live_input_gaps?: string[];
};

function tone(label: string): string {
  if (label === 'bullish') return 'text-emerald-400';
  if (label === 'bearish') return 'text-red-400';
  return 'text-slate-400';
}

function fmt(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? '—' : `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
}

/**
 * Per-currency market bias and per-pair relative bias (H6 signal, /sentiment/currency-bias).
 * Renders nothing unless the semantic service runs with SEMANTIC_CURRENCY_BIAS on.
 */
export function CurrencyBiasPanel() {
  const [data, setData] = useState<CurrencyBiasResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/sentiment/currency-bias`, { cache: 'no-store' });
        if (response.ok && !cancelled) {
          setData((await response.json()) as CurrencyBiasResponse);
        }
      } catch {
        // backend unreachable: keep the last snapshot; the panel never blocks the workbench
      }
    };
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (!data?.enabled || !data.currencies || !data.pairs) {
    return null;
  }
  const currencies = data.currencies;
  const pairs = data.pairs;

  return (
    <div className="rounded border border-[#1e2235] bg-[#10131b] p-3">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <span className="uppercase tracking-widest text-slate-500">Currency Market Bias</span>
        <span className="text-slate-500">
          horizon {data.horizon?.label ?? 'none established'} · {data.applied_to_ranking ? 'weighting ranking' : 'shadow only'}
        </span>
      </div>
      <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
        {CURRENCIES.filter((code) => currencies[code]).map((code) => {
          const row = currencies[code];
          return (
            <div key={code} className="rounded border border-[#1e2235] bg-[#0f1219] p-2" title={row.top_headlines.join('\n')}>
              <div className="text-slate-500">{code}</div>
              <div className={`mt-1 text-[12px] ${tone(row.label)}`}>{row.label}</div>
              <div className="text-slate-500">z {fmt(row.bias)} · conf {row.confidence.toFixed(2)}</div>
              <div className="text-slate-600">{row.headline_count} items · {row.status}</div>
            </div>
          );
        })}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2 md:grid-cols-5">
        {Object.entries(pairs).map(([pair, row]) => (
          <div key={pair} className="rounded border border-[#1e2235] bg-[#0f1219] p-2">
            <div className="text-slate-500">{pair}</div>
            <div className={`mt-1 text-[12px] ${tone(row.label)}`}>{row.label} {fmt(row.relative_bias)}</div>
            <div className="text-slate-500">conf {row.confidence.toFixed(2)} · risk {row.event_risk.expected_move_bps.toFixed(1)}bp</div>
          </div>
        ))}
      </div>
      {data.live_input_gaps?.length ? (
        <div className="mt-2 text-slate-600">Live gaps: {data.live_input_gaps.join('; ')}</div>
      ) : null}
    </div>
  );
}
