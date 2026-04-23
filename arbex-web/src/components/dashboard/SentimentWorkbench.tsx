"use client";

import React, { startTransition, useDeferredValue, useEffect, useState } from 'react';
import { Card } from '@/components/ui/card';
import { useArbexStore } from '@/lib/store';

const API_BASE_URL = process.env.NEXT_PUBLIC_ARBEX_API_BASE_URL || 'http://127.0.0.1:8000';
const LIVE_WINDOW_MINUTES = 30;
const ARTICLE_LIMIT = 8;
const BIAS_LOOKBACK_HOURS = 7 * 24;
const BIAS_INTERVAL_MINUTES = 360;
const BIAS_HORIZON_MINUTES = 60;

interface SentimentStatusResponse {
  pair: string;
  news_stream: {
    enabled: boolean;
    base_url: string;
    available: boolean;
    last_success_at: string | null;
    last_error: string | null;
    last_latency_ms: number | null;
  };
  runtime: {
    rag_enabled: boolean;
    window_minutes: number;
  };
  dataset: {
    available: boolean;
    training_table_path: string;
    artifact_path: string;
    row_count: number;
    first_timestamp: string | null;
    last_timestamp: string | null;
  };
}

interface LiveArticleItem {
  id: string;
  headline: string;
  source: string;
  category: string;
  sentiment: number;
  impact_score: number;
  impact_label: 'high' | 'medium' | 'low' | string;
  impact_direction: 'bullish' | 'bearish' | 'neutral' | string;
  active_in_window: boolean;
  observations: number;
  first_seen_at: string | null;
  last_seen_at: string | null;
  window_confidence: number;
  window_item_count: number;
}

interface ArticleTapeResponse {
  pair: string;
  window_minutes: number;
  items: LiveArticleItem[];
  summary: {
    current_window_confidence: number;
    current_window_item_count: number;
    active_items: number;
    tracked_items: number;
  };
}

interface FeedItem {
  event_id: string;
  headline: string;
  source: string;
  pair: string;
  category: string;
  url: string | null;
  timestamp: string;
  live: boolean;
  provider: string;
  provenance: string;
  feed_mode: string;
  upstream_status: string;
  upstream_note: string;
  last_upstream_item_at: string | null;
  feed_checked_at: string | null;
}

interface FeedSnapshotResponse {
  pair: string;
  items: FeedItem[];
  summary: {
    tracked_items: number;
    ingest_active: boolean;
    ingest_started: boolean;
  };
}

interface EvidenceItem {
  evidence_id: string;
  source: string;
  evidence_type: string;
  title: string;
  snippet: string;
  url: string | null;
  published_at: string | null;
  relevance_score: number;
  pair: string;
  horizon_minutes: number;
  event_category: string | null;
  macro_event_type: string | null;
  scheduled: boolean | null;
  actual: number | null;
  forecast: number | null;
  previous: number | null;
  standardized_surprise: number | null;
  calendar_source: string | null;
  matching_reason: string | null;
  stance: string | null;
  historical_reaction: number | null;
  historical_direction: number | null;
}

interface KeyDriverItem {
  label: string;
  rationale: string;
  evidence_ids: string[];
}

interface MethodologyBreakdown {
  sentiment: string;
  market: string;
  confidence: string;
  evidence: string;
}

interface FeedContext {
  provider: string;
  provenance: string;
  feed_mode: string;
  source: string;
  timestamp: string;
  url: string | null;
  headline: string;
  upstream_status: string;
  upstream_note: string;
}

interface AnalysisItem {
  event_id: string;
  sentiment_score: number;
  market_score: number;
  affected_symbols: string[];
  confidence: number;
  evidence: EvidenceItem[];
  risk_flags: string[];
  produced_at: string;
  key_drivers: KeyDriverItem[];
  methodology: MethodologyBreakdown;
  trading_signal: string;
  signal_rationale: string;
  feed?: FeedContext;
}

interface AnalysisHistoryResponse {
  pair: string;
  items: AnalysisItem[];
  summary: {
    tracked_items: number;
    with_evidence: number;
    rag_enabled: boolean;
  };
}

interface ExplanationResponse {
  pair: string;
  event_id: string;
  available: boolean;
  analysis: AnalysisItem | null;
}

interface BiasBin {
  timestamp: string;
  bias_score: number;
  confidence: number;
  average_reaction: number;
  average_surprise: number;
  bullish_count: number;
  bearish_count: number;
  neutral_count: number;
  item_count: number;
}

interface HistoricalBiasResponse {
  pair: string;
  bins: BiasBin[];
  summary: {
    average_bias_score: number;
    average_confidence: number;
    average_reaction: number;
    average_surprise: number;
    total_items: number;
    bullish_ratio: number;
    bearish_ratio: number;
    neutral_ratio: number;
  };
}

const statusTone = (active: boolean) =>
  active
    ? 'border-emerald-800 bg-emerald-950/30 text-emerald-400'
    : 'border-amber-800 bg-amber-950/30 text-amber-400';

const chipTone = (tone: 'emerald' | 'amber' | 'blue' | 'slate') => {
  if (tone === 'emerald') return 'border-emerald-800 bg-emerald-950/30 text-emerald-400';
  if (tone === 'amber') return 'border-amber-800 bg-amber-950/30 text-amber-400';
  if (tone === 'blue') return 'border-blue-800 bg-blue-950/30 text-blue-400';
  return 'border-slate-700 bg-slate-800/50 text-slate-300';
};

function normalizePair(symbol?: string | null): string {
  const raw = (symbol || 'USDINR').trim().toUpperCase().replace(/_/g, '/');
  if (raw.includes('/')) return raw;
  if (raw.length === 6) return `${raw.slice(0, 3)}/${raw.slice(3)}`;
  return 'USD/INR';
}

function formatRelative(timestamp?: string | null): string {
  if (!timestamp) return 'unknown';
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return 'unknown';
  const diffSeconds = Math.max(0, Math.round((Date.now() - parsed.getTime()) / 1000));
  if (diffSeconds < 60) return `${diffSeconds}s ago`;
  if (diffSeconds < 3600) return `${Math.round(diffSeconds / 60)}m ago`;
  if (diffSeconds < 86400) return `${Math.round(diffSeconds / 3600)}h ago`;
  return `${Math.round(diffSeconds / 86400)}d ago`;
}

function formatBinLabel(timestamp: string): string {
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return timestamp;
  return parsed.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function biasWidthClass(biasScore: number): string {
  const magnitude = Math.abs(biasScore);
  if (magnitude >= 0.9) return 'w-full';
  if (magnitude >= 0.8) return 'w-11/12';
  if (magnitude >= 0.7) return 'w-10/12';
  if (magnitude >= 0.6) return 'w-9/12';
  if (magnitude >= 0.5) return 'w-8/12';
  if (magnitude >= 0.4) return 'w-7/12';
  if (magnitude >= 0.3) return 'w-6/12';
  if (magnitude >= 0.2) return 'w-5/12';
  if (magnitude >= 0.1) return 'w-4/12';
  if (magnitude >= 0.05) return 'w-3/12';
  return 'w-2/12';
}

function formatSigned(value: number, digits = 2): string {
  const sign = value > 0 ? '+' : '';
  return `${sign}${value.toFixed(digits)}`;
}

function formatPercent(value: number): string {
  return `${Math.round(value * 100)}%`;
}

function scoreTone(value: number): string {
  if (value > 0.05) return 'text-emerald-400';
  if (value < -0.05) return 'text-red-400';
  return 'text-slate-300';
}

function compactSource(source: string): string {
  if (source.startsWith('FINNHUB')) return 'FINNHUB';
  return source;
}

function buildBiasQuery(dataset: SentimentStatusResponse['dataset'], pair: string): URLSearchParams | null {
  if (!dataset.available || !dataset.last_timestamp) {
    return null;
  }
  const datasetStart = dataset.first_timestamp ? new Date(dataset.first_timestamp) : null;
  const datasetEnd = new Date(dataset.last_timestamp);
  if (Number.isNaN(datasetEnd.getTime())) {
    return null;
  }
  const fromTimestamp = new Date(datasetEnd.getTime() - (BIAS_LOOKBACK_HOURS * 60 * 60 * 1000));
  const resolvedFrom = datasetStart && fromTimestamp < datasetStart ? datasetStart : fromTimestamp;
  return new URLSearchParams({
    pair,
    from_timestamp: resolvedFrom.toISOString(),
    to_timestamp: datasetEnd.toISOString(),
    aggregate_interval_minutes: String(BIAS_INTERVAL_MINUTES),
    horizon_minutes: String(BIAS_HORIZON_MINUTES),
  });
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function SentimentWorkbench() {
  const topSymbol = useArbexStore((state) => state.rankedOpportunities[0]?.opportunity.symbols[0] ?? 'USDINR');
  const pair = useDeferredValue(normalizePair(topSymbol));

  const [status, setStatus] = useState<SentimentStatusResponse | null>(null);
  const [articles, setArticles] = useState<ArticleTapeResponse | null>(null);
  const [feed, setFeed] = useState<FeedSnapshotResponse | null>(null);
  const [history, setHistory] = useState<AnalysisHistoryResponse | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<ExplanationResponse | null>(null);
  const [bias, setBias] = useState<HistoricalBiasResponse | null>(null);
  const [historySearch, setHistorySearch] = useState('');
  const [historyMinConfidence, setHistoryMinConfidence] = useState('0');
  const [historyEvidenceOnly, setHistoryEvidenceOnly] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const deferredHistorySearch = useDeferredValue(historySearch.trim());

  useEffect(() => {
    let cancelled = false;

    const refresh = async () => {
      try {
        const statusPayload = await fetchJson<SentimentStatusResponse>(`/sentiment/status?pair=${encodeURIComponent(pair)}`);
        const articlePromise = fetchJson<ArticleTapeResponse>(
          `/sentiment/articles/live?pair=${encodeURIComponent(pair)}&window_minutes=${LIVE_WINDOW_MINUTES}&limit=${ARTICLE_LIMIT}`,
        );
        const feedPromise = fetchJson<FeedSnapshotResponse>(`/sentiment/feed/live?pair=${encodeURIComponent(pair)}&limit=6`);
        const historyQuery = new URLSearchParams({
          pair,
          limit: '6',
        });
        if (deferredHistorySearch) {
          historyQuery.set('q', deferredHistorySearch);
        }
        if (historyMinConfidence !== '0') {
          historyQuery.set('min_confidence', historyMinConfidence);
        }
        if (historyEvidenceOnly) {
          historyQuery.set('has_evidence', 'true');
        }
        const historyPromise = fetchJson<AnalysisHistoryResponse>(`/sentiment/history/live?${historyQuery.toString()}`);
        const biasQuery = buildBiasQuery(statusPayload.dataset, pair);
        const biasPromise = biasQuery
          ? fetchJson<HistoricalBiasResponse>(`/sentiment/bias?${biasQuery.toString()}`)
          : Promise.resolve<HistoricalBiasResponse | null>(null);

        const [articlePayload, feedPayload, historyPayload, biasPayload] = await Promise.all([
          articlePromise,
          feedPromise,
          historyPromise,
          biasPromise,
        ]);
        if (cancelled) {
          return;
        }

        startTransition(() => {
          setStatus(statusPayload);
          setArticles(articlePayload);
          setFeed(feedPayload);
          setHistory(historyPayload);
          setSelectedEventId((currentEventId) => (
            currentEventId && historyPayload.items.some((item) => item.event_id === currentEventId)
              ? currentEventId
              : historyPayload.items[0]?.event_id ?? null
          ));
          setBias(biasPayload);
          setError(null);
          setIsLoading(false);
        });
      } catch (fetchError) {
        if (cancelled) {
          return;
        }
        const message = fetchError instanceof Error ? fetchError.message : 'Sentiment panel failed to load.';
        startTransition(() => {
          setError(message);
          setIsLoading(false);
        });
      }
    };

    void refresh();
    const intervalId = window.setInterval(() => {
      void refresh();
    }, 15000);

    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [pair, deferredHistorySearch, historyEvidenceOnly, historyMinConfidence]);

  useEffect(() => {
    let cancelled = false;

    const refreshExplanation = async () => {
      if (!selectedEventId) {
        startTransition(() => setExplanation(null));
        return;
      }
      try {
        const payload = await fetchJson<ExplanationResponse>(
          `/sentiment/explanation/${encodeURIComponent(selectedEventId)}?pair=${encodeURIComponent(pair)}`,
        );
        if (cancelled) {
          return;
        }
        startTransition(() => setExplanation(payload));
      } catch {
        if (cancelled) {
          return;
        }
        startTransition(() => setExplanation(null));
      }
    };

    void refreshExplanation();

    return () => {
      cancelled = true;
    };
  }, [pair, selectedEventId]);

  const biasBins = bias?.bins.slice(-6) ?? [];
  const selectedAnalysis = explanation?.analysis ?? history?.items.find((item) => item.event_id === selectedEventId) ?? null;

  return (
    <Card className="bg-[#141620] border-[#1e2235] p-3 text-[10px] font-mono">
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-[10px] uppercase tracking-widest text-[#505872]">Sentiment Intelligence</div>
            <div className="mt-1 text-[11px] text-slate-300">{pair} raw feed, RAG explanation, rolling impact, and local bias archive</div>
            <div className="mt-1 text-slate-500">
              {status?.dataset.available
                ? `${status.dataset.row_count} local rows mirrored into Arbex`
                : 'Local Arbex dataset mirror unavailable'}
            </div>
          </div>
          <div className="flex flex-wrap gap-1.5 justify-end">
            <span className={`rounded border px-2 py-0.5 uppercase tracking-widest ${statusTone(Boolean(status?.news_stream.available))}`}>
              {status?.news_stream.available ? 'service live' : 'service degraded'}
            </span>
            <span className={`rounded border px-2 py-0.5 uppercase tracking-widest ${chipTone(status?.runtime.rag_enabled ? 'emerald' : 'amber')}`}>
              {status?.runtime.rag_enabled ? 'rag on' : 'rag off'}
            </span>
            <span className={`rounded border px-2 py-0.5 uppercase tracking-widest ${chipTone('blue')}`}>
              {status?.dataset.row_count ?? 0} rows
            </span>
          </div>
        </div>

        {error ? (
          <div className="rounded border border-red-900/70 bg-red-950/20 p-3 text-red-300">
            {error}
          </div>
        ) : null}

        <div className="rounded border border-[#1e2235] bg-[#10131b] p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="uppercase tracking-widest text-slate-500">Raw Live News Feed</span>
            <span className="text-slate-500">
              {feed?.summary.tracked_items ?? 0} mirrored / {feed?.summary.ingest_active ? 'ingest on' : 'ingest off'}
            </span>
          </div>

          {feed && feed.items.length > 0 ? (
            <div className="flex flex-col gap-2">
              {feed.items.map((item) => (
                <div key={item.event_id} className="rounded border border-[#1e2235] bg-[#0f1219] p-2">
                  <div className="flex items-start justify-between gap-3">
                    <div className="text-slate-200 leading-relaxed">{item.headline}</div>
                    <span className={`shrink-0 rounded border px-2 py-0.5 uppercase tracking-widest ${chipTone(item.provenance === 'upstream' ? 'emerald' : 'amber')}`}>
                      {item.provenance}
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-500">
                    <span>{compactSource(item.source)}</span>
                    <span>{item.category}</span>
                    <span>{item.provider}</span>
                    <span>{formatRelative(item.timestamp)}</span>
                    <span>{item.feed_mode}</span>
                  </div>
                  {item.url ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2 block break-all text-blue-400 hover:text-blue-300"
                    >
                      {item.url}
                    </a>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-slate-500">
              {isLoading ? 'Loading raw live feed from news_stream...' : 'No live feed items mirrored into Arbex yet.'}
            </div>
          )}
        </div>

        <div className="rounded border border-[#1e2235] bg-[#10131b] p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="uppercase tracking-widest text-slate-500">Analyzed Live History</span>
            <span className="text-slate-500">
              {history?.summary.tracked_items ?? 0} analyzed / {history?.summary.with_evidence ?? 0} with evidence
            </span>
          </div>

          <div className="mb-3 grid gap-2 md:grid-cols-[minmax(0,1fr)_130px_auto]">
            <input
              value={historySearch}
              onChange={(event) => setHistorySearch(event.target.value)}
              placeholder="Search headlines, drivers, evidence"
              className="rounded border border-[#1e2235] bg-[#0f1219] px-2 py-1 text-slate-200 outline-none placeholder:text-slate-600"
            />
            <select
              aria-label="Minimum analyzed history confidence"
              value={historyMinConfidence}
              onChange={(event) => setHistoryMinConfidence(event.target.value)}
              className="rounded border border-[#1e2235] bg-[#0f1219] px-2 py-1 text-slate-200 outline-none"
            >
              <option value="0">all confidence</option>
              <option value="0.5">50%+</option>
              <option value="0.65">65%+</option>
              <option value="0.8">80%+</option>
            </select>
            <label className="flex items-center gap-2 rounded border border-[#1e2235] bg-[#0f1219] px-2 py-1 text-slate-300">
              <input
                type="checkbox"
                checked={historyEvidenceOnly}
                onChange={(event) => setHistoryEvidenceOnly(event.target.checked)}
                className="accent-blue-500"
              />
              evidence only
            </label>
          </div>

          {history && history.items.length > 0 ? (
            <div className="flex flex-col gap-2">
              {history.items.map((item) => {
                const isSelected = item.event_id === selectedEventId;
                return (
                  <button
                    key={item.event_id}
                    type="button"
                    onClick={() => setSelectedEventId(item.event_id)}
                    className={`rounded border p-2 text-left transition-colors ${isSelected ? 'border-[#35508f] bg-[#11182a]' : 'border-[#1e2235] bg-[#0f1219] hover:border-[#2a3556]'}`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-slate-200 leading-relaxed">{item.feed?.headline ?? item.event_id}</div>
                        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-500">
                          <span>{item.trading_signal}</span>
                          <span>conf {formatPercent(item.confidence)}</span>
                          <span>{item.evidence.length} evidence</span>
                          <span>{formatRelative(item.produced_at)}</span>
                        </div>
                      </div>
                      <span className={`shrink-0 ${scoreTone(item.sentiment_score)}`}>{formatSigned(item.sentiment_score, 2)}</span>
                    </div>
                    {item.risk_flags.length > 0 ? (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {item.risk_flags.slice(0, 3).map((flag) => (
                          <span key={flag} className={`rounded border px-2 py-0.5 uppercase tracking-widest ${chipTone('amber')}`}>
                            {flag.replaceAll('_', ' ')}
                          </span>
                        ))}
                      </div>
                    ) : null}
                  </button>
                );
              })}
            </div>
          ) : (
            <div className="text-slate-500">
              {isLoading ? 'Loading analyzed live history...' : 'No analyzed live articles are available yet.'}
            </div>
          )}
        </div>

        <div className="rounded border border-[#1e2235] bg-[#10131b] p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="uppercase tracking-widest text-slate-500">Semantic Explanation</span>
            <span className="text-slate-500">{selectedAnalysis ? selectedAnalysis.trading_signal : 'No selection'}</span>
          </div>

          {selectedAnalysis ? (
            <div className="flex flex-col gap-3">
              <div className="rounded border border-[#1e2235] bg-[#0f1219] p-2">
                <div className="text-slate-200 leading-relaxed">{selectedAnalysis.feed?.headline ?? selectedAnalysis.event_id}</div>
                <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-500">
                  <span className={scoreTone(selectedAnalysis.sentiment_score)}>sent {formatSigned(selectedAnalysis.sentiment_score, 2)}</span>
                  <span className={scoreTone(selectedAnalysis.market_score)}>mkt {formatSigned(selectedAnalysis.market_score, 4)}</span>
                  <span>conf {formatPercent(selectedAnalysis.confidence)}</span>
                  <span>{selectedAnalysis.affected_symbols.join(', ')}</span>
                </div>
                <div className="mt-2 text-slate-400">{selectedAnalysis.signal_rationale}</div>
                {selectedAnalysis.feed?.url ? (
                  <a
                    href={selectedAnalysis.feed.url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-2 block break-all text-blue-400 hover:text-blue-300"
                  >
                    {selectedAnalysis.feed.url}
                  </a>
                ) : null}
              </div>

              <div className="grid gap-2 md:grid-cols-2">
                <div className="rounded border border-[#1e2235] bg-[#0f1219] p-2 text-slate-400">
                  <div className="uppercase tracking-widest text-slate-500">Methodology</div>
                  <div className="mt-2">sentiment: {selectedAnalysis.methodology.sentiment}</div>
                  <div className="mt-1">market: {selectedAnalysis.methodology.market}</div>
                  <div className="mt-1">confidence: {selectedAnalysis.methodology.confidence}</div>
                  <div className="mt-1">evidence: {selectedAnalysis.methodology.evidence}</div>
                </div>
                <div className="rounded border border-[#1e2235] bg-[#0f1219] p-2 text-slate-400">
                  <div className="uppercase tracking-widest text-slate-500">Key Drivers</div>
                  <div className="mt-2 flex flex-col gap-2">
                    {selectedAnalysis.key_drivers.slice(0, 5).map((driver) => (
                      <div key={`${selectedAnalysis.event_id}-${driver.label}`}>
                        <div className="text-slate-200">{driver.label}</div>
                        <div>{driver.rationale}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>

              <div className="rounded border border-[#1e2235] bg-[#0f1219] p-2">
                <div className="mb-2 uppercase tracking-widest text-slate-500">Retrieved Evidence</div>
                {selectedAnalysis.evidence.length > 0 ? (
                  <div className="flex flex-col gap-2">
                    {selectedAnalysis.evidence.slice(0, 4).map((evidence) => (
                      <div key={evidence.evidence_id} className="rounded border border-[#1e2235] bg-[#10131b] p-2">
                        <div className="flex items-start justify-between gap-3">
                          <div className="text-slate-200 leading-relaxed">{evidence.title}</div>
                          <span className={`shrink-0 rounded border px-2 py-0.5 uppercase tracking-widest ${chipTone('blue')}`}>
                            rel {evidence.relevance_score.toFixed(2)}
                          </span>
                        </div>
                        <div className="mt-2 text-slate-400">{evidence.snippet}</div>
                        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-500">
                          <span>{evidence.evidence_type}</span>
                          <span>{evidence.matching_reason ?? 'retrieval'}</span>
                          <span>{evidence.stance ?? 'neutral'}</span>
                          <span>{evidence.horizon_minutes}m</span>
                          <span>{evidence.published_at ? formatRelative(evidence.published_at) : 'no time'}</span>
                        </div>
                        {evidence.actual !== null || evidence.forecast !== null || evidence.previous !== null ? (
                          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-500">
                            <span>a {evidence.actual ?? 'n/a'}</span>
                            <span>f {evidence.forecast ?? 'n/a'}</span>
                            <span>p {evidence.previous ?? 'n/a'}</span>
                            {evidence.standardized_surprise !== null ? <span>surprise {formatSigned(evidence.standardized_surprise, 2)}</span> : null}
                          </div>
                        ) : null}
                        {evidence.url ? (
                          <a
                            href={evidence.url}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-2 block break-all text-blue-400 hover:text-blue-300"
                          >
                            {evidence.url}
                          </a>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-slate-500">No retrieved evidence was attached to the selected analysis.</div>
                )}
              </div>
            </div>
          ) : (
            <div className="text-slate-500">
              {isLoading ? 'Loading semantic explanation...' : 'Select an analyzed live article to inspect its explanation and evidence.'}
            </div>
          )}
        </div>

        <div className="rounded border border-[#1e2235] bg-[#10131b] p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="uppercase tracking-widest text-slate-500">Live Article Impact Tape</span>
            <span className="text-slate-500">
              {articles?.summary.active_items ?? 0} active / {articles?.summary.tracked_items ?? 0} tracked
            </span>
          </div>

          {articles && articles.items.length > 0 ? (
            <div className="flex flex-col gap-2">
              {articles.items.map((item) => (
                <div
                  key={item.id}
                  className={`rounded border p-2 ${item.active_in_window ? 'border-[#2a3556] bg-[#121725]' : 'border-[#1e2235] bg-[#0f1219] opacity-70'}`}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="text-slate-200 leading-relaxed">{item.headline}</div>
                    <span
                      className={`shrink-0 rounded border px-2 py-0.5 uppercase tracking-widest ${
                        item.impact_label === 'high'
                          ? chipTone('emerald')
                          : item.impact_label === 'medium'
                            ? chipTone('amber')
                            : chipTone('slate')
                      }`}
                    >
                      {item.impact_label} impact
                    </span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-slate-500">
                    <span>{item.source}</span>
                    <span>{item.category}</span>
                    <span>{item.impact_direction}</span>
                    <span>{formatRelative(item.last_seen_at)}</span>
                    <span>impact {(item.impact_score * 100).toFixed(1)}</span>
                    <span>sent {item.sentiment.toFixed(2)}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-slate-500">
              {isLoading ? 'Loading live sentiment article tape...' : 'No active live sentiment drivers are available yet.'}
            </div>
          )}
        </div>

        <div className="rounded border border-[#1e2235] bg-[#10131b] p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="uppercase tracking-widest text-slate-500">Historical Bias Archive</span>
            <span className="text-slate-500">
              avg {(bias?.summary.average_bias_score ?? 0).toFixed(2)} / conf {(bias?.summary.average_confidence ?? 0).toFixed(2)}
            </span>
          </div>

          {biasBins.length > 0 ? (
            <div className="flex flex-col gap-2">
              {biasBins.map((bin) => {
                const barClass = bin.bias_score >= 0 ? 'bg-emerald-500/70' : 'bg-red-500/70';
                return (
                  <div key={bin.timestamp} className="rounded border border-[#1e2235] bg-[#0f1219] p-2">
                    <div className="mb-1 flex items-center justify-between text-slate-500">
                      <span>{formatBinLabel(bin.timestamp)}</span>
                      <span>{bin.bias_score.toFixed(2)} bias</span>
                    </div>
                    <div className="h-2 rounded bg-[#090b11]">
                      <div className={`h-2 rounded ${barClass} ${biasWidthClass(bin.bias_score)}`} />
                    </div>
                    <div className="mt-1 flex items-center justify-between text-slate-500">
                      <span>{bin.item_count} items</span>
                      <span>conf {bin.confidence.toFixed(2)}</span>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="text-slate-500">
              {status?.dataset.available
                ? 'No historical bias bins matched the mirrored Arbex dataset range.'
                : 'Mirror the sentiment training table into Arbex to unlock historical bias playback context.'}
            </div>
          )}
        </div>

        <div className="rounded border border-[#1e2235] bg-[#10131b] p-3 text-slate-500">
          <div className="uppercase tracking-widest">Dataset</div>
          <div className="mt-1 break-all">{status?.dataset.training_table_path ?? 'Unavailable'}</div>
          <div className="mt-1">{status?.dataset.first_timestamp ? `${status.dataset.first_timestamp} -> ${status.dataset.last_timestamp}` : 'No local dataset coverage found.'}</div>
          <div className="mt-1">Latency {status?.news_stream.last_latency_ms?.toFixed(1) ?? 'n/a'} ms</div>
        </div>
      </div>
    </Card>
  );
}