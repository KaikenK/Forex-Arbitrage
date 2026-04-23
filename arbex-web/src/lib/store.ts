import { create } from 'zustand';
import { subscribeWithSelector } from 'zustand/middleware';

// --------------------------------------------------------
// Interfaces matching the Python Backend Payloads
// --------------------------------------------------------

export interface ArbitrageOpportunity {
  symbols: string[];
  buy_source: string;
  sell_source: string;
  buy_price: number;
  sell_price: number;
  spread_pips?: number;
  estimated_profit_pips: number;
  latency_risk_ms: number;
  confidence_score: number;
  type: string;
  session?: string;
  timestamp_ms?: number;
  window_size_ms?: number;
}

export interface MarketSourceUpdate {
  source_id: string;
  session?: string;
  bid: number;
  ask: number;
  drift?: number;
}

export interface NewsBiasDriver {
  headline?: string;
  sentiment?: string;
  source?: string;
  category?: string;
}

export interface NewsBias {
  pair: string;
  status: 'live' | 'stale_cache' | 'neutral' | 'neutral_fallback' | 'disabled';
  window_minutes: number;
  overall_sentiment: number;
  semantic_score: number;
  confidence: number;
  confidence_adjustment: number;
  direction: 'risk_on' | 'risk_off' | 'neutral' | string;
  regime: 'high_conviction' | 'directional' | 'conflicted' | 'neutral' | string;
  item_count: number;
  bullish_count: number;
  bearish_count: number;
  neutral_count: number;
  freshness_seconds: number;
  service_updated_at: string | null;
  top_drivers: NewsBiasDriver[];
  reason: string;
}

export interface RankedOpportunity {
  opportunity: ArbitrageOpportunity;
  composite_score: number;
  dimension_scores: Record<string, number>;
  ranking_reason: string;
  rank: number;
  persistence_count: number;
  persistence_class: 'ephemeral' | 'flickering' | 'persistent';
  execution_verdict: 'viable' | 'risky' | 'unknown';
  execution_reasons: string[];
  news_bias?: NewsBias;
  first_seen_ms: number;
  last_seen_ms: number;
  duration_ms: number;
  id: string;
}

export interface SessionPriceInfo {
  session: string;
  bid: number;
  ask: number;
  drift: number;
}

export interface OrderbookLevel {
  price: number;
  volume: number;
}

export interface OrderbookUpdate {
  symbol: string;
  source: string;
  timestamp: number;
  bids: OrderbookLevel[];
  asks: OrderbookLevel[];
}

// --------------------------------------------------------
// Zustand Store Definition
// --------------------------------------------------------

interface ArbexState {
  // Global Meta
  isConnected: boolean;
  activeSession: string;
  globalLatency: number;
  detectedRate: number;
  totalTicksLogged: number;
  
  // Market Ticker Tape
  activePairs: Record<string, SessionPriceInfo>;
  orderbookData: Record<string, OrderbookUpdate>;
  
  // High-Frequency Arrays
  rankedOpportunities: RankedOpportunity[];
  topCompositeScore: number;
  
  // Actions
  setConnectionStatus: (status: boolean) => void;
  updateMarketData: (data: MarketSourceUpdate) => void;
  updateOrderbook: (data: OrderbookUpdate) => void;
  flushOpportunities: (opportunities: RankedOpportunity[], totalTicks: number, detectionRate: number) => void;
  setMetrics: (totalTicks: number, detectionRate: number) => void;
}

export const useArbexStore = create<ArbexState>()(
  subscribeWithSelector((set) => ({
    // Initial State
    isConnected: false,
    activeSession: 'UNKNOWN',
    globalLatency: 0,
    detectedRate: 0,
    totalTicksLogged: 0,
    
    activePairs: {},
    orderbookData: {},
    rankedOpportunities: [],
    topCompositeScore: 0,
    
    // Writers
    setConnectionStatus: (status) => set({ isConnected: status }),
    
    updateMarketData: (data) => set((state) => ({
      activePairs: {
        ...state.activePairs,
        [data.source_id]: {
          session: data.session || 'UNKNOWN',
          bid: data.bid,
          ask: data.ask,
          drift: data.drift || 0,
        }
      }
    })),
    
    updateOrderbook: (data) => set((state) => ({
      orderbookData: {
        ...state.orderbookData,
        [data.source]: data
      }
    })),
    
    flushOpportunities: (opportunities, totalTicks, detectionRate) => set(() => ({
      rankedOpportunities: opportunities,
      topCompositeScore: opportunities.length > 0 ? opportunities[0].composite_score : 0,
      totalTicksLogged: totalTicks,
      detectedRate: detectionRate
    })),
    
    setMetrics: (totalTicks, detectionRate) => set(() => ({
      totalTicksLogged: totalTicks,
      detectedRate: detectionRate
    }))
  }))
);
