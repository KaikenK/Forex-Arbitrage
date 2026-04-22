"use client";

import React from 'react';
import { useWebSocket } from '@/hooks/useWebSocket';
import { useArbexStore } from '@/lib/store';
import { TickerStrip } from '@/components/dashboard/TickerStrip';
import { SessionChartGrid } from '@/components/dashboard/SessionChartGrid';
import { ArbitrageFeed } from '@/components/dashboard/ArbitrageFeed';
import { ExecutionPanel } from '@/components/dashboard/ExecutionPanel';
import { OrderbookFeed } from '@/components/dashboard/OrderbookFeed';

export default function ArbexDashboard() {
  // Initialize the Buffered WebSocket Connection
  useWebSocket();

  const isConnected = useArbexStore((state) => state.isConnected);

  return (
    <div className="bg-[#08090d] text-[#e8ecf4] min-h-screen font-sans overflow-hidden flex flex-col">
      {/* ═══ TOP BAR ═══ */}
      <header className="bg-[#0e1018] border-b border-[#1e2235] h-10 px-4 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-2.5">
          <svg viewBox="0 0 24 24" className="w-[18px] h-[18px] fill-blue-500">
             <path d="M3 13h8V3H3v10zm0 8h8v-6H3v6zm10 0h8V11h-8v10zm0-18v6h8V3h-8z" />
          </svg>
          <h1 className="text-[13px] font-semibold tracking-wide uppercase">Arbex</h1>
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-500 text-white font-semibold tracking-wide ml-2">
            SYNTHETIC USD/INR
          </span>
        </div>

        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5 text-[11px] text-slate-400 font-mono">
            <span 
              className={`w-[7px] h-[7px] rounded-full transition-colors duration-300 ${
                isConnected ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)] animate-pulse' : 'bg-red-500'
              }`} 
            />
            {isConnected ? 'LIVE' : 'OFFLINE'}
          </div>
          <div className="text-[11px] text-slate-400 font-mono tracking-widest">
            SESSION: NEW YORK
          </div>
        </div>
      </header>

      {/* ═══ TICKER STRIP ═══ */}
      <TickerStrip />

      {/* ═══ MAIN 3-COL GRID ═══ */}
      <main className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-[1fr_1.1fr_0.9fr] gap-[1px] bg-[#1e2235] h-[calc(100vh-72px)] overflow-hidden">
        
        {/* COL 1: Sessions */}
        <section className="bg-[#0e1018] flex flex-col overflow-hidden">
          <header className="bg-[#141620] px-3.5 py-2 border-b border-[#1e2235] flex justify-between items-center shrink-0">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-[#505872]">Market Sessions</span>
            <div className="flex gap-1.5">
              <span className="text-[9px] px-1.5 py-0.5 rounded border border-red-400 text-red-400">TYO</span>
              <span className="text-[9px] px-1.5 py-0.5 rounded border border-blue-400 text-blue-400">LDN</span>
              <span className="text-[9px] px-1.5 py-0.5 rounded border border-emerald-400 text-emerald-400">NYC</span>
            </div>
          </header>
          <div className="flex-1 overflow-y-auto p-2.5 custom-scrollbar">
            <SessionChartGrid />
          </div>
        </section>

        {/* COL 2: Opportunities */}
        <section className="bg-[#0e1018] flex flex-col overflow-hidden border-x border-[#1e2235]">
          <header className="bg-[#141620] px-3.5 py-2 border-b border-[#1e2235] flex justify-between items-center shrink-0">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-[#505872]">Arbitrage Feed</span>
          </header>
          <div className="flex-1 overflow-y-auto p-2.5 custom-scrollbar">
             <ArbitrageFeed />
          </div>
        </section>

        {/* COL 3: Orderbook & Execution */}
        <section className="bg-[#0e1018] flex flex-col overflow-hidden">
          <header className="bg-[#141620] px-3.5 py-2 border-b border-[#1e2235] flex justify-between items-center shrink-0">
            <span className="text-[10px] font-semibold uppercase tracking-widest text-[#505872]">Market Depth & Execution</span>
          </header>
          <div className="flex-1 overflow-y-auto p-2.5 custom-scrollbar flex flex-col gap-4">
            <div className="shrink-0">
               <OrderbookFeed />
            </div>
            <ExecutionPanel />
          </div>
        </section>
      </main>
    </div>
  );
}
