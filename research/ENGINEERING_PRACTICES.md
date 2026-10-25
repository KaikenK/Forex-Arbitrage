# Engineering practices — ML and code standards for this project

Companion to `RESEARCH_TRACKER.md`. That document says *what* we build; this one says
*how*. These are the rules that keep results trustworthy, and they are the rules a panel
will probe hardest, because they are what separates a result from a coincidence.

---

## 1. The leakage rules (non-negotiable)

Leakage is the single most common way a financial ML result turns out to be worthless.
Every rule below is enforced in code, not by convention.

1. **Entry price is the first bar at or after the event timestamp.** You cannot trade on
   information before it is public. Implemented in `PriceSeries.price_at_or_after`.
2. **Market context uses as-of lookups only** (`price_as_of` = last bar ≤ t). No forward fill
   from the future, no interpolation across the event.
3. **Fail closed, never impute.** If a horizon window has not matured, drop that horizon.
   A missing label is honest; a guessed label is fabrication.
4. **Expected-return models fit on a strictly pre-event window** (default 60 trading days),
   and that window must not overlap a prior event's response.
5. **Never shuffle time series.** Walk-forward or expanding-window splits only. A random
   `train_test_split` on time-ordered data invalidates everything downstream.
6. **Fit scalers, embedders, and imputers on training folds only**, then apply to validation.
   Fitting on the full dataset leaks the test distribution into training.
7. **Novelty windows are strictly prior** (`t_j < t_i`), and same-timestamp ties are excluded
   because quantized timestamps make "earlier" unknowable within a bucket.

**Test for it.** Every leakage rule above has a corresponding regression test. A rule
without a test is a rule that will be broken during a refactor.

---

## 2. Statistical practice

- **Cluster standard errors by event**, not by observation. One macro release affecting six
  pairs is ~1 independent observation, not 6. Ignoring this inflates t-statistics.
- **Correct for multiple testing.** A grid of category × horizon cells at α = 0.05 produces
  false positives by construction. Use Benjamini-Hochberg FDR (`eventstudy/stats.py`).
- **Run placebo tests.** Pseudo-events at random timestamps must show no abnormal return.
  If they do, the pipeline is broken. This is the cheapest credibility check available.
- **Report effect sizes with confidence intervals**, not just p-values. "−7.8 bps, 95% CI
  [−12.1, −3.5]" is informative; "p < 0.05" alone is not.
- **Pre-register the decision rule** before looking at results (see tracker §2).
- **Winsorize outliers** and document the threshold. A handful of gap prints can manufacture
  an entire finding.
- **State the sample size everywhere.** An accuracy figure without n is not a result.
  (Ours: 0.667 direction accuracy on n = 12 is 8 of 12 — reported as underpowered, not as a claim.)

---

## 3. Reproducibility

- **Seed everything** and record the seed in the artifact.
- **Immutable raw data.** Downloads land once and are never edited in place; all cleaning
  happens downstream into derived artifacts.
- **Fetch manifests.** Every ingest records source, parameters, and retrieval time
  (`EventStore._record_manifest`).
- **Idempotent pipelines.** Re-running produces the same output; it never double-counts.
  Dedup by stable `event_id`.
- **Resumable long jobs.** Shard plus sentinel, so a multi-hour backfill survives a crash
  and loses at most the in-flight unit.
- **Cache raw payloads, not parsed results.** A cached artifact must decode to exactly what
  a fresh fetch would produce. (We shipped a bug where the cache stored an empty marker and
  every cached hour silently returned zero ticks — hence this rule.)
- **Pin the derived dataset** used for final results, so the write-up's numbers can be regenerated.

---

## 4. Code standards

- **Type hints on public functions.** They document intent and catch category errors.
- **Dataclasses for records**, not bare dicts or tuples. `RawEvent`, `Event`, `ReactionFeatures`.
- **Pure functions where possible.** Anything doing I/O takes an injectable fetch function so
  tests run offline and deterministically.
- **Protocols for swappable components** (`Embedder`, `ExpectedReturnModel`, `EventSentimentScorer`).
  This is what makes ablations cheap: swap the implementation, keep the interface.
- **Comments explain why, not what.** Non-obvious decisions get a reason; obvious code gets none.
- **No silent `except: pass`.** Either handle the error or surface it through a callback.
  A swallowed exception cost us a debugging session when Dukascopy rejected our user agent
  and the pipeline reported "no data" instead of "request refused".
- **Small, named modules.** `alignment.py`, `atlas.py`, `stats.py` — one responsibility each.
- **Configuration over hard-coding.** Horizons, windows, and thresholds are parameters with
  documented defaults.

---

## 5. Testing

- **Test the contract, not the implementation**, so refactors don't rewrite the suite.
- **Every bug gets a named regression test** before the fix is called done.
  (`test_cache_stores_real_bytes_and_reuses_them` exists because of a real bug.)
- **Deterministic and offline.** Inject fetchers; never hit the network in the suite.
- **Test the edges**: empty input, single element, unmatured windows, missing prices,
  timezone boundaries, duplicate identifiers.
- **Verify statistics against a known-value oracle.** BH-FDR is checked against hand-computed
  q-values; the DP solution was validated against brute force.
- **Run the full suite before declaring done** — including across package boundaries. A suite
  that passes per-directory but fails combined is a broken suite.

---

## 6. ML modelling discipline

- **Baselines first, and take them seriously.** Random-walk, constant-mean, and sentiment-only
  baselines. A complex model that cannot beat a linear baseline is a negative result, not a
  tuning problem.
- **Prefer simple models at small n.** With thousands of events and dozens of features, ridge
  and gradient boosting are appropriate; deep sequence models are not.
- **Calibration is separate from accuracy.** A model can rank well and still be overconfident.
  Use temperature scaling / isotonic regression, and report a reliability metric.
- **Report the full confusion picture** for classification, and the residual distribution for
  regression. Aggregate accuracy hides class collapse.
- **Ablate every component.** If novelty is the claim, show the model with and without it.
  An improvement that cannot be attributed is not a finding.
- **Feature provenance.** Every feature must be traceable to information available at the
  event timestamp. If you cannot say when a feature was knowable, delete it.
- **Never tune on the test set.** Validation folds for selection; test touched once, at the end.

---

## 7. Definition of done for a research component

A component is done when all of the following are true:

1. It has a **typed interface** and an injectable dependency for I/O.
2. It has **tests**, including edge cases and any leakage rule it touches.
3. It has been **run on real data** at least once, and the output was inspected — not just
   assumed from a green test.
4. Its **number is recorded** in the tracker's status board.
5. Its **limitations are written down** while they are still fresh.

Green tests alone are not done. Two real bugs in this project (cache poisoning, user-agent
rejection) passed every unit test and were only caught by running against live data.
