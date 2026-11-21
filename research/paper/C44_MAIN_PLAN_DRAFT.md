# C44 main analysis plan: DRAFT for the owner's approval

This plan must be logged in `RESEARCH_TRACKER.md` §6 **before the 5 Dec interim look** (C44 amendment). If it isn't logged by then, the main analysis may use only data recorded after the interim.

Nothing in it has been run. No cloud outcome has been examined.

## Sample

- **Span:** cloud recordings (C44) from the first weekday, Monday 2026-11-23 00:00 UTC, to the earlier of:
  - 30 complete weekdays, which is Friday 2027-01-01 21:00 UTC;
  - the owner stopping the recorder or the subscription's credit running out.
- **Thin days:** 25 Dec and 1 Jan are thin-market days. They are kept and reported, not dropped.
- **Gaps:** outages are found by the C40 gap rule (silences over 5 s in the union of hub receipt times) and logged in §8.
- **Units:** day-cluster bootstraps use UTC weekdays.

## Analyses

### A1 (primary): does the news risk penalty improve ranking?

- **Outcome:** H6b's locked next-poll outcome ("still crossed"). One-sided next quotes are dropped, as C19.
- **Comparison:** AUC of the recorded, adjusted `confidence_score` against the detector's `raw_confidence_score`, on the same opportunities.
- **Statistic:** the 95 % CI of the difference, from a day-cluster bootstrap with 2,000 draws and seed 20261025.
- **Decision:** supported if the difference is above 0 and its CI excludes 0. Harmful if the CI lies below 0. Otherwise inconclusive.
- **Reported alongside:** the RQ5 arms computed from the raw score (risk, risk + direction, V1 window, v2) and the realised edge at the next poll.

### A2: news as risk on the cloud recorder (C40 rules, unchanged)

- **C40-1:** the share still crossed in release windows (−5 to +15 min) against matched controls (±1–3 weeks, same clock, clear of releases by 60 min).
  - Releases come from the cloud calendar archive.
  - Controls are allowed only in weeks the archive covers and the recorder recorded.
  - Not computable below 30 release windows with an opportunity, which is likely at 30 weekdays; that gets reported as found.
- **C40-2:** older-leg quote age as a feed-quality check.
- **C40-3:** window `activity_factor`.
- **Statistics:** BH over the three, with one-sided day-cluster bootstrap p-values.
- **Decision:** "news as risk" is supported if C40-1 or C40-3 passes.

### A3: prospective release-model test (V2.1 rules)

- **Data:** release-basis rows of the cloud prediction log, counted once per headline × pair (C36). Synthetic rows are excluded (C27).
- **Outcome:** Yahoo hourly bars, as before.
- **Clustering:** by **release**, not by date, because one USD release fans out to every pair.
- **Reported:**
  - sign accuracy against 50 %;
  - Spearman IC;
  - ECE;
  - the number of distinct releases.
- **Status:** descriptive below 30 releases.

### A4: arrival latency

- **Method:** the 2026-11-02 definitions, on the cloud log and archive.
- **Re-test:** if 10 or more releases are matched, the RQ2 arrival-time re-test runs once at the observed offset a*.

## Rules

- **No changes after the plan is logged:** no outcome is re-specified once logged; any change gets its own §6 entry first.
- **Ordering:** results are logged as read, A1 to A4 in order.
- **The interim does not change this plan.** Interim numbers (5 Dec) are reported separately and never pooled as a separate test.
