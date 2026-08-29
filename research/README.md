# Arbex — research harness

Reproducible offline evaluation of the synthetic USD/INR arbitrage-detection
pipeline. Produces the numbers and figures used in the Phase-3 review.

## What it does

`run_experiment.py` drives the **real production pipeline classes** against the
six session-aware synthetic USD/INR feeds:

```
SessionAwareSyntheticSource x6
  -> TickNormalizer
  -> 200 ms time-windowing  (AlignedTickWindow)
  -> ArbitrageEngine.detect
  -> OpportunityTracker      (ephemeral / flickering / persistent)
  -> SimulatedExecutionFilter (viable / risky / unlikely, L2 + latency costs)
  -> OpportunityRanker.rank_with_context  (composite leaderboard)
  -> MetricsCollector + per-detection log
```

### Determinism

* a fake monotonic clock replaces `time.time` (the pipeline is wall-clock driven);
* the live USD/INR rate fetch is disabled — base price is the fixed
  `USDINR_BASE_PRICE = 86.50`;
* every RNG is seeded (`--seed`), including the `random` module global used by
  `ArbitrageEngine` and the per-source `hash(source_id)` seed (`PYTHONHASHSEED=0`,
  set automatically via a one-time re-exec).

A given `(seed, minutes, step-ms, window-ms)` produces byte-identical
`opportunities.csv` and `summary.json` on any machine.

## Reproduce the review results

```bash
# headline run — 30 min sim, ~15 s wall
python -m research.run_experiment --minutes 30 --seed 42 --run-id main

# seed variance
python -m research.run_experiment --minutes 30 --seed 7    --run-id seed7
python -m research.run_experiment --minutes 30 --seed 123  --run-id seed123
python -m research.run_experiment --minutes 30 --seed 2025 --run-id seed2025

# alignment-window ablation
python -m research.run_experiment --minutes 30 --seed 42 --window-ms 20  --run-id abl_w20
python -m research.run_experiment --minutes 30 --seed 42 --window-ms 50  --run-id abl_w50
python -m research.run_experiment --minutes 30 --seed 42 --window-ms 100 --run-id abl_w100
python -m research.run_experiment --minutes 30 --seed 42 --window-ms 500 --run-id abl_w500

# figures + consolidated table
python -m research.make_figures
```

Outputs land in `research/results/<run-id>/` (`summary.json`, `opportunities.csv`,
`manifest.json`) and `research/results/figures/*.png` + `research/results/REPORT.md`.

## Requirements

```bash
pip install -r requirements.txt      # core
pip install matplotlib pandas pytest # harness + figures + tests
```

## Notes / limitations

* This is the **synthetic research mode** — it exercises detection, persistence,
  feasibility and ranking logic, not real market data. The Path-2 real-feed
  integration is tracked in `docs/SPEC.md`.
* `ArbitrageEngine` also runs via the live server + semantic microservice over
  Redis; this harness isolates the deterministic core so results are reproducible
  without Redis or the semantic engine.
* The 20 ms `TickAligner` async path is covered by `tests/` (the harness uses
  equivalent synchronous windowing so a fake clock can drive it).
