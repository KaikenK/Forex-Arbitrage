# USD/INR Dataset Generation Demo

## Goal
Improve the USD/INR reaction-training dataset for the sentiment and market-reaction stack, and document a clear exploration and generation path for a demo.

## Data Exploration
We benchmarked the existing labeled USD/INR tables first to measure the starting point before adding new data.

Baseline benchmark report:
- news_stream/artifacts/benchmark/baseline_report.json

Initial table sizes observed:
- news_stream/data/usdinr_reaction_training_rows.jsonl: 12 rows
- Forex-Arbitrage/sentiment_assets/usdinr/usdinr_reaction_training_rows_targeted_expanded.jsonl: 37 rows

Historical seed-ledger exploration showed that the older macro ledgers in news_stream/data had already effectively collapsed into a 24-event unique seed set, so they were not enough on their own to create a materially larger training table.

## What Was Implemented
### 1. Benchmark surface with SVM + TF-IDF
Script:
- news_stream/experiments/benchmark_reaction_models.py

What it does:
- Inventories labeled JSONL datasets
- Benchmarks knn, linear, and svm_tfidf on the same report format
- Reuses the existing time-ordered split philosophy so comparisons are apples-to-apples

### 2. Live-archive auto-label pipeline pieces
Scripts:
- news_stream/scripts/export_live_archive_event_seeds.py
- news_stream/scripts/append_live_archive_reaction_rows.py

What they do:
- Export persisted Arbex live-analysis archives into timestamped event seeds
- Download intraday USD/INR price bars
- Build leak-safe future price-window labels
- Append only new reaction rows into the main USD/INR table

Important demo note:
- The live archive exporter worked and created a 545-row seed ledger
- The append stage produced 0 new rows today from that ledger because the current live archive is too recent, so future price windows for 60m/240m labeling have not matured yet
- This is expected behavior and is a sign that the auto-labeler is failing closed rather than inventing labels

Outputs:
- Forex-Arbitrage/sentiment_assets/usdinr/usdinr_live_archive_event_seeds.jsonl
- Forex-Arbitrage/sentiment_assets/usdinr/live_archive_append_summary.json

### 3. Official speeches and policy feed adapter
Files:
- news_stream/src/news/ingestion/official_feeds_config.py
- news_stream/src/news/ingestion/official_feeds_ingester.py
- news_stream/src/news/normalizer/official_feeds_normalizer.py
- news_stream/src/news/factory.py
- news_stream/src/news/models.py
- news_stream/src/news/pipeline.py
- news_stream/config.toml
- news_stream/config.toml.example

What it does:
- Adds a dedicated OFFICIAL source family
- Pulls official monetary-policy and speech feeds separately from generic RSS
- Normalizes them through the same unified event pipeline
- Tolerates Federal Reserve feeds that are marked bozo by feedparser but still contain valid entries

### 4. Official-feed seed importer and dataset expansion
Script:
- news_stream/scripts/import_official_feed_event_seeds.py

What it does:
- Pulls the configured official feed URLs
- Converts official speech and policy entries into timestamped USD/INR seed rows
- Classifies them into central-bank, policy, or official categories using the existing context tagger
- Feeds those seeds into the same intraday price-label append script

Outputs:
- Forex-Arbitrage/sentiment_assets/usdinr/usdinr_official_policy_event_seeds.jsonl
- Forex-Arbitrage/sentiment_assets/usdinr/official_feed_append_summary.json

## How The Improved Dataset Was Generated
### Step 1. Benchmark the existing dataset
We measured the starting point so dataset growth would be grounded in actual numbers rather than assumptions.

### Step 2. Try live-archive auto-labeling
We exported live analyzed articles into a seed ledger and attempted intraday price labeling.
Result:
- Good pipeline validation
- No new labels yet because the latest live articles are too fresh for matured forward windows

### Step 3. Backfill from official speeches and policy headlines
We imported official central-bank and policy feeds, converted them into timestamped seed rows, then labeled them against intraday USD/INR price bars.

This produced the actual dataset growth used for the demo.

## Final Dataset Growth
Before expansion:
- Main USD/INR table: 37 rows

Official-feed seed import:
- Official seed rows imported: 65

Official-feed price labeling:
- Candidate rows built: 153
- Rows appended: 153
- Duplicate rows skipped: 0

After expansion:
- Main USD/INR table: 190 rows

## Updated Benchmark Snapshot
Expanded-table benchmark results:
- knn: accuracy 0.4035, macro-F1 0.3772, Brier 0.7438
- linear: accuracy 0.6140, macro-F1 0.5943, Brier 0.5742
- svm_tfidf: accuracy 0.5439, macro-F1 0.3838, Brier 0.5475

Interpretation for the demo:
- The bigger dataset changed the model comparison materially
- The linear baseline is currently the strongest overall direction model on the expanded 190-row table
- The SVM + TF-IDF experiment is now part of the benchmark surface, but it is not yet the winning model on this dataset
- The dataset expansion is therefore the main deliverable today, and the benchmark framework is the evidence trail for evaluating future model upgrades

## Files To Show In The Demo
- Forex-Arbitrage/sentiment_assets/usdinr/usdinr_reaction_training_rows_targeted_expanded.jsonl
- Forex-Arbitrage/sentiment_assets/usdinr/usdinr_official_policy_event_seeds.jsonl
- Forex-Arbitrage/sentiment_assets/usdinr/official_feed_append_summary.json
- Forex-Arbitrage/sentiment_assets/usdinr/usdinr_live_archive_event_seeds.jsonl
- Forex-Arbitrage/sentiment_assets/usdinr/live_archive_append_summary.json
- news_stream/artifacts/benchmark/baseline_report.json

## Honest Caveats
- Live-archive auto-labeling is implemented but depends on forward price windows maturing; same-day articles may not be labelable immediately
- Official-feed backfill is the source of the dataset increase shown today
- The SVM + TF-IDF path is benchmark-only for now; it is not promoted into the live route
