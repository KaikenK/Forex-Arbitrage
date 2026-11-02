# Research Tracker — scope lock and decision log

**This document governs the research.** Before starting any new work, check it against
§2 (locked hypotheses) and §3 (scope boundary). If the work does not serve a locked
hypothesis, it does not get built — it gets logged in §7 as a deferred idea.

Purpose: prevent the failure mode where a component underperforms, we try to fix it,
the fix underperforms, and the project becomes an endless improvement loop with no
finished result. **A measured negative result is a finished result.** Time-boxes and
kill criteria below make that explicit.

---

## 1. Thesis (the one sentence)

> **Semantic novelty — how much genuinely new information a news event carries relative
> to recent coverage — predicts the magnitude of foreign-exchange market reactions better
> than sentiment polarity does.**

Everything in this project exists to test that claim or to make testing it credible.

Why this thesis survives contact with reality:
- It is **falsifiable**, with a concrete baseline to beat (lexicon / FinBERT sentiment).
- It is **publishable either way**: "novelty beats sentiment" is a finding; "novelty does
  not beat sentiment on FX" is also a finding, provided the test was fair.
- It does **not** require beating the market. We are explaining reactions, not forecasting
  profits — a much more defensible bar for a capstone panel.

---

## 2. Locked hypotheses with pre-registered decision rules

Pre-registering the decision rule *before* seeing results is what stops p-hacking and
stops the endless-tuning trap. Each hypothesis states in advance what counts as support,
what counts as rejection, and when we stop.

### H1 (primary) — Novelty predicts reaction magnitude
Novelty score has incremental predictive power for |abnormal return| beyond sentiment.

- **Test**: nested model comparison. Baseline = controls + sentiment. Full = controls +
  sentiment + novelty. Walk-forward out-of-sample.
- **Supported if**: full model improves out-of-sample R² (or Spearman rank correlation)
  over baseline, and the novelty coefficient is significant under event-clustered
  standard errors.
- **Rejected if**: no improvement after testing all three embedding backends.
- **Stop condition**: 3 embedding backends × 1 novelty definition. Then report the result,
  whatever it is. **Do not invent a fourth embedder to rescue the hypothesis.**

### H2 — Event categories produce distinct abnormal-return signatures
- **Test**: per-category CAR with event-clustered SEs and Benjamini-Hochberg FDR.
- **Supported if**: ≥2 categories survive FDR at q ≤ 0.05.
- **Status**: preliminary support (5/24 cells at daily resolution, uncorrected for clustering).
- **Stop condition**: one pass with clustering applied. Report surviving cells.

### H3 — Reaction speed varies by category
- **Test**: reaction half-life per category, scheduled events only (exact timestamps).
- **Supported if**: half-life confidence intervals are disjoint between ≥2 categories.
- **Stop condition**: one pass on the calendar sample.

### H4 — Reactions are regime-conditional
- **Test**: interaction terms (category × VIX tercile) or a causal forest for heterogeneity.
- **Supported if**: interaction is significant, or CATE variation exceeds a placebo permutation.
- **Priority**: lowest. **Cut this first if time runs short.**

### H5 — News sentiment is a currency-level factor *(pre-registered 2026-10-24, before any H5 data was examined)*

A pair's price is a ratio of two currencies, so its news sentiment should be the difference
of two currency-level sentiments rather than a property of the pair.

- **Construct**: a daily news-sentiment index per currency, `S_c,t` for
  c ∈ {USD, EUR, JPY, GBP, CHF, INR}. Each deduplicated GDELT headline is scored and attributed
  to every currency it is tagged with (`data/tagging.py`). Implied pair sentiment is
  `S_base − S_quote`.
- **Market-wide component**: the first principal component of the currency indices, fitted
  walk-forward (each date uses only past data), sign fixed so that higher = risk-on.
- **Control**: a Citi-style economic surprise index per currency built from the stored
  standardized calendar surprises, so news is tested for information *beyond* macro surprises.
- **Test (a), decomposition**: walk-forward comparison of `S_base − S_quote` against
  pair-tagged sentiment as a predictor of daily pair returns; standard errors clustered by date.
- **Test (b), market component**: correlation of the market component with the same-day VIX
  change and with JPY and CHF safe-haven returns, controlling for the surprise index.
- **Supported if**: (a) the decomposed measure improves out-of-sample R² or Spearman over the
  pair-tagged baseline, **and** (b) the market component is significant with the predicted
  sign (risk-on ↔ VIX falls, JPY and CHF weaken).
- **Rejected if**: neither (a) nor (b) holds with either sentiment backend.
- **Stop condition**: one index definition × the two existing backends (lexicon, FinBERT).
  No third backend, no alternative index definitions after seeing results.
- **Resolution**: daily. This is a sentiment-level claim tested against daily returns and
  daily VIX; it does not depend on the intraday arm, so R1 does not block it.
- **Precedent**: Baker, Bloom & Davis (2016) news-based policy-uncertainty index; the Citi
  Economic Surprise Index.

### RQ1–RQ5 — Article → affected pairs, direction, magnitude, confidence *(pre-registered 2026-11-02 IST, before any RQ result exists; project owner set the goal 2026-11-01)*

The semantic wing's job is: an article arrives → for each pair, is it affected, which way, how
far, how sure. RQ1–RQ5 test exactly that. They are the paper's primary questions; H1, H5, H6
become the unscheduled-news arm (§6 DEVIATION 3). **Disclosure:** H2 and the positive control
already used the 2018-01..2025-04 release sample, and the structured design below is motivated
by them; the out-of-sample evidence is the walk-forward, plus the prospective live tests.

**Historical unit.** Each high-impact, exact-time release (incl. validated release-clock fills)
with an Option A standardized surprise `z` ≠ 0, currency ∈ {USD, EUR, GBP, JPY, CHF},
2018-01-01..2025-04-30, × each of the four majors (EUR/USD, GBP/USD, USD/JPY, USD/CHF).
Returns from HistData M1 (bars stamped at close, New York DST-corrected), log returns in bps,
reference = last bar closing at/before the release; bar lags ≤ 2 min entry / ≤ 5 min exit, fail
closed. SEs and bootstrap CIs cluster by release moment (same timestamp). USD/INR: no intraday
data, so not in RQ1–RQ4 (limitation).

**Horizons.** Release-time returns r(0,h), h ∈ {1, 5, 15, 30, 60, 120} min; **primary h = 15**.
Arrival-time returns r(15, 15+h), h ∈ {15, 45, 105} (an article is assumed to arrive 15 min
after the release); **primary = r(15, 60)**. Non-primary horizons BH-corrected.

**Model (fixed).** Expanding-window, monthly walk-forward; the first 12 months training-only (as
C5); every fit uses only releases strictly before the test month.
- Direction: ŝ = sign(β̂_{cat,pair} · z), β̂ from an OLS of the pair's signed return on z within
  category × pair (category = `data/tagging.py` first category); fewer than 20 training releases
  in a cell → β̂ of the pooled-category pair model.
- Expected move: |r| = a + b·|z| + c·v, per category × pair (same fallback), v = mean |r| over
  the same clock window on the previous 20 weekdays (≥ 10 required, as C3/C4).
- Confidence: p = P(ŝ correct) from a logistic regression of correctness on |z| and log(1+|z|),
  per category (fallback pooled), fitted on training folds only.
- Interval: split-conformal on |r| residuals, calibration set = the last 12 training months;
  nominal 80% and 90%.
- Horizon output: the release-time cumulative profile (mean oriented move at each h) and the
  arrival-time residual move.

**RQ1 — affected pairs.** Label affected = |r(0,15)| > 2 × v. Rule = pair contains the release
currency (USD → all four). Report precision/recall/F1 of the rule; AUC of the model's expected
move as a score for "affected"; spillover rates for pairs not containing the currency.
**RQ2 — direction.** Sign accuracy (realised r ≠ 0) and AUC of ŝ·p vs realised sign, against:
majority class (training), theory rule sign(z)·news_sign (`sign_conventions.news_sign`), and
coin flip. Release-time and arrival-time. Accuracy by |z| tercile.
**RQ3 — magnitude.** MAE and Spearman of expected vs realised |r|, against: training mean per
pair, category × pair training mean, v alone. Model-minus-baseline absolute-error difference with
release-moment-clustered SE.
**RQ4 — confidence.** ECE (10 equal-mass bins), Brier, reliability curve; conformal coverage at
80/90% (pass band ±5 pp, reported, not a gate).
**RQ2-E2E — real headlines (secondary).** Release headlines in the GDELT slice sample (below)
matched to a calendar release (same currency and category, timestamp within 60 min after it):
extractor success rate and accuracy of the extracted actual vs the calendar actual (exact to the
published precision); direction accuracy from the article timestamp of (i) the structured
pipeline, (ii) FinBERT polarity oriented to the currency, (iii) the current text model
(`ArtifactReactionScorer`, pooled_multipair artifact).
**RQ5 — ranking value (descriptive).** On recorded Arbex opportunities (H6b sample, plus
recordings to 2026-11-18 reported separately as an extension): still-crossed AUC of
detector-only vs + the new risk channel vs + direction/stale-leg channel; bootstrap by day.

**Prospective test of the deployed (before) model.** Every row of
`sentiment_assets/runtime/predictions/live_predictions.jsonl` with `published_at` from 2026-10-25
up to the new model's deployment and direction ≠ 0. Outcome = log return of the pair from the
first Yahoo hourly bar opening at/after `published_at` to the first bar at/after `published_at` +
`horizon_minutes` (EURUSD=X, GBPUSD=X, JPY=X, CHF=X, INR=X; pair orientation applied); rows whose
outcome bar does not exist yet are dropped. Metrics: sign accuracy vs 50% and Spearman IC of
`market_score`, clustered by UTC date; reliability of `confidence`. The new model gets the same
test on its own logged predictions from deployment to 2026-11-18. Descriptive (few date clusters).

**Stop condition.** One model specification as above; no re-specification after any test-month
result is seen. Negative results reported.

---

## 3. Scope boundary

### In scope
- Event database: GDELT (unscheduled) + ForexFactory calendar (scheduled).
- Pairs: EUR/USD, GBP/USD, USD/JPY, USD/CHF at intraday resolution; USD/INR at daily
  resolution. *(AUD/USD dropped 2026-10-24 — see §6.)*
- Compute: everything runs on one laptop. No cloud services, one heavy job at a time.
- Horizons: 1m–1d for scheduled events; **≥30m only for GDELT** (15-minute quantization).
- Semantic layer: 3 embedding backends, one novelty definition, syndication dedup.
- Event-study statistics: abnormal returns, clustered SEs, FDR, placebo tests.
- Models: ridge/logistic → gradient boosting. Transformers **only** as sentiment baselines.

### Out of scope — do not build these
| Excluded | Why |
|---|---|
| Trading strategy, backtest P&L, Sharpe | We explain reactions; we do not claim alpha. Different, much higher bar. |
| Training a custom language model | No compute, no data. Use pretrained as a baseline only. |
| Deep sequence models (LSTM/TFT) as the headline | Cannot beat linear baselines at this sample size. Optional appendix at best. |
| Social media (Twitter/X, Reddit) | Free historical access is restricted. Documented limitation, not a task. |
| More pairs / EM currencies | Marginal value, linear cost. |
| Improving the production arbitrage engine | Already works. Not part of the research claim. |
| Reinforcement learning, LLM agents | Scope-creep magnets with no connection to the thesis. |

### The two-attempt rule
Any component gets **two** serious attempts to meet its bar. If attempt 2 fails, we
**stop, write down the negative result with its numbers, and move on.** No third attempt
without an explicit entry in §6 justifying it. This rule exists specifically to prevent
the improvement trap.

---

## 4. Milestones and definition of done

A milestone is done when its artifact exists, its tests pass, and its number is recorded
here. Not when it "works on my machine".

| # | Milestone | Definition of done | Status |
|---|---|---|---|
| M0 | Event-study core | Leak-safe alignment + atlas + tests | **Done** |
| M1 | Event database at scale | ≥50k tagged events, reproducible, resumable | In progress (5.9k, 12% backfilled) |
| M2 | Calendar / surprise | Scheduled events with standardized surprise in the store | **Done** — 34,333 events, 22,517 standardized |
| M3 | Intraday prices | Event-windowed minute bars for scheduled events; EUR/USD positive control replicates Andersen et al. (2003) | HistData 2018–2025 cached; control run twice — pooled + placebo pass, payrolls criterion fails (§6) |
| M4 | Abnormal returns | 3 expected-return models + placebo test passes | Models done, placebo pending |
| M5 | Semantic layer | Novelty scores joined onto the feature table | Module done, not joined |
| M6 | H1 test | Nested comparison, walk-forward, clustered SEs | Not started |
| M7 | Atlas artifact | Retrieval + conformal intervals | Not started |
| M8 | Write-up | Methods, results, limitations, negative results | Not started |
| M9 | H5 test | Currency indices + market component + surprise-index control, walk-forward | Not started |

**M6 is the deliverable.** M1–M5 exist only to make M6 credible. If schedule slips,
cut M7 and H4 — never cut M6. M9 (H5) ranks below M6 and above M7.

---

## 5. Kill criteria

Stop and report rather than continue if any of these hold:

- **Sample too small**: a hypothesis cell has < 30 independent events after clustering →
  report as underpowered, do not tune to significance.
- **Placebo fails**: pseudo-events at random timestamps show significant abnormal returns
  → the pipeline is broken. Fix the pipeline; do not report findings until it passes.
- **No signal after two attempts** → negative result, documented with numbers.
- **A dependency needs > 1 day of engineering** → substitute or drop it, log in §6.

---

## 6. Decision log (append-only)

Never re-litigate a logged decision without a new entry stating what changed.

| Date | Decision | Rationale |
|---|---|---|
| 2026-08 | Scheduled and unscheduled events analysed separately | GDELT timestamps quantized to 15 min (82% collisions); calendar releases are exact. Mixing them invalidates short horizons. |
| 2026-08 | Daily resolution abandoned for the causal arm | FX reacts in minutes; daily horizons are mostly noise (\|AR\| 40–70bp vs mean 5–25bp). |
| 2026-08 | Static calendar CSV over live scraping | Reproducible, versionable, no ToS risk. |
| 2026-08 | Precision over recall in tagging | A mistagged story injects an unrelated price path, biasing estimates toward zero. |
| 2026-08 | No trading-strategy claims | Explaining reactions is defensible; claiming alpha is not, at this sample size. |
| 2026-08 | H1 (novelty) is the headline, not sentiment | Sentiment analysis alone is a saturated, unremarkable contribution. |
| 2026-08 | USD releases attributed to EUR/USD only (`usd_policy="primary"`) | A USD release moves every USD pair, so it has no natural single pair. Attributing to all majors triples fetch cost and forces the same event to appear 5×. EUR/USD is the most liquid pair, giving the cleanest reaction and one independent observation per event. |
| 2026-08 | Intraday horizons capped at 120m; 4h/1d come from daily bars | Extending the fetch window to cover a 1-day horizon costs ~4× the tick data for horizons the daily series already provides. Hybrid resolution, documented. |
| 2026-10-24 | **Supersedes the 2026-08 `usd_policy="primary"` entry.** Each US release is studied in every study pair (EUR/USD, GBP/USD, USD/JPY, USD/CHF intraday; USD/INR daily). | *What changed:* the project now covers multiple pairs. Under "primary", USD/JPY had 4 usable scheduled events, so the cross-pair comparison could not exist. *Benefit:* ~4× observations and a within-release cross-pair design, which H5 requires. *Cost:* the observations from one release are not independent — every estimate clusters standard errors by release, and effective sample sizes are reported as releases, not rows. |
| 2026-10-24 | AUD/USD dropped from the study set | Not part of the requested pair set; dropping it trims the intraday fetch and disk budget, which matters on a laptop. Existing AUD/USD rows in `features.parquet` are retained but excluded from analysis. |
| 2026-10-24 | USD/INR stays a study pair, at daily resolution | No free intraday USD/INR exists (§8). It is studied through US releases as exogenous shocks and through GDELT INR news, and enters H5 as a currency index. Stated as a limitation, not worked around. |
| 2026-10-24 | H5 pre-registered before any H5 data was examined | Adds the general-market-sentiment question as a falsifiable hypothesis with a fixed rule and stop condition, rather than an exploratory index tuned after the fact. |
| 2026-10-24 | Laptop-scale execution | All jobs run locally, one heavy job at a time, with bounded fetch workers and capped torch threads. Bulk caches live outside OneDrive (`%LOCALAPPDATA%\arbex`), so thousands of cache files are not synced. An earlier session crashed the machine by exhausting virtual memory. |
| 2026-10-24 | Intraday source: parallel Dukascopy prefetch, pilot first | The existing fetcher is sequential at 10–25 s per pair-hour (31–78 h). A bounded 4-worker prefetch over the deduplicated hour set reuses the existing cache. A 50-hour pilot measures throughput and 429s first; if throttled, the majors switch to HistData monthly M1 files via the existing loader. |
| 2026-10-24 | **R1 is confounded: no surprise sign adjustment existed.** Fixed via `eventstudy/sign_conventions.py`. | R1 pooled raw standardized surprises across all US releases. For unemployment rate and jobless claims a higher print is *bad* for USD, for payrolls/CPI/retail sales *good*, so pooling cancels the two and biases β toward zero. R1's conclusion ("daily resolution cannot detect reactions") may partly reflect this and must be re-run with oriented surprises before it is cited. The sign map is fixed a priori from the standard macro prior, before any result. |
| 2026-10-24 | **Positive-control gate, pre-stated before running.** | Sample: high-impact US releases with exact time and standardized surprise, EUR/USD, HistData M1 stamped at bar close, entry lag ≤ 2 min, exit lag ≤ 5 min, surprises winsorized at ±5σ, SEs clustered by release date. **PASS requires all three:** (a) Non-Farm Employment Change β at 5 min is negative with p < 0.05 (stronger US payrolls → EUR/USD falls); (b) the pooled sign-oriented β at 5 min — return oriented as the releasing currency's return — is positive with p < 0.05; (c) the placebo return from −5 min to the release is insignificant (p > 0.05). FAIL on any → fix the pipeline before reporting H1–H5 (§5). |
| 2026-10-24 | Minute bars are stamped at bar close | HistData labels a bar by its start, but its close is observed at the end. Using start labels, the entry bar for a 13:30 release would already contain up to 1 minute of reaction. The Dukascopy resampler (`resample_to_bars`) has the same start-label convention; it is unused now but must be fixed before it is used. |
| 2026-10-24 | Alignment gains an exit-lag guard | `align_event` rejected stale *entry* bars but not stale *exit* bars, so a data gap silently stretched a 60-minute return across the gap. Added `max_exit_lag_minutes`, default off so the daily R1 reproduces; the intraday arm uses 5. |
| 2026-10-24 | **Pilot result: parallel Dukascopy rejected; majors switch to HistData bulk M1** | 50 EUR/USD hours, 4 workers: 9.1 min (0.09 h/s ≈ 11 s/hour effective — no faster than sequential), 4/50 hours (8%) failed after 5 attempts. No 429s: the server shed load with HTTP 503, connection resets (WinError 10054) and timeouts (10060). Projected full fetch ≈ 63 h. Throughput is server-limited, so concurrency cannot fix it. HistData serves complete years as one file, so ~11 downloads per pair replace ~5,000 hourly requests. Dukascopy is kept for gap-filling, with 503s and resets now pausing all workers. Logged in `artifacts/prefetch_runs.jsonl`. |
| 2026-10-24 | **Positive control, run 1: FAIL on (a).** Counted as attempt 1 of 2 for M3 (§3). | 32 pair-years of HistData M1 (2018–2025, four majors). (b) and (c) passed: the pooled sign-oriented β was positive in all four pairs at every horizon (EUR/USD 5m p < 1e-3) and the placebo was insignificant (p 0.19–0.43). (a) failed: payrolls 5-min β on EUR/USD was **+0.37 bps/σ (p ≈ 0.1), the wrong sign**. Diagnosis below; the gate is **not** changed. |
| 2026-10-24 | **Calendar timestamp bug found by the positive control; fixed before re-running.** | Every 2018–2025 payrolls row sat at Thursday 19:30/20:30 UTC, not Friday 12:30/13:30. Two defects in the acquired dump, both confirmed on raw rows: **(1) missing times stored as local 00:00:00.** ~30% of all rows, ~45% of US high-impact rows, every year; whole days collapse to 00:00 (e.g. 2018-01-05: EUR CPI flash, NFP and ISM Services all at 00:00), so the time cannot be recovered from row order. In 2018–2025 this covers 88/88 payrolls, 87/87 retail sales and 86/87 CPI m/m rows. The parser treated 00:00 as a real release time; the store's "95% exact time" (§8) was wrong for the same reason. **(2) stale Iranian DST.** The dump's wall clock is Tehran time, but its offsets were generated with rules that still apply +04:30 after Iran abolished DST in 2022, so 2023+ summer rows are 1 h early in UTC (5,071 rows; e.g. Claims at 07:30 ET). **Fix, fixed from documentation and the dump's own observed rows, not from prices:** re-localise the wall clock in `Asia/Tehran` with current tzdata (this reproduces the dump's offsets before 2022); treat local 00:00:00 as time missing (fail closed); fill missing times only for US release families with one documented agency clock (BLS/Census/BEA 08:30 ET, ADP 08:15, ISM/CB/JOLTS/housing 10:00, FOMC minutes 14:00 — `data/release_schedule.py`), and only if ≥ 95% of ≥ 20 of the family's *observed* times agree with that clock. Observed times are never overwritten; filled rows are labelled `timestamp_source="schedule"`. Excluded as having no single clock: FOMC statements/rate decisions, speeches, UoM, crude inventories, Philly Fed. ABDV (2003) take release times from agency schedules in the same way. Non-US rows with missing times stay date-only until their own families are validated. **Pre-stated for run 2:** same gate, same sample definition, same parameters; in addition a non-gating robustness table re-fits the pooled β on observed times only. The store is rebuilt (`ingest_calendar --replace`; event ids hash the timestamp), the old one kept as a backup. **R1 is also affected** — at daily resolution a 20:30 UTC Thursday stamp moves the release into the previous day's bar — and must be re-run on the rebuilt store. |
| 2026-10-24 | **Positive control, run 2 (attempt 2 of 2): FAIL on (a). (b) and (c) pass.** | Rebuilt store: 17 of 19 families validated (Residential Construction 94.8% and JOLTS 94.1% fell just short and stay date-only); 3,261 times filled. All 88 payrolls rows now sit on Fridays 12:30/13:30 UTC (one Thursday, 2020-07-02, a real holiday shift). 1,482 US releases, 1,429–1,437 aligned per pair. **(b)** pooled sign-oriented β at 5 min: EUR/USD +2.14 bps/σ (p 4e-4), GBP/USD +2.07, USD/JPY +2.65, USD/CHF +2.04, all p < 0.002; 4× run 1. Holds on observed times only (EUR/USD +1.41, p 0.008; n = 622). **(c)** placebo p 0.18–0.87. Per-indicator signs are now coherent in all four pairs (CPI m/m, Core CPI, ISM significant; EUR/USD and GBP/USD negative, USD/JPY and USD/CHF positive). **(a)** payrolls 5-min EUR/USD β = −2.43 bps/σ, **right sign, p = 0.17: fails**. Per §3 no third attempt at the gate. |
| 2026-10-24 | **Post-hoc diagnosis of (a) — explanatory, not a re-test.** | Payrolls' σ is computed over 2007–2025 and is dominated by pandemic surprises: 2020-06-05 was +10.3M jobs = 14.3σ. Half of all payrolls months then sit below 0.1σ, so the slope is carried by a few 2020–21 points. Excluding 2020-03 → 2021-06 (n = 68): β = −7.2 bps per normal-month σ (−5.5 bps per 100K jobs), p = 0.005, in line with ABDV (2003). This was found *after* seeing the gate result and is reported as such. The same σ inflation affects any surprise whose history includes the pandemic, so it bears on every later analysis that uses standardized surprises. **Open decision for the project owner** before H1–H5 run: how surprises are standardized from here on. |
| 2026-10-24 | **Decided (project owner): Option A — pandemic-robust surprise standardization, pre-registered before any H1–H5 or R1 re-run.** | **Primary:** for each (currency, indicator), σ is estimated from surprises *outside* the pandemic window 2020-03-01 → 2021-06-30 and applied to every release, including those inside it; winsorize at ±5σ as before. **Robustness (reported alongside every estimate):** drop releases inside the window entirely. The window is fixed here and will not be moved. **The positive control stays recorded as FAIL on (a).** Re-running it under the new σ is reported as a post-hoc consistency check, never as a pass. Applies to R1 re-run, H2/H3, H1 controls, the H5 surprise-index control, and the live training rows. Known limitation, accepted: σ uses the whole sample, including later releases, as in ABDV (2003); the paper says so. |
| 2026-10-24 | **Option A implemented and store rebuilt.** | σ per (currency, indicator) from releases outside 2020-03-01..2021-06-30 (`data/calendar.py` PANDEMIC_WINDOW_*), applied to all; ±5σ winsorization unchanged; store column `in_pandemic_window` (4,159 standardized releases inside). `ingest_calendar` re-standardizes after release-clock imputation so window membership uses filled times. 34,333 events, 23,636 timed, 22,517 standardized. Backup of the pre-Option-A store: `calendar_events.parquet.bak-20261024-175504`. |
| 2026-10-24 | **Positive control under Option A — post-hoc consistency check, NOT a pass.** Record stays FAIL on (a). | Pooled oriented β 5m: EUR/USD +1.73 (p 1e-5), GBP/USD +1.68, USD/JPY +2.18, USD/CHF +1.72; observed-times-only +1.19..+1.67 (p<0.005); excl. window +3.32..+4.21. Payrolls 5m EUR/USD −2.55 (p 0.005), all four pairs correct sign p≤0.03; excl. window −5.16 (p 0.004). Placebo p 0.11–0.91. Script prints `gate_record` separately from `gate_consistency_check_option_a`. |
| 2026-10-24 | **R1 re-run: daily EUR/USD detects US surprises once oriented; USD/INR does not.** Supersedes R1's conclusion. | Legacy mode reproduces R1 exactly. Sign-oriented (USD's return): EUR/USD bar +1 +5.67 bps/σ (q<1e-4; excl. window +8.95), bar 0 null — Yahoo daily close precedes the release on the same date, so the reaction lands one bar later (conservative, not leakage); bar +3 −3.01 (q 0.006, partial reversal). USD/INR: no offset survives BH excl. window (bar +1 +2.19, p 0.051); bar +3 −2.82 (q 0.012) only with the window. Placebos clean throughout. |
| 2026-10-24 | **H2 supported; H3 not supported (one pass each, per §2).** | US high-impact × 4 majors, sign-oriented CAR, release-clustered, BH over category×horizon cells with ≥30 release dates. Employment, growth, inflation all survive at every horizon 1–120m (5m: +3.93/+2.20/+7.22 bps; q≤0.009); same excl. window. The rule tests significance, not distinctness: shapes are similar, inflation ≈2× employment. H3: pre-registered decay half-life undefined in 100% of bootstrap draws — reactions do not decay within 120m, so the measure cannot separate categories; reported as a negative, measure not replaced. |
| 2026-10-24 | Open item: 60→120m CAR jump. | Oriented CAR roughly doubles from 60m to 120m in all categories and pairs (pooled +4.7 → +10.1 bps), also for releases with no other US high-impact release within 120m (+4.16 → +10.04). Candidate: US equity open drift. Check before citing 120m numbers (e.g. 90m horizon, split by release clock). |
| 2026-10-24 | **Resolved: the 60→120m jump was a price-clock bug. HistData M1 is New York wall clock *with* DST, not fixed EST.** All intraday results above (positive-control runs 1–2 and the Option A check, H2, H3) were computed on mistimed bars and are **void**; they are kept in the log for the record. | Diagnosis (post-hoc, before any re-run): at 5-minute steps the jump sat exactly 60→65 min after release for 08:15, 08:30 *and* 10:00 ET releases alike (t ≈ 6), so it followed time-since-release, not clock time — not the equity open. Split by US DST season with the fixed −5 h parse: summer releases +0.5 bps at 1m, +9.0 at 61m; winter +10.2 at 1m. HistData describes its files as "EST without DST", and the parser trusted that after a single February check. Fix: `parse_histdata_m1_line` converts via America/New_York (`data/prices_intraday.py`). After the fix: summer +8.5 bps at 1m and flat thereafter, no jump. In summer the old parse took entry an hour *before* the release, so it diluted reactions rather than leaking future prices. Daily R1 (Yahoo) is unaffected. **Pre-stated for the re-runs:** same scripts, rules and Option A store; the only change is the price clock. The positive-control record stays FAIL on (a) (runs 1–2, final); re-running the original gate specification on correctly timed data is reported as post-hoc. H2/H3 are re-run once as their valid pass, since the previous pass measured mistimed data. |
| 2026-10-24 | **Positive control on correctly timed data (post-hoc; the record stays FAIL on (a)).** The earlier "pandemic σ" diagnosis is **superseded**: it was itself measured on mistimed bars. | **Original pre-registered specification** (full-sample σ, store backup `.bak-20261024-175504`): (a) payrolls 5m EUR/USD β = −8.99 bps/σ, p = 0.010 ✓; (b) pooled oriented 5m +6.51, p = 1e-11 ✓; (c) placebo p = 0.33 ✓ — **all three hold**. Both recorded failures were therefore caused by the two clock defects (calendar placeholders; HistData DST). **Option A store** (`artifacts/positive_control.json`): pooled oriented β at 1m +4.20..+5.34 bps/σ in all four pairs (p ≤ 7e-14), flat to 120m; observed-times-only +4.2..+5.3; excl. window +7.9..+9.4. Payrolls 5m: EUR/USD −5.67 (p 8e-5), GBP/USD −5.03, USD/JPY +7.22, USD/CHF +6.26; excl. window −9.32 (p 1e-5). Unemployment rate and claims now carry the economically correct signs. Placebo: EUR/USD p 0.42, USD/JPY 0.14, USD/CHF 0.67; **GBP/USD p = 0.012** (β −0.25 bps, opposite sign to the reaction, so not anticipation) — reported, not explained away. Option A stays the method going forward: it was fixed before H1–H5 and changing it again would be a forking path. |
| 2026-10-24 | **H2 SUPPORTED, H3 NOT supported — valid pass on correctly timed data.** Supersedes the void pass above. | Employment, growth, inflation survive at every horizon 1–120m (all q < 0.001), same excluding the window. Oriented CAR at 1m: inflation +17.3 bps, employment +7.6, growth +5.4 — inflation ≈ 2.3× employment, ≈ 3.2× growth. H3: decay half-life undefined in 98–100% of bootstrap draws for all three; time to half of peak is 1 min for all (CI [1, 1]). **Substantive finding:** reactions are complete within one minute and do not reverse within two hours, in every category — immediate, permanent incorporation, consistent with ABDV (2003). Categories differ in *size*, not *speed*. |
| 2026-10-24 | GDELT backfill: adaptive pacing, defaults set by measurement | Pilot 1 (no wait, 2 workers): 70 throttles for 10 windows, 0.0074 win/s. Pilot 2 (5 s floor, 1 worker): 18/20 windows, 40 throttles, 0.0061 win/s, pacer settled at 54 s. GDELT, not the client, sets the throughput; the old 4 s loop managed a median 57 s per window. Shards and sentinels now atomic; GDELT's rate-limit notice now counts as throttling. Logged in `artifacts/gdelt_runs.jsonl`. |
| 2026-10-24 | **Data-loss bug: rate-limit notices and malformed JSON were recorded as empty days** | The old fetch returned [] for non-JSON bodies and for JSON with control characters or bad escapes, so those windows were marked done with 0 events. Failed windows also left ~47 gaps in 2018. Old 0-count sentinels are re-checked once (5/5 so far had events); JSON is parsed leniently. |
| 2026-10-24 | Feature-table discrepancy explained | 2,009 rows = the current pipeline on the 5,884-event store (2018-01-01 → 2018-12-26). 4,473 is unreproducible (no manifest); most likely pre-tagging-fix (pre-fix tags give 5,791 rows on today's shards, USD/CHF 723 from "franc" inside "France"). `python -m research.build_features` now rebuilds with a manifest: 6,782 events → 2,402 rows (2,091 study pairs). Backups `events.parquet.bak-20261024`, `features.parquet.bak-20261024`. |
| 2026-10-24 | **Backfill scope: 2018–2021 first** (the plan's fallback) | Remaining 2,554 windows ≈ 40–116 h for the full range; 2018–2021 is ≈1,093 windows, 17–50 h. Extend to 2022+ only if time allows. The H1/H5 sample is whatever 2018–2021 data is complete when the run finishes; it is not cut on results. |
| 2026-10-24 | H1/H5 code complete, **not run on real data** | `semantic/h1.py`, `h5.py`, `currency_index.py`, `walkforward.py`, `run_h1.py`, `run_h5.py`, `score_archive.py`. The §2 rules leave specification gaps (H1 unit/target/horizon, baseline, embedder count vs R2's closed axis, multiple testing, decision metric; several H5 details). They must be settled in this log before either script runs. |
| 2026-10-25 | **LOCKED PROTOCOL for H1 and H5 (project owner approved 2026-10-25, before any H1/H5 result on real data).** No change after results are inspected; any departure is logged below as a numbered DEVIATION with reason. | **H1 — unit:** deduplicated GDELT event × study pair it is tagged to. **Primary target (majors: EUR/USD, GBP/USD, USD/JPY, USD/CHF):** absolute log return from the first HistData M1 bar at/after (GDELT timestamp − 15 min) to the first bar at/after (GDELT timestamp + 60 min); entry lag ≤ 2 min, exit lag ≤ 5 min, fail closed. The window starts before the timestamp because GDELT logs articles after publication; the test is explanatory, and features use text and prior news only. **USD/INR (secondary, no intraday data):** absolute daily return, R2 design. **Exclusions, fixed:** (1) any event whose window [t−45 min, t+90 min] contains a high-impact, exact-time calendar release of either currency in the pair; (2) market-wrap headlines — case-insensitive match of an FX term (`dollar|greenback|euro|yen|sterling|pound|franc|rupee|forex|fx|currenc(y|ies)` or a pair code such as `EUR/USD`, `USDJPY`) together with a move term (`rise[sn]?|rose|rising|gain(s|ed)?|climb(s|ed)?|jump(s|ed)?|surge[sd]?|soar(s|ed)?|rall(y|ies|ied)|fall(s|en)?|fell|drop(s|ped)?|slip(s|ped)?|slide[sd]?|slid|tumble[sd]?|plunge[sd]?|sink(s)?|sank|weaken(s|ed)?|strengthen(s|ed)?|firm(s|ed)?|ease[sd]?|steady|steadies|edge[sd]? (higher|lower|up|down)|(hit|hits|touch(es)?) .{0,20}(high|low)|record (high|low)|(week|month|year)-?(high|low)`). **Controls:** mean absolute return over the same 75-minute clock window on the previous 20 trading days for that pair, plus pair fixed effects. **Baseline:** FinBERT absolute polarity (lexicon reported as robustness only). **Embedder:** sentence-transformer is the sole primary embedder (R2 closed the axis); TF-IDF and hashing reported, no decision weight. **Pass requires both:** (i) novelty coefficient > 0 with p < 0.05 in the nested model (controls + FinBERT baseline + novelty), standard errors clustered by UTC date; (ii) walk-forward out-of-sample improvement — expanding window refit by calendar month, out-of-sample MSE of the novelty model below the baseline model's over the pooled test months. Otherwise H1 is not supported; < 30 events after exclusions = underpowered (kill rule). **H5:** same-day framing — returns over the 24 h ending 21:00 UTC, reported as *explaining* moves, not forecasting; market component (walk-forward PC1) sign fixed so non-safe-haven currencies load positively (not anchored on VIX, which would make (b) circular); (b) passes only if VIX change, USD/JPY and USD/CHF all show the predicted sign with p < 0.05, controlling for the surprise index; "supported" = (a) and (b) both hold for at least one sentiment backend, per-backend verdicts reported; both models in (a) carry the same surprise-index control; no-news day = 0; surprise index uses high-impact exact-time releases (Option A σ); headline naming two currencies gives both the same polarity (accepted limitation); pandemic-window robustness via `in_pandemic_window`. **Sample cutoff:** H1/H5 use only GDELT windows for 2018-01-01..2021-12-31 whose shard completed before **2026-10-31 00:00 IST (2026-10-30 18:30 UTC)**; the shard list is frozen at the cutoff with a content hash recorded here. Coverage at the cutoff (windows done / 1,461, events) is reported as is: the cutoff is not moved and gaps are not selectively filled. Data downloaded after the cutoff is not used for H1/H5. Scoring (lexicon, FinBERT, embeddings) runs after the cutoff on the frozen list only. **Feasibility at lock time (2026-10-25 07:52 IST):** 417/1,461 windows done; ~1,044 left at ~0.005 windows/s with ~25% of requests throttled ⇒ ~58–70 h of uptime needed vs ~136 h to cutoff. |
| 2026-10-25 | **H1 protocol CLARIFICATIONS C1–C5 (implementation details the locked text left open; decided before any H1 result exists — no H1 code has run on real data, and none can before the 31 Oct cutoff).** These are not deviations: each fills a gap without changing a locked clause. | **C1** market-wrap regex matches whole words only (`\b…\b`): the literal substring reading turns "release", "surprise", "against", "Europe", "France" into wraps and would drop ordinary macro headlines, defeating the clause's stated purpose. **C2** a USD-only GDELT headline is attributed to all four majors (`usd_policy="study"`), following the 2026-10-24 attribution decision for US releases; the four rows share a date cluster, so SEs are not overstated. **C3** "previous 20 trading days" = the 20 previous weekdays, averaging those whose window is computable. **C4** a control value needs ≥ 10 of those 20 days; otherwise the row is dropped (fail closed). **C5** the first 12 calendar months are training-only before the first walk-forward test month. **Accepted interpretations** (reported by the implementer, confirmed here): exact-time releases include validated release-clock fills; a pair code is two ISO codes from the project list with or without a slash; the dedup unit is `score_archive`'s archive-wide hashing dedup at similarity 0.85; novelty context includes excluded stories (exclusions apply to analysis rows only); the control window uses the UTC clock; USD/INR exclusions apply per event before daily aggregation and its kill rule counts news-days. **Implementation status:** `semantic/h1.py`, `run_h1.py`, `freeze_gdelt_sample.py` (refuses before the cutoff, never overwrites, hash-verified), `score_archive` stamps the freeze hash; research suite 190 passing. Measured scoring cost for the full 2018–2021 sample: < 15 min (FinBERT 40 headlines/s, sentence-transformer 359/s), so scoring is not a bottleneck. Freeze preview at 2026-10-25: 417/1,461 windows (28.5%), 12,860 events. |
| 2026-10-25 | **H6 PRE-REGISTERED (project owner approved 2026-10-25, before any H6 data was examined): a news-derived currency bias forecasts pair returns beyond a sentiment-only baseline.** Added alongside H5, which is unchanged and stays same-day explanatory. Same freeze, same cutoff, same Option A surprises. | **Signal (fixed a priori, no tuning):** for each currency c in {USD, EUR, GBP, JPY, CHF, INR}, at time t, B_c,t = Σ w_i · s_i / Σ w_i over GDELT headlines attributed to c (`data/tagging.py`) with GDELT timestamp ≤ t − 15 min, where s_i = FinBERT polarity and w_i = exp(−age/12 h) × FinBERT confidence × novelty_i (sentence-transformer) × relevance_i (1 if c is named explicitly in the headline, 0.5 if attributed only through a country/institution term); plus an event component E_c,t = the Option A surprise index for c (sign-oriented, exact-time high-impact releases ≤ t, same 12 h decay). Currency bias = standardized B + standardized E (each z-scored on its own training-window history, equal weights). Pair bias = bias_base − bias_quote. **Baseline (sentiment-only):** confidence-weighted mean FinBERT polarity of headlines tagged directly to the pair over the same 24 h window, no novelty, relevance, currency factoring or event component. **Primary test (H6a):** daily, four majors plus USD/INR; signal at 21:00 UTC, target = the pair's log return over the next 24 h (majors from HistData M1, USD/INR from the R1 daily source, dated as in the R1 re-run). Nested model: next-day return on baseline + pair bias, pair fixed effects, SEs clustered by UTC date. **Pass requires both:** (i) pair-bias coefficient > 0 with p < 0.05; (ii) expanding-window monthly walk-forward (12 training-only months, as C5) with pooled out-of-sample MSE of baseline + pair bias below baseline alone. **Reported, no decision weight:** out-of-sample hit rate and rank IC; ablations (news only, event only); intraday secondary horizons 60 and 240 min on the majors, sampled hourly, BH-corrected; USD/INR alone. **Horizon output:** the live signal's stated horizon is the longest tested horizon whose out-of-sample IC is positive with q ≤ 0.05; if none, the live signal ships with horizon "none established". **H6b (prospective, exploratory — small sample):** live detector opportunities recorded from 2026-10-25 (quotes + `opportunities.jsonl`, plus timestamped per-pair semantic predictions). The detector finds cross-venue crossed books, which are close to market-neutral; directional exposure exists only through legging risk. Outcome = whether the cross is still present at the next poll of both legs, plus the realised edge at that poll. Compare ranking quality (AUC for "still crossed") of detector-only vs + risk channel vs + direction channel, bootstrap CI by day. Reported descriptively; not a decision test. The recorder data is not examined before the H6b analysis script exists and is logged here. |
| 2026-10-25 | **H6 CLARIFICATIONS C6–C19 (implementation details the locked H6 text left open; decided before any H6 data was examined — the GDELT sample is not frozen yet and no recorder file has been read).** Not deviations. Code: `semantic/currency_bias.py` `UNRESOLVED` / `H6B_UNRESOLVED`; `run_h6.py`, `run_h6b.py` (written, not run). | **C6** E_c,t window = 24 h, matching B's "same 24 h window". **C7** a component with no data in its window has standardized value 0 (neutral), matching H5's "no-news day = 0". **C8** headlines with undefined novelty (< 3 prior same-currency stories) are dropped (fail closed). **C9** a currency attributed by tagging but with no attribution term found in the text gets relevance 0.5 (indirect). **C10** z-scores use training-window statistics inside each walk-forward fold (`training_months`), the literal reading of "z-scored on its own training-window history"; the live service necessarily uses a causal expanding z. **C11** live/expanding z needs ≥ 30 earlier values. **C12** the baseline attributes USD-only headlines to all four majors (`study`), as H1 C2. **C13** intraday entry/exit lags 2/5 min, as everywhere. **C14** USD/INR target = log(c[d+2]/c[d+1]) (`skip_one_bar`): leak-safe because the Yahoo close of date d is observed before 21:00 UTC (R1 re-run). **C15** out-of-sample IC is computed on the full model's walk-forward predictions. **C16** IC p-value from a rank regression with SEs clustered by UTC date, consistent with every other test. **C17** a signal time whose news window touches a GDELT window missing from the freeze is dropped (a data gap is not "no news"). **C18** H6b nests cumulatively: detector-only, + risk, + risk + direction. **C19** H6b drops opportunities whose next poll has a one-sided quote on either leg (unknown outcome). **Accepted readings:** B's window is 24 h; "named explicitly" = currency name or ISO code, institution/region terms (ECB, Fed, eurozone, US Treasury, …) = 0.5; H6b "detector-only" = the opportunity's `confidence_score`. **Consequence noted:** with a 24 h target ending at a weekend close, Friday signals fail closed, so the daily sample is Monday–Thursday signal days. **Live gaps (reported in every payload, not filled):** no consensus forecasts live, so the live bias is the news component only; no live economic calendar yet, so release proximity is null; novelty uses the hashing embedder unless the sentence-transformer is enabled; horizon reads "none established" until `run_h6` has run. |
| 2026-10-25 | **H6b sample window fixed (before any recorder file has been read).** | H6b uses recorder sessions from the first poll after the 2026-10-25 restart (snapshot code live) up to **2026-11-05 00:00 IST (2026-11-04 18:30 UTC)**; data after that is not used. Coverage (hours recorded per venue, network gaps such as the 2026-10-25 17:59 IST Wi-Fi drop) is reported as is. USD/INR legs are absent unless an Upstox token is supplied before the window ends; that is reported, not worked around. |
| 2026-10-25 | **Live economic calendar for the risk channel (engineering, not a research input).** | Source: ForexFactory weekly JSON. Verified on a real response: ISO New York wall-clock times with offset (−04:00 in the EDT week; 0 mismatches after re-localising in America/New_York), US high-impact rows 100% on agency clocks, Holiday rows carry arbitrary clock times (treated as time-unknown, as are New-York-midnight stamps), **no actuals** — so the live event-surprise component stays absent. Fetched ≤ 1/h, cached atomically, stale cache served on failure. `release_proximity` is non-null only 15 min before → 10 min after a high-impact, time-known release of either pair currency (live engineering default; H6b evaluates it). Behind SEMANTIC_CURRENCY_BIAS (service) / ARBEX_CURRENCY_BIAS (backend); flag-off parity tested. Current week only. |
| 2026-10-25 | **H6b window enforced in code.** `run_h6b.py` refuses to run before 2026-11-04 18:30 UTC and drops opportunities at/after it (`after_window_end`); start bound = rows carrying a `semantic_snapshot`. | The locked window previously existed only in the tracker; without the end bound a run after 4 Nov would have used post-window data. |
| 2026-10-25 | **Reproducibility.** `python -m research.reproduce --list / --run <step> / --run all` runs every paper result in order behind its protocol gate (cutoff, freeze, freeze-hash-stamped scores, H6b window end); `research/REPRODUCE.md` maps each table to its command and artifact. | One command per result; the gates duplicate the scripts' own refusals so order cannot be violated. |
| 2026-10-25 | **Full-stack end-to-end test passed; two live-channel fixes made before the H6b window opened (before market open, no recorder file read).** | Stack: semantic service, live backend + recorder, Redis (dedicated `arbex-redis` container, localhost only, no persistence), Redis opportunity scorer, dashboard. A synthetic opportunity (`e2e-synthetic-1`, sent only to `arbex.raw_opps`, never to the recorder log) was scored end to end with news bias from 11 live articles; flag-on run showed both channels in the ranking reason; fallback held while the service restarted. **Fix 1 — risk channel saturated:** the absolute rule (2 bps free, cap at ~17 bps) hit its cap on ordinary news (47 bps observed), so it could not rank. Now relative: penalty = 0.06 × clip((expected / pair's 24 h median expected move − 1) / 2, 0, 1); no magnitude penalty while the baseline has < 20 items; release term unchanged. **Fix 2 — live settings aligned with H6 C7/C8** (`no_data_value=z_zero`, `novelty_missing=drop_headline`), so the live signal is the one the paper tests; only the z-score stays causal. H6b therefore evaluates the channels as defined from this restart on. Dashboard fixes: evaluation crash on the weights-only model (metrics now from `training_report.json`, live-served views only), benchmark no longer read from the out-of-repo `news_stream` folder. |
| 2026-10-28 | **DEVIATION 1 — GDELT sample cutoff moved from 2026-10-31 00:00 IST to 2026-11-02 00:00 IST (2026-11-01 18:30 UTC), by the project owner.** This is the only change to the locked H1/H5/H6 protocol; the cutoff will not move again. | *Reason:* the laptop was off 2026-10-25 23:20 → 2026-10-28 22:55 IST, so ~3 days of download time were lost to an outage, not to anything in the data. *Why it is not results-driven:* no H1, H5 or H6 analysis has run on real data, the GDELT sample has not been frozen or scored, and no coverage-dependent statistic has been computed — the only information used is the download count (514/1,461 windows). Everything else in the locked protocol is unchanged, including the H6b window end (2026-11-05 00:00 IST). Code: `freeze_gdelt_sample.CUTOFF_UTC`, `reproduce.GDELT_CUTOFF_UTC`. |
| 2026-10-25 | **Code home.** Research code is copied into the `semantic-microservice` branch (`research/`) at milestones; until results are final, the original checkout's `research/` stays the working copy (the live backfill and artifacts live there). Data artifacts are never committed; small result JSONs, logs and manifests are. | Keeps the paper reproducible from the repo while the long-running jobs keep their paths. |
| 2026-11-01 | **DEVIATION 2 — GDELT sample cutoff moved from 2026-11-02 00:00 IST to 2026-11-04 00:00 IST (2026-11-03 18:30 UTC), by the project owner.** This overrides DEVIATION 1's statement that the cutoff would not move again; that statement is now false and is left in place, not edited. | *Reason:* further outages after DEVIATION 1 (laptop off 29 Oct 06:51 → 31 Oct 13:57, ~1.5 h on 31 Oct, Wi-Fi off 1 Nov 04:36 → 06:50, laptop off 1 Nov 12:02 → 19:48 IST) left coverage at ~890/1,461 windows (to ~mid-2020) under the 2 Nov cutoff. *Why it is not results-driven:* decided 2026-11-01 ~19:55 IST, before the freeze: no H1, H5 or H6 analysis has run, nothing has been scored, no shard content has been read. *What it changes:* only `CUTOFF_UTC` (`freeze_gdelt_sample.py`), `GDELT_CUTOFF_UTC` (`reproduce.py`), and the backfill loop's stop time. Every other locked clause, including the H6b window end (2026-11-05 00:00 IST), is unchanged. Coverage at the new cutoff is reported as is. *Reviewer note:* two cutoff moves are disclosed in the paper's methods section with both reasons; results under the 2 Nov cutoff (shards with `completed_at` < 2026-11-01 18:30 UTC) are reported as a robustness check, so a reader can confirm the extension did not change the conclusions. |
| 2026-11-02 | **DEVIATION 3 — GDELT news sample re-collected as a time-stratified slice sample; H1, H5, H6 become the unscheduled-news arm of RQ1–RQ5 (project owner set the article→reaction goal 2026-11-01).** Found by a timestamp-only coverage check (no headline text, price or score read): every 24 h DOC request (`sort=datedesc`, `maxrecords=250`, broad query) returns only the ~12 minutes before 00:00 UTC — in a 169-day sample 88% of kept headlines fall 23:00–01:00 UTC, median per-day span 0.2 h. The daily sample is therefore not a sample of the trading day; it is kept only as the defect exhibit (paper §data errors) and is never used for an H1/H5/H6 result. | **New sample:** 1 h windows whose end falls on a fixed UTC hour set, weekdays only (`gdelt_backfill --window-hours 1 --slice-hours … --weekdays-only`, shard dir `artifacts/gdelt_slices`). Each slice = the last 250 matching articles before the hour, so the sample is spread over the trading day and selected by clock time, not content. Slice hours, period and query are fixed from pilot throughput and yield only (count and time span per slice, never headline content), recorded below before the full run. **Cutoff:** downloads stop and the freeze runs at 2026-11-10 23:59 IST; supersedes DEVIATION 2's cutoff and its promised 2 Nov robustness check (that check was for the daily sample, now withdrawn). **H1/H5/H6:** every other locked clause unchanged; the H1 sample period becomes the slice period; C17 counts a scheduled slice as the missing unit. H6b (window end 2026-11-05 00:00 IST) unchanged. |
| 2026-11-02 | **DEVIATION 3 slice design fixed (from pilot throughput and per-slice counts only; no headline text read).** | Pilot 1 (default query, 7 slices from 2024-01-01): ~21 slices/h under GDELT throttling, 4.7 currency-tagged headlines per slice. **Period** 2023-07-01..2025-04-30 (ends with the calendar store, inside HistData; ≈ 3,700 slices ≈ 7–8 days at the pilot rate, so it fits before the cutoff with margin; ≥ 12 training months for C5). **Slices:** weekdays, 1 h windows ending 07, 09, 11, 13, 14, 15, 17, 20 UTC (13–15 UTC cover the minutes after 12:30/13:30/14:00 UTC US releases). **Query rule, fixed before pilot 2 finished:** `data/gdelt.CURRENCY_QUERY` (built a priori from the study-currency terms of `data/tagging.py`, so the 250-article cap is spent on taggable headlines) if the pilot on the same 7 windows completes ≥ 5 slices, else the default query; the choice is written to `artifacts/gdelt_slices_query_choice.txt`. Shard dir `artifacts/gdelt_slices_main`; loop `research/run_slices_loop.ps1` stops at 2026-11-10 23:59 IST. Pilot dirs (`gdelt_slices`, `gdelt_slices_q2pilot`) are not part of the sample. |
| 2026-11-02 | **Slice query rule amended before any slice of the main sample exists.** | The 18-term currency query was refused by the API ("Your query was too short or too long."); the downloader misread that plain-text reply as throttling and retried (fixed: `GdeltQueryRejected` is now permanent, test added). Pilot 2 therefore produced 0 slices and was stopped. `CURRENCY_QUERY` is now 8 terms (dollar, euro, yen, sterling, forex, "exchange rate", ECB, "Federal Reserve"). **Rule:** `run_slices_loop.ps1` probes it once (`probe_gdelt_query`, waiting out 429s up to 90 min): JSON reply → currency query, refusal → default query; the choice is written to `artifacts/gdelt_slices_query_choice.txt` and reused on every restart so the sample never mixes queries. No headline content informs the choice. |
| 2026-11-02 | **RQ clarifications C20–C25 (implementation details the RQ text left open; decided before any RQ result exists).** Not deviations. | **C20** β and move cells are keyed by (release currency, category, pair), fallback (currency, pair): a USD and a EUR inflation surprise move EUR/USD in opposite directions, so a category × pair cell pooling currencies cancels by construction; β is through the origin (only sign(β·z) is used). **C21** the confidence logistic is fitted on the training rows' in-sample correctness under the same fold's β̂. **C22** conformal: the move model is refitted on training minus its last 12 months, the absolute residuals on those 12 months give the finite-sample quantile, and the interval is centred on that split model; intervals exist only for test months with > 12 training months. **C23** reference price = first bar at/after the release (bars stamped at close, so it is the close of the minute ending at the release), entry lag ≤ 2 min — identical to H2 and the positive control. **C24** baseline "v alone" = OLS of \|r\| on 1 + v per pair; RQ2–RQ4 use own pairs (pair contains the release currency); RQ1 uses all four pairs; RQ1's AUC score is the raw expected move. **C25** JPY (3 releases) and CHF (30) mostly fall back or abstain; abstentions are counted and reported, not imputed. |
| 2026-11-02 | **RQ1–RQ4 RESULTS (historical walk-forward, one pass, `run_reaction_eval`, `artifacts/reaction_eval.json`).** 1,385 releases with z ≠ 0 × 4 majors; 76 test months (2019-01..2025-04); release-moment clusters ≈ 900. Reported as is, including negatives. | **RQ2 direction, release-time 15 m (primary):** model 63.5% [60.2, 66.6] (AUC 0.668) vs theory rule sign(z)·news_sign 68.4% [65.4, 71.3] vs majority 51.5%. **The learned β model is worse than the a-priori theory rule** (they agree on 82% of rows; the disagreements are noisy cells). Accuracy rises with |z| (58% → 65% → 68% by tercile) and decays with horizon (1 m 68.2%, 120 m 57.7%; theory 75.2% → 61.0%), all BH q < 0.001. **Arrival-time (article 15 min after release), primary 15→60 m: 48.8% [46.6, 51.0], p = 0.27 — no direction left to predict;** 15→30 m 47.9% (q = 0.095), 15→120 m 50.2%. Mean move in the predicted direction is 7–8 bps from 1 m to 120 m and ≈ 0 after arrival (−0.14 bps, p = 0.68): H3 confirmed out of sample. **RQ3 magnitude, 15 m:** model Spearman 0.287 vs pair mean 0.189, cell mean 0.242, v-fit 0.285; MAE model 12.01 vs 11.54 / 11.59 / 11.85 bps — **no MAE gain over any baseline** (model − pair-mean +0.47 bps, p = 0.15). Arrival-time: the same-clock volatility baseline beats the model (MAE 7.11 vs 7.40, p < 0.001). **RQ4 confidence, 15 m:** ECE 0.041 (calibrated) but Brier 0.234 vs 0.232 for a constant rate — calibrated, no resolution at 15 m; at 1 m Brier 0.207 vs 0.217. Conformal coverage 76.5% at nominal 80%, 86.9% at 90% (within the ±5 pp band, slightly under). **RQ1 affected pairs:** the currency rule has recall 0.90, precision 0.41, F1 0.56; affected rate 41% own vs 19% cross (a EUR release moves GBP/USD and USD/JPY abnormally ~22% of the time); AUC of the expected move for 'affected' 0.558. *Product consequence (engineering, not a paper claim):* the live release scorer uses the theory-rule direction with a calibration map for its correctness, and the same-clock baseline for post-arrival volatility; the paper reports the learned model as pre-registered and the comparison as found. |
| 2026-11-02 | **ARRIVAL-LATENCY STUDY PRE-REGISTERED (before any latency value is computed; fields deployed 2026-11-02 03:19 IST).** RQ2's arrival-time result assumed an article arrives 15 min after its release; this measures the real delay and re-tests at it. | **Sample:** `live_predictions.jsonl` rows with basis = release and signal_scope = article, logged 2026-11-02 03:19 IST → 2026-11-13 23:59 IST; one row per (release_event, release_utc, feed_url), earliest receipt kept. **Measures:** receipt latency = our receipt time (`published_at`) − `release_utc`; publisher latency = `publisher_time` − `release_utc`; per source and overall: median, IQR, share within 1/2/5/15 min; and per release the FIRST receipt across all sources (the system's effective latency). Live match coverage = share of high-impact study-currency calendar rows in the window (weekly archive, `calendar/archive/`) with ≥ 1 matched headline within 90 min. **Observed offset a\*** = median over releases of the first receipt, rounded UP to {1, 2, 3, 5, 10, 15} min; if < 10 distinct releases are matched, a\* is not estimated and the 15-min assumption stands (reported). **Re-test (RQ2-arrival-observed, secondary):** `run_reaction_eval` arrival targets with offset a\*: r(a\*, a\*+15) and r(a\*, a\*+45) (primary), same model, folds and baselines; one run on 2026-11-14. **Feeds changed at the same deployment** (from the 2026-11-02 live inventory, `research/paper/news_sources.md`): dead feeds removed, ForexLive → investinglive.com, central-bank and BLS/BEA/Census feeds added, TLS via certifi (ECB/SNB/Eurostat had failed verification), Finnhub enabled in the service (key was missing). |
| 2026-11-02 | **FINDING — synthetic demo headlines in the live pipeline; fixed 2026-11-02 03:23 IST.** The service's SSE feed filled every quiet 5 s with made-up USD/INR headlines (provenance `synthetic`), and the backend analysed them like real news. Of 14,486 rows in `live_predictions.jsonl` (2026-10-25 → 2026-11-02), **13,311 (92%) are synthetic**; real: 1,095 RSS + 80 Finnhub rows (455 article-level). They also fed the live window bias and, through the analysis observer, the service's currency index, i.e. the inputs of every H6b `semantic_snapshot` logged before the fix. No outcome or recorder data has been examined. | **Fix:** synthetic items are opt-in (`SEMANTIC_SYNTHETIC_FEED=1`, demo only; off, the stream sends keep-alives); the backend drops any `synthetic` item before analysis; real items are no longer paced 4–8 s apart (a release headline had queued behind the refill backlog). Tests added. **Clarifications decided before any outcome is joined:** **C27** the prospective test excludes rows with provider/provenance `synthetic` (they are not articles). **C28** H6b is reported for its locked window as pre-registered, with the contamination disclosed, AND for the clean sub-window 2026-11-02 03:23 IST → 2026-11-05 00:00 IST; neither is given more weight after results. **C29** the arrival-latency study counts receipt as the item's fetch time (`published_at`) and also reports the backend's processing time (`logged_at` − release) because the two differed while pacing was on. |
| 2026-11-02 | **FINDING — synthetic headlines also in the V1 training data; recorder coverage audited (counts and per-venue quality only; no cross-venue comparison, no H6b outcome).** | **Training data:** 190 of the 743 USD/INR rows in `services/semantic/data/usdinr_reaction_training_rows_combined.jsonl` (26%) are the feed's synthetic demo headlines labelled with real USD/INR moves; 72 of 345 live-archive event seeds (21%). The 190-row targeted file (`usdinr_reaction_training_rows_targeted_expanded.jsonl`) has none. Every V1 USD/INR metric from the pooled model (pooled_multipair) is therefore void; the majors part of V1 was trained on template headlines (artificial, not poisoned). No research result (positive control, H2/H3, R1, RQ1–RQ4) uses these files. **Recorder (UTC):** two-venue data (Kraken + TrueFX) exists only for 2026-10-28 17:25 → 10-29 01:21 (~8 h) and from 2026-11-01 22:01 on; 10-25 and 10-31/11-01 are Kraken-only weekends. 7,212 cross_source opportunities logged on 10-28/29 (EUR/USD 3,966, GBP/USD 2,104, USD/CHF 1,142), all with contaminated snapshots (C28). Per-venue: Kraken has no exchange timestamp (receipt time only); Kraken USD/JPY median spread ≈ 200–237 bps (no usable book; no USD/JPY opportunities were logged); TrueFX is an indicative aggregated feed, not an executable venue, so detected crosses are price discrepancies, not executable arbitrage — RQ5/H6b are framed accordingly. |
| 2026-11-02 | **Owner decisions after the contamination audit; C32 (before any V1-clean or E2E result).** | (1) **V1-clean baseline:** the pooled V1 text model is retrained with the same script, settings and split (`train_pooled_reaction_model.py`, defaults) on the USD/INR rows minus the 190 synthetic ones (`usdinr_reaction_training_rows_combined_clean.jsonl`, 553 rows; rule: normalized headline equals, or shares its first 50 characters with, one of the feed's 72 synthetic templates) into `artifacts/pooled_multipair_clean/`. **C32:** RQ2-E2E reports both V1 (deployed) and V1-clean; V1-clean is never deployed, and the prospective test scores the deployed V1 as pre-registered. No V1-clean setting is changed after its holdout is seen. (2) **Synthetic records filtered at load, nothing deleted:** backend runtime histories skip records whose provenance/provider is synthetic; the service skips 13,311 synthetic analyses listed in `artifacts/history/excluded_event_ids.txt`. The currency-bias tracker keeps state in memory only, so it restarted clean at 03:23 IST. (3) **Recorder kept running to 2026-11-18** for the RQ5 extension; the H6b window end (2026-11-05 00:00 IST) is unchanged. |
| 2026-11-02 | **Numbering note.** | Clarification number C26 was never assigned (the RQ2-E2E details planned under it were logged as C30). No entry is missing; the paper's appendix states the gap. |
| 2026-11-02 | **C30 — RQ2-E2E details (decided before any E2E result; `run_rq2_e2e.py`, tests `test_rq2_e2e.py`).** Not a deviation. | Candidate = frozen slice headline whose `data/tagging` currency and category match a high-impact exact-time store release released ≤ 60 min before it. Extractor = the live `release_extractor`, run on the headline against the store rows of that window, forecast strings rebuilt with the indicator's unit (% for m/m, y/y, q/q, rates, CPI/PPI/PCE; K-count for *Change*, *Claims*, openings, permits, starts; plain otherwise), σ from the store. Correct actual = |extracted − calendar actual| ≤ 0.05 (%, plain) or 500 (counts). Direction targets: **r(t, t + 60 min)** from the article's GDELT time t on each own pair (primary), and r(release, +15 min) for the structured pipeline; a model that gives no direction abstains (counted). Also reported on the rows where all three models give a direction. **Known limitation, stated now:** GDELT ingestion keeps only headlines the tagger attributes to a currency, so plain release headlines ("US CPI rises 0.5%") without a currency or central-bank word never enter the sample; E2E covers only release headlines that also name the currency. |
| 2026-11-02 | **DEVIATION 3 implemented in code; C31 (decided before any H1/H5/H6 result).** | `freeze_gdelt_sample.py` now freezes the slice sample: shard dir `artifacts/gdelt_slices_main`, schedule taken from `gdelt_backfill.scheduled_window` (3,824 scheduled slices, 2023-07-01..2025-04-30), cutoff 2026-11-10 18:29 UTC; `missing_windows` = dates with any scheduled slice missing, `missing_slices` listed; `reproduce.py` gate moved to the same cutoff. H1 and H6 need no further change (H1 rows are events; H6 reads SAMPLE_START/END and C17 from the freeze). **C31** H5 drops weekdays listed in `missing_windows` (a data gap, not a no-news day — the unit C17 drops for H6); days with every slice present keep the locked "no news = 0"; the count dropped is reported. Also: `run_reaction_eval --arrival-offset N` (arrival-latency re-test; offset 15 reproduces the primary arrival tables exactly apart from the BH q-value key only the full run adds). Research suite 239 passing. |

---

## 7. Deferred ideas (parking lot — not tasks)

Write ideas here instead of building them. Revisit only after M6 is done.

- Event embedding → full response curve (functional PCA / neural operator).
- Cross-asset spillover (oil/DXY/yields → INR).
- LLM structured event extraction versus embeddings.
- Attention/coverage intensity as a magnitude predictor (partly free from dedup).
- Conformal prediction intervals on reaction magnitude.

---

## 8. Status board — update every session

- **Tests** (2026-10-24): 350 passing — research 125, news_stream 134, Arbex 91
- **Uptime gaps (reported as is, per the locked coverage rule)**: laptop off 2026-10-25 23:20 →
  2026-10-28 22:55 IST — no GDELT download and no recorder data in that span (Mon–Wed trading
  lost for H6b). GDELT at restart: 514/1,461 windows, 15,446 events (Jan 2018 → mid-2019).
  Second gap: off 2026-10-29 06:51 → 2026-10-31 13:57 IST (Thu–Fri trading lost for H6b;
  GDELT at restart 620/1,461). On the 2026-10-31 network Kraken refuses connections
  (WinError 10054) while TrueFX and GDELT are reachable — crossed books need both venues, so
  H6b collection depends on Kraken being reachable after the Sunday market open.
  Third gap: jobs stopped 2026-10-31 ~14:09 → 15:31 IST (host terminal tabs closed). FX
  market closed (Saturday), so no recorder data lost; GDELT throughput only (~20 windows).
  Integrity check after restart: 677 sentinels, 676 shards (the extra is 20190202, a
  confirmed-empty window), no orphan or zero-byte shards, all recording files end on a
  complete JSON line.
  2026-10-31 20:58 IST: the backfill had skipped 23 days inside its covered range (windows that
  failed after GDELT throttling, plus ~20:40 local network drop); failed windows carry no
  sentinel, so the backfill was restarted to re-fetch them chronologically before continuing.
  Mechanical and result-blind (no data inspected); total window throughput unchanged.
  2026-11-01 02:15 IST: ~20% of windows were still exhausting the 6-try limit under throttling
  (21 gaps at 764 sentinels). Added `--max-attempts` (default unchanged at 6) and restarted
  with 12, so throttled windows are retried in place instead of left as holes. Fetch logic,
  query and window definition unchanged; no data inspected.
  2026-11-01: Wi-Fi disconnected 04:36 → 06:50 IST (WLAN event log); recorder polls failed
  with DNS errors and GDELT windows in that span exhausted their retries (13 holes at 795
  sentinels). From 08:30 IST the backfill runs as a loop of 30-window runs, each re-planned
  from 2018, so failed days are retried before new ones; the loop stops at the cutoff.
  Fourth gap: laptop off 2026-11-01 12:02 → 19:48 IST (Sunday, FX closed: no recorder data
  lost; ~95 GDELT windows of throughput lost). At restart 840/1,461 windows, gap-free to
  2020-04-20, no temp or zero-byte shards. Network after restart: phone hotspot.
  **Release model deployed 2026-11-02 02:39 IST (2026-11-01 21:09 UTC)**, before the Monday FX
  open: semantic service restarted with SEMANTIC_REACTION_V2=1 (release extractor + scorer,
  euro/pound pair-attribution fix), backend restarted with release fields in
  `live_predictions.jsonl` and a shadow `release_channel` in each opportunity's
  `semantic_snapshot` (penalty 0; risk and direction channels unchanged, so H6b is unaffected).
  This timestamp ends the prospective "before" window (V1 rows published 2026-10-25 → 2026-11-01
  21:09 UTC). Two synthetic test POSTs to /v1/analyze at 02:40 IST went to the service history
  only (not the backend prediction log). Recorder gap ≈ 1 min, market closed.
  **Slice download started 2026-11-02 ~03:05 IST:** the probe was accepted, so the sample uses
  `CURRENCY_QUERY` (choice file written 02:53 IST); first 10 slices: 14.1 currency-tagged
  headlines per slice vs 4.7 under the default query in pilot 1 (counts only), ~21 slices/h.
  Services restarted 03:19 and 03:23 IST (feeds, publisher time, Finnhub, synthetic off;
  recorder gaps < 1 min each, before the 03:30 IST open). At 03:24 IST Kraken legs healthy.
  **Slice pacing (engineering, sample unchanged), 2026-11-02 07:28 IST:** first 60-slice run:
  161 throttle replies, 58 slices in 3.9 h, the pacer ending at 54 s; completions fell from
  ~19/h to 8-10/h. At that rate coverage at the cutoff would be ~55% of the 3,824 slices. Each
  loop run restarted the pacer at GDELT's documented 5 s, so `run_slices_loop.ps1` now passes
  `--min-interval 20`; schedule, query and period are unchanged. Restarted 07:28 IST (63 done).
  Kraken legs read "stale" in the Asian session (no quote change for > 60 s), not failing.
- **Positive control (M3)**: record = FAIL on (a), runs 1–2. Both failures traced to clock
  defects (calendar placeholders, HistData DST). On correctly timed data the original gate
  specification passes all three criteria (post-hoc), and so does the Option A store.
- **Results so far** (correct clocks): H2 SUPPORTED (inflation ≈ 2.3× employment ≈ 3.2×
  growth); H3 NOT supported — reactions complete within 1 min and persist to 120m; R1
  superseded — EUR/USD daily reaction at bar +1, USD/INR null (daily, unaffected by the
  HistData bug). Artifacts: `positive_control.json`, `r1_rerun.json`, `h2h3.json`
  (void pre-fix copy kept as `h2h3_void_pre_dst_fix.json`).
- **Calendar store rebuilt 2026-10-24** after the timestamp fix (§6): 34,333 events,
  23,636 (69%, not the earlier 95%) with a usable release time, 3,261 of them filled from
  validated release clocks. **Causal-arm sample is now 2,778** (US 1,482, AU 296, CA 281,
  UK 256, EA 200, NZ 134, CN 96, CH 30). Non-US releases lost many rows to the missing-time
  fix; they need their own validated release families (Eurostat, ONS, SNB, ...) before H2/H3
  across the majors. Old store kept as `calendar_events.parquet.bak-20261024-173354`.
- **Unscheduled events**: 5,884 tagged (GDELT), 2018-01-01 → 2018-12-26
- **Backfill**: 347 / 2,921 windows (12%)
- **Feature table discrepancy (2026-10-24)**: `artifacts/features.parquet` measures **2,009 rows**
  (EUR/USD 645, USD/INR 462, GBP/USD 361, USD/JPY 263, AUD/USD 259, USD/CHF 19), not the 4,473
  recorded below. Rebuild and reconcile before M6 uses it.
- **Live-system performance work (2026-10-24, outside research scope, recorded for context)**:
  linear reaction artifacts are now weights-only (1.32 MB → 52 KB; 49 MB → 0.05 MB at 5k rows)
  and evidence retrieval is vectorised (1.19 s → 11 ms per article at 5k rows), both with
  identical outputs. Growing the dataset no longer slows the running app.
- **Scheduled events**: **34,333 ingested, 2018-01 → 2025-04**
  - 32,714 (95%) with an exact release time — usable for 1m/5m horizons
  - 22,517 with a standardized surprise
  - **3,594 high-impact + exact-time + standardized surprise — the causal-arm sample**
- **Feature table**: 4,473 rows × 55 columns (GDELT arm only; calendar not yet joined)
- **Next**: re-size M3 under the 2026-10-24 attribution (four majors), run the 50-hour pilot,
  then the parallel intraday fetch, EUR/USD first as the positive control.

### M3 fetch plan (sized 2026-08 under the old policy; re-size before running)
Causal sample 3,594 events → 3,594 study observations under `usd_policy="primary"`.
Window −1h/+2h covers the 1m–120m horizons; 4h/1d come from existing daily bars.

| Scenario | Events | Unique hours | Wall clock |
|---|---|---|---|
| EUR/USD only | 1,917 | 5,287 | 15–37 h |
| EUR/USD + GBP/USD | 2,347 | 6,949 | 19–48 h |
| All tradeable (CNY dropped) | 3,487 | 11,269 | 31–78 h |

Window dedup removes 19–24% of naive per-event hours. The fetch is cached and resumable,
so it can run in stages.

### USD/INR data availability — investigated 2026-08, settled

USD/INR is the project's primary pair, so its data constraints were checked exhaustively
rather than assumed. Results:

**Intraday USD/INR prices — not freely available for 2018–2025.**

| Source | Result |
|---|---|
| Dukascopy tick feed | **HTTP 404** — USDINR is not an instrument on the feed |
| HistData M1 | **Absent** — 66 pairs listed, none involving INR |
| Alpha Vantage `FX_INTRADAY` | **Premium endpoint**, no longer on the free tier |
| TraderMade free tier | 2 days of minute history — unusable for a multi-year study |
| yfinance `USDINR=X` | Intraday limited to ~60 days |

**India economic calendar with consensus — no free programmatic source found.**

| Source | Result |
|---|---|
| ForexFactory dump (acquired) | Zero INR rows; covers USD/EUR/GBP/JPY/CHF/AUD/NZD/CAD/CNY only |
| TradingEconomics guest API | **HTTP 410 Gone** — retired (this is what `news_stream` previously used) |
| Investing.com / StockeZee / indiamacroindicators | Scrape-only, ToS risk, consensus coverage unverified |

**Consequence (accepted, not worked around):** the USD/INR arm runs at **daily resolution**.
Per §5 kill criteria, no further engineering is spent trying to manufacture INR intraday data.

**What this does not block.** USD/INR is a USD pair, so the 1,608 high-impact **US** releases
with consensus forecasts are exogenous shocks to USD/INR and carry the causal arm without any
Indian calendar. Combined with GDELT India/INR news for the semantic arm, the primary pair
remains fully studiable.

### Result R1 — daily resolution cannot detect FX news reactions (established 2026-08)

Surprise-response regression, 1,608 US high-impact releases (2018-01 → 2025-04), standardized
surprise winsorized at ±5σ, standard errors clustered by release date (932 clusters).

| Pair | Signed β at bar 0 | p | Magnitude β at bar 0 | p |
|---|---|---|---|---|
| USD/INR | +0.62 bps/σ | 0.60 | +1.35 | 0.30 |
| EUR/USD | +0.35 bps/σ | 0.76 | +0.31 | 0.74 |

**Placebo tests pass.** Bars −1 and −2 are insignificant for both pairs, so the pipeline is
not leaking or misaligned. The measurement chain is validated.

**EUR/USD is the positive control and it fails to show a known effect.** Andersen, Bollerslev,
Diebold & Vega (2003) document large, immediate EUR/USD responses to US macro surprises at
5-minute resolution. Detecting nothing at daily resolution — signed *or* magnitude — is
therefore not evidence of no reaction; it is evidence that a 24-hour window buries a
5-minute response under unrelated variance.

A weak effect appears at bar +2 for USD/INR (signed β = +3.44 bps/σ, q = 0.046 across 6
offsets; magnitude β = +2.76, p = 0.023). It is directionally coherent with EUR/USD at the
same offset (−3.29 bps/σ — both imply dollar strength), but the control pair does not survive
correction, so this is **not claimed as a finding** without intraday confirmation.

**Consequence:** intraday resolution is a *requirement*, not a refinement. Since no free
intraday USD/INR data exists, the primary pair cannot support a causal arm on free data alone.
This is now the project's binding constraint and is escalated for a scope decision.

### Result R2 — H1 pilot on USD/INR news (2026-08). Underpowered, not rejected.

Scope narrowed to the sentiment wing. Pipeline: GDELT INR headlines → syndication dedup →
embeddings → novelty + sentiment → daily aggregation → USD/INR daily |return|.

**Embedding backend mattered, and the first attempt was broken.**

| Backend | Pairwise similarity | Novelty mean (sd) | Verdict |
|---|---|---|---|
| TF-IDF + SVD | +0.032, near-orthogonal | 0.897 (0.083) | **Degenerate** — everything looks novel, no discrimination |
| sentence-transformer | — | 0.587 (0.092), range 0.32–0.86 | Usable |

Attempt 1 failed on *measurement*, not on the hypothesis. Attempt 2 fixed it. Per §3's
two-attempt rule the embedder axis is now closed; no third backend will be tried.

**Nested test, n = 132 news-days, control = prior 20-day realized volatility:**

| Predictor of \|daily return\| | β (bps) | se | p |
|---|---|---|---|
| sentiment_abs_mean (baseline) | 8.79 | 12.40 | 0.48 |
| news_count (attention) | 1.85 | 1.77 | 0.30 |
| novelty_mean (H1) | 31.32 | 33.44 | 0.35 |
| novelty_max (H1) | 37.71 | 33.28 | 0.26 |

Novelty adds ΔR² = +0.0051 over the sentiment baseline (p = 0.35). **Nothing is significant.**

**This is a power problem, not a verdict.** Observed |t| ≈ 0.9–1.1; reaching 80% power needs
roughly (2.8/0.95)² ≈ 9× the current sample, i.e. ~1,150 news-days. The GDELT backfill sits at
12% of its range, so completing it yields ~8× more news-days — almost exactly the requirement.

**Consequence:** the backfill is not optional side work; it is the sentiment wing's data supply.
H1 cannot be adjudicated until it completes. Per §5, no tuning against this sample in the
meantime — that would manufacture significance from noise.

### Scope limitations discovered while sizing
- **USD/JPY has only 4 usable events** — JPY calendar rows rarely carry a forecast, so the
  JPY arm effectively does not exist in the scheduled sample. Document, do not fix.
- **USD/CNY has 107 events but no free tick data** — excluded from the fetch.
- **USD/INR has zero scheduled events** — ForexFactory does not cover Indian releases.
  **USD/INR can therefore only be studied through the GDELT/news arm, never the calendar arm.**
  This is a material constraint on the original India focus and must be stated in the write-up.

### Open data-quality item
Standardized surprise has extreme tails (min −13.1σ, max +23.9σ) against a mean of 0.04 and
std of 1.29. Consistent with unit inconsistencies or events whose historical surprise variance
is near zero. **Winsorize at ±3σ or ±5σ and document the threshold** before modelling
(`ENGINEERING_PRACTICES.md` §2).
