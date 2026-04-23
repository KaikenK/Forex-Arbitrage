"use client";

import { useEffect, useRef } from 'react';
import { MarketSourceUpdate, NewsBias, RankedOpportunity, useArbexStore } from '@/lib/store';

const WS_BASE_URL = process.env.NEXT_PUBLIC_ARBEX_WS_BASE_URL || 'ws://127.0.0.1:8000';
const ARBITRAGE_WS_URL = `${WS_BASE_URL}/ws/arbitrage`;
const SOURCES_WS_URL = `${WS_BASE_URL}/ws/sources/USDINR`;
const DASHBOARD_WS_URL = `${WS_BASE_URL}/ws/dashboard`;

interface SourceComparisonPayload {
  sources?: MarketSourceUpdate[];
  source_id?: string;
  session?: string;
  bid?: number;
  ask?: number;
  drift?: number;
}

interface DashboardStatsPayload {
  type?: string;
  data?: {
    source?: string;
    symbol?: string;
    timestamp?: number;
    bids?: { price: number; volume: number }[];
    asks?: { price: number; volume: number }[];
    ticks_processed?: number;
    detection_rate_pct?: number;
  };
}

interface ArbitrageWsPayload {
  opportunity?: RankedOpportunity['opportunity'];
  composite_score?: number;
  dimension_scores?: Record<string, number>;
  ranking_reason?: string;
  rank?: number;
  persistence?: {
    class?: RankedOpportunity['persistence_class'];
    detection_count?: number;
    duration_ms?: number;
    first_seen_ts?: number;
  };
  execution?: {
    verdict?: RankedOpportunity['execution_verdict'];
    verdict_reasons?: string[];
  };
  news_bias?: NewsBias;
}

export function useWebSocket() {
  const setConnectionStatus = useArbexStore((state) => state.setConnectionStatus);
  const updateMarketData = useArbexStore((state) => state.updateMarketData);
  const flushOpportunities = useArbexStore((state) => state.flushOpportunities);
  const updateOrderbook = useArbexStore((state) => state.updateOrderbook);

  // --------------------------------------------------------
  // Buffering Layer (High Priority HFT Mitigation)
  // --------------------------------------------------------
  // Instead of violently flushing React state on every single WS tick,
  // we accumulate the latest states in mutative Refs, and flush them
  // asynchronously in a controlled 50ms loop.
  
  const opportunitiesBuffer = useRef<RankedOpportunity[]>([]);
  const metricsBuffer = useRef({
    total_ticks: 0,
    detection_rate: 0
  });

  useEffect(() => {
    let arbWs: WebSocket | null = null;
    let srcWs: WebSocket | null = null;
    let dashWs: WebSocket | null = null;
    let flushInterval: NodeJS.Timeout;

    const connect = () => {
      arbWs = new WebSocket(ARBITRAGE_WS_URL);
      srcWs = new WebSocket(SOURCES_WS_URL);
      dashWs = new WebSocket(DASHBOARD_WS_URL);

      arbWs.onopen = () => setConnectionStatus(true);
      arbWs.onclose = () => {
        setConnectionStatus(false);
        // Retry connection roughly every 3 seconds if Python server drops
        setTimeout(connect, 3000);
      };
      
      dashWs.onmessage = (event) => {
        if (event.data === 'ping') return;
        try {
          const payload = JSON.parse(event.data) as DashboardStatsPayload;
          if (
            payload.type === 'orderbook' &&
            payload.data &&
            typeof payload.data.symbol === 'string' &&
            typeof payload.data.source === 'string' &&
            typeof payload.data.timestamp === 'number' &&
            Array.isArray(payload.data.bids) &&
            Array.isArray(payload.data.asks)
          ) {
             // console.log("[Arbex] Received Orderbook Data:", payload.data.source);
             if (updateOrderbook) {
               updateOrderbook({
                 symbol: payload.data.symbol,
                 source: payload.data.source,
                 timestamp: payload.data.timestamp,
                 bids: payload.data.bids,
                 asks: payload.data.asks,
               });
             }
          } else if (payload.type === 'stats' && payload.data) {
             // Update ticks scanned
             if (payload.data.ticks_processed) {
               useArbexStore.getState().setMetrics(payload.data.ticks_processed, payload.data.detection_rate_pct || 0);
             }
          }
        } catch {}
      };

      // ------------------------------------------------------
      // Ticker Tape Stream (Direct State Ingestion)
      // ------------------------------------------------------
      srcWs.onmessage = (event) => {
        if (event.data === 'ping') return;
        try {
          const data = JSON.parse(event.data) as SourceComparisonPayload;
          
          // The python backend's `broadcast_source_comparison` payload wraps sources in an array
          if (data.sources && Array.isArray(data.sources)) {
            data.sources.forEach((src) => {
              // Map session from the source_id (e.g. 'bloomberg_tokyo_usdinr' -> 'TOKYO')
              if (!src.session && src.source_id) {
                src.session = src.source_id.split('_')[1].toUpperCase();
              }
              updateMarketData(src);
            });
          } else if (
            data.source_id &&
            typeof data.bid === 'number' &&
            typeof data.ask === 'number'
          ) {
            updateMarketData({
              source_id: data.source_id,
              session: data.session,
              bid: data.bid,
              ask: data.ask,
              drift: data.drift,
            });
          }
        } catch (e) {
          console.error('[Arbex] Source Parse Error:', e);
        }
      };

      // ------------------------------------------------------
      // Arbitrage Engine Stream (Buffered Ingestion)
      // ------------------------------------------------------
      arbWs.onmessage = (event) => {
        if (event.data === 'ping') return;
        try {
          const data = JSON.parse(event.data) as ArbitrageWsPayload;
          
            // The backend now broadcasts a single 'best' opportunity with nested context dicts
          if (data && data.opportunity) {
            const mappedOpp: RankedOpportunity = {
              opportunity: data.opportunity,
              composite_score: data.composite_score || 0,
              dimension_scores: data.dimension_scores || {},
              ranking_reason: data.ranking_reason || '',
              rank: data.rank || 1,
              // Flatten persistence
              persistence_class: data.persistence?.class || 'ephemeral',
              persistence_count: data.persistence?.detection_count || 0,
              duration_ms: data.persistence?.duration_ms || 0,
              first_seen_ms: data.persistence?.first_seen_ts || 0,
              last_seen_ms: Date.now(),
              // Flatten execution
              execution_verdict: data.execution?.verdict || 'unknown',
              execution_reasons: data.execution?.verdict_reasons || [],
              news_bias: data.news_bias || undefined,
              // Temporary placeholder for TypeScript strict typing, will be assigned below
              id: '',
            };
            
            const currentFeed = opportunitiesBuffer.current;
            
            // Check if this exact arbitrage route already exists ANYWHERE in the feed
            const existingIdx = currentFeed.findIndex(o => 
              o.opportunity.type === mappedOpp.opportunity.type &&
              o.opportunity.buy_source === mappedOpp.opportunity.buy_source &&
              o.opportunity.sell_source === mappedOpp.opportunity.sell_source
            );
            
            if (existingIdx >= 0) {
              // If it exists, keep its stable ID so React doesn't destroy it
              mappedOpp.id = currentFeed[existingIdx].id;
              
              if (existingIdx === 0) {
                 // It's already at the top, just update its numbers in place!
                 currentFeed[0] = mappedOpp;
                 opportunitiesBuffer.current = [...currentFeed];
              } else {
                 // It's lower down in the feed. Pull it out and promote it to the top!
                 // Framer Motion will smoothly glide it upward to slot 0.
                 currentFeed.splice(existingIdx, 1);
                 opportunitiesBuffer.current = [mappedOpp, ...currentFeed];
              }
            } else {
              // It's a completely new route! Assign stable ID and cascade it from the top.
              mappedOpp.id = Math.random().toString(36).substring(2, 11);
              opportunitiesBuffer.current = [mappedOpp, ...currentFeed].slice(0, 15);
            }
            
            // The python backend doesn't send total_ticks or detection_rate_pct in this payload anymore,
            // we will just keep them at current value or calculate them locally if needed.
          }
        } catch (e) {
          console.error('[Arbex] Arbitrage Parse Error:', e);
        }
      };
      
      // Controlled Flush (50ms ~ 20 FPS UI Update Limit)
      // This prevents the browser layout engine from hanging when the Python
      // server evaluates 500+ crossing spread anomalies a second.
      flushInterval = setInterval(() => {
        if (opportunitiesBuffer.current.length > 0) {
          flushOpportunities(
            opportunitiesBuffer.current, 
            metricsBuffer.current.total_ticks, 
            metricsBuffer.current.detection_rate
          );
        }
      }, 50);
    };

    connect();

    return () => {
      clearInterval(flushInterval);
      if (arbWs) arbWs.close();
      if (srcWs) srcWs.close();
      if (dashWs) dashWs.close();
    };
  }, [setConnectionStatus, updateMarketData, flushOpportunities, updateOrderbook]);
}
