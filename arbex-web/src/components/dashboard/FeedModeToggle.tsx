"use client";

import React from 'react';
import { FeedMode } from '@/lib/store';
import { useFeedMode } from '@/hooks/useFeedMode';
import { Chip } from '@/components/terminal/primitives';
import { cn } from '@/lib/utils';

const MODES: { mode: FeedMode; label: string; title: string }[] = [
  { mode: 'synthetic', label: 'Synthetic', title: 'Generated session feeds (six per pair)' },
  { mode: 'live', label: 'Live', title: 'Real spot venues (Kraken / TrueFX) and, with tokens, NSE/BSE futures' },
  { mode: 'replay', label: 'Replay', title: 'Replay a recorded live session' },
];

/** Health › Controls: switch the backend between synthetic, live and replay feeds. Every switch asks
 *  first, because leaving the live feed stops live recording until it is switched back. */
export function FeedModeToggle() {
  const { feedStatus, switchMode, switching, error } = useFeedMode({ poll: false });
  const current = feedStatus?.mode ?? null;
  const sessions = feedStatus?.available.replay.sessions ?? [];
  const [session, setSession] = React.useState<string>('');
  const [pending, setPending] = React.useState<{ mode: FeedMode; session?: string } | null>(null);
  const chosenSession = session || feedStatus?.replay_session || sessions[0] || '';

  if (!feedStatus) return <span className="text-[11px] text-ink-3">Feed status loading…</span>;
  if (!feedStatus.available.switchable) return <Chip tone="muted">MT5 feed (not switchable)</Chip>;

  // A stale backend error is only worth showing while the feed is actually empty.
  const problem = error || (feedStatus.sources.length === 0 ? feedStatus.last_error : null);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <div role="radiogroup" aria-label="Data feed" className="flex overflow-hidden rounded-term border border-line-strong">
          {MODES.map(({ mode, label, title }) => {
            const info = feedStatus.available[mode];
            const active = current === mode;
            const disabled = switching || !info.available;
            return (
              <button key={mode} type="button" role="radio" aria-checked={active}
                disabled={disabled && !active}
                title={info.available ? title : info.reason ?? title}
                onClick={() => !active && setPending({ mode, session: mode === 'replay' ? chosenSession : undefined })}
                className={cn('px-3 py-1 text-[11px] font-semibold uppercase tracking-wide transition-colors',
                  active ? (mode === 'live' ? 'bg-up text-canvas' : mode === 'replay' ? 'bg-info text-white' : 'bg-raised text-ink')
                    : 'text-ink-2 hover:bg-raised',
                  disabled && !active ? 'cursor-not-allowed opacity-40 hover:bg-transparent' : 'cursor-pointer')}>
                {label}
              </button>
            );
          })}
        </div>
        {sessions.length > 0 && (
          <select aria-label="Recorded session to replay" value={chosenSession} disabled={switching}
            className="rounded-term border border-line-strong bg-raised px-1.5 py-1 text-[11px] text-ink outline-none"
            onChange={(e) => {
              setSession(e.target.value);
              if (current === 'replay') setPending({ mode: 'replay', session: e.target.value });
            }}>
            {sessions.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        )}
        {switching && <span className="num text-[11px] text-ink-2">switching…</span>}
      </div>

      {pending && (
        <div role="alertdialog" aria-label="Confirm feed switch"
          className="flex flex-wrap items-center gap-3 rounded-term border border-risk/40 bg-risk/10 px-3 py-2">
          <span className="text-[12px] text-ink">
            Switch to <b className="uppercase">{pending.mode}</b>{pending.session ? ` (${pending.session})` : ''}?
            {current === 'live' && ' Live recording stops until the feed is switched back.'}
          </span>
          <span className="ml-auto flex gap-2">
            <button type="button" onClick={() => setPending(null)}
              className="rounded-term border border-line-strong px-2.5 py-1 text-[11px] text-ink-2 hover:bg-raised">Cancel</button>
            <button type="button"
              onClick={() => { const p = pending; setPending(null); switchMode(p.mode, p.session); }}
              className="rounded-term border border-risk/60 bg-risk/20 px-2.5 py-1 text-[11px] font-semibold text-risk hover:bg-risk/30">
              Switch feed
            </button>
          </span>
        </div>
      )}
      {problem && !switching && <span className="truncate text-[11px] text-down" title={problem}>{problem}</span>}
    </div>
  );
}
