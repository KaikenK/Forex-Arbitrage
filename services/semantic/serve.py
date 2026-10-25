"""Semantic microservice: analysis API + SSE news feed + static dashboard.

This is the process the Arbex backend talks to over HTTP (SEMANTIC_SERVICE_URL,
default http://127.0.0.1:9000). Every API route is served both bare (/analyze) and
versioned (/v1/analyze). Configuration: CLI flags > SEMANTIC_* env vars > config.toml
(SEMANTIC_CONFIG, default ./config.toml; only config.toml.example is committed).
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import os
import site
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("SEMANTIC_CONFIG") or (PROJECT_ROOT / "config.toml"))
SRC_PATH = PROJECT_ROOT / "src"
USER_SITE_PATH = Path(site.getusersitepackages())
for import_path in (str(USER_SITE_PATH), str(SRC_PATH)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from api.dashboard_service import DashboardDataService
from api.main import build_app, strip_version_prefix
from api.schemas import API_VERSION
from api.routes.feed import generate_feed_events
from market.background_poller import BackgroundNewsPoller
from market.history_store import JsonlAnalysisHistoryStore
from market.keyword_sentiment_scorer import KeywordSentimentScorer
from market.live_sentiment import LiveSentimentAggregator
from market.runtime_factory import build_runtime_service


def _load_evaluation_from_artifact(artifact_path: Path) -> dict[str, object] | None:
    """Reads evaluation metrics embedded in the reaction artifact."""
    try:
        raw = json.loads(artifact_path.read_text(encoding="utf-8"))
        return raw.get("evaluation")
    except (OSError, json.JSONDecodeError):
        return None


FRONTEND_DIR = PROJECT_ROOT / "frontend"
MIME_OVERRIDES = {".js": "application/javascript", ".mjs": "application/javascript"}

_live_aggregator: LiveSentimentAggregator | None = None
_background_poller: BackgroundNewsPoller | None = None
_sse_scorer: KeywordSentimentScorer | None = None


async def _read_body(receive):
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body", False):
            break
    return body


async def _send_bytes(send, status: int, content_type: str, body: bytes, extra_headers=None):
    headers = [
        [b"content-type", content_type.encode()],
        [b"content-length", str(len(body)).encode()],
        [b"access-control-allow-origin", b"*"],
    ]
    if extra_headers:
        headers.extend(extra_headers)
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


async def _handle_sse(send):
    """Stream SSE events for live news feed, also recording into live sentiment."""
    await send({
        "type": "http.response.start",
        "status": 200,
        "headers": [
            [b"content-type", b"text/event-stream"],
            [b"cache-control", b"no-cache"],
            [b"connection", b"keep-alive"],
            [b"access-control-allow-origin", b"*"],
        ],
    })
    try:
        async for chunk in generate_feed_events():
            await send({"type": "http.response.body", "body": chunk.encode(), "more_body": True})
            # Feed each SSE item into the live sentiment aggregator
            if _live_aggregator is not None:
                try:
                    payload = chunk.strip()
                    if payload.startswith("data: "):
                        item = json.loads(payload[6:])
                        headline = item.get("headline", "")
                        if headline and _sse_scorer is not None:
                            from market.schemas import AnalysisContext, ArticleInput
                            from news.models import SentimentSource, SourceType, UnifiedNewsEvent
                            now = datetime.now(UTC)
                            event = UnifiedNewsEvent(
                                event_id="sse-live", schema_version=1,
                                source=SourceType.RSS, headline=headline,
                                body=None, published_at=now, symbols=[],
                                raw_sentiment=0.0, sentiment_source=SentimentSource.NONE,
                                ingested_at=now,
                            )
                            ctx = AnalysisContext(
                                article=ArticleInput(article_text=headline, headline=headline),
                                event=event,
                            )
                            sentiment = await _sse_scorer.score(ctx)
                            pair = item.get("pair", "USD/INR")
                            await _live_aggregator.record(
                                headline=headline,
                                sentiment_score=sentiment.score,
                                confidence=sentiment.confidence,
                                pair=pair,
                                # Feed events carry "category" (routes/feed.py
                                # _rss_to_event / _finnhub_to_event); reading
                                # "event_category" always fell through to "feed".
                                category=item.get("category", "feed"),
                                source=item.get("source", "SSE"),
                            )
                except (json.JSONDecodeError, KeyError, TypeError):
                    pass
    except (asyncio.CancelledError, ConnectionError):
        pass
    finally:
        await send({"type": "http.response.body", "body": b"", "more_body": False})


def _serve_static(path: str):
    """Resolve a static file from the frontend directory."""
    clean = path.lstrip("/")
    if not clean or clean == "/":
        clean = "index.html"
    file_path = (FRONTEND_DIR / clean).resolve()
    if not str(file_path).startswith(str(FRONTEND_DIR.resolve())):
        return None
    if file_path.is_file():
        return file_path
    return None


async def _handle_lifespan(scope, receive, send):
    """ASGI lifespan handler: start/stop background poller."""
    while True:
        message = await receive()
        if message["type"] == "lifespan.startup":
            if _background_poller is not None:
                await _background_poller.start()
            await send({"type": "lifespan.startup.complete"})
        elif message["type"] == "lifespan.shutdown":
            if _background_poller is not None:
                await _background_poller.stop()
            await send({"type": "lifespan.shutdown.complete"})
            return


def build_full_app(api_app):
    """ASGI app that routes /feed to SSE, /api paths to the API, and everything else to static files."""

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            await _handle_lifespan(scope, receive, send)
            return

        if scope["type"] != "http":
            return

        path = strip_version_prefix(scope.get("path", "/"))
        method = scope.get("method", "GET")

        # CORS preflight
        if method == "OPTIONS":
            await _send_bytes(send, 204, "text/plain", b"", extra_headers=[
                [b"access-control-allow-methods", b"GET, POST, OPTIONS"],
                [b"access-control-allow-headers", b"content-type"],
            ])
            return

        # SSE feed
        if path == "/feed" and method == "GET":
            await _handle_sse(send)
            return

        # API routes
        if path in (
            "/analyze",
            "/history",
            "/health",
            "/metrics",
            "/evaluation",
            "/correlation",
            "/sentiment/live",
            "/sentiment/bias",
            "/currency-index",
            "/calendar/upcoming",
        ):
            await api_app(scope, receive, send)
            return

        # Static files
        static_file = _serve_static(path)
        if static_file:
            content_type = MIME_OVERRIDES.get(static_file.suffix, mimetypes.guess_type(str(static_file))[0] or "application/octet-stream")
            body = static_file.read_bytes()
            await _send_bytes(send, 200, content_type, body)
            return

        await _send_bytes(send, 404, "application/json", json.dumps({"error": "Not found"}).encode())

    return app


def _load_hf_token() -> None:
    """Loads HuggingFace token from config.toml if present and sets HF_TOKEN env var."""
    if os.environ.get("HF_TOKEN"):
        return
    config_path = CONFIG_PATH
    if not config_path.exists():
        return
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        with open(config_path, "rb") as f:
            config = tomllib.load(f)
        token = config.get("huggingface", {}).get("token", "")
        if token:
            os.environ["HF_TOKEN"] = token
            os.environ["HUGGING_FACE_HUB_TOKEN"] = token
    except (OSError, ValueError):
        pass


def _load_config() -> dict[str, object]:
    config_path = CONFIG_PATH
    if not config_path.exists():
        return {}
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        with open(config_path, "rb") as handle:
            loaded = tomllib.load(handle)
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _env_path(name: str, default: Path) -> Path:
    raw_value = os.environ.get(name)
    return Path(raw_value) if raw_value and raw_value.strip() else default


def _artifact_identity(artifact_path: Path) -> dict[str, object]:
    """Identity of the served reaction model, read once at startup for /health."""
    try:
        raw_bytes = artifact_path.read_bytes()
        payload = json.loads(raw_bytes.decode("utf-8"))
    except (OSError, ValueError):
        return {"path": artifact_path.name, "loaded": False}
    return {
        "path": f"{artifact_path.parent.name}/{artifact_path.name}",
        "sha256": hashlib.sha256(raw_bytes).hexdigest()[:16],
        "model_family": payload.get("model_family"),
        "weights_only": bool(payload.get("weights_only", False)),
        "training_row_count": payload.get("training_row_count", len(payload.get("training_rows") or [])),
        "available_horizons": payload.get("available_horizons"),
        "bytes": len(raw_bytes),
    }


def build_service_info(artifact_path: Path, features: dict[str, bool]):
    """Closure for the /health microservice block; uptime is computed per call."""
    started_monotonic = time.monotonic()
    started_at = datetime.now(UTC).isoformat()
    artifact = _artifact_identity(artifact_path)

    def service_info() -> dict[str, object]:
        return {
            "name": "arbex-semantic",
            "api_version": API_VERSION,
            "started_at": started_at,
            "uptime_seconds": round(time.monotonic() - started_monotonic, 1),
            "model_loaded": artifact.get("loaded", True) is not False,
            "artifact": artifact,
            "features": dict(features),
        }

    return service_info


def _env_flag(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().casefold() not in {"0", "false", "no", "off", ""}


def _config_flag(config: dict[str, object], section: str, key: str, default: bool) -> bool:
    section_data = config.get(section)
    if not isinstance(section_data, dict):
        return default
    value = section_data.get(key)
    if isinstance(value, bool):
        return value
    return default


def _config_path(config: dict[str, object], section: str, key: str, default: Path) -> Path:
    section_data = config.get(section)
    if not isinstance(section_data, dict):
        return default
    value = section_data.get(key)
    if not isinstance(value, str) or not value.strip():
        return default
    candidate = Path(value.strip())
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def _build_economic_calendar():
    """Live calendar for release proximity (cached, refreshed at most hourly in a daemon thread).

    SEMANTIC_CALENDAR=0 turns it off while keeping the currency index on.
    """
    if not _env_flag("SEMANTIC_CALENDAR", True):
        return None
    from market.economic_calendar import EconomicCalendar, calendar_cache_dir

    calendar = EconomicCalendar(_env_path("SEMANTIC_CALENDAR_DIR", calendar_cache_dir()))
    calendar.start()
    return calendar


def _build_currency_bias_tracker(embedder, calendar=None):
    """Live H6 tracker. Novelty uses the service's embedder (hashing unless ST is on)."""
    from market.currency_bias_tracker import CurrencyBiasTracker
    from retrieval.embedder import LocalHashingEmbedder

    resolved = embedder or LocalHashingEmbedder(dimensions=256)
    horizon_file = _env_path("SEMANTIC_CURRENCY_BIAS_HORIZON", PROJECT_ROOT / "artifacts" / "h6_horizon.json")
    return CurrencyBiasTracker(resolved.embed, horizon_file=horizon_file,
                               embedder_name="sentence_transformer" if embedder is not None else "hashing",
                               calendar=calendar)


def _build_live_sentiment_scorer(use_finbert: bool):
    if not use_finbert:
        return KeywordSentimentScorer()
    try:
        from market.finbert_sentiment_scorer import FinBERTSentimentScorer
        return FinBERTSentimentScorer()
    except ImportError:
        return KeywordSentimentScorer()


def main():
    _load_hf_token()
    config = _load_config()

    default_use_rag = _env_flag("NEWS_STREAM_USE_RAG", _config_flag(config, "runtime", "use_rag", False))
    default_use_sentence_transformer = _env_flag(
        "NEWS_STREAM_USE_SENTENCE_TRANSFORMER",
        _config_flag(config, "runtime", "use_sentence_transformer", False),
    )
    default_use_finbert = _env_flag("NEWS_STREAM_USE_FINBERT", _config_flag(config, "runtime", "use_finbert", False))
    default_history_path = _env_path(
        "SEMANTIC_HISTORY_PATH",
        _config_path(
            config,
            "storage",
            "analysis_history_path",
            PROJECT_ROOT / "artifacts" / "history" / "analysis_history.jsonl",
        ),
    )

    parser = argparse.ArgumentParser(description="Serve the Forex Intelligence Dashboard")
    parser.add_argument(
        "--reaction-artifact",
        type=Path,
        default=_env_path(
            "SEMANTIC_REACTION_ARTIFACT",
            # Pooled multi-pair model (scripts/train_pooled_reaction_model.py). The earlier
            # USD/INR-only artifact stays at artifacts/usdinr_targeted_expanded/.
            PROJECT_ROOT / "artifacts" / "pooled_multipair" / "reaction_model_artifact.json",
        ),
    )
    parser.add_argument(
        "--training-table",
        type=Path,
        default=_env_path(
            "SEMANTIC_TRAINING_TABLE",
            PROJECT_ROOT / "data" / "usdinr_reaction_training_rows_targeted_expanded.jsonl",
        ),
    )
    parser.add_argument("--host", default=os.environ.get("SEMANTIC_HOST", "127.0.0.1"))
    # 9000, not 8000: Forex-Arbitrage's FastAPI server owns 8000, and its
    # NEWS_STREAM_BASE_URL defaults to http://127.0.0.1:9000. Defaulting to 8000
    # made the two silently fight over the port.
    parser.add_argument("--port", type=int, default=int(os.environ.get("SEMANTIC_PORT", "9000")))
    parser.add_argument(
        "--use-rag",
        action=argparse.BooleanOptionalAction,
        default=default_use_rag,
        help="Enable RAG-based reaction scoring.",
    )
    parser.add_argument(
        "--use-sentence-transformer",
        action=argparse.BooleanOptionalAction,
        default=default_use_sentence_transformer,
        help="Enable dense sentence-transformer retrieval embeddings.",
    )
    parser.add_argument(
        "--use-finbert",
        action=argparse.BooleanOptionalAction,
        default=default_use_finbert,
        help="Use FinBERT transformer for sentiment scoring.",
    )
    parser.add_argument(
        "--analysis-history-path",
        type=Path,
        default=default_history_path,
        help="Persist analyzed article history to this JSONL file.",
    )
    args = parser.parse_args()

    embedder = None
    if args.use_sentence_transformer:
        try:
            from retrieval.sentence_transformer_embedder import SentenceTransformerEmbedder
            embedder = SentenceTransformerEmbedder()
        except ImportError:
            print("Warning: sentence-transformers unavailable, falling back to local hashing embeddings.")
            args.use_sentence_transformer = False

    training_table = args.training_table if args.training_table.exists() else None
    history_store = JsonlAnalysisHistoryStore(args.analysis_history_path.resolve())
    # Per-currency market bias (H6 formula) on /v1/currency-index. Off by default, so the
    # service behaves exactly as before; SEMANTIC_ENABLE_CURRENCY_INDEX is the older alias.
    currency_bias_enabled = _env_flag("SEMANTIC_CURRENCY_BIAS", _env_flag("SEMANTIC_ENABLE_CURRENCY_INDEX", False))
    calendar = _build_economic_calendar() if currency_bias_enabled else None
    tracker = _build_currency_bias_tracker(embedder, calendar) if currency_bias_enabled else None
    tap, observer = tracker.hooks() if tracker is not None else (None, None)
    service = build_runtime_service(
        reaction_artifact_path=args.reaction_artifact.resolve(),
        training_table_path=training_table.resolve() if training_table else None,
        embedder=embedder,
        use_rag=args.use_rag,
        use_finbert=args.use_finbert,
        history_store=history_store,
        analysis_observer=observer,
        sentiment_tap=tap,
    )
    evaluation_payload = _load_evaluation_from_artifact(args.reaction_artifact.resolve())

    # Live sentiment (optional background poller)
    global _live_aggregator, _background_poller, _sse_scorer
    _live_aggregator = LiveSentimentAggregator()
    poller_scorer = _build_live_sentiment_scorer(args.use_finbert)
    _sse_scorer = poller_scorer
    _background_poller = BackgroundNewsPoller(
        aggregator=_live_aggregator,
        sentiment_scorer=poller_scorer,
        poll_interval_seconds=60,
    )

    dashboard_data = DashboardDataService(
        evaluation_payload=evaluation_payload,
        training_table_path=training_table.resolve() if training_table else None,
        live_aggregator=_live_aggregator,
    )
    # Report the flags the server actually resolved (env OR config.toml), so the
    # Arbex status chip stops guessing from its own environment and printing
    # "rag off" while RAG is running.
    runtime_flags = {
        "use_rag": bool(args.use_rag),
        "use_sentence_transformer": bool(args.use_sentence_transformer),
        "use_finbert": bool(args.use_finbert),
    }
    # /currency-index answers 501 unless SEMANTIC_CURRENCY_BIAS is on; the flag is reported
    # either way so the backend can detect the capability.
    service_info = build_service_info(
        args.reaction_artifact.resolve(),
        {**runtime_flags, "currency_index": currency_bias_enabled, "economic_calendar": calendar is not None},
    )
    api_app = build_app(service, dashboard_data, runtime_flags, service_info=service_info,
                        currency_index_provider=tracker.snapshot if tracker is not None else None,
                        calendar=calendar)
    full_app = build_full_app(api_app)

    import uvicorn
    print(f"\n  Dashboard: http://{args.host}:{args.port}/")
    print(f"  API:       http://{args.host}:{args.port}/analyze")
    print(f"  Feed:      http://{args.host}:{args.port}/feed")
    print(f"  Live:      http://{args.host}:{args.port}/sentiment/live")
    print(f"  Bias:      http://{args.host}:{args.port}/sentiment/bias")
    print(f"  History:   {args.analysis_history_path.resolve()}")
    print(f"  RAG: {'ON' if args.use_rag else 'OFF'}  |  Sentence-Transformer: {'ON' if args.use_sentence_transformer else 'OFF'}  |  FinBERT: {'ON' if args.use_finbert else 'OFF'}\n")
    uvicorn.run(full_app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
