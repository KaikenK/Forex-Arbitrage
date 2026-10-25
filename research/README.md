# FX Event-Impact Research Workspace

Research question:

> Do distinct **categories** of textual events (scheduled macro releases, central-bank
> speeches, geopolitical shocks, ratings, unscheduled breaking news) produce measurably
> different **abnormal-return and volatility signatures** in FX — in magnitude, speed
> (half-life), and predictability — and can a time-aligned multi-source model exploit
> those category-specific responses out of sample?

Two contributions (hybrid framing):
1. **Impact atlas** — an event-study characterization of per-category abnormal-return
   signatures with statistical significance (explanatory / causal).
2. **Predictive model** — conditioned on event category + surprise + novelty, evaluated
   walk-forward against efficient-market baselines (predictive).

Scope: majors (EUR/USD, USD/JPY, GBP/USD, AUD/USD, USD/CHF) + USD/INR.
Sources for v1: news headlines, economic calendar (surprise), central-bank text. No social
(free live Twitter/X and Reddit access is restricted; deferred to a later extension).

This workspace reuses `news_stream`'s ingestion + normalizer rather than re-wiring them.

## Layout

```
research/
  README.md              # this file
  DATA_SOURCES.md        # free data-source registry + access/legal notes
  eventstudy/            # phase-1 core: time-alignment + event-study atlas
    schema.py            # Event, PriceSeries, AlignedObservation
    alignment.py         # leak-safe event -> forward-return alignment (fail-closed)
    atlas.py             # per-category aggregation + significance (the atlas)
  tests/                 # deterministic unit tests (no network)
```

## Pipeline (target)

```
Ingestion (news_stream + GDELT + calendar/ALFRED + BIS speeches)
   -> Normalize (multi-label event_category, surprise, novelty, reliability)
   -> Time-Align  [eventstudy.alignment]  leak-safe forward windows
   -> Abnormal returns (market model vs DXY / oil / yield-diff)   [TODO]
   -> Atlas [eventstudy.atlas]  per-category CAR + significance
   -> Features (embeddings + event-type + surprise z + novelty + regime)  [TODO]
   -> Models: event-study panel (atlas) -> predictive (GBM / temporal / LLM features) [TODO]
   -> Eval: walk-forward, per-category ablation, direction + magnitude + vol + conformal [TODO]
```

## Evaluation protocol (planned)

- **Walk-forward** splits by time (no random shuffling); report per-fold.
- **Leak-safety**: entry price is the first bar at/after the event's publication time;
  windows that have not matured are dropped (fail-closed), never imputed.
- **Atlas**: per category per horizon — mean abnormal return, |AR| (impact), t-stat, p-value,
  direction hit-rate, reaction half-life.
- **Predictive**: direction (accuracy, macro-F1), magnitude (MAE), volatility (QLIKE),
  calibration (reliability + conformal coverage). Baselines: lexicon, FinBERT, LLM, random walk.

## Milestones

- [x] M0 — workspace + event-study alignment/atlas core + tests
- [ ] M1 — data connectors: price (yfinance/AlphaVantage/FRED), GDELT, calendar (ALFRED), BIS speeches
- [ ] M2 — market-model abnormal returns (covariates: DXY, oil, US-IN yield diff)
- [ ] M3 — build the unified time-aligned dataset; first impact atlas
- [ ] M4 — features + baselines (lexicon / FinBERT / LLM) with walk-forward
- [ ] M5 — predictive models + conformal calibration; ablations by category
```
