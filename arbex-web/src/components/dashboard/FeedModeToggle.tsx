"use client";

import React from 'react';
import { FeedMode } from '@/lib/store';
import { useFeedMode } from '@/hooks/useFeedMode';

const MODES: { mode: FeedMode; label: string; title: string }[] = [
  { mode: 'synthetic', label: 'Synthetic', title: 'Six generated Bloomberg/Reuters x session feeds' },
  { mode: 'live', label: 'Live', title: 'Real NSE/BSE USD/INR futures books (Upstox / Kite)' },
  { mode: 'replay', label: 'Replay', title: 'Replay a recorded live session' },
];

/** Header control: switch the backend between synthetic, live and replay feeds. */
export function FeedModeToggle() {
  const { feedStatus, switchMode, switching, error } = useFeedMode();
  const current = feedStatus?.mode ?? null;
  const sessions = feedStatus?.available.replay.sessions ?? [];
  const [session, setSession] = React.useState<string>('');
  const chosenSession = session || feedStatus?.replay_session || sessions[0] || '';

  if (!feedStatus) {
    return <span className="text-[10px] font-mono text-slate-500">FEED: …</span>;
  }
  if (!feedStatus.available.switchable) {
    return (
      <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-700 text-white font-semibold tracking-wide">
        MT5
      </span>
    );
  }

  // A stale backend error is only worth showing while the feed is actually empty.
  const problem = error || (feedStatus.sources.length === 0 ? feedStatus.last_error : null);

  return (
    <div className="flex items-center gap-2">
      <div role="radiogroup" aria-label="Data feed" className="flex rounded border border-[#2a2f45] overflow-hidden">
        {MODES.map(({ mode, label, title }) => {
          const info = feedStatus.available[mode];
          const active = current === mode;
          const disabled = switching || !info.available;
          return (
            <button
              key={mode}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={disabled && !active}
              title={info.available ? title : info.reason ?? title}
              onClick={() => !active && switchMode(mode, mode === 'replay' ? chosenSession : undefined)}
              className={[
                'text-[10px] px-2 py-0.5 font-semibold tracking-wide uppercase transition-colors',
                active
                  ? mode === 'synthetic' ? 'bg-purple-500 text-white' : mode === 'live' ? 'bg-emerald-600 text-white' : 'bg-amber-500 text-black'
                  : 'text-slate-400 hover:bg-[#1e2235]',
                disabled && !active ? 'opacity-40 cursor-not-allowed hover:bg-transparent' : 'cursor-pointer',
              ].join(' ')}
            >
              {label}
            </button>
          );
        })}
      </div>
      {sessions.length > 0 && (
        <select
          aria-label="Recorded session to replay"
          className="text-[10px] bg-[#1e2235] text-slate-300 rounded px-1 py-0.5 outline-none border border-[#2a2f45]"
          value={chosenSession}
          disabled={switching}
          onChange={(e) => {
            setSession(e.target.value);
            if (current === 'replay') switchMode('replay', e.target.value);
          }}
        >
          {sessions.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
      )}
      {switching && <span className="text-[10px] font-mono text-slate-400">switching…</span>}
      {problem && !switching && (
        <span className="text-[10px] text-red-400 max-w-[320px] truncate" title={problem}>{problem}</span>
      )}
    </div>
  );
}
