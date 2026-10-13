"use client";

import React, { useEffect, useRef, useState } from 'react';
import { createChart, IChartApi, ISeriesApi, LineSeries } from 'lightweight-charts';
import { useArbexStore } from '@/lib/store';
import { sourceLabel, sourceSymbol } from '@/lib/sources';
import { useInstrument } from '@/hooks/useInstrument';

const SESSIONS = [
  { id: 'tokyo', label: 'Tokyo (TYO)' },
  { id: 'london', label: 'London (LDN)' },
  { id: 'newyork', label: 'New York (NYC)' }
];

// Live / replay venues: one line per source, colours assigned in arrival order.
const VENUE_COLORS = ['#10b981', '#38bdf8', '#f59e0b', '#f472b6'];

const CHART_OPTIONS = {
  layout: { background: { color: 'transparent' }, textColor: '#64748b' },
  grid: { vertLines: { color: '#1e2235' }, horzLines: { color: '#1e2235' } },
  timeScale: { timeVisible: true, secondsVisible: true, borderVisible: false },
  rightPriceScale: { borderVisible: false },
  crosshair: { mode: 0 },
  handleScroll: false,
  handleScale: false,
} as const;

// Price axis for a pair: enough decimals to see a one-tick gap between feeds.
function priceFormat(decimals: number, tick: number) {
  return { type: 'price' as const, precision: decimals, minMove: Math.min(tick, Math.pow(10, -decimals)) };
}

/** Charts for the selected pair (the parent remounts this when the pair changes). */
export function SessionChartGrid({ venueMode = false }: { venueMode?: boolean }) {
  const { symbol, price_decimals, tick_size } = useInstrument();
  const format = priceFormat(price_decimals, tick_size);
  if (venueMode) return <VenueChart symbol={symbol} format={format} />;
  return (
    <div className="flex flex-col gap-3 pb-10">
      {SESSIONS.map((session) => (
        <SessionChart key={session.id} sessionId={session.id} label={session.label} symbol={symbol} format={format} />
      ))}
    </div>
  );
}

type PriceFormat = ReturnType<typeof priceFormat>;

function SessionChart({ sessionId, label, symbol, format }: { sessionId: string; label: string; symbol: string; format: PriceFormat }) {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  
  // We keep references to the specific series so we can update them imperatively 
  // bypassing the React Virtual DOM entirely for HFT performance.
  const bloombergSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const reutersSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // 1. Initialize Lightweight Chart
    const chart = createChart(chartContainerRef.current, {
      layout: { background: { color: 'transparent' }, textColor: '#64748b' },
      grid: { vertLines: { color: '#1e2235' }, horzLines: { color: '#1e2235' } },
      timeScale: { timeVisible: true, secondsVisible: true, borderVisible: false },
      rightPriceScale: { borderVisible: false },
      crosshair: { mode: 0 },
      handleScroll: false,
      handleScale: false
    });

    // 2. Add HFT Series
    const bbgSeries = chart.addSeries(LineSeries, { color: '#f97316', lineWidth: 1, title: 'BBG', priceFormat: format }); // Orange
    const rtSeries = chart.addSeries(LineSeries, { color: '#06b6d4', lineWidth: 1, title: 'RT', priceFormat: format }); // Cyan

    chartRef.current = chart;
    bloombergSeriesRef.current = bbgSeries;
    reutersSeriesRef.current = rtSeries;

    // Resize Observer to keep charts responsive
    const resizeObserver = new ResizeObserver(entries => {
      if (entries.length === 0 || entries[0].target !== chartContainerRef.current) return;
      const newRect = entries[0].contentRect;
      chart.applyOptions({ width: newRect.width, height: newRect.height });
    });
    resizeObserver.observe(chartContainerRef.current);

    // 3. Zustand HFT Subscription (Vanilla JS Binding)
    // We subscribe directly to the store outside of the React render cycle.
    // Every time a new tick hits, we directly mutate the canvas.
    const unsubscribe = useArbexStore.subscribe(
      (state) => ({
        bbg: state.activePairs[`bloomberg_${sessionId}_${symbol.toLowerCase()}`],
        rt: state.activePairs[`reuters_${sessionId}_${symbol.toLowerCase()}`]
      }),
      (pairs) => {
        // Use Math.floor to ensure time is a valid lightweight-charts integer
        const time = Math.floor(Date.now() / 1000) as import('lightweight-charts').Time;
        
        // Update Bloomberg
        if (pairs.bbg && bloombergSeriesRef.current) {
           // Average the bid/ask for the unified chart line
           const midPrice = (pairs.bbg.bid + pairs.bbg.ask) / 2;
           bloombergSeriesRef.current.update({ time, value: midPrice });
        }
        
        // Update Reuters
        if (pairs.rt && reutersSeriesRef.current) {
           const midPrice = (pairs.rt.bid + pairs.rt.ask) / 2;
           reutersSeriesRef.current.update({ time, value: midPrice });
        }
      },
      { fireImmediately: true }
    );

    return () => {
      unsubscribe();
      resizeObserver.disconnect();
      if (chartRef.current) {
        chartRef.current.remove();
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, symbol]);

  return (
    <div className="bg-[#141620] border border-[#1e2235] rounded-lg p-2 flex flex-col h-[280px]">
      <div className="flex justify-between items-center mb-2 px-1">
        <span className="text-[11px] font-mono text-slate-400 tracking-widest uppercase">{label}</span>
        <div className="flex gap-3">
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-orange-500"></span>
            <span className="text-[9px] text-slate-500 font-mono tracking-widest">BBG</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-cyan-500"></span>
            <span className="text-[9px] text-slate-500 font-mono tracking-widest">RT</span>
          </div>
        </div>
      </div>
      
      {/* Target div for the Canvas Injection */}
      <div ref={chartContainerRef} className="flex-1 w-full relative" />
    </div>
  );
}

/**
 * Live and replay feeds: whatever sources the backend streams (NSE, BSE, a
 * second provider...) get a line each, created on first sight, so no source id
 * is hardcoded here.
 */
function VenueChart({ symbol, format }: { symbol: string; format: PriceFormat }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [legend, setLegend] = useState<{ id: string; color: string }[]>([]);

  useEffect(() => {
    if (!containerRef.current) return;
    const chart = createChart(containerRef.current, CHART_OPTIONS);
    const series = new Map<string, ISeriesApi<"Line">>();

    const resizeObserver = new ResizeObserver(entries => {
      if (entries.length === 0) return;
      const rect = entries[0].contentRect;
      chart.applyOptions({ width: rect.width, height: rect.height });
    });
    resizeObserver.observe(containerRef.current);

    const unsubscribe = useArbexStore.subscribe(
      (state) => state.activePairs,
      (pairs) => {
        const time = Math.floor(Date.now() / 1000) as import('lightweight-charts').Time;
        let added = false;
        for (const id of Object.keys(pairs).sort()) {
          if (sourceSymbol(id) !== symbol) continue;
          const p = pairs[id];
          if (!(p.bid > 0 && p.ask > 0)) continue;   // only two-sided quotes are plotted
          let line = series.get(id);
          if (!line) {
            line = chart.addSeries(LineSeries, {
              color: VENUE_COLORS[series.size % VENUE_COLORS.length],
              lineWidth: 2,
              title: sourceLabel(id),
              // e.g. USD/INR futures tick is 0.0025: two decimals would hide the venue gap
              priceFormat: format,
            });
            series.set(id, line);
            added = true;
          }
          line.update({ time, value: (p.bid + p.ask) / 2 });
        }
        if (added) {
          setLegend([...series.keys()].map((id, i) => ({ id, color: VENUE_COLORS[i % VENUE_COLORS.length] })));
        }
      },
      { fireImmediately: true }
    );

    return () => {
      unsubscribe();
      resizeObserver.disconnect();
      chart.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbol]);

  return (
    <div className="bg-[#141620] border border-[#1e2235] rounded-lg p-2 flex flex-col h-[420px]">
      <div className="flex justify-between items-center mb-2 px-1 gap-2">
        <span className="text-[11px] font-mono text-slate-400 tracking-widest uppercase">Mid price by venue</span>
        <div className="flex flex-wrap gap-3 justify-end">
          {legend.map(({ id, color }) => (
            <div key={id} className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full" style={{ background: color }}></span>
              <span className="text-[9px] text-slate-500 font-mono tracking-widest uppercase">{sourceLabel(id)}</span>
            </div>
          ))}
        </div>
      </div>
      <div className="flex-1 w-full relative">
        <div ref={containerRef} className="absolute inset-0" />
        {legend.length === 0 && (
          <div className="absolute inset-0 grid place-items-center text-center px-6 text-[10px] text-slate-500 font-mono tracking-wide">
            Waiting for two-sided quotes. Outside market hours (Mon-Fri 09:00-17:00 IST) books are empty, and BSE often has no orders.
          </div>
        )}
      </div>
      {legend.length === 1 && (
        <p className="text-[9px] text-slate-500 font-mono mt-1 px-1">
          Only one venue has a live book, so there is nothing to compare yet.
        </p>
      )}
    </div>
  );
}
