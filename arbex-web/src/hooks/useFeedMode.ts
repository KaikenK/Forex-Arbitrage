"use client";

import { useCallback, useEffect, useState } from 'react';
import { FeedMode, FeedStatus, useArbexStore } from '@/lib/store';

const API_BASE_URL = process.env.NEXT_PUBLIC_ARBEX_API_BASE_URL || 'http://127.0.0.1:8000';
const POLL_MS = 3000;

async function fetchStatus(): Promise<FeedStatus | null> {
  try {
    const res = await fetch(`${API_BASE_URL}/feeds/mode`, { cache: 'no-store' });
    return res.ok ? ((await res.json()) as FeedStatus) : null;
  } catch {
    return null;
  }
}

/**
 * Current feed (synthetic / live / replay) from the backend, polled so the UI
 * notices switches made elsewhere (another tab, a restart). `switchMode` asks
 * the backend to swap feeds; it keeps the old feed running if the switch fails.
 */
export function useFeedMode() {
  const feedStatus = useArbexStore((state) => state.feedStatus);
  const setFeedStatus = useArbexStore((state) => state.setFeedStatus);
  const [switching, setSwitching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      const status = await fetchStatus();
      if (status && !cancelled) setFeedStatus(status);
    };
    tick();
    const id = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [setFeedStatus]);

  const switchMode = useCallback(async (mode: FeedMode, session?: string) => {
    setSwitching(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE_URL}/feeds/mode`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, session }),
      });
      const body = await res.json();
      if (!res.ok) setError(body.error || `Switch failed (${res.status})`);
      const status = await fetchStatus();
      if (status) setFeedStatus(status);
    } catch {
      setError('Backend unreachable');
    } finally {
      setSwitching(false);
    }
  }, [setFeedStatus]);

  return { feedStatus, switchMode, switching, error };
}
