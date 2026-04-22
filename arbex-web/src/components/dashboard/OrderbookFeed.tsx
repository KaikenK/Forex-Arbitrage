"use client";

import React, { useMemo } from 'react';
import { useArbexStore } from '@/lib/store';
import { Card } from '@/components/ui/card';
import { motion } from 'framer-motion';

export function OrderbookFeed() {
  const orderbookData = useArbexStore((state) => state.orderbookData);

  const [selectedSource, setSelectedSource] = React.useState<string | null>(null);

  const availableSources = useMemo(() => Object.keys(orderbookData), [orderbookData]);

  const activeOrderbook = useMemo(() => {
    if (availableSources.length === 0) return null;
    if (selectedSource && orderbookData[selectedSource]) return orderbookData[selectedSource];
    if (orderbookData['bloomberg_tokyo_usdinr']) return orderbookData['bloomberg_tokyo_usdinr'];
    return orderbookData[availableSources[0]];
  }, [orderbookData, selectedSource, availableSources]);

  if (!activeOrderbook) {
    return (
      <Card className="bg-[#141620] border-[#1e2235] p-4 flex flex-col items-center justify-center h-[200px]">
        <span className="text-[10px] text-slate-500 uppercase tracking-widest">No Depth Data Available</span>
      </Card>
    );
  }

  // Calculate max volume for relative progress bars
  const maxVolume = useMemo(() => {
    let max = 0;
    activeOrderbook.bids.forEach(b => { if (b.volume > max) max = b.volume; });
    activeOrderbook.asks.forEach(a => { if (a.volume > max) max = a.volume; });
    return max || 1;
  }, [activeOrderbook]);

  return (
    <Card className="bg-[#141620] border-[#1e2235] p-3 flex flex-col font-mono">
      <div className="flex justify-between items-center mb-3">
        <h3 className="text-[10px] font-semibold uppercase tracking-widest text-[#505872]">Depth of Market (L2)</h3>
        <select 
          className="text-[9px] bg-[#1e2235] px-1.5 py-0.5 rounded text-blue-400 uppercase outline-none cursor-pointer border border-[#1e2235] hover:border-blue-500/50"
          value={activeOrderbook.source}
          onChange={(e) => setSelectedSource(e.target.value)}
        >
          {availableSources.map(src => (
            <option key={src} value={src}>{src.split('_')[0] + ' ' + src.split('_')[1]}</option>
          ))}
        </select>
      </div>

      <div className="flex flex-col gap-1 w-full text-[10px]">
        {/* Header */}
        <div className="flex justify-between text-slate-500 uppercase tracking-widest mb-1 pb-1 border-b border-[#1e2235]">
          <span>Size</span>
          <span>Price</span>
        </div>

        {/* Asks (Sell Orders) - Reverse order so lowest price is at the bottom */}
        <div className="flex flex-col gap-[2px] mb-2">
          {activeOrderbook.asks.slice().reverse().map((ask, idx) => {
            const widthPct = (ask.volume / maxVolume) * 100;
            return (
              <div key={`ask-${idx}`} className="flex justify-between relative py-0.5 group cursor-pointer hover:bg-white/5">
                <div 
                  className="absolute left-0 top-0 h-full bg-red-500/10 z-0 transition-all duration-100" 
                  style={{ width: `${widthPct}%` }}
                />
                <span className="text-slate-400 z-10 pl-1">{ask.volume.toLocaleString()}</span>
                <span className="text-red-400 z-10 pr-1">{ask.price.toFixed(4)}</span>
              </div>
            );
          })}
        </div>

        {/* Spread / Mid Indicator */}
        <div className="flex justify-center items-center py-1 bg-black/20 border-y border-[#1e2235]/50 mb-2">
          <span className="text-slate-500 text-[9px] tracking-widest">
             SPREAD: {((activeOrderbook.asks[0]?.price - activeOrderbook.bids[0]?.price) * 100).toFixed(1)} PIPS
          </span>
        </div>

        {/* Bids (Buy Orders) - Highest price at the top */}
        <div className="flex flex-col gap-[2px]">
          {activeOrderbook.bids.map((bid, idx) => {
            const widthPct = (bid.volume / maxVolume) * 100;
            return (
              <div key={`bid-${idx}`} className="flex justify-between relative py-0.5 group cursor-pointer hover:bg-white/5">
                <div 
                  className="absolute left-0 top-0 h-full bg-emerald-500/10 z-0 transition-all duration-100" 
                  style={{ width: `${widthPct}%` }}
                />
                <span className="text-slate-400 z-10 pl-1">{bid.volume.toLocaleString()}</span>
                <span className="text-emerald-400 z-10 pr-1">{bid.price.toFixed(4)}</span>
              </div>
            );
          })}
        </div>
      </div>
    </Card>
  );
}
