import React from 'react';
import { useArbexStore, SessionPriceInfo } from '@/lib/store';
import { Badge } from '@/components/ui/badge';
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area';

export function TickerStrip() {
  const activePairs = useArbexStore((state) => state.activePairs);
  const totalTicksLogged = useArbexStore((state) => state.totalTicksLogged);
  const detectedRate = useArbexStore((state) => state.detectedRate);

  return (
    <div className="bg-[#141620] border-b border-[#1e2235] px-2 py-1 flex items-center justify-between shrink-0 overflow-hidden">
      <ScrollArea className="w-full whitespace-nowrap">
        <div className="flex w-max space-x-4 p-1">
          {Object.entries(activePairs).map(([sourceId, data]) => (
            <div key={sourceId} className="flex flex-col border-r border-[#1e2235] pr-4 min-w-[120px]">
              <div className="text-[9px] font-mono text-slate-500 uppercase tracking-wider mb-0.5">
                {data.session} - {sourceId.split('_')[0]}
              </div>
              <div className="flex justify-between items-center text-[10px] space-x-3">
                <span className="text-slate-400">BID <span className={`font-mono text-[11px] font-medium ${data.drift > 0 ? 'text-emerald-400' : data.drift < 0 ? 'text-red-400' : 'text-slate-300'}`}>{(data.bid ?? 0).toFixed(4)}</span></span>
                <span className="text-slate-400">ASK <span className={`font-mono text-[11px] font-medium ${data.drift > 0 ? 'text-emerald-400' : data.drift < 0 ? 'text-red-400' : 'text-slate-300'}`}>{(data.ask ?? 0).toFixed(4)}</span></span>
              </div>
            </div>
          ))}
        </div>
        <ScrollBar orientation="horizontal" className="hidden" />
      </ScrollArea>

      <div className="flex items-center gap-3 pl-4 border-l border-[#1e2235] shrink-0">
        <div className="flex flex-col items-end">
          <span className="text-[8px] text-slate-500 uppercase tracking-widest">Ticks Scanned</span>
          <span className="text-[10px] font-mono text-blue-400">{totalTicksLogged.toLocaleString()}</span>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-[8px] text-slate-500 uppercase tracking-widest">Anomaly %</span>
          <span className="text-[10px] font-mono text-purple-400">{(detectedRate ?? 0).toFixed(2)}%</span>
        </div>
      </div>
    </div>
  );
}
