import test from "node:test";
import assert from "node:assert/strict";

import { buildResultViewModel, buildUiState, buildFeedItemViewModel } from "../view-model.js";

test("buildUiState returns loading and error visibility flags", () => {
  const loadingState = buildUiState({ loading: true, error: "", analysis: null });
  const errorState = buildUiState({ loading: false, error: "boom", analysis: null });

  assert.equal(loadingState.showLoading, true);
  assert.equal(loadingState.showResults, false);
  assert.equal(errorState.showError, true);
  assert.equal(errorState.showResults, false);
});

test("buildResultViewModel formats summary, pairs, and history data", () => {
  const analysis = {
    sentiment_score: 0.42,
    market_score: -0.35,
    confidence: 0.73,
    affected_symbols: ["EUR/USD", "USD/JPY"],
    evidence: [{ evidence_id: "ev-1" }],
    risk_flags: ["HIGH_EVENT_RISK"],
    key_drivers: [{ label: "ECB tone", rationale: "hawkish" }],
  };
  const historyItems = [
    { produced_at: "2026-03-24T10:00:00+00:00", market_score: 0.1 },
    { produced_at: "2026-03-25T10:00:00+00:00", market_score: -0.2 },
  ];

  const viewModel = buildResultViewModel(analysis, historyItems);

  assert.equal(viewModel.sentimentDisplay, "+0.4200");
  assert.equal(viewModel.marketDisplay, "-0.3500");
  assert.equal(viewModel.confidenceDisplay, "73%");
  assert.deepEqual(viewModel.pairs, ["EUR/USD", "USD/JPY"]);
  assert.deepEqual(viewModel.historySeries, [
    { timestamp: "2026-03-25T10:00:00+00:00", value: -0.2 },
    { timestamp: "2026-03-24T10:00:00+00:00", value: 0.1 },
  ]);
});

test("buildFeedItemViewModel creates a feed view model", () => {
  const event = {
    event_id: "test-1",
    headline: "RBI cuts rate",
    source: "FINNHUB",
    symbols: ["USD/INR"],
    published_at: "2026-03-25T10:00:00+00:00",
  };

  const vm = buildFeedItemViewModel(event);

  assert.equal(vm.id, "test-1");
  assert.equal(vm.headline, "RBI cuts rate");
  assert.equal(vm.source, "FINNHUB");
  assert.equal(vm.sourceLabel, "FINNHUB");
  assert.deepEqual(vm.symbols, ["USD/INR"]);
  assert.ok(vm.time.length > 0);
  assert.equal(vm.analysisResult, null);
  assert.equal(vm.analyzing, false);
});

test("buildFeedItemViewModel normalizes provider-qualified Finnhub sources", () => {
  const event = {
    headline: "Germany halves 2026 growth forecast amid Iran war",
    source: "FINNHUB (Reuters)",
    pair: "USD/INR",
    timestamp: "2026-03-25T12:00:00+00:00",
  };

  const vm = buildFeedItemViewModel(event);

  assert.equal(vm.source, "FINNHUB");
  assert.equal(vm.sourceLabel, "FINNHUB (REUTERS)");
});

test("buildFeedItemViewModel preserves feed provenance metadata", () => {
  const event = {
    headline: "Fallback RBI scenario",
    source: "RSS",
    pair: "USD/INR",
    timestamp: "2026-03-25T12:00:00+00:00",
    provenance: "synthetic",
    provider: "synthetic",
    feed_mode: "fallback",
    upstream_status: "fallback",
    upstream_note: "Synthetic fallback may appear.",
    last_upstream_item_at: "2026-03-25T11:55:00+00:00",
  };

  const vm = buildFeedItemViewModel(event);

  assert.equal(vm.provenance, "synthetic");
  assert.equal(vm.provider, "synthetic");
  assert.equal(vm.feedMode, "fallback");
  assert.equal(vm.upstreamStatus, "fallback");
  assert.equal(vm.lastUpstreamItemAt, "2026-03-25T11:55:00+00:00");
});

test("buildFeedItemViewModel maps pair field to symbols", () => {
  const event = {
    headline: "Brent crude tops $92",
    source: "FINNHUB",
    pair: "USD/CAD",
    timestamp: "2026-03-25T12:00:00+00:00",
  };

  const vm = buildFeedItemViewModel(event);

  assert.deepEqual(vm.symbols, ["USD/CAD"]);
  assert.ok(vm.id.length > 0);
});
