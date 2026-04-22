"use client";

import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useArbexStore, RankedOpportunity } from '@/lib/store';
import { Card } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

const getConfidenceColor = (score: number) => {
  if (score >= 90) return 'text-emerald-400 bg-emerald-400/10 border-emerald-400/20';
  if (score >= 70) return 'text-blue-400 bg-blue-400/10 border-blue-400/20';
  if (score >= 50) return 'text-amber-400 bg-amber-400/10 border-amber-400/20';
  return 'text-red-400 bg-red-400/10 border-red-400/20';
};

const OpportunityCard = ({ rankObj }: { rankObj: RankedOpportunity }) => {
  const { opportunity, composite_score, rank, persistence_class, execution_verdict } = rankObj;
  
  // Generating a deterministic key for framer motion re-ordering
  const uniqueKey = `${opportunity.buy_source}-${opportunity.sell_source}-${opportunity.type}-${composite_score.toFixed(1)}`;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, scale: 0.95, y: -20 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.95, transition: { duration: 0.2 } }}
      transition={{ type: 'spring', stiffness: 350, damping: 25 }}
      // Use the raw ranking score as part of the key to force re-animation if the score meaningfully changes,
      // or just base it on the exchanges if we want smooth layout re-ordering.
      // We will use layout animation strictly based on identity.
    >
      <Card className="bg-[#141620] border-[#1e2235] p-3 mb-2 shadow-[0_4px_12px_rgba(0,0,0,0.5)] hover:border-[#2a2f4c] transition-colors cursor-pointer relative overflow-hidden group">
        
        {/* Rank Accents */}
        {rank === 1 && (
          <div className="absolute top-0 left-0 w-1 h-full bg-emerald-500 shadow-[0_0_12px_#10b981]" />
        )}
        
        <div className="flex justify-between items-start mb-2 pl-2">
          <div className="flex flex-col">
             <div className="flex items-center gap-2">
               <span className="text-white text-xs font-bold font-mono tracking-widest">{opportunity.symbols[0]}</span>
               <Badge variant="outline" className={`text-[9px] uppercase tracking-widest px-1.5 py-0 border-0 ${getConfidenceColor(Number(opportunity.confidence_score) * 100)}`}>
                 {(Number(opportunity.confidence_score) * 100).toFixed(1)}% CONF
               </Badge>
             </div>
             <span className="text-[10px] text-slate-500 uppercase tracking-widest mt-0.5">
               {opportunity.type}
             </span>
          </div>

          <div className="flex flex-col items-end">
             <span className="text-[14px] font-mono text-emerald-400 font-bold tracking-tight">
               +{opportunity.estimated_profit_pips.toFixed(1)} PIPS
             </span>
             <span className="text-[9px] text-slate-500 font-mono tracking-widest">
               {rankObj.duration_ms}ms TTL
             </span>
          </div>
        </div>

        <div className="bg-[#0e1018] rounded px-2 py-1.5 flex justify-between items-center mt-3 border border-[#1e2235]">
          <div className="flex flex-col">
            <span className="text-[8px] text-slate-500 uppercase tracking-widest">BUY</span>
            <span className="text-[10px] font-mono text-blue-400">{opportunity.buy_source}</span>
          </div>
          <svg className="w-4 h-4 text-slate-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
             <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M14 5l7 7m0 0l-7 7m7-7H3" />
          </svg>
          <div className="flex flex-col text-right">
            <span className="text-[8px] text-slate-500 uppercase tracking-widest">SELL</span>
            <span className="text-[10px] font-mono text-purple-400">{opportunity.sell_source}</span>
          </div>
        </div>

        <div className="flex justify-between items-center mt-2.5">
           <div className="flex gap-1.5">
             <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded border ${
               execution_verdict === 'viable' ? 'bg-emerald-900/30 text-emerald-400 border-emerald-800' :
               execution_verdict === 'risky' ? 'bg-amber-900/30 text-amber-400 border-amber-800' :
               'bg-slate-800/50 text-slate-400 border-slate-700'
             }`}>
               {execution_verdict === 'viable' ? '✅ VIABLE' : execution_verdict === 'risky' ? '⚠️ RISKY' : '❓ UNKNOWN'}
             </span>
             <span className={`text-[9px] font-semibold px-1.5 py-0.5 rounded border ${
               persistence_class === 'persistent' ? 'bg-blue-900/30 text-blue-400 border-blue-800' :
               persistence_class === 'flickering' ? 'bg-purple-900/30 text-purple-400 border-purple-800' :
               'bg-red-900/30 text-red-400 border-red-800'
             }`}>
               {persistence_class === 'persistent' ? '🧱 PERSISTENT' : persistence_class === 'flickering' ? '⚡ FLICKERING' : '👻 EPHEMERAL'}
             </span>
           </div>
           
           <span className="text-[10px] text-slate-400 font-mono font-bold bg-[#1e2235] px-2 py-0.5 rounded">
             {composite_score.toFixed(1)}
           </span>
        </div>
      </Card>
    </motion.div>
  );
};

export function ArbitrageFeed() {
  const rankedOpportunities = useArbexStore((state) => state.rankedOpportunities);

  return (
    <div className="w-full flex flex-col gap-1 pb-10">
      <AnimatePresence mode="popLayout">
        {rankedOpportunities.length === 0 ? (
          <motion.div 
            initial={{ opacity: 0 }} 
            animate={{ opacity: 1 }} 
            className="flex flex-col items-center justify-center p-10 text-slate-600 border border-dashed border-[#1e2235] rounded-lg mt-4"
          >
            <svg className="w-8 h-8 mb-3 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M13 10V3L4 14h7v7l9-11h-7z" />
            </svg>
            <span className="text-[11px] uppercase tracking-widest font-mono">No Active Anomalies</span>
            <span className="text-[9px] uppercase tracking-widest mt-1">Waiting for Tick Stream...</span>
          </motion.div>
        ) : (
          rankedOpportunities.map((opp) => (
             // Use the strict identity assigned at birth to ensure Framer Motion `layout` triggers smoothly 
             // without unmounting and remounting the div on metric updates
            <OpportunityCard 
              key={opp.id} 
              rankObj={opp} 
            />
          ))
        )}
      </AnimatePresence>
    </div>
  );
}
