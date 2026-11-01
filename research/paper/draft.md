# From Headline to Currency Reaction: Predicting Affected Pairs, Direction, Magnitude and Confidence of FX Moves from News, for Ranking Arbitrage Opportunities

*Working draft — 2026-11-02. Numbers marked [TBD] are produced by runs scheduled after the
2026-11-10 GDELT freeze or the 2026-11-18 prospective scoring. Every number here comes from a
named artifact; see `research/REPRODUCE.md`.*

---

## Abstract  [draft after results]

Cross-venue FX arbitrage systems detect crossed books but cannot tell which opportunities a
piece of news makes dangerous. We build and evaluate a semantic layer that, for each incoming
article, states which currency pairs are affected, in which direction, by how much and with what
calibrated confidence. For scheduled macroeconomic releases — the news that moves major pairs
most — a structured pipeline grounded in 1,385 high-impact releases (2018–2025, four majors,
one-minute bars) predicts the direction of the 15-minute release reaction 68% of the time with a
theory-signed surprise rule, with calibrated confidence (ECE 0.04) and conformal intervals for the
move size. Under a pre-registered walk-forward design, a learned per-cell response model does
*not* beat the theory rule, and from the moment a news article typically arrives (15 minutes
after the release) no direction remains to predict (48.8%, p = 0.27): the reaction is complete
within a minute and persists. We report three silent errors in widely used free data
(ForexFactory timestamps, HistData daylight-saving clocks, GDELT DOC truncation), each of which
changes a published-style conclusion, and [TBD: unscheduled-news arm H1/H5/H6, prospective live
test, ranking value].

---

## 1. Introduction

- Arbitrage detection vs ranking: crossed books are near market-neutral once both legs fill;
  the residual risks are legging exposure and stale quotes around news.
- What a semantic layer must output to be useful to a ranker: affected pairs, direction,
  magnitude, confidence, horizon — and what "confidence" must mean (a calibrated probability).
- Contributions:
  1. A pre-registered evaluation of an article → per-pair reaction pipeline, with the
     arrival-time distinction that matters for live use.
  2. Evidence that a theory-signed surprise rule beats a learned response model out of sample,
     and that the direction signal is exhausted by the time an article arrives.
  3. Three silent data errors in free FX/news data, each shown to flip a result.
  4. A live microservice implementation and a prospective test of the deployed model.

## 2. Related work  [refs to verify against originals before submission]

- Macro announcements and FX at high frequency: Andersen, Bollerslev, Diebold & Vega (2003,
  *AER*); Faust, Rogers, Wang & Wright (2007, *JME*); Evans & Lyons (2008, *JFE*).
- Stale vs new news: Tetlock (2011, *RFS*).
- News-based indices: Baker, Bloom & Davis (2016, *QJE*).
- Financial NLP baselines: FinBERT (Araci 2019).
- GDELT: Leetaru & Schrodt (2013).

## 3. Data, and three silent errors

### 3.1 Sources
- Economic calendar: ForexFactory-format dump, 34,333 events 2018-01 → 2025-04; high-impact,
  exact-time releases with a standardized surprise: 2,778 (1,482 US).
  Option A standardization: σ per (currency, indicator) estimated outside 2020-03-01..2021-06-30,
  applied to all, winsorized at ±5σ.
- Prices: HistData M1 bid closes 2018–2025 for EUR/USD, GBP/USD, USD/JPY, USD/CHF.
- News: GDELT DOC 2.0 API (English).

### 3.2 Error 1 — calendar timestamps
- Missing times stored as local 00:00:00 (~30% of rows; 88/88 payrolls rows 2018–2025), and
  stale Iranian DST in the dump's offsets (2023+ summer rows 1 h early; 5,071 rows).
- Effect: the pre-registered positive control failed twice; on corrected clocks the original
  specification passes all three criteria (payrolls 5 m EUR/USD β = −8.99 bps/σ, p = 0.010;
  pooled +6.51, p = 1e-11; placebo p = 0.33). Fix from documentation and observed rows only.

### 3.3 Error 2 — HistData daylight saving
- Files described as fixed EST are New York wall clock with DST. Symptom: a reaction "jump"
  exactly 60 min after every release in summer. Effect: summer 1 m reaction +0.5 bps under the
  documented clock vs +8.5 bps after the fix; H2/H3 computed on the documented clock were void.

### 3.4 Error 3 — GDELT DOC truncation
- A 24 h request with `sort=datedesc` and the 250-record cap returns the ~12 minutes before
  00:00 UTC: in a 169-day sample 88% of kept headlines fall 23:00–01:00 UTC; median per-day span
  0.2 h. [TBD: volume ratio from `timelinevolraw`.] Remedy: time-stratified 1 h slices.

## 4. Method

### 4.1 Pre-registration
All hypotheses, metrics, baselines and stop rules were fixed in a dated, append-only tracker
before results (RESEARCH_TRACKER §2, §6); deviations are numbered (DEVIATION 1–3) with reasons.

### 4.2 Unit and targets
- Release × pair: 1,385 releases with z ≠ 0 (USD 1,482→ after z ≠ 0 filtering [count by currency TBD]) × 4 majors.
- Release-time returns r(0,h), h ∈ {1,5,15,30,60,120} min (primary 15).
- Arrival-time returns r(15,15+h), h ∈ {15,45,105} (primary 15→60): an article is assumed to
  arrive 15 min after the release.
- Control v: mean |return| over the same clock window on the previous 20 weekdays.

### 4.3 Model
- Direction: sign(β̂·z), β̂ per (currency, category, pair), fallback (currency, pair).
- Move: |r| = a + b|z| + c·v per cell.
- Confidence: logistic P(correct | |z|) per category.
- Interval: split-conformal, last 12 training months as calibration.
- Expanding monthly walk-forward, 12 training-only months, 76 test months; SEs and bootstrap CIs
  clustered by release moment (~900 clusters).

## 5. Results — scheduled releases (RQ1–RQ4)

Source: `research/artifacts/reaction_eval.json`.

### 5.1 Direction (RQ2)

| Horizon | Model | Theory rule | Majority |
|---|---|---|---|
| release 1 m | 68.2% | **75.2%** | 51.5% |
| release 5 m | 64.6% | **71.8%** | 52.4% |
| **release 15 m (primary)** | 63.5% [60.2, 66.6] | **68.4%** [65.4, 71.3] | 51.5% |
| release 60 m | 60.4% | **64.4%** | 51.8% |
| release 120 m | 57.7% | **61.0%** | 52.2% |
| arrival 15→30 m | 47.9% | 50.5% | 51.5% |
| **arrival 15→60 m (primary)** | 48.8% [46.6, 51.0], p = 0.27 | 51.0% | 50.5% |
| arrival 15→120 m | 50.2% | 50.1% | 49.7% |

- Accuracy rises with |z| (15 m: 58% → 65% → 68% by tercile).
- The learned β agrees with the theory sign on 82% of rows; the disagreements are noise.
- Mean move in the predicted direction: 7–8 bps from 1 m to 120 m; after arrival ≈ 0
  (−0.14 bps, p = 0.68).

### 5.2 Magnitude (RQ3)
- 15 m: Spearman 0.287 (model) vs 0.189 (pair mean), 0.242 (cell mean), 0.285 (same-clock v);
  MAE no better than any baseline (12.01 vs 11.54 bps; difference +0.47, p = 0.15).
- After arrival the same-clock baseline beats the model (MAE 7.11 vs 7.40, p < 0.001).

### 5.3 Confidence (RQ4)
- ECE 0.041 at 15 m; Brier 0.234 vs 0.232 for a constant rate (calibrated, little resolution).
  At 1 m: Brier 0.207 vs 0.217.
- Conformal coverage 76.5% (nominal 80%), 86.9% (90%).

### 5.4 Affected pairs (RQ1)
- Currency rule: recall 0.90, precision 0.41 (F1 0.56). Abnormal-move rate 41% for own pairs vs
  19% for cross pairs (a EUR release moves GBP/USD and USD/JPY abnormally ~22% of the time).

## 6. Unscheduled news (H1, H5, H6)  [TBD after the 2026-11-10 freeze]

## 7. Live system and prospective test  [TBD]
- Architecture: detector (Arbex backend) → semantic microservice (/v1/analyze, release extractor
  + scorer behind SEMANTIC_REACTION_V2) → ranking channels.
- Prospective test of the V1 model (2026-10-25 → 2026-11-01 21:09 UTC) and of the release model
  (2026-11-01 21:09 UTC → 2026-11-18). [TBD]
- RQ5 ranking value on recorded opportunities. [TBD]
- Real-headline extraction (RQ2-E2E). [TBD]

## 8. Discussion
- For a ranker, the semantic layer's direction output describes a move that has already
  happened; its value is in labelling which venue is stale and in release-window risk, not in
  forecasting post-arrival drift.
- Calibration matters more than accuracy for a weighting factor.
- Free-data errors are silent and directional: each one we found biased results toward a
  null.

## 9. Limitations
- USD/INR has no free intraday data; JPY/CHF releases are few (3 / 30).
- GDELT headlines only, 15-minute timestamp quantization, sampled slices.
- The H2/positive-control analyses used the same 2018–2025 sample; walk-forward is the
  out-of-sample evidence for RQ1–RQ4.
- Two cutoff moves and one sample re-collection (DEVIATIONS 1–3), all before results.
