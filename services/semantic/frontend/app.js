import { analyzeArticle, fetchHistory, fetchMetrics, connectToFeed, fetchLiveSentiment, fetchCorrelation } from "./api.js";
import { buildResultViewModel, buildUiState, buildFeedItemViewModel, formatSignedScore, formatPercent } from "./view-model.js";

/* ══════════ state ══════════ */
const state = {
  analysis: null,
  historyItems: [],
  metrics: null,
  loading: false,
  error: "",
  feedItems: [],        // most-recent first
  feedFilter: "all",    // "all" | "FINNHUB" | "RSS"
  feedHandle: null,
  autoAnalyze: false,
  evaluation: null,
  feedTelemetry: {
    connectionLive: false,
    mode: "awaiting",
    note: "Waiting for the first headline…",
    lastUpstreamItemAt: null,
  },
};

const MAX_FEED_ITEMS = 100;
const AUTO_ANALYSIS_QUEUE = [];
let autoAnalysisBusy = false;

/* ══════════ boot ══════════ */
document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("analysis-form").addEventListener("submit", handleSubmit);

  // feed filters
  document.querySelectorAll(".chip[data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => setFeedFilter(btn.dataset.filter));
  });

  // auto-analyze toggle
  document.getElementById("auto-analyze-toggle").addEventListener("change", (e) => {
    state.autoAnalyze = e.target.checked;
  });

  startFeed();
  void loadSupportingData();
  void loadEvaluation();
  startLiveSentimentPoller();
  wireCorrelationPanel();
  render();
});

/* ══════════ SSE feed ══════════ */
function startFeed() {
  state.feedHandle = connectToFeed({
    onEvent: (data) => {
      const item = buildFeedItemViewModel(data);
      state.feedItems.unshift(item);
      if (state.feedItems.length > MAX_FEED_ITEMS) state.feedItems.length = MAX_FEED_ITEMS;
      updateFeedTelemetry(item);
      renderFeed();
      if (state.autoAnalyze) {
        enqueueAutoAnalysis(item);
      }
    },
    onOpen: () => setFeedStatus(true),
    onError: () => setFeedStatus(false),
  });
}

function setFeedStatus(connected) {
  state.feedTelemetry.connectionLive = connected;
  const el = document.getElementById("feed-status");
  el.innerHTML = connected
    ? '<span class="dot dot--on"></span> Live'
    : '<span class="dot dot--off"></span> Reconnecting…';
  renderFeedTelemetry();
}

function updateFeedTelemetry(item) {
  const synthetic = item.provenance === "synthetic";
  state.feedTelemetry.mode = synthetic ? "synthetic" : "upstream";
  state.feedTelemetry.lastUpstreamItemAt = item.lastUpstreamItemAt ?? state.feedTelemetry.lastUpstreamItemAt;
  state.feedTelemetry.note = item.upstreamNote || (synthetic
    ? "No recent upstream headlines were available; local fallback scenarios are active."
    : `Latest upstream headline via ${String(item.provider).toUpperCase()}.`);
  renderFeedTelemetry();
}

function renderFeedTelemetry() {
  const pill = document.getElementById("feed-provenance-pill");
  const meta = document.getElementById("feed-provenance-meta");
  if (!pill || !meta) return;

  let label = "Awaiting feed";
  let modifier = "idle";
  if (!state.feedTelemetry.connectionLive) {
    label = "Reconnecting";
    modifier = "reconnecting";
  } else if (state.feedTelemetry.mode === "upstream") {
    label = "Upstream live";
    modifier = "live";
  } else if (state.feedTelemetry.mode === "synthetic") {
    label = "Simulated fallback";
    modifier = "synthetic";
  }

  const lastUpstream = state.feedTelemetry.lastUpstreamItemAt
    ? ` Last real headline ${formatFeedTimestamp(state.feedTelemetry.lastUpstreamItemAt)}.`
    : "";

  pill.textContent = label;
  pill.className = `feed-provenance-pill feed-provenance-pill--${modifier}`;
  meta.textContent = `${state.feedTelemetry.note}${lastUpstream}`.trim();
}

function setFeedFilter(filter) {
  state.feedFilter = filter;
  document.querySelectorAll(".chip[data-filter]").forEach((btn) => {
    btn.classList.toggle("chip--active", btn.dataset.filter === filter);
  });
  renderFeed();
}

/* ══════════ auto-analysis ══════════ */
function enqueueAutoAnalysis(item) {
  AUTO_ANALYSIS_QUEUE.push(item);
  if (!autoAnalysisBusy) drainAutoQueue();
}

async function drainAutoQueue() {
  autoAnalysisBusy = true;
  while (AUTO_ANALYSIS_QUEUE.length > 0) {
    const item = AUTO_ANALYSIS_QUEUE.shift();
    item.analyzing = true;
    renderFeed();
    try {
      const response = await analyzeArticle({
        article_text: item.headline,
        headline: item.headline,
        source: item.source || null,
        currency_pair: item.symbols[0] || null,
        event_category: item.category || null,
      });
      item.analysisResult = response.result;
      // also display most recent auto-analyzed in right panel
      state.analysis = response.result;
      state.error = "";
      render();
      void loadSupportingData();
    } catch {
      /* auto-analysis failures are silent */
    } finally {
      item.analyzing = false;
      renderFeed();
    }
  }
  autoAnalysisBusy = false;
}

/* ══════════ form ══════════ */
async function handleSubmit(event) {
  event.preventDefault();
  const fd = new FormData(event.currentTarget);
  state.loading = true;
  state.error = "";
  render();

  try {
    const headline = (fd.get("headline") || "").trim();
    const body = (fd.get("body") || "").trim();
    const articleText = body || headline;

    const response = await analyzeArticle({
      article_text: articleText,
      headline: headline || null,
      source: fd.get("source") || null,
      published_at: fd.get("published_at") || null,
      currency_pair: fd.get("currency_pair") || null,
      event_category: fd.get("event_category") || null,
    });
    state.analysis = response.result;
    await loadSupportingData();
  } catch (err) {
    state.error = err.message || "Unable to analyze the article.";
  } finally {
    state.loading = false;
    render();
  }
}

/* ══════════ data loaders ══════════ */
async function loadSupportingData() {
  try {
    const [historyPayload, metricsPayload] = await Promise.all([fetchHistory(12), fetchMetrics()]);
    state.historyItems = historyPayload.items ?? [];
    state.metrics = metricsPayload.metrics ?? null;
  } catch {
    /* non-critical */
  }
  renderMetrics();
}

/* ══════════ render: feed ══════════ */
function renderFeed() {
  const container = document.getElementById("feed-list");
  const filtered = state.feedFilter === "all"
    ? state.feedItems
    : state.feedFilter.includes("/")
      ? state.feedItems.filter((i) => i.symbols.some((s) => s.toUpperCase().includes(state.feedFilter)) || i.headline.toUpperCase().includes(state.feedFilter.replace("/", "")))
      : state.feedItems.filter((i) => i.source === state.feedFilter);

  if (!filtered.length) {
    container.innerHTML = '<p class="feed-empty">Waiting for headlines…</p>';
    return;
  }

  container.innerHTML = filtered.map((item) => {
    const r = item.analysisResult;
    const provenanceBadge = item.provenance === "synthetic"
      ? '<span class="provenance-badge provenance-badge--synthetic">SIMULATED</span>'
      : '<span class="provenance-badge provenance-badge--upstream">UPSTREAM</span>';
    const resultBadges = r ? `
      <div class="feed-item-result">
        <span class="mini-badge ${Number(r.sentiment_score) >= 0 ? 'mini-badge--pos' : 'mini-badge--neg'}">S ${formatSignedScore(r.sentiment_score)}</span>
        <span class="mini-badge ${Number(r.market_score) >= 0 ? 'mini-badge--pos' : 'mini-badge--neg'}">M ${formatSignedScore(r.market_score)}</span>
        <span class="mini-badge">C ${formatPercent(r.confidence)}</span>
      </div>` : "";
    const analyzingClass = item.analyzing ? " feed-item--analyzing" : "";
    return `
    <div class="feed-item${analyzingClass}" data-feed-id="${esc(item.id)}">
      <div class="feed-item-top">
        <span class="source-badge source-badge--${esc(item.source)}">${esc(item.sourceLabel || item.source)}</span>
        ${provenanceBadge}
        ${item.live ? '<span class="live-badge">LIVE</span>' : ''}
        <span class="feed-item-time">${esc(item.time)}</span>
      </div>
      <div class="feed-item-headline">${esc(item.headline)}</div>
      ${item.symbols.length ? `<div class="feed-item-symbols">${item.symbols.map((s) => `<span class="symbol-tag">${esc(s)}</span>`).join("")}</div>` : ""}
      ${resultBadges}
    </div>`;
  }).join("");

  // click-to-fill (or click to show result if already analyzed)
  container.querySelectorAll(".feed-item").forEach((el) => {
    el.addEventListener("click", () => {
      const id = el.dataset.feedId;
      const item = state.feedItems.find((i) => i.id === id);
      if (!item) return;

      // if this item has been auto-analyzed, show its result in the right panel
      if (item.analysisResult) {
        state.analysis = item.analysisResult;
        state.error = "";
        state.loading = false;
        render();
        void loadSupportingData();
        return;
      }

      // otherwise fill the form
      const form = document.getElementById("analysis-form");
      form.elements.headline.value = item.headline;
      form.elements.body.value = "";
      form.elements.source.value = item.source;
      if (item.symbols.length) {
        form.elements.currency_pair.value = item.symbols[0];
      }
      form.elements.event_category.value = detectEventCategory(item.headline);
      document.querySelector(".col--input").scrollIntoView({ behavior: "smooth" });
    });
  });
}

/* ══════════ render: main ══════════ */
function render() {
  renderFeedTelemetry();
  const uiState = buildUiState(state);
  const vm = buildResultViewModel(state.analysis, state.historyItems);

  document.getElementById("loading-state").hidden = !uiState.showLoading;
  document.getElementById("error-state").hidden = !uiState.showError;
  document.getElementById("error-text").textContent = state.error;
  document.getElementById("empty-state").hidden = uiState.showResults || uiState.showLoading || uiState.showError;
  document.getElementById("results-shell").hidden = !uiState.showResults;

  // toggle submit button
  const btn = document.getElementById("submit-btn");
  btn.disabled = state.loading;
  btn.querySelector(".btn-label").textContent = state.loading ? "Analyzing…" : "Analyze";
  btn.querySelector(".btn-spinner").hidden = !state.loading;

  if (!vm) return;

  // re-render validation (it lives inside results-shell which was hidden on load)
  renderValidation();

  // metric cards
  const sentEl = document.getElementById("sentiment-score");
  sentEl.textContent = vm.sentimentDisplay;
  sentEl.className = "metric-value" + (vm.sentimentScore > 0 ? " metric-value--positive" : vm.sentimentScore < 0 ? " metric-value--negative" : "");
  document.getElementById("sentiment-bar").style.width = `${Math.min(100, Math.abs(vm.sentimentScore) * 100)}%`;

  const mktEl = document.getElementById("market-score");
  mktEl.textContent = vm.marketDisplay;
  mktEl.className = "metric-value" + (vm.marketScore > 0 ? " metric-value--positive" : vm.marketScore < 0 ? " metric-value--negative" : "");
  document.getElementById("market-bar").style.width = `${Math.min(100, Math.abs(vm.marketScore) * 100)}%`;

  document.getElementById("confidence-score").textContent = vm.confidenceDisplay;
  document.getElementById("confidence-bar").style.width = `${vm.confidence * 100}%`;

  // pairs
  document.getElementById("pair-chips").innerHTML = vm.pairs
    .map((symbol) => `<span class="pair-chip">${esc(symbol)}</span>`)
    .join("") || '<span style="color:var(--text-muted);font-size:.82rem">None detected</span>';

  // trading signal
  const signalSection = document.getElementById("signal-section");
  const signal = state.analysis?.trading_signal;
  const rationale = state.analysis?.signal_rationale;
  if (signal && signal !== "HOLD") {
    signalSection.hidden = false;
    const badgeEl = document.getElementById("signal-badge");
    const signalLabel = String(signal).replace(/_/g, " ");
    badgeEl.textContent = signalLabel;
    badgeEl.className = "signal-badge signal-badge--" + String(signal).toLowerCase().replace(/_/g, "-");
    document.getElementById("signal-rationale").textContent = rationale || "";
  } else if (signal === "HOLD") {
    signalSection.hidden = false;
    const badgeEl = document.getElementById("signal-badge");
    badgeEl.textContent = "HOLD";
    badgeEl.className = "signal-badge signal-badge--hold";
    document.getElementById("signal-rationale").textContent = rationale || "No clear directional edge.";
  } else {
    signalSection.hidden = true;
  }

  // risk flags
  const riskSection = document.getElementById("risk-section");
  if (!vm.riskFlags.length) {
    riskSection.hidden = true;
  } else {
    riskSection.hidden = false;
    document.getElementById("risk-list").innerHTML = vm.riskFlags
      .map((flag) => {
        const label = typeof flag === "string" ? flag : flag.label ?? flag;
        const isHigh = String(label).toLowerCase().includes("high") || String(label).toLowerCase().includes("critical");
        return `<div class="risk-flag${isHigh ? " risk-flag--high" : ""}">
          <span class="risk-icon">${isHigh ? "🔴" : "🟡"}</span> ${esc(label)}
        </div>`;
      })
      .join("");
  }

  // drivers
  document.getElementById("driver-list").innerHTML = vm.keyDrivers.length
    ? vm.keyDrivers.map((d) => {
        const label = typeof d === "string" ? d : d.label ?? "";
        const rationale = typeof d === "string" ? "" : d.rationale ?? "";
        return `<li><strong>${esc(label)}</strong>${rationale ? ` — ${esc(rationale)}` : ""}</li>`;
      }).join("")
    : '<li style="color:var(--text-muted)">No key drivers identified</li>';

  // evidence
  document.getElementById("evidence-list").innerHTML = vm.evidence.length
    ? vm.evidence.map((ev) => `
        <div class="evidence-item">
          <div class="ev-title">${esc(ev.title)}</div>
          <div class="ev-snippet">${esc(ev.snippet)}</div>
          <div class="ev-meta">
            <span>${esc(ev.source)}</span>
            <span>relevance ${Number(ev.relevance_score).toFixed(2)}</span>
          </div>
        </div>
      `).join("")
    : '<p class="feed-empty">No evidence retrieved.</p>';

  // methodology
  const methBody = document.getElementById("methodology-body");
  const meth = state.analysis?.methodology;
  if (meth && typeof meth === "object") {
    methBody.innerHTML = Object.entries(meth)
      .map(([key, val]) => `<div class="method-row"><span class="method-label">${esc(key)}</span><span class="method-value">${esc(val)}</span></div>`)
      .join("");
  } else {
    methBody.innerHTML = '<span style="color:var(--text-muted);font-size:.78rem">No methodology data available.</span>';
  }

  // chart
  renderChart(vm.historySeries);
  renderMetrics();
}

/* ══════════ render: chart ══════════ */
function renderChart(series) {
  const container = document.getElementById("history-chart");
  if (!series.length) {
    container.innerHTML = '<p class="feed-empty" style="width:100%;text-align:center">No prior reactions yet.</p>';
    return;
  }
  const maxAbs = Math.max(...series.map((p) => Math.abs(p.value)), 0.001);
  container.innerHTML = series.map((p) => {
    const pct = Math.max(4, (Math.abs(p.value) / maxAbs) * 100);
    const cls = p.value >= 0 ? "chart-bar--pos" : "chart-bar--neg";
    return `<div class="chart-bar ${cls}" style="height:${pct}%" title="${p.value.toFixed(4)}"></div>`;
  }).join("");
}

/* ══════════ evaluation loader ══════════ */
async function loadEvaluation() {
  try {
    const res = await fetch("/evaluation");
    if (!res.ok) return;
    state.evaluation = await res.json();
    renderValidation();
  } catch { /* non-critical */ }
}

function renderValidation() {
  const container = document.getElementById("validation-body");
  const ev = state.evaluation;
  if (!ev || ev.error) {
    container.innerHTML = '<span style="color:var(--text-muted);font-size:.82rem">No evaluation data available.</span>';
    return;
  }

  const acc = (ev.direction_accuracy * 100).toFixed(1);
  const f1 = (ev.direction_macro_f1 * 100).toFixed(1);
  const mae = ev.magnitude_mae.toFixed(4);
  const brier = ev.calibration_score.toFixed(3);
  const cm = ev.confusion_matrix || [];
  const labels = ["Bearish", "Neutral", "Bullish"];

  let cmHtml = '<table class="cm-table"><thead><tr><th></th>';
  labels.forEach(l => { cmHtml += `<th>${l}</th>`; });
  cmHtml += "</tr></thead><tbody>";
  const maxVal = Math.max(1, ...cm.flat());
  labels.forEach((rowLabel, r) => {
    cmHtml += `<tr><th>${rowLabel}</th>`;
    (cm[r] || []).forEach(val => {
      const intensity = Math.round((val / maxVal) * 100);
      cmHtml += `<td class="cm-cell" style="--intensity:${intensity}%">${val}</td>`;
    });
    cmHtml += "</tr>";
  });
  cmHtml += "</tbody></table>";

  let breakdown = "";
  if (ev.pair_breakdown) {
    for (const [pair, m] of Object.entries(ev.pair_breakdown)) {
      breakdown += `<div class="method-row"><span class="method-label">${esc(pair)}</span><span class="method-value">${m.count.toFixed(0)} samples · ${(m.direction_accuracy * 100).toFixed(1)}% acc · MAE ${m.magnitude_mae.toFixed(4)}</span></div>`;
    }
  }

  container.innerHTML = `
    <div class="validation-metrics">
      <div class="method-row"><span class="method-label">Direction Accuracy</span><span class="method-value val-metric">${acc}%</span></div>
      <div class="method-row"><span class="method-label">Macro F1-Score</span><span class="method-value val-metric">${f1}%</span></div>
      <div class="method-row"><span class="method-label">Magnitude MAE</span><span class="method-value val-metric">${mae}</span></div>
      <div class="method-row"><span class="method-label">Calibration (Brier)</span><span class="method-value val-metric">${brier}</span></div>
    </div>
    <h4 style="margin:.6rem 0 .3rem;font-size:.82rem;color:var(--text-secondary)">Confusion Matrix (held-out test set)</h4>
    ${cmHtml}
    ${breakdown ? '<h4 style="margin:.6rem 0 .3rem;font-size:.82rem;color:var(--text-secondary)">Per-Pair Breakdown</h4>' + breakdown : ""}
    <p style="margin:.5rem 0 0;font-size:.75rem;color:var(--text-muted)">Evaluated on 30% held-out data using walk-forward temporal split (no future leakage).</p>
  `;
}

/* ══════════ correlation browser ══════════ */
let corrOffset = 0;
const CORR_PAGE = 50;
let corrLoaded = false;

function wireCorrelationPanel() {
  const details = document.querySelector("details:has(#correlation-body)");
  if (!details) return;
  details.addEventListener("toggle", () => {
    if (details.open && !corrLoaded) loadCorrelation();
  });
  const prev = document.getElementById("corr-prev");
  const next = document.getElementById("corr-next");
  if (prev) prev.addEventListener("click", () => { corrOffset = Math.max(0, corrOffset - CORR_PAGE); loadCorrelation(); });
  if (next) next.addEventListener("click", () => { corrOffset += CORR_PAGE; loadCorrelation(); });
}

async function loadCorrelation() {
  const body = document.getElementById("correlation-body");
  const nav = document.getElementById("correlation-nav");
  if (!body) return;
  body.innerHTML = '<p style="color:var(--text-muted);font-size:.82rem">Loading…</p>';
  try {
    const data = await fetchCorrelation(corrOffset, CORR_PAGE);
    corrLoaded = true;
    renderCorrelation(data, body);
    if (nav) {
      nav.hidden = false;
      document.getElementById("corr-prev").disabled = corrOffset === 0;
      document.getElementById("corr-next").disabled = corrOffset + CORR_PAGE >= data.total;
      document.getElementById("corr-info").textContent = `${corrOffset + 1}–${Math.min(corrOffset + CORR_PAGE, data.total)} of ${data.total}`;
    }
  } catch {
    body.innerHTML = '<p style="color:var(--text-muted);font-size:.82rem">No correlation data available. Run the alignment script first.</p>';
  }
}

function renderCorrelation(data, container) {
  if (!data.items || data.items.length === 0) {
    container.innerHTML = '<p style="color:var(--text-muted);font-size:.82rem">No correlation data available.</p>';
    return;
  }
  container.innerHTML = data.items.map(row => {
    const text = (row.article_text || "").slice(0, 120);
    const reaction = row.reaction_label ?? 0;
    const cls = reaction > 0 ? "corr-reaction--pos" : reaction < 0 ? "corr-reaction--neg" : "";
    const ts = row.timestamp ? row.timestamp.slice(0, 10) : "—";
    const horizon = row.horizon_minutes ? `${Math.round(row.horizon_minutes / 60)}h` : "—";
    const sent = row.sentiment_label != null ? (row.sentiment_label >= 0 ? "+" : "") + Number(row.sentiment_label).toFixed(2) : "—";
    return `<div class="corr-row">
      <div class="corr-headline">${esc(text)}</div>
      <div class="corr-meta">
        <span>${ts}</span>
        <span>Reaction: <span class="corr-reaction ${cls}">${reaction >= 0 ? "+" : ""}${(reaction * 100).toFixed(3)}%</span></span>
        <span>Sentiment: ${sent}</span>
        <span>Horizon: ${horizon}</span>
        <span>${row.pair || "USD/INR"}</span>
      </div>
    </div>`;
  }).join("");
}

/* ══════════ live sentiment poller ══════════ */
function startLiveSentimentPoller() {
  pollLiveSentiment();
  setInterval(pollLiveSentiment, 30_000);
}

async function pollLiveSentiment() {
  try {
    const snap = await fetchLiveSentiment();
    renderLiveSentiment(snap);
  } catch {
    /* server may not have live sentiment yet — ignore silently */
  }
}

function renderLiveSentiment(snap) {
  const scoreEl = document.getElementById("live-score");
  const metaEl = document.getElementById("live-meta");
  const dot = document.querySelector(".live-dot");
  if (!scoreEl || !metaEl) return;

  const val = snap.overall_sentiment;
  const count = snap.item_count ?? 0;
  const conf = snap.confidence ?? 0;

  scoreEl.textContent = (val >= 0 ? "+" : "") + val.toFixed(3);
  scoreEl.style.color = val > 0.05 ? "#22c55e" : val < -0.05 ? "#ef4444" : "var(--text-main)";

  const label = count === 0 ? "No data yet" : `${count} items · ${(conf * 100).toFixed(0)}% conf · ${snap.window_minutes}m window`;
  metaEl.textContent = label;

  if (dot) {
    dot.classList.toggle("live-dot--active", count > 0);
  }
}

/* ══════════ render: topbar metrics ══════════ */
function renderMetrics() {
  const el = document.getElementById("topbar-metrics");
  if (!state.metrics) { el.textContent = "—"; return; }
  el.textContent = `${state.metrics.analysis_requests ?? 0} requests · ${state.metrics.analyses_completed ?? 0} completed`;

  const metricsBlock = document.getElementById("metrics-block");
  if (metricsBlock) {
    metricsBlock.textContent = state.metrics
      ? `Req: ${state.metrics.analysis_requests} · Done: ${state.metrics.analyses_completed}`
      : "—";
  }
}

function formatFeedTimestamp(value) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "recently";
  return timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/* ══════════ util ══════════ */
function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

/* ══════════ event category auto-detect ══════════ */
const CATEGORY_RULES = [
  { pattern: /\b(rate\s*decision|repo\s*rate|interest\s*rate|rate\s*cut|rate\s*hike|basis\s*points?|bps)\b/i, category: "rate_decision" },
  { pattern: /\b(fed\b|fomc|federal\s*reserve|powell)/i, category: "fed_decision" },
  { pattern: /\b(central\s*bank|ecb|boj|boe|rbi|das|monetary\s*policy)\b/i, category: "central_bank" },
  { pattern: /\b(gdp|cpi|inflation|pmi|jobs?\s*report|nonfarm|payroll|unemployment|manufacturing|retail\s*sales)\b/i, category: "macro_data" },
  { pattern: /\b(oil|gold|crude|commodity|brent|wti|opec|energy)\b/i, category: "commodity" },
  { pattern: /\b(fdi|capital\s*flow|foreign\s*investment|portfolio\s*flow|fii|fpi)\b/i, category: "capital_flows" },
  { pattern: /\b(trade\s*deficit|trade\s*surplus|export|import|trade\s*balance|current\s*account)\b/i, category: "trade_data" },
  { pattern: /\b(war|conflict|sanction|geopolitical|missile|nuclear|tension)\b/i, category: "geopolitical" },
  { pattern: /\b(risk\s*on|risk\s*off|sentiment|haven|safe\s*haven|risk\s*appetite|crash|correction|bear\s*market)\b/i, category: "risk_sentiment" },
  { pattern: /\b(budget|fiscal\s*deficit|fiscal|gst|tax\s*reform|government\s*spending)\b/i, category: "fiscal" },
  { pattern: /\b(tariff|trade\s*war|regulation|reform|financial\s*law|policy)\b/i, category: "policy" },
  { pattern: /\b(bond\s*yield|treasury\s*yield|10.year|yield\s*curve)\b/i, category: "yields" },
  { pattern: /\b(dollar\s*index|dxy)\b/i, category: "dxy" },
  { pattern: /\b(moody|fitch|s&p|rating|upgrade|downgrade)\b/i, category: "rating" },
  { pattern: /\b(intervention|forex\s*swap|forex\s*window)\b/i, category: "intervention" },
];

function detectEventCategory(headline) {
  if (!headline) return "";
  for (const rule of CATEGORY_RULES) {
    if (rule.pattern.test(headline)) return rule.category;
  }
  return "";
}
