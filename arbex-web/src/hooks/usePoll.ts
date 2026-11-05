"use client";

import { useEffect, useState } from "react";
import { API_BASE_URL } from "@/lib/schedule";

export interface Polled<T> {
  data: T | null;
  error: string | null;
  updatedAt: number | null;
}

/** GET `path` on the backend every `ms` (read-only; never used for POST). A 404 is reported as
 *  "not deployed" so views can degrade while a backend addition is waiting for its deploy. */
export function usePoll<T>(path: string, ms: number): Polled<T> {
  const [state, setState] = useState<Polled<T>>({ data: null, error: null, updatedAt: null });
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const res = await fetch(`${API_BASE_URL}${path}`, { cache: "no-store" });
        if (cancelled) return;
        if (res.status === 404) {
          setState((s) => ({ ...s, error: "not deployed" }));
          return;
        }
        if (!res.ok) {
          setState((s) => ({ ...s, error: `HTTP ${res.status}` }));
          return;
        }
        const data = (await res.json()) as T;
        if (!cancelled) setState({ data, error: null, updatedAt: Date.now() });
      } catch {
        if (!cancelled) setState((s) => ({ ...s, error: "backend unreachable" }));
      }
    };
    tick();
    const id = setInterval(tick, ms);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [path, ms]);
  return state;
}
