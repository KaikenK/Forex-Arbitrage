# Arbex semantic wing: complete status for the paper draft

> **Snapshot of 2026-11-17, kept for the record.** Since then recording was concluded and every sealed test was run (2026-11-21). For the final results see `SEMANTIC_WING_EXPLAINED.md` §8 and the 2026-11-21 entries in `RESEARCH_TRACKER.md` §6 and §8.
>
> - The C43 analysis script now exists (`research/run_c43.py`).
> - The shadow rows were merged from both folders.
> - The Phase 2 switches are on.
> - A demo mode exists (`C:\Users\xyreo\arbex\run\arbex_demo.bat`).
>
> The "sealed" wording in §8 and the to-do list in §9 below are out of date.

Status as of 2026-11-17, 18:30 IST. Branch `semantic-microservice`, last commit e7a253f.

Sources:
- `research/RESEARCH_TRACKER.md`: §2 locked hypotheses, §6 decision log, §8 status board.
- `research/artifacts/*.json`: result files.
- `research/REPRODUCE.md` and `research/RUNBOOK_18NOV.md`.
- Recorder input counts, read for this document (counts only).

Every number below comes from a committed artifact or a tracker entry.

**Sealed until 18 Nov 00:00 IST (2026-11-17 18:30 UTC).** No live outcome has been examined. This covers persistence of crosses, realised edge, and the prospective accuracy of logged predictions. Section 8 lists those tests with their input counts only.

Paper due: 20 Nov 2026. Full draft was planned for 17 Nov.

---

## 1. What the semantic wing is, in one paragraph

Arbex detects cross-venue FX price discrepancies (Kraken vs TrueFX) on EUR/USD, GBP/USD, USD/JPY and USD/CHF. The semantic wing is the news layer beside the detector. For each incoming article it answers four questions for each pair:
- whether the pair is affected;
- in which direction;
- by how much;
- with what calibrated confidence.

It also keeps a rolling 30-minute news bias per pair that is meant to help the detector rank opportunities. The research asked whether any of this carries usable information.

**The answer so far:** news carries no usable direction once an article can be read, at any tested horizon, historically or live. News activity and release timing do carry risk information: how large the next move is, and how much caution is warranted.

**The paper's thesis has therefore moved:**
- **Original thesis (§1 of the tracker):** "semantic novelty predicts the magnitude of FX reactions better than sentiment". It was tested and not supported; the novelty coefficient came out significant but with the wrong sign.
- **Current thesis:** "for an arbitrage ranker, news is a risk signal, not a direction signal".

---

## 2. System built (engineering; not a paper claim unless tested)

### 2.1 Components

| Component | Where | What it does |
|---|---|---|
| Semantic microservice (:9000) | `services/semantic/src/` (`api`, `market`, `news`, `retrieval`, `training`) | RSS and Finnhub ingestion; per-headline sentiment (FinBERT since the C38 deploy, keyword scorer before); reaction scoring; currency index; release extractor and scorer; `/v1/analyze`, `/v1/feed` (SSE), `/v1/metrics`. |
| V1 text model | `artifact_reaction_scorer.py`, artifact `pooled_multipair` | Headline → per-pair direction and move. USD/INR part void (synthetic training rows, §5.5). Retrained clean copy `pooled_multipair_clean` (C32), never deployed. |
| V2 release model | `release_extractor.py`, `release_reaction_scorer.py`, artifact `release_reaction/reaction_model.json` | Matches a headline to a calendar release, extracts the actual, computes the standardized surprise z, and applies the theory-rule direction sign(z)·news_sign with a calibrated p_direction and an expected move. Deployed 2026-11-02 02:39 IST (`SEMANTIC_REACTION_V2=1`). |
| Currency index (H6 signal, live) | `currency_bias_tracker.py`, `currency_bias_core.py` | Per-currency news bias (FinBERT polarity × confidence × novelty × relevance, 12 h decay). Pair bias = base − quote. |
| Risk channel | `backend/core/currency_bias_channels.py` | Penalty = 0.06 × clip((expected move / pair's 24 h median − 1) / 2, 0, 1), plus a release-proximity term. Release proximity is non-null from 15 min before to 10 min after a high-impact release. |
| Direction channel | same | Penalty when the currency-index direction opposes the trade's residual exposure. |
| Window bias V1 (30 min) | `backend/core/sentiment_bridge.py`, `semantic_engine.py` | Keyword or FinBERT sentiment, timed by receipt time. The adjustment depends on \|sentiment\| and conflict (caution only, ≤ 0). |
| Window bias v2 (shadow) | `backend/core/window_bias_v2.py` | Dedup, publisher time, and release direction × p. Score = Σ r·d / (Σ r + 1). Logged as `semantic_snapshot.window_bias_v2`; adjusts nothing. |
| Phase 2 switches (C42, all OFF) | `backend/core/semantic_apply.py`; service `analyzer.py` | `ARBEX_APPLY_SEMANTIC=risk` (apply risk penalty + stale-leg caution); `SEMANTIC_DIRECTION_SOURCE=currency_index`; `SEMANTIC_FX_RELEVANCE_FILTER=1`; `SEMANTIC_RELEASE_ACTUALS` not built. |
| Shadow engine (C43, ON since 2026-11-05 19:49 IST) | `backend/core/semantic_shadow.py` | Writes, per opportunity, what the C42 switches would have recorded. Output: `%LOCALAPPDATA%\arbex\shadow\<UTC date>\semantic_shadow.jsonl`. |
| Recorder | backend (:8000) | `%LOCALAPPDATA%\arbex\recordings\<UTC date>\{spot.jsonl, opportunities.jsonl}`. Each opportunity carries a `semantic_snapshot` of every channel. |
| Prediction log | `sentiment_assets/runtime/predictions/live_predictions.jsonl` | Every per-pair call the service makes, with basis (news or release), publisher time and feed URL. |

### 2.2 Key facts about the live system

- **The live ranker applies no semantic adjustment.** The recorded `confidence_score` is the raw detector score and `snapshot.applied=False`. Every channel is only shadow-logged, so detector-only comparisons are clean.
- **The live confidence floor is 0.0**, so no penalty can drop a recorded opportunity.
- **TrueFX is an indicative aggregated feed.** Detected crosses are price discrepancies, not executable arbitrage.
- **Kraken** has no exchange timestamp. Its USD/JPY book is unusable (median spread about 190–237 bps). On the phone hotspot (from 4 Nov) Kraken was chronically degraded.
- **Measured speed** (C38, FinBERT on):
  - analysis about 40 ms (FinBERT 28, reaction 9, tracker 2.8);
  - live p50 90 ms;
  - event-loop lag max 17–38 ms.
- **Test suites:** research 288, backend 243, semantic service 253, all passing.

### 2.3 Engineering changes in the record (not paper claims)

| Change | What it did |
|---|---|
| C33 | Matcher anchors on publisher time; ISM/ADP must be named on both sides (fixed an S&P Global PMI → ISM false match). |
| C38 (deployed 2026-11-05 00:07:56 IST) | FinBERT on and corrected (P(pos) − P(neg), confidence 1 − P(neutral)); sign fixes (falling verbs, unemployment-type releases, no-USD pairs, no cross-pair evidence, monotone p_direction); window v2 maths; backend dedup and retry; feed-storm fix; speed work. |
| Phase 1 display fixes | In-process dashboard scorer, `/live` stale-cross labels (5 Nov). |
| C42 | Phase 2 switches built and tested; all off. |

The UI overhaul branch `ui-updates` was reverted and is not part of the paper.

---

## 3. Research design and integrity

- **Governing document:** `research/RESEARCH_TRACKER.md`.
  - §2 holds the hypotheses, each with a pre-registered decision rule and stop condition.
  - §6 is an append-only decision log.
  - The two-attempt rule (§3) applies, and §5 sets the kill criteria.
- **Every result is logged as read.** No test was re-specified after its result was seen. Negative results are reported.
- **Product choices that follow results** are labelled engineering, not paper claims.
- **Reproducibility:** `python -m research.reproduce --list | --run <step>` runs every paper result behind its protocol gate (cutoff, freeze hash, window ends, the 18 Nov gate). `REPRODUCE.md` maps each table to its command and artifact.

### 3.1 Deviations from the locked protocol (all logged before the affected results)

| # | Date | Change | Reason |
|---|---|---|---|
| D1 | 28 Oct | GDELT cutoff 31 Oct → 2 Nov 00:00 IST | Laptop outage, 3 days of download lost |
| D2 | 1 Nov | Cutoff → 4 Nov 00:00 IST | Further outages; coverage ~890/1,461 windows |
| D3 | 2 Nov | GDELT DOC daily sample withdrawn (truncation defect, §5.3); time-stratified 1 h slice sample; H1/H5/H6 become the unscheduled-news arm of RQ1–RQ5; cutoff 10 Nov | Defect found by a timestamp-only check |
| D4 | 2 Nov | News re-collected from the GDELT GKG on BigQuery (sandbox, no billing), text stages on Colab | DOC rate limit allowed only ~20% sampling |
| D5 | 2 Nov | H1/H5/H6 period starts 2019-10-01 (67 months); a second gap rule on tagged-event counts | GKG page titles absent before Sep 2019 (§5.4) |
| D6 | 5 Nov | Freeze cutoff moved earlier, to 5 Nov 05:30 IST | Extraction complete; no reason to wait |

The paper appendix currently lists D1–D3 only; D4–D6 still need to be added.

### 3.2 Clarifications (implementation details decided before results; not deviations)

| # | Subject |
|---|---|
| C1–C5 | H1 details: whole-word market-wrap regex; USD-only headlines attributed to all four majors; 20 previous weekdays as control, at least 10 required; first 12 months training-only. |
| C6–C19 | H6 details: windows, neutral-when-empty, novelty-missing drops the headline, relevance 0.5, training-window z-scores, intraday lags 2/5 min, USD/INR target timing, IC p-values, missing-day drop, H6b nesting and one-sided-quote drop. |
| C20–C25 | RQ details: cells keyed by currency × category × pair; confidence-fit and conformal procedures; reference bar; baselines; JPY/CHF abstentions. C26 was never assigned. |
| C27–C29 | After the synthetic-headline finding: prospective test excludes synthetic rows; H6b reported on the locked window and the clean sub-window (from 2 Nov 03:23 IST); latency receipt definition. |
| C30 | RQ2-E2E details. |
| C31 | H5 drops missing days. |
| C32 | V1-clean baseline (reported, never deployed). |
| C33 | Release matcher uses publisher time and named surveys (engineering). |
| C34 | 30-min window as its own H6b/RQ5 arm; RQ5 extension to 18 Nov. |
| C35 | window30 historical test and window v2 (shadow). |
| C36 | Prospective test counts each headline once per pair (earliest receipt). |
| C37 | Secondary prospective result on items at most 24 h old by publisher time. |
| C38 | Engine update; prospective test split V2.0 / V2.1 (primary). |
| C39 | H6b sensitivity section: clean window minus feed-resend periods. |
| C40 | News as risk on the live recorder (pre-registered test; §8.3). |
| C41 | Rupee/RBI headlines → USD/INR in the H1 INR arm and H6 baseline; H6 stream crash fix. |
| C42 | Phase 2 switches, off until after 18 Nov. |
| C43 | Shadow engine and its pre-specified analysis. |

The paper appendix currently lists C1–C32; C33–C43 still need to be added.

---

## 4. Data

| Data | Details |
|---|---|
| Economic calendar | ForexFactory-format dump, 2018-01 → 2025-04: 34,333 events, 23,636 with a usable time, 22,517 with a standardized surprise. Option A σ: per (currency, indicator), estimated outside 2020-03-01..2021-06-30, applied to all, winsorized at ±5σ. Release-clock fills for 17 validated US families (3,261 rows, `timestamp_source="schedule"`). |
| RQ1–RQ4 sample | 1,385 high-impact exact-time releases with z ≠ 0 (USD, EUR, GBP, JPY, CHF) × 4 majors; 76 walk-forward test months (2019-01..2025-04); about 900 release-moment clusters. |
| Intraday prices | HistData M1, 2018–2025, four majors. Bars stamped at close and parsed as New York wall clock with DST. Entry lag ≤ 2 min, exit lag ≤ 5 min, fail closed. |
| Daily prices | Yahoo (R1, USD/INR). No free intraday USD/INR exists (Dukascopy 404, HistData absent, Alpha Vantage premium, yfinance 60 days). |
| Unscheduled news | GDELT GKG via BigQuery: 3,690,637 FX-term headlines extracted, 1,576,465 currency-tagged. Frozen sample (2019-10..2025-04): 67/67 months, 1,569,940 events, 1 missing day, SHA-256 `d4ef3d7f…`. Loaded 1,562,129, of which 955,570 are canonical after dedup. Scored with lexicon, FinBERT and sentence-transformer. |
| Live recordings | 37,604 opportunities in total (25 Oct → 6 Nov). Two-venue data only 28 Oct 17:25 → 29 Oct 01:21 UTC and from 1 Nov 22:01 UTC. 30,392 from the C28 clean start; 916 after the H6b window end; 902 after the C38 deploy. |
| C43 shadow rows | 313 (300 on 5 Nov UTC, 13 on 6 Nov). |
| Prediction log | To 2 Nov: 14,486 rows, 13,311 of them synthetic. After the C38 deploy: 1,509 news-basis and 44 release-basis rows, before the C36 dedup. |

### 4.1 Live sample coverage problem (must be stated in the paper)

The services have not run since **2026-11-07 01:19:47 IST**, when they were stopped for a laptop shutdown. They were never restarted.

Every live sample nominally ends at 18 Nov 00:00 IST, but no data exists after 7 Nov 01:19 IST. That is about 10.9 days of missing coverage, and it is **not yet logged in tracker §8**.

On top of this, the live record has many logged outages: laptop off, app closures, Wi-Fi and hotspot drops. On 6 Nov alone about 5.7 h were lost to hotspot outages. Kraken was degraded on the hotspot from 4 Nov.

**Coverage must be reported as is.** No gap may be filled or worked around.

---

## 5. Data errors found (a methods contribution)

Each of these was silent, found by a check rather than by a result, and each changed or would have changed a conclusion.

1. **Calendar timestamps (ForexFactory-format dump).**
   - **Defect:** missing times were stored as local 00:00:00 (about 30% of rows; 88/88 payrolls rows 2018–2025). Separately, stale Iranian DST in the dump's offsets put 2023+ summer rows 1 h early (5,071 rows).
   - **Effect:** the pre-registered positive control failed twice.
   - **Fix:** made from documentation and observed rows only, never from prices.
2. **HistData DST.** The files are documented as fixed EST but are New York wall clock with DST.
   - **Symptom:** a reaction "jump" exactly 60 min after every summer release.
   - **Effect:** the summer 1 m reaction was +0.5 bps before the fix and +8.5 bps after. The first H2/H3 pass was void.
3. **GDELT DOC truncation.**
   - **Defect:** a 24 h request with `sort=datedesc` and the 250 cap returns only the ~12 minutes before 00:00 UTC. In a 169-day sample, 88% of headlines fall 23:00–01:00 UTC, and the median per-day span is 0.2 h.
   - **Consequence:** the daily sample was withdrawn (D3).
4. **GDELT GKG page titles are absent before Sep 2019.** The original gap rule would have counted 20 empty months as "no FX news". The fix was D5.
5. **Synthetic demo headlines in the live pipeline.**
   - **Defect:** 13,311 of the 14,486 prediction-log rows to 2 Nov (92%) were the service's made-up quiet-time headlines. They fed the window and currency index of every pre-fix snapshot.
   - **Effect on training data:** 190 of the 743 USD/INR V1 training rows (26%) were synthetic, which makes V1 USD/INR metrics void.
   - **Fix:** 2 Nov 03:23 IST, with C27, C28 and C32.
6. **Feed reconnect storm (3 Nov).**
   - **Defect:** backend timeouts made the service resend its whole backlog under new ids, and leaked pollers piled up to 18 at once. That produced 8,355 repeated rows, and 1,298 opportunities got backlog-inflated window snapshots.
   - **Handling:** C39 sensitivity section; the fix shipped with C38.
7. **FinBERT off in the live service until 5 Nov.** The `[runtime]` config section was never carried over, so the live window ran on the keyword scorer.
8. **R1 sign confound.** Raw surprises pooled across releases whose "good for USD" sign differs (for example unemployment vs payrolls) cancel each other. Fixed by `sign_conventions.py`.
9. **H6 numerical blow-up.** A near-constant training stream (std about 1e-17) produced z-scores of about 1e16 in the intraday secondaries. Fixed, and the daily primary is unchanged.
10. **Restart backlogs.** 206 of 343 distinct headlines were first seen in restart bursts, and 108 were more than 24 h old. The V1 window timed them as fresh; window v2 and C37 handle this.

---

## 6. Results so far (all final; one pass each)

### 6.1 Summary table

| Test | Verdict | Key numbers | Artifact |
|---|---|---|---|
| Positive control (ABDV replication) | **Record: FAIL on (a)**, runs 1–2. Both failures traced to clock defects. Post-hoc on correct clocks: passes all three. | Original spec on correct clocks: payrolls 5 m EUR/USD β −8.99 bps/σ (p 0.010); pooled oriented +6.51 (p 1e-11); placebo p 0.33. Option A: pooled oriented 1 m +4.20..+5.34 bps/σ all pairs (p ≤ 7e-14); payrolls 5 m EUR/USD −5.67 (p 8e-5). GBP/USD placebo p 0.012 (opposite sign; reported). | `positive_control.json` |
| R1 re-run (daily) | EUR/USD detects US surprises once oriented; USD/INR does not | EUR/USD bar +1 +5.67 bps/σ (q < 1e-4); USD/INR no offset survives BH | `r1_rerun.json` |
| H2: category signatures | **SUPPORTED** | Employment, growth, inflation survive at every horizon 1–120 m (q < 0.001); 1 m CAR inflation +17.3 bps, employment +7.6, growth +5.4 | `h2h3.json` |
| H3: reaction speed by category | **NOT SUPPORTED** | Half-life undefined in 98–100% of draws; reactions complete within 1 min and persist to 120 m in every category (size differs, speed does not) | `h2h3.json` |
| H4: regime-conditional | **Not run** (lowest priority, cut) | none | none |
| RQ2: direction, release-time 15 m (primary) | Learned model **worse** than the a-priori theory rule | Model 63.5% [60.2, 66.6] (AUC 0.668); theory rule sign(z)·news_sign 68.4% [65.4, 71.3]; majority 51.5%. 1 m: 68.2% vs 75.2%. 120 m: 57.7% vs 61.0%. Accuracy rises with \|z\| (58 → 65 → 68%). | `reaction_eval.json` |
| RQ2: direction from article arrival (release + 15 min), 15 → 60 m (primary) | **No direction left** | 48.8% [46.6, 51.0], p 0.27; mean move in predicted direction −0.14 bps (p 0.68) | `reaction_eval.json` |
| RQ3: magnitude, 15 m | Better rank, **no MAE gain** | Spearman 0.287 vs 0.189 / 0.242 / 0.285 (pair mean / cell mean / same-clock v); MAE 12.01 vs 11.54 (+0.47, p 0.15); after arrival the same-clock baseline beats the model (MAE 7.11 vs 7.40, p < 0.001) | `reaction_eval.json` |
| RQ4: confidence | Calibrated, little resolution | ECE 0.041; Brier 0.234 vs 0.232 constant (1 m: 0.207 vs 0.217); conformal coverage 76.5% at 80%, 86.9% at 90% | `reaction_eval.json` |
| RQ1: affected pairs | Currency rule high recall, low precision | Recall 0.90, precision 0.41, F1 0.56; affected 41% own vs 19% cross pairs (EUR release moves GBP/USD, USD/JPY ~22%); expected-move AUC 0.558 | `reaction_eval.json` |
| RQ2-E2E: real release headlines | Extraction rarely works; **no method beats 50%** | 2,410 candidates / 535 moments; matched 6.6%, correct actual 4.7% (70.6% given a match). Article-time r60: structured 41.6% [28.8, 54.2]; FinBERT 51.6% [48.5, 54.4]; V1 53.5% [49.1, 57.7]; V1-clean 53.4%. Release-time r15 structured 53.2% [38.6, 68.3] | `rq2_e2e.json` |
| H1: novelty predicts \|move\| (primary) | **NOT SUPPORTED** (significant, wrong sign) | Majors, sentence-transformer: 243,360 rows, 234,273 events, 1,717 dates; novelty β −0.968 (SE 0.279, p 0.0005); OOS MSE 89.0017 → 88.9892 (0.01%). Lexicon baseline β −0.937 (p 0.0007). TF-IDF −0.495 (p 0.081), hashing −0.431 (p 0.098). USD/INR arm (101,176 events): β −10.4 (p 0.54), OOS not better | `h1_results.json` |
| H5: sentiment as a currency factor | **PARTIAL** (lexicon passes (a), fails (b)) | Lexicon (a): OOS R² 0.0073 vs −0.0006, Spearman 0.123 vs −0.045 (p 0.003). Lexicon (b): VIX p 0.14, USD/JPY p 0.80, USD/CHF wrong sign (p 0.002). FinBERT: (a) fails; (b) VIX passes (p 0.026), JPY ns, CHF wrong sign. Same verdicts excluding the pandemic window | `h5_results.json`, `h5_market_component_*.json` |
| H6: currency bias forecasts returns | **NOT SUPPORTED** | Daily: 5,006 rows, 1,455 dates, 55 OOS months; pair_bias β 0.095 (p 0.89); OOS MSE 2,270.5 vs 2,268.4; hit 49.4%; ICs −0.02 to −0.03 (ns). Rerun after the z-score fix: daily identical (56/56); intraday secondaries sane, none evidence | `h6_results.json` (+ `h6_results_fc0d7df.json`), `h6_horizon*.json` |
| window30 (C35, exploratory, BH over 6) | **Direction null; activity predicts move size** | T1 direction p 0.95 / 0.74 (hit 50.0%); T2 activity β 0.043 (30 m, q 6.1e-9) and 0.048 (60 m, q 1.5e-10), n ≈ 258k, release β 0.286; T3 caution 60 m q 0.044, 30 m q 0.139 | `window30_results.json` |
| H6b: live ranking value (prospective, descriptive) | Risk and direction channels **slightly hurt** ranking; window channels indistinguishable | See §6.2 | `h6b_results.json` |
| R2 pilot (Aug, USD/INR) | Underpowered; superseded by H1 | n = 132 news-days, nothing significant | tracker §8 |

### 6.2 H6b detail (still crossed at the next poll, AUC, day bootstrap)

| Section | Rows / days / negatives | Detector | + risk | + risk + direction | + window | + v2 |
|---|---|---|---|---|---|---|
| Locked window | 36,668 / 6 / 790 | 0.918 [0.863, 0.972] | 0.908, diff [−0.016, −0.003] | 0.906, diff [−0.017, −0.004] | 0.920, diff [−0.002, +0.004] | 0.918, diff ≈ 0 |
| Clean (C28) | 29,456 / 4 / 573 | 0.948 [0.925, 0.972] | 0.942, diff [−0.011, −0.001] | 0.941, diff [−0.012, −0.002] | 0.949, diff [−0.004, +0.002] | 0.948, diff ≈ 0 |
| Clean minus resends (C39) | 25,987 / 4 / 448 | 0.948 [0.929, 0.970] | 0.944, diff [−0.012, −0.002] | 0.944, diff [−0.013, −0.003] | 0.948, diff [−0.006, +0.001] | 0.948, diff ≈ 0 |

- **Realised edge at the next poll:** mean 2.3–2.4 pips, median 1.8–1.9 pips.
- **Limits:**
  - 4–6 days give a coarse bootstrap;
  - 97.8–98.3% of crosses were still crossed, so each AUC rests on 448–790 negatives;
  - TrueFX is indicative;
  - Kraken was degraded on 4 Nov.

### 6.3 Synthesis for the paper

1. **Direction.**
   - At release time, a fixed theory rule gives 68% accuracy at 15 m; a learned model does worse.
   - Once an article can arrive (about 15 min later), no direction remains at any horizon. This holds for the release model (RQ2 arrival), real headlines (RQ2-E2E), novelty (H1), the currency bias (H6) and the 30-min window (window30 T1).
   - This is consistent with immediate, permanent incorporation (H3; ABDV 2003).
2. **Risk.**
   - News activity predicts the size of the next 30–60 min move (window30 T2, strongly); caution predicts it weakly (T3-60).
   - Releases move pairs that do not contain the currency about 19–22% of the time (RQ1).
   - Release-time expected moves are well ranked (RQ3 Spearman) but not better in MAE.
3. **Live ranking.**
   - The shipped risk channel did not improve ranking on H6b; it lowered AUC by 0.003–0.012.
   - This is the open tension that C40, the RQ5 extension and C43 address: is the channel's form wrong, or is news risk not visible in next-poll persistence?
4. **Free-data errors** are silent and directional (§5); this is a contribution in its own right.

---

## 7. Paper state

- **Current files:**
  - `research/paper/draft.md` (working draft, 2 Nov: abstract, introduction, data errors, method, RQ1–RQ4 results; sections 6–7 marked TBD);
  - `research/paper/latex/main.tex` + `appendix.tex` + `references.bib` (9 pages, compiles with Tectonic 0.17.0);
  - tables and figures from `python -m research.paper.make_paper_assets`;
  - `research/paper/news_sources.md` and `news_sources_assessment.md` (feed inventory).
- **Out of date:**
  - The title and abstract predate the H1/H5/H6/window30 results and the "news as risk" reframing.
  - The appendix lists D1–D3 and C1–C32 only.
  - Section 3.4 still describes the slice remedy, not the GKG route (D4–D6).
  - Sections 6 (unscheduled news) and 7 (live system, prospective test, RQ5) are empty.
- **Suggested structure:**
  1. Introduction (ranker framing; risk vs direction);
  2. Related work;
  3. Data and the silent errors (§5 of this file);
  4. Pre-registration and design (§3);
  5. Scheduled releases: RQ1–RQ4, positive control, H2/H3;
  6. Unscheduled news: H1, H5, H6, window30, RQ2-E2E;
  7. Live system: architecture (§2), H6b, then the 18 Nov results (prospective V1/V2.0/V2.1, RQ5 extension, C40, C43, latency);
  8. Discussion (news as risk; calibration over accuracy; what a ranker should take from news);
  9. Limitations;
  10. Appendix with the deviations, clarifications, and downtime log.

---

## 8. Being tested now: sealed until 18 Nov 00:00 IST

All of these refuse to run before 2026-11-17 18:30 UTC. Run them in order with `RUNBOOK_18NOV.md`, and log each result in §6 before running the next.

### 8.1 Prospective test of the deployed models (`score_live_predictions`)

- **Outcome:** Yahoo hourly bars from the first bar at/after `published_at` to `published_at` + horizon.
- **Metrics:** sign accuracy vs 50%, Spearman IC of `market_score`, reliability of `confidence`, clustered by UTC date.
- **Rules:** C27 (no synthetic rows), C36 (one row per headline × pair), C37 (secondary set: at most 24 h old by publisher time).
- **Sample sizes** (`reproduce` step names):
  - `live_scoring_v1`: V1 window 25 Oct → 1 Nov 21:09 UTC; 203 rows over 5 dates under C36; C37 set empty (no publisher time before 2 Nov).
  - `live_scoring_release_v20`: V2 deploy → C38 deploy (4 Nov 18:37:56 UTC); 142 release-basis rows before dedup.
  - `live_scoring_release_v21` (primary): C38 deploy → 18 Nov; **44 release-basis rows before dedup**, all within 4–7 Nov.
- **Note:** descriptive only, with few date clusters.

### 8.2 RQ5 extension (`run_h6b --rq5-extension`, C34)

- Same arms and outcome as H6b, on opportunities after the H6b window end.
- Inputs: **916 opportunities**, 902 of them V2.1, all between 4 Nov 18:30 UTC and 6 Nov. Kraken was degraded throughout.

### 8.3 C40: news as risk on the live recorder (pre-registered 5 Nov; `run_c40.py`)

- **Primary sample:** from the C28 clean start (2 Nov 03:23 IST), 30,392 opportunities in total. Gaps over 5 s are dropped.
- **C40-1 (primary):** share still crossed is lower in release windows (−5 to +15 min) than in matched controls (same pair, weekday and clock, ±1–3 weeks, no release within ±60 min).
- **C40-2:** older-leg quote age predicts a vanishing cross (feed-quality check).
- **C40-3:** window `activity_factor` predicts a vanishing cross.
- **Statistics:** BH over the three; one-sided day-cluster bootstrap, 2,000 draws.
- **Decision rule:** "news as risk" is supported live if C40-1 or C40-3 passes.
- **Not computable** if fewer than 30 release windows contain an opportunity, or fewer than 200 outcomes.
- **Controls risk:** the ±1–3 week controls need recorded spans, and none exist after 7 Nov, so many windows may lack controls. That is a counts issue, to be reported as found.

### 8.4 C43: shadow analysis (pre-specified 5 Nov; no decision weight)

- **(a)** AUC of `adjusted_confidence_score` vs `confidence_score`.
- **(b)** filtered vs unfiltered `activity_factor` AUC.
- **(c)** agreement of the V1 and currency-index window directions.
- **(d)** share of window items the FX filter removes.

**Problem 1: the analysis script has not been written.** The pre-registration says it is written and tested on synthetic data before 18 Nov, so this must be done tonight, before 00:00 IST. Otherwise, log the delay in §6 before running it.

**Problem 2: the shadow rows are split across two locations.** This was found while preparing this file.
- 313 rows were written while the backend ran inside the Claude desktop app. Windows redirected them to `C:\Users\xyreo\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\arbex\shadow\`, which Explorer does not show.
- The last 5 opportunities (6 Nov 21:24 IST, backend started from the Windows shell) have no row there. They should be in the real `C:\Users\xyreo\AppData\Local\arbex\shadow\2026-11-06\`.
- The analysis must read and merge both locations.
- The redirected copy should be backed up outside AppData: it would be lost if the app were reset or uninstalled.
- The recordings themselves are in the real folder and are unaffected.

### 8.5 Arrival-latency study and RQ2 re-test

- **Pre-registered:** 2 Nov.
- **Sample:** release-basis article rows, 2 Nov 03:19 IST → 13 Nov 23:59 IST. In practice the data ends at 7 Nov 01:19 IST.
- **Measures:** receipt and publisher latency per source; first receipt per release; observed offset a* (rounded up to {1, 2, 3, 5, 10, 15} min).
- **Rule:** if fewer than 10 distinct releases are matched, a* is not estimated and the 15-min assumption stands.
- **Re-test:** `run_reaction_eval` at a*, scheduled for 14 Nov.
- **Status: not run.** The gate opened 14 Nov. Steps: `reproduce --run arrival_latency`, then `arrival_retest`. It is allowed now (the latency sample is closed); log it as run late.

---

## 9. What is left (in order)

### Before 18 Nov 00:00 IST (tonight)

1. **Log the 7–17 Nov downtime in §8:** last quote 2026-11-07 01:19:47 IST, services stopped, not restarted. State its effect on every live sample.
2. **Write and synthetic-test the C43 analysis script**, reading both shadow locations. Alternatively, log in §6 that it will be late.
3. **Back up the redirected shadow rows** to a folder outside AppData. This is a copy only, nothing is moved.
4. Optional: run the arrival-latency study now (§8.5).
5. **Decide whether to restart the services before midnight.** There is little value: the sample closes in about 6 h and the Phase 2 switch-on comes after the 18 Nov steps.

### 18 Nov (`RUNBOOK_18NOV.md`)

6. `live_scoring_v1`, then `live_scoring_release_v20`, then `live_scoring_release_v21`, then `rq5_extension`, then `c40`, then the C43 analysis. Log each one as read.
7. Then switch on the Phase 2 flags (engineering), restart, log the time in §8, and commit the result JSONs.

### Smaller open research items (from the 5 Nov plan)

8. **Step E: H1 USD-only check.** USD-only headlines get no novelty score and are dropped. Quantify the share (counts only) and disclose it.
9. **Step F: released-actuals check.** Do ForexFactory or Finnhub publish `actual` soon enough after a release? `SEMANTIC_RELEASE_ACTUALS` depends on it.
10. **Step G: Dukascopy tick study.** About 0.75 GB, throttled to about 20 s per file, so it would take days. **Recommendation:** drop it for this paper and state it as future work.
11. fxstreet cache-busting and a shared poller (engineering, after the latency study).

### Paper (due 20 Nov)

12. **Rewrite the title and abstract** around "news as risk, not direction".
13. **Write sections 6 and 7** from §6 and §8 of this file.
14. **Update section 3** with the GKG route (D4–D6) and errors 4–10.
15. **Appendix:** add D4–D6, C33–C43 and the downtime table.
16. **Verify every reference** against the original before citing.
17. **Regenerate tables and figures** (`make_paper_assets`); add the H1/H5/H6/window30/H6b and 18 Nov tables.

---

## 10. Limitations to state

- USD/INR has no free intraday data, so it is daily only; it is absent from RQ1–RQ4. No Indian calendar with consensus exists.
- The JPY calendar rarely carries forecasts (3 JPY releases in RQ), and CHF has 30; most fall back or abstain.
- The GDELT sample is English only, starts 2019-10 (titles), and relies on 15-min batch timestamps. Tagging is precision-oriented: release headlines without a currency word never enter (RQ2-E2E).
- The ±5σ winsorization uses the whole-sample σ (as in ABDV 2003).
- Live data:
  - TrueFX is indicative, so crosses are not executable;
  - Kraken has no exchange timestamp, its USD/JPY is unusable, and it was degraded on the hotspot;
  - the live samples are short (H6b 4–6 days) and riddled with logged outages;
  - nothing was recorded after 7 Nov 01:19 IST.
- The live window before 5 Nov used the keyword sentiment scorer (FinBERT off). Before 2 Nov 03:23 IST snapshots carried synthetic headlines (C28), and on 3 Nov resend artefacts (C39).
- Three cutoff moves (D1, D2, D6) and two sample redesigns (D3, D4/D5), each with a logged reason and logged before results.
- The positive control's pre-registered record stays FAIL on (a). Its passes are post-hoc.

---

## 11. Literature to cite (verify against originals)

- **Macro announcements and FX:**
  - Andersen, Bollerslev, Diebold & Vega (2003, AER);
  - Ederington & Lee (1993, JF; 1995, JFQA): adjustment within 10–40 s, volatility raised about 15 min;
  - Faust, Rogers, Wang & Wright (2007, JME);
  - Chaboud, Chernenko & Wright (2008, JEEA);
  - Evans & Lyons (2005, JIMF; 2008, JFE);
  - Melvin & Yin (2000, EJ).
- **News and stale news:**
  - Tetlock (2011, RFS);
  - Groß-Klußmann & Hautsch (2011, JEF).
- **News indices:** Baker, Bloom & Davis (2016, QJE).
- **NLP baseline:** FinBERT (Araci 2019).
- **GDELT:** Leetaru & Schrodt (2013).

---

## 12. Where everything is

| Item | Location |
|---|---|
| Tracker (authoritative log) | `research/RESEARCH_TRACKER.md` |
| Result files | `research/artifacts/` |
| Reproduction map | `research/REPRODUCE.md`; `python -m research.reproduce --list` |
| 18 Nov steps | `research/RUNBOOK_18NOV.md` |
| Paper | `research/paper/draft.md`, `research/paper/latex/` |
| Colab notebook (GKG pipeline) | `research/colab/arbex_gkg_pipeline.ipynb`; Drive `MyDrive/arbex/` |
| Live recordings | `C:\Users\xyreo\AppData\Local\arbex\recordings\<UTC date>\` |
| Shadow rows (C43) | Redirected copy: `C:\Users\xyreo\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\Local\arbex\shadow\`; real folder: `C:\Users\xyreo\AppData\Local\arbex\shadow\` |
| Prediction log | `sentiment_assets/runtime/predictions/live_predictions.jsonl` |
| Service launchers and logs | `C:\Users\xyreo\arbex\run\` (`arbex_restart.bat`, `services.ps1`), `C:\Users\xyreo\arbex\logs\` |
