# Integrating `news_stream` with Forex-Arbitrage

> **Update 2026-10-24:** `news_stream` now lives in this repo as the semantic
> microservice, `services/semantic/` — see `services/semantic/README.md` for the
> current contract. Routes are versioned (`/v1/...`), the backend's base URL is
> `SEMANTIC_SERVICE_URL` (`NEWS_STREAM_BASE_URL` still works), and macro headlines
> are scored for all five study pairs in one `/v1/analyze` call. The history below
> describes the original sibling-repo integration and is kept for reference.

`news_stream` was a **separate sibling repository** (`../news_stream`). It is the
upstream sentiment / news service. Forex-Arbitrage consumes it over HTTP through
two independent clients:

| Client | File | Purpose |
| --- | --- | --- |
| `SentimentBridge` | `backend/core/sentiment_bridge.py` | Powers the `/sentiment/*` REST endpoints on the :8000 server and the Arbex web workbench. |
| `NewsBias` client | `backend/core/semantic_engine.py` | Redis microservice; adjusts arbitrage opportunity confidence. |

Both default to `NEWS_STREAM_BASE_URL=http://127.0.0.1:9000`.

## Startup order (important)

1. **Start `news_stream` first, on port 9000.** Its launcher defaults to `--port 8000`,
   which **collides** with the Forex-Arbitrage server. You must override the port:

   ```bash
   cd ../news_stream
   python serve.py --port 9000
   ```

2. Start the Forex-Arbitrage server (defaults to :8000):

   ```bash
   run_server.bat            # or: python -m backend.server.main
   ```

3. (Optional) Start the semantic engine microservice (needs Redis):

   ```bash
   run_semantic.bat
   ```

If `news_stream` is not running, `SentimentBridge` degrades gracefully: live
sentiment falls back to a neutral payload and the feed/history endpoints serve
from the last persisted runtime cache.

## Environment variables

| Variable | Default | Notes |
| --- | --- | --- |
| `NEWS_STREAM_BASE_URL` | `http://127.0.0.1:9000` | Base URL of the news_stream service. |
| `NEWS_STREAM_ENABLE` | `true` | Set false to disable all upstream calls (neutral fallback). |
| `NEWS_STREAM_TIMEOUT_MS` | `1200` | Per-request timeout for `/sentiment/live` and `/analyze`. |
| `NEWS_STREAM_FEED_TIMEOUT_SECONDS` | `20` | SSE `/feed` read timeout for the background ingest loop. |
| `NEWS_STREAM_LIVE_CACHE_TTL_SECONDS` | `10` | TTL for the `get_live_sentiment` response cache (successful fetches only). |
| `NEWS_STREAM_PAIR_ALLOWLIST` | `USD/INR,EUR/USD,GBP/USD,USD/JPY,USD/CHF,AUD/USD` | Comma-separated. Only feed events whose `pair` is in this set are tracked; everything else is dropped. At runtime the server also unions in the arbitrage engine's active symbols. |
| `NEWS_STREAM_WINDOW_MINUTES` | `30` | Rolling live-sentiment window. |

## Pair allowlist

The upstream feed's `_detect_pair` regex tags any two hyphenated 3-letter words as
a "pair" (e.g. `wait-and-see` -> `AND/SEE`, `mid-cap` -> `MID/CAP`). Without a
guard these junk pairs each spawned a `sentiment_assets/runtime/<slug>/` directory.

`SentimentBridge` now filters every feed event against `NEWS_STREAM_PAIR_ALLOWLIST`
(`_tracked_pairs_for_feed_event`). Macro-level events (`MACRO`/`GLOBAL`/`RISK`/blank)
collapse to `USD/INR`. The default allowlist mirrors the `news_stream` `[watchlist]`
plus `USD/INR` (the arbitrage engine's only tradable pair in research mode).

## Runtime state

`sentiment_assets/runtime/<slug>/live_feed.jsonl` and `live_analysis.jsonl` are
**regenerated at runtime** by the ingest loop and are gitignored. The
feed/history REST endpoints serve purely from the in-memory cache + these files;
they never open the SSE `/feed` connection inside a request (that path is owned
by the single background ingest thread started at server startup).
