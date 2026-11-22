# Draft tracker entry: window30 volatility control

For the project owner to review and append to `research/RESEARCH_TRACKER.md` section 6 **before**
`python -m research.run_window30_volctl` is run. This file does not edit the tracker. Replace the
date and the code commit when appending. The row format below follows the tracker's decision-log
table.

---

| 2026-11-XX | **C46 PRE-REGISTERED (draft) - volatility control for window30 T2 ("news activity predicts the size of the next move"). Logged before `run_window30_volctl` has been run and before any control variable has been computed.** The activity result replicated out of sample (C45), but neither test controls for volatility that is already running in the news window, so busy news and an already volatile market could produce the same coefficient. This asks how much of the activity coefficient survives such a control. | **Data:** the frozen 2019-10..2025-04 sample and the scores already used by window30 (freeze SHA-256 `d4ef3d7f...`); nothing new is extracted or scored. **Specification:** window30's T2 unchanged (OLS of log abnormal move on log(1 + activity), release flag and pair dummies; SEs clustered by UTC date; h in {30, 60} min; window (t - 45, t - 15]; abnormal move = abs(r) over the median abs(r) at the same UTC clock on the previous 20 weekdays, at least 10; C17 missing-day drop). **Controls, defined now:** (1) *prior abnormal move* = abs(r(t - 45, t - 15)) over the median of the same window at the same clock on the previous 20 weekdays (at least 10); (2) *trailing 24 h level* = the mean of abs(30-minute return) over the 30-minute windows that start within 24 h before t and end at or before t (at least 24 of the 48), divided by the median of the same quantity at the same clock on the previous 20 weekdays (at least 10). Both enter as natural logs; rows where either is undefined are dropped from all four models so the comparison is like for like. **Models:** base (T2), base + prior, base + level, base + both. **Reported:** the activity coefficient, SE and p in each model, the number of rows with and without the controls, and the shrinkage 1 - beta(controlled) / beta(base). **No decision rule and no decision weight:** exploratory, reported as found. The sentence that the activity result is "not yet controlled for realised volatility" stays in the paper until this has been run, and afterwards the paper states the shrinkage whatever its size. **Not tested here:** whether activity predicts moves beyond volatility in the C45 sample (the new data would need the same controls and a separate entry). **Stated now:** the controls use returns that end at or before t, so they use no information from the target window; GKG batch timestamps (15 min) are not modelled. **Code:** `research/run_window30_volctl.py` (commit to be logged), tests `research/tests/test_ieee_assets.py` on synthetic data only (a planted confound shrinks, a planted effect survives). Heavy: loads HistData for all four majors; run on its own, not while the live services are running. |

---

Checks before appending:

- The definitions above match the docstring of `research/run_window30_volctl.py` (they are written
  from it).
- No control variable has been computed on real data. If it has, say so in the entry instead of
  the "before any control" wording.
- If the owner wants a decision rule (for example "the activity effect is reported as surviving
  if beta(+both) > 0 at p < 0.05 at 60 min"), add it here before the run; none is proposed.
