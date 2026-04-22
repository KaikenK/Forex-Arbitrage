"use client";

import React, { useEffect, useRef } from 'react';
import { createChart, IChartApi, ISeriesApi, LineSeries } from 'lightweight-charts';
import { useArbexStore } from '@/lib/store';

const SESSIONS = [
  { id: 'tokyo', label: 'Tokyo (TYO)' },
  { id: 'london', label: 'London (LDN)' },
  { id: 'newyork', label: 'New York (NYC)' }
];

export function SessionChartGrid() {
  return (
    <div className="flex flex-col gap-3 pb-10">
      {SESSIONS.map((session) => (
        <SessionChart key={session.id} sessionId={session.id} label={session.label} />
      ))}
    </div>
  );
}

function SessionChart({ sessionId, label }: { sessionId: string; label: string }) {
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
    const bbgSeries = chart.addSeries(LineSeries, { color: '#f97316', lineWidth: 1, title: 'BBG' }); // Orange
    const rtSeries = chart.addSeries(LineSeries, { color: '#06b6d4', lineWidth: 1, title: 'RT' }); // Cyan

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
        bbg: state.activePairs[`bloomberg_${sessionId}_usdinr`],
        rt: state.activePairs[`reuters_${sessionId}_usdinr`]
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
  }, [sessionId]);

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
