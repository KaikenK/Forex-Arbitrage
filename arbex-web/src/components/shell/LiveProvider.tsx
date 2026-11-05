"use client";

import React from "react";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useFeedMode } from "@/hooks/useFeedMode";

/** One WebSocket connection and one feed-status poller for every tab (read-only: GET and WS). */
export function LiveProvider({ children }: { children: React.ReactNode }) {
  useWebSocket();
  useFeedMode();
  return <>{children}</>;
}
