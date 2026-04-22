"use client";

import React from 'react';
import { useArbexStore, RankedOpportunity } from '@/lib/store';
import { Card } from '@/components/ui/card';
import { motion, AnimatePresence } from 'framer-motion';

const getConfidenceColor = (score: number) => {
  if (score >= 90) return 'text-emerald-400';
  if (score >= 70) return 'text-blue-400';
  if (score >= 50) return 'text-amber-400';
  return 'text-red-400';
};

const formatTime = (timestampMs: number) => {
  const date = new Date(timestampMs);
  return date.toISOString().split('T')[1].slice(0, 8); // HH:MM:SS
};

const RankingRow = ({ opp, index }: { opp: RankedOpportunity; index: number }) => {
  const confidencePercent = (Number(opp.opportunity.confidence_score) * 100).toFixed(1);
  const profit = opp.opportunity.estimated_profit_pips.toFixed(1);

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      transition={{ duration: 0.2 }}
      className="flex items-center justify-between p-2.5 border-b border-[#1e2235] bg-[#0e1018] hover:bg-[#141620] transition-colors"
    >
      <div className="flex items-center gap-3">
        <span className={`text-[10px] font-bold font-mono w-4 ${index === 0 ? 'text-amber-400' : 'text-slate-500'}`}>
          #{index + 1}
        </span>
        <div className="flex flex-col">
          <span className="text-[11px] font-semibold text-slate-200">
            {opp.opportunity.symbols[0]}
          </span>
          <span className="text-[9px] text-slate-500 font-mono">
            {formatTime(opp.opportunity.timestamp_ms || Date.now())}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-4 text-right">
        <div className="flex flex-col">
          <span className="text-[11px] font-bold text-emerald-400 font-mono">
            +{profit} PIPS
          </span>
          <span className={`text-[9px] font-mono ${getConfidenceColor(Number(confidencePercent))}`}>
            {confidencePercent}% CONF
          </span>
        </div>
        <div className="flex flex-col items-end w-[40px]">
          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${
            opp.persistence_class === 'persistent' ? 'bg-emerald-900/30 text-emerald-400 border-emerald-800' :
            opp.persistence_class === 'flickering' ? 'bg-amber-900/30 text-amber-400 border-amber-800' :
            'bg-slate-800/50 text-slate-400 border-slate-700'
          }`}>
            {opp.persistence_class.substring(0, 4).toUpperCase()}
          </span>
          <span className="text-[9px] text-slate-500 font-mono mt-0.5">
            {opp.duration_ms}ms
          </span>
        </div>
      </div>
    </motion.div>
  );
};

export function GlobalRanking() {
  const rankedOpportunities = useArbexStore((state) => state.rankedOpportunities);

  return (
    <div className="flex flex-col gap-2 h-full">
      <h3 className="text-[10px] font-semibold uppercase tracking-widest text-[#505872] mt-1 mb-1 pl-1 shrink-0 flex items-center justify-between">
        <span>Global Opportunity Leaderboard</span>
        <span className="text-[9px] bg-blue-500/10 text-blue-400 border border-blue-500/20 px-1.5 py-0.5 rounded">
          TOP {Math.min(5, rankedOpportunities.length)}
        </span>
      </h3>
      
      <Card className="bg-[#141620] border-[#1e2235] flex flex-col flex-1 min-h-0 overflow-hidden shadow-[0_4px_12px_rgba(0,0,0,0.5)]">
        {rankedOpportunities.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-slate-600 p-6">
            <svg className="w-6 h-6 mb-2 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            <span className="text-[10px] uppercase tracking-widest font-mono text-center">Scanning for<br/>opportunities</span>
          </div>
        ) : (
          <div className="flex-1 overflow-y-auto custom-scrollbar">
            <AnimatePresence mode="popLayout">
              {[...rankedOpportunities]
                .sort((a, b) => b.opportunity.estimated_profit_pips - a.opportunity.estimated_profit_pips)
                .slice(0, 5)
                .map((opp, index) => {
                // Ensure unique key for animations without using fluctuating scores
                const uniqueKey = opp.id || `${opp.opportunity.buy_source}-${opp.opportunity.sell_source}-${opp.opportunity.type}`;
                return (
                  <RankingRow key={uniqueKey} opp={opp} index={index} />
                );
              })}
            </AnimatePresence>
          </div>
        )}
      </Card>
    </div>
  );
}
