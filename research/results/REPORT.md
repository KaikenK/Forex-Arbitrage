# Arbex - experiment results

_Generated from `research/results/`. Harness: fake-clock, seeded, git `be0bc3f`._

## Headline run (`main`: 30 min, seed 42, 200 ms window, 6 synthetic USD/INR feeds)

| metric | value |
|---|---|
| ticks processed | 540,000 (18,000/min) |
| raw detections | 2,575 (85.83/min) |
| opportunity episodes | 2,280 |
| persistence (episodes) | {'ephemeral': 1931, 'flickering': 232, 'persistent': 117} |
| execution verdicts (detections) | {'viable': 389, 'risky': 2186, 'unlikely': 0} |
| cost filter removes | 84.9% of paper opportunities |
| gross profit (pips) | mean 4.79, median 3.65, max 15.388 |
| by session (detections) | LONDON 142, NEW_YORK 805, TOKYO 1628 |

## Alignment-window ablation (30 min, seed 42)

| window ms | detections/min | % episodes persistent | % detections viable |
|---|---|---|---|
| 20 | 562.7 | 53.7% | 74.8% |
| 50 | 331.03 | 39.2% | 60.6% |
| 100 | 169.67 | 16.5% | 35.2% |
| 200 | 85.83 | 5.1% | 15.1% |
| 500 | 33.3 | 0.0% | 0.0% |

## Seed variance (30 min, 200 ms window)

| seed | detections | episodes | % filtered | persistent episodes |
|---|---|---|---|---|
| 42 | 2,575 | 2,280 | 84.9% | 117 |
| 7 | 2,531 | 2,269 | 86.4% | 89 |
| 123 | 2,561 | 2,316 | 86.9% | 83 |
| 2025 | 2,551 | 2,271 | 85.8% | 115 |
