# Arbex semantic microservice

The news-intelligence side of Arbex as its own process: live headline ingestion,
FinBERT / keyword sentiment, a linear market-reaction model, RAG evidence retrieval,
and a live sentiment window. The Arbex backend (`backend/core/sentiment_bridge.py`)
talks to it **only over HTTP**; nothing in the backend imports this code.

Moved here from the sibling `news_stream` repository on 2026-10-24 (allowlist copy:
code, tests, scripts, the small training table and the weights-only model — no
secrets, caches or runtime history).

```
 RSS / Finnhub / official feeds
            │
   services/semantic  (:9000)   ── /v1/feed (SSE) ──►  backend SentimentBridge (:8000)
   FinBERT + reaction model     ◄─ POST /v1/analyze ──   one call per headline,
   + RAG evidence                    {currency_pair,       all study pairs requested
                                      currency_pairs}
                                ── pair_scores ───────►  per-pair history + window bias
                                                         + live_predictions.jsonl
                                                                │
                                                 semantic_engine (Redis scorer)
                                                 and the dashboard (arbex-web)
```

## Run

```bash
pip install -r requirements.txt          # core: keyword sentiment + linear model
pip install -r requirements-ml.txt       # optional: FinBERT, sentence-transformers (several GB)
cp config.toml.example config.toml       # optional: API keys (config.toml is gitignored)

run.bat                                   # Windows   (repo root: run_semantic_service.bat)
./run.sh                                  # macOS / Linux
run.bat --use-finbert --use-rag           # heavier models; one heavy process at a time on a laptop
```

Start this before the backend. If it is down the backend degrades instead of failing:
the dashboard serves the mirrored runtime cache plus direct RSS headlines
(`backend/core/news_fallback.py`), clearly labelled as a fallback.

Optional containers: `docker compose up --build` from the repo root
(`docker-compose.yml`, `Dockerfile` here). The plain scripts are lighter on a laptop.

## Contract

Every route answers both bare (`/analyze`) and versioned (`/v1/analyze`). Clients use
`/v1`; the bare paths remain for older callers.

| Route | Method | Purpose |
|---|---|---|
| `/v1/health` | GET | `health`, resolved `runtime` flags, and a `service` block: `api_version`, `started_at`, `uptime_seconds`, `model_loaded`, served `artifact` (path, sha256 prefix, family, weights_only, training_row_count, horizons), `features` |
| `/v1/analyze` | POST | One article. `currency_pair` = primary pair; optional `currency_pairs` (≤ 12) scores more pairs in the **same** FinBERT pass. Response `result.pair_scores[pair]` = `market_score`, `confidence`, `direction`, `magnitude`, `horizon_minutes` per pair |
| `/v1/feed` | GET | Server-sent events, one live headline per event |
| `/v1/sentiment/live` | GET | Aggregated live window for `pair`, `window_minutes` |
| `/v1/sentiment/bias`, `/v1/history`, `/v1/metrics`, `/v1/evaluation`, `/v1/correlation` | GET | Dashboard and model telemetry |
| `/v1/currency-index` | GET | Per-currency market bias + per-pair relative bias (H6 formula), with `SEMANTIC_CURRENCY_BIAS=1`; otherwise 501 `NOT_IMPLEMENTED` |
| `/v1/calendar/upcoming` | GET | Cached live economic calendar: `currencies` (comma list), `hours` (≤ 168), `include_unknown`. With `SEMANTIC_CURRENCY_BIAS=1` (and `SEMANTIC_CALENDAR` not `0`); otherwise 501 |

Article-level fields (`market_score`, `trading_signal`, risk flags) describe the
primary pair only. Pairs are never averaged: EUR/USD falling and USD/JPY rising are the
same dollar move.

## Configuration

Precedence: CLI flag > environment variable > `config.toml` > default.

| Variable | Default | Meaning |
|---|---|---|
| `SEMANTIC_HOST` / `SEMANTIC_PORT` | `127.0.0.1` / `9000` | Bind address |
| `SEMANTIC_CONFIG` | `./config.toml` | Config file (API keys; never committed) |
| `SEMANTIC_REACTION_ARTIFACT` | `artifacts/pooled_multipair/reaction_model_artifact.json` | Served reaction model (pooled multi-pair, USD-oriented; rebuild: `scripts/export_calendar_reaction_rows.py` then `scripts/train_pooled_reaction_model.py`; the USD/INR-only model is `artifacts/usdinr_targeted_expanded/…`) |
| `SEMANTIC_TRAINING_TABLE` | `data/usdinr_reaction_training_rows_targeted_expanded.jsonl` | RAG / correlation table |
| `SEMANTIC_HISTORY_PATH` | `artifacts/history/analysis_history.jsonl` | Analysis history (gitignored) |
| `SEMANTIC_CURRENCY_BIAS` | off | Serve `/v1/currency-index` from the live tracker (alias `SEMANTIC_ENABLE_CURRENCY_INDEX`); reported in `/health.service.features.currency_index` |
| `SEMANTIC_CURRENCY_BIAS_HORIZON` | `artifacts/h6_horizon.json` | Horizon-output file written by `research/run_h6.py`; until it exists the horizon is "none established" |
| `SEMANTIC_CALENDAR` | on (when the currency index is on) | `0` disables the live economic calendar while keeping the currency index |
| `SEMANTIC_CALENDAR_DIR` | `%LOCALAPPDATA%\arbex\calendar` | Calendar cache (follows `ARBEX_CACHE_DIR` if set) |
| `ARBEX_CACHE_DIR` | `%LOCALAPPDATA%\arbex` | Root for regenerable caches (embeddings, calendar), kept out of OneDrive |
| `NEWS_STREAM_USE_RAG`, `NEWS_STREAM_USE_FINBERT`, `NEWS_STREAM_USE_SENTENCE_TRANSFORMER` | off | Model toggles (legacy names kept) |
| `ARBEX_ROOT` | repo root | Where the archive scripts find `sentiment_assets/` |

Backend side (`backend/core/sentiment_bridge.py`):

| Variable | Default | Meaning |
|---|---|---|
| `SEMANTIC_SERVICE_URL` | `http://127.0.0.1:9000` | Service base URL (legacy `NEWS_STREAM_BASE_URL` still honoured) |
| `SEMANTIC_API_PREFIX` | `/v1` | Set `""` to talk to an old unversioned `news_stream` |
| `SEMANTIC_TIMEOUT_MS` | `1200` | Per-request timeout (legacy `NEWS_STREAM_TIMEOUT_MS`) |
| `SEMANTIC_RUNTIME_DIR` | `sentiment_assets/runtime` | Live archives + prediction log (gitignored) |
| `NEWS_STREAM_PAIR_ALLOWLIST` | the five study pairs + AUD/USD | Pairs the bridge files headlines under |

## Live multi-pair behaviour

- Study pairs: USD/INR (primary), EUR/USD, GBP/USD, USD/JPY, USD/CHF
  (`market/pair_assets.py`, mirrored as `STUDY_PAIRS` in the bridge).
- A macro / global / bare-USD headline is filed under every study pair the allowlist
  permits and analysed **once** with all of them requested. A pair-specific headline
  is analysed for that pair only.
- Each pair's history and window bias use that pair's own `pair_scores` entry. A
  secondary pair the service gave no score for is not filed (its headline still
  appears in that pair's feed).
- **Prospective evaluation log:** every filed prediction is appended to
  `sentiment_assets/runtime/predictions/live_predictions.jsonl` — one row per pair with
  `logged_at`, `published_at`, `produced_at`, `event_id`, `headline`, `market_score`,
  `direction`, `magnitude`, `horizon_minutes`, `confidence`, `signal_scope`. Join it to
  later prices to score live predictions out of sample.
- Shipped model: pooled multi-pair, trained on US calendar releases for the majors plus
  the USD/INR rows, USD-oriented, text tokens scoped per pair group (`inr` / `majors`).
  It beats the majority baseline on the calendar holdout only slightly, and on real
  headlines the majors mostly predict neutral; treat its direction as a demo, not evidence.
  Revert to the USD/INR-only model with
  `SEMANTIC_REACTION_ARTIFACT=artifacts/usdinr_targeted_expanded/reaction_model_artifact.json`.

Labelling live archives per pair (tickers from `PAIR_TICKERS`: INR=X, EURUSD=X,
GBPUSD=X, JPY=X, CHF=X):

```bash
python scripts/export_live_archive_event_seeds.py --pair EUR/USD
python scripts/append_live_archive_reaction_rows.py --pair EUR/USD
python scripts/make_weights_only_artifact.py <full_artifact.json> <out.json>   # shrink an old linear artifact
```

## Per-currency market bias (`SEMANTIC_CURRENCY_BIAS=1`)

`src/market/currency_bias_tracker.py` keeps 7 days of scored headlines per currency and
serves, per currency, `bias`/`strength` (z vs the currency's own B history), `label`
(bullish / bearish / neutral), `confidence` = volume × agreement × freshness, `horizon`,
`top_headlines`; and per pair `relative_bias` = bias_base − bias_quote plus a separate
`event_risk` block: the reaction model's recent expected move, and release timing from
the live economic calendar (`next_release`, `last_release`, and `release_proximity`,
which is non-null only from 15 min before to 10 min after a high-impact, time-known
release of either pair currency). The formula is `src/market/currency_bias_core.py`,
byte-identical to `research/semantic/currency_bias_core.py` (a test in each tree checks
the hash). The service fills the open H6 clauses with the labelled
`provisional_settings` reported in every payload (live engineering defaults; the
research uses the logged §6 values), and lists its `live_input_gaps`. The backend weights
opportunities with it only when `ARBEX_CURRENCY_BIAS=1` (`backend/core/currency_bias_channels.py`);
otherwise it is logged in shadow on every opportunity for H6b.

### Live economic calendar (`src/market/economic_calendar.py`)

- Source: the free ForexFactory weekly JSON (`nfs.faireconomy.media/ff_calendar_thisweek.json`).
  Fields: `title`, `country` (currency code), `date` (ISO, New York wall clock with offset),
  `impact` (Low / Medium / High / Holiday), `forecast`, `previous`. **No actuals.**
- Parsing re-localises every time in America/New_York and counts disagreements with the
  embedded offset (`offset_mismatches`); US high-impact rows are checked against the known
  agency clocks (`usd_clock_agreement`). Holiday rows and New-York-midnight stamps are
  "time unknown" and never produce a proximity.
- Fetched at most once an hour by a background thread, cached atomically; requests read
  memory only. On failure the stale cache is served with `age_seconds` and `last_error`.
- Gaps: current week only (`coverage_end`); no actuals, so no live event surprise.

## Extension points (next phase)

- **Per-article novelty** — add the research novelty score next to `pair_scores` in
  `ArticleAnalysis` (`src/market/models.py`) and the history store, so the bridge can
  log it in the prediction row. Not implemented yet.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

No test touches the network; HTTP is stubbed (`respx`), models are faked, and the
calendar tests use a recorded response (`tests/fixtures/ff_calendar_thisweek_2026-10-25.json`).
