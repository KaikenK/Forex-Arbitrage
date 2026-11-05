"use client";

import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { useArbexStore, RankedOpportunity } from '@/lib/store';
import { spreadUnit } from '@/lib/instruments';
import { parseSourceId } from '@/lib/sources';
import { riskOf } from '@/lib/semantic';
import { Chip, EmptyState, ago } from '@/components/terminal/primitives';
import { cn } from '@/lib/utils';

const venue = (id: string) => parseSourceId(id).group || id;
const PERSISTENCE_TONE = { persistent: 'info', flickering: 'muted', ephemeral: 'muted' } as const;

function CrossRow({ opp, rank, selected, now, onSelect }: {
  opp: RankedOpportunity; rank: number; selected: boolean; now: number; onSelect: () => void;
}) {
  const { opportunity } = opp;
  const risk = riskOf(opp);
  const pair = opportunity.symbols[0] ?? '';
  const conf = Number(opportunity.confidence_score) * 100;
  return (
    <motion.button
      type="button"
      layout
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, transition: { duration: 0.15 } }}
      transition={{ type: 'spring', stiffness: 380, damping: 30 }}
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'relative grid w-full grid-cols-[1fr_auto] gap-x-3 gap-y-1 border-b border-line px-3 py-2 text-left transition-colors',
        selected ? 'bg-info/10' : 'hover:bg-raised',
      )}
    >
      <span className={cn('absolute inset-y-0 left-0 w-0.5', rank === 1 ? 'bg-up' : selected ? 'bg-info' : 'bg-transparent')} />
      <span className="flex min-w-0 items-center gap-2">
        <span className="num text-[13px] font-semibold text-ink">{pair}</span>
        <span className="num truncate text-[11px] text-ink-3">
          {venue(opportunity.buy_source)} <span className="text-ink-3/60">→</span> {venue(opportunity.sell_source)}
        </span>
      </span>
      <span className="num text-right text-[14px] font-semibold text-up">
        +{opportunity.estimated_profit_pips.toFixed(2)}
        <span className="ml-1 text-[10px] font-normal text-ink-3">{spreadUnit(pair).toLowerCase()}</span>
      </span>
      <span className="flex min-w-0 flex-wrap items-center gap-1">
        {risk.available ? (
          <Chip tone={risk.penalty > 0 ? 'risk' : 'muted'} title={risk.reason}>
            risk {risk.penalty > 0 ? `−${risk.penalty.toFixed(3)}` : '0'}
          </Chip>
        ) : (
          <Chip tone="muted" title={risk.reason}>risk —</Chip>
        )}
        {risk.proximity && <Chip tone="risk" title="Scheduled release near this cross">{risk.proximity}</Chip>}
        <Chip tone={PERSISTENCE_TONE[opp.persistence_class] ?? 'muted'}>{opp.persistence_class}</Chip>
      </span>
      <span className="num flex items-center justify-end gap-2 text-[11px] text-ink-3">
        <span title="Confidence shown by the display ranking">{conf.toFixed(1)}%</span>
        <span>{ago(opp.last_seen_ms, now)}</span>
      </span>
    </motion.button>
  );
}

/** Live crosses, best first. Selecting one shows it in "Why this cross". */
export function ArbitrageFeed() {
  const ranked = useArbexStore((s) => s.rankedOpportunities);
  const selectedId = useArbexStore((s) => s.selectedOpportunityId);
  const select = useArbexStore((s) => s.setSelectedOpportunity);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  if (ranked.length === 0) {
    return <EmptyState title="No crosses yet"
      detail="A cross appears when one venue's bid rises above another venue's ask for the same pair." />;
  }
  const active = selectedId && ranked.some((o) => o.id === selectedId) ? selectedId : ranked[0].id;
  return (
    <div className="flex flex-col">
      <AnimatePresence mode="popLayout" initial={false}>
        {ranked.map((opp, i) => (
          <CrossRow key={opp.id} opp={opp} rank={i + 1} now={now} selected={opp.id === active}
            onSelect={() => select(opp.id)} />
        ))}
      </AnimatePresence>
    </div>
  );
}
