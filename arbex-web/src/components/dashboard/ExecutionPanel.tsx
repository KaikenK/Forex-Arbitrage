"use client";

import React from 'react';
import { useArbexStore } from '@/lib/store';
import { Card } from '@/components/ui/card';

const MetricBadge = ({ label, value, unit, color }: { label: string, value: string | number, unit: string, color?: string }) => (
  <div className="flex flex-col bg-[#141620] p-2 rounded border border-[#1e2235]">
    <span className="text-[9px] uppercase tracking-widest text-slate-500 mb-1">{label}</span>
    <div className="flex items-baseline gap-1">
      <span className={`text-[15px] font-mono leading-none ${color || 'text-slate-300'}`}>{value}</span>
      <span className="text-[10px] font-mono text-slate-500 leading-none">{unit}</span>
    </div>
  </div>
);

export function ExecutionPanel() {
  // We strictly subscribe to the #1 ranked opportunity. 
  // If the #1 spot changes, the execution panel flips to the new target.
  const topOpportunity = useArbexStore((state) => 
    state.rankedOpportunities.length > 0 ? state.rankedOpportunities[0] : null
  );

  if (!topOpportunity) {
    return (
      <div className="h-full flex flex-col items-center justify-center p-10 text-slate-600 border border-dashed border-[#1e2235] rounded-lg mt-4">
        <svg className="w-8 h-8 mb-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
        </svg>
        <span className="text-[11px] uppercase tracking-widest font-mono">No Execution Targets</span>
      </div>
    );
  }

  const { opportunity, execution_verdict, persistence_class, duration_ms, persistence_count, execution_reasons } = topOpportunity;

  return (
    <div className="flex flex-col gap-4">
      {/* Prime Header */}
      <Card className="bg-[#141620] border-[#1e2235] p-4 shadow-[0_4px_12px_rgba(0,0,0,0.5)]">
        <div className="flex justify-between items-start mb-4">
          <div className="flex items-center gap-3">
            <span className="text-white text-sm font-bold font-mono tracking-widest">{opportunity.symbols[0]}</span>
            <span className={`text-[10px] font-semibold px-2 py-0.5 rounded border ${
               execution_verdict === 'viable' ? 'bg-emerald-900/30 text-emerald-400 border-emerald-800 animate-pulse' :
               execution_verdict === 'risky' ? 'bg-amber-900/30 text-amber-400 border-amber-800' :
               'bg-slate-800/50 text-slate-400 border-slate-700'
             }`}>
               {execution_verdict === 'viable' ? '✅ VIABLE EXECUTION' : execution_verdict === 'risky' ? '⚠️ HIGH EX RISK' : '❓ UNKNOWN'}
            </span>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-2">
           <MetricBadge 
             label="Net Profit" 
             value={opportunity.estimated_profit_pips.toFixed(1)} 
             unit="PIPS" 
             color="text-emerald-400 font-bold" 
           />
           <MetricBadge 
             label="Confidence" 
             value={(Number(opportunity.confidence_score) * 100).toFixed(1)} 
             unit="%" 
             color="text-blue-400" 
           />
           <MetricBadge 
             label="Hardware Latency" 
             value={opportunity.latency_risk_ms} 
             unit="ms" 
             color="text-amber-400" 
           />
           <MetricBadge 
             label="Persistence TTL" 
             value={duration_ms} 
             unit="ms" 
             color={duration_ms > 2500 ? 'text-emerald-400' : 'text-red-400'} 
           />
        </div>
      </Card>

      {/* Routing Diagram */}
      <h3 className="text-[10px] font-semibold uppercase tracking-widest text-[#505872] mt-2 mb-1 pl-1">Routing Hypothesis</h3>
      <Card className="bg-[#141620] border-[#1e2235] p-3 text-xs font-mono scroll-area">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-slate-500">BUY LEG</span>
            <span className="text-blue-400">{opportunity.buy_source}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-slate-500">PRICE</span>
            <span className="text-slate-300">{opportunity.buy_price.toFixed(5)}</span>
          </div>
          <div className="border-t border-[#1e2235] my-1" />
          <div className="flex items-center justify-between">
            <span className="text-slate-500">SELL LEG</span>
            <span className="text-purple-400">{opportunity.sell_source}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-slate-500">PRICE</span>
            <span className="text-slate-300">{opportunity.sell_price.toFixed(5)}</span>
          </div>
        </div>
      </Card>

      {/* Semantic Diagnostics */}
      <h3 className="text-[10px] font-semibold uppercase tracking-widest text-[#505872] mt-2 mb-1 pl-1">Semantic Engine Output</h3>
      <Card className="bg-[#141620] border-[#1e2235] p-3 text-[10px] font-mono scroll-area">
        <ul className="list-disc list-inside text-slate-400 space-y-1">
          {execution_reasons && execution_reasons.length > 0 ? (
            execution_reasons.map((reason, i) => (
              <li key={i} className={reason.includes('⚠️') ? 'text-amber-400' : reason.includes('✅') ? 'text-emerald-400' : ''}>
                {reason}
              </li>
            ))
          ) : (
            <li>No execution grading reasons provided by backend.</li>
          )}
        </ul>
      </Card>
    </div>
  );
}
