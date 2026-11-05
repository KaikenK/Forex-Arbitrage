"use client";

import { useSyncExternalStore } from "react";

/* One shared 1-second clock for every clock and countdown. null on the server and on the first
   client render, so server and client markup match; the time arrives right after mount. */
let now = 0;
let timer: ReturnType<typeof setInterval> | null = null;
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  if (timer === null) {
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      listeners.forEach((l) => l());
    }, 1000);
  }
  queueMicrotask(listener);
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && timer !== null) {
      clearInterval(timer);
      timer = null;
      now = 0;
    }
  };
}

const snapshot = () => (now === 0 ? null : now);
const serverSnapshot = () => null;

export function useNow(): number | null {
  return useSyncExternalStore(subscribe, snapshot, serverSnapshot);
}
