"use client";

import React from 'react';
import { useArbexStore, RankedOpportunity } from '@/lib/store';
import { motion, AnimatePresence } from 'framer-motion';
import { spreadUnit } from '@/lib/instruments';

const getConfidenceColor = (score: number) => {
  if (score >= 90) return 'text-up';
  if (score >= 70) return 'text-info';
  if (score >= 50) return 'text-risk';
  return 'text-down';
};

const formatTime = (timestampMs: number) => {
  const date = new Date(timestampMs);
  return date.toISOString().split('T')[1].slice(0, 8); // HH:MM:SS
};

const RankingRow = ({ opp, index }: { opp: RankedOpportunity; index: number }) => {
  const confidencePercent = (Number(opp.opportunity.confidence_score) * 100).toFixed(1);
  const profit = opp.opportunity.estimated_profit_pips.toFixed(2);
  const displayTimestamp = opp.opportunity.timestamp_ms ?? opp.last_seen_ms;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95 }}
      transition={{ duration: 0.2 }}
      className="flex items-center justify-between p-2.5 border-b border-line bg-panel hover:bg-raised transition-colors"
    >
      <div className="flex items-center gap-3">
        <span className={`text-[10px] font-bold font-mono w-4 ${index === 0 ? 'text-risk' : 'text-ink-3'}`}>
          #{index + 1}
        </span>
        <div className="flex flex-col">
          <span className="text-[11px] font-semibold text-ink">
            {opp.opportunity.symbols[0]}
          </span>
          <span className="text-[9px] text-ink-3 font-mono">
            {formatTime(displayTimestamp)}
          </span>
        </div>
      </div>

      <div className="flex items-center gap-4 text-right">
        <div className="flex flex-col">
          <span className="text-[11px] font-bold text-up font-mono">
            +{profit} {spreadUnit(opp.opportunity.symbols[0] ?? '')}
          </span>
          <span className={`text-[9px] font-mono ${getConfidenceColor(Number(confidencePercent))}`}>
            {confidencePercent}% CONF
          </span>
        </div>
        <div className="flex flex-col items-end w-[40px]">
          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border ${
            opp.persistence_class === 'persistent' ? 'bg-up/10 text-up border-up/40' :
            opp.persistence_class === 'flickering' ? 'bg-risk/10 text-risk border-risk/40' :
            'bg-raised/50 text-ink-2 border-line-strong'
          }`}>
            {opp.persistence_class.substring(0, 4).toUpperCase()}
          </span>
          <span className="text-[9px] text-ink-3 font-mono mt-0.5">
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
    <div className="flex h-full flex-col">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {rankedOpportunities.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-ink-3 p-6">
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
                .map((opp, index) => (
                  <RankingRow
                    key={opp.id || `${opp.opportunity.buy_source}-${opp.opportunity.sell_source}-${opp.opportunity.type}`}
                    opp={opp}
                    index={index}
                  />
                ))}
            </AnimatePresence>
          </div>
        )}
      </div>
    </div>
  );
}
