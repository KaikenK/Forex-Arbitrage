const DEFAULT_NO_DATA_MESSAGE = "Submit an article to see analysis results.";

function normalizeSource(value) {
  const rawSource = String(value ?? "RSS").trim();
  const upperSource = rawSource.toUpperCase();
  if (upperSource.startsWith("FINNHUB")) return "FINNHUB";
  if (upperSource.startsWith("REDDIT")) return "REDDIT";
  return "RSS";
}

export function buildUiState({ loading, error, analysis }) {
  return {
    showLoading: Boolean(loading),
    showError: Boolean(error),
    showResults: Boolean(analysis) && !loading && !error,
    emptyMessage: analysis ? "" : DEFAULT_NO_DATA_MESSAGE,
  };
}

export function buildResultViewModel(analysis, historyItems = []) {
  if (!analysis) return null;
  return {
    sentimentScore: Number(analysis.sentiment_score ?? 0),
    marketScore: Number(analysis.market_score ?? 0),
    confidence: Number(analysis.confidence ?? 0),
    sentimentDisplay: formatSignedScore(analysis.sentiment_score),
    marketDisplay: formatSignedScore(analysis.market_score),
    confidenceDisplay: formatPercent(analysis.confidence),
    pairs: (analysis.affected_symbols ?? []),
    evidence: analysis.evidence ?? [],
    riskFlags: analysis.risk_flags ?? [],
    keyDrivers: analysis.key_drivers ?? [],
    historySeries: buildHistorySeries(historyItems),
  };
}

export function buildFeedItemViewModel(event) {
  const timestamp = event.published_at ?? event.timestamp;
  const ts = timestamp ? new Date(timestamp) : new Date();
  const pair = event.pair ?? null;
  const symbols = event.symbols ?? (pair ? [pair] : []);
  const sourceLabel = String(event.source ?? "RSS").toUpperCase();
  const provenance = event.provenance === "synthetic" ? "synthetic" : "upstream";
  return {
    id: event.event_id ?? crypto.randomUUID(),
    headline: event.headline ?? "",
    source: normalizeSource(event.source),
    sourceLabel,
    provenance,
    provider: String(event.provider ?? provenance),
    feedMode: String(event.feed_mode ?? (provenance === "synthetic" ? "fallback" : "live")),
    upstreamStatus: event.upstream_status ?? null,
    upstreamNote: event.upstream_note ?? "",
    lastUpstreamItemAt: event.last_upstream_item_at ?? null,
    feedCheckedAt: event.feed_checked_at ?? null,
    symbols,
    category: event.category ?? null,
    live: Boolean(event.live),
    time: ts.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
    raw: event,
    analysisResult: null,   // filled by auto-analysis
    analyzing: false,
  };
}

export function buildHistorySeries(historyItems) {
  return (historyItems ?? [])
    .slice()
    .reverse()
    .map((item) => ({
      timestamp: item.produced_at,
      value: item.market_score,
    }));
}

export function formatSignedScore(value) {
  const n = Number(value ?? 0);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(4)}`;
}

export function formatPercent(value) {
  return `${Math.round(Number(value ?? 0) * 100)}%`;
}
