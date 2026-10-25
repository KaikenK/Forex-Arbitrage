from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from typing import Callable
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen

from backend.core.news_fallback import RssHeadlines


def normalize_pair(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("_", "/")
    if "/" in normalized:
        return normalized
    if len(normalized) == 6 and normalized.isalpha():
        return f"{normalized[:3]}/{normalized[3:]}"
    return normalized or "USD/INR"


# USD/INR is the arbitrage engine's pair; the majors mirror the news_stream
# [watchlist] so real upstream headlines for those pairs are still tracked.
# Anything outside this set (junk "pairs" like MID/CAP, AND/SEE) is dropped.
_DEFAULT_PAIR_ALLOWLIST = (
    "USD/INR",
    "EUR/USD",
    "GBP/USD",
    "USD/JPY",
    "USD/CHF",
    "AUD/USD",
)

# The study pairs (research/RESEARCH_TRACKER.md scope, 2026-10-24). A macro/global
# headline is a USD shock to every one of them, so it is analysed ONCE with all of them
# requested, and each pair is filed with its own reaction-model score. USD/INR stays
# first: it is the arbitrage engine's pair, and the service's article-level fields
# describe the first ("primary") pair.
STUDY_PAIRS = ("USD/INR", "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF")
_MACRO_PAIR_TOKENS = {"", "MACRO", "GLOBAL", "RISK", "USD"}

# Path prefix of the semantic microservice's versioned API (services/semantic).
# SEMANTIC_API_PREFIX="" talks to a legacy, unversioned news_stream instead.
DEFAULT_API_PREFIX = "/v1"


# The reaction model emits a raw return (roughly +/-0.005). news_stream's
# analyzer._trading_signal rescales it by 200 onto a +/-1 scale before applying
# thresholds; we use the same factor so both codebases agree on what "strong" means.
MARKET_SCORE_NORMALIZER = 200.0

# 0.0005 is the artifact's direction_neutral_threshold; x200 puts it on the
# normalized scale, so a bar counts as directional only if the reaction model
# would itself have called a direction rather than neutral.
MARKET_DIRECTION_THRESHOLD = 0.1

# The window is treated as "full" at this many analysed articles; below it the
# bias is scaled down proportionally so a single article cannot swing the engine.
ACTIVITY_SATURATION_COUNT = 5.0

# news_stream caps /correlation at 100 rows per page.
CORRELATION_PAGE_SIZE = 100
MAX_CORRELATION_PAGES = 10


def _bias_direction(semantic_score: float) -> str:
    """Map an activity-damped bias onto risk_on / risk_off / neutral.

    Defined here so the REST payload, the semantic engine and the dashboard all
    read one implementation instead of three that can drift apart.
    """
    if semantic_score > MARKET_DIRECTION_THRESHOLD:
        return "risk_on"
    if semantic_score < -MARKET_DIRECTION_THRESHOLD:
        return "risk_off"
    return "neutral"


def _resolve_pair_allowlist() -> set[str]:
    """Build the tracked-pair allowlist, overridable via NEWS_STREAM_PAIR_ALLOWLIST."""
    raw = os.environ.get("NEWS_STREAM_PAIR_ALLOWLIST")
    if raw and raw.strip():
        configured = {normalize_pair(part) for part in raw.split(",") if part.strip()}
        if configured:
            return configured
    return {normalize_pair(pair) for pair in _DEFAULT_PAIR_ALLOWLIST}


class SentimentBridge:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        enabled: bool | None = None,
        timeout_ms: int | None = None,
        clock: Callable[[], datetime] | None = None,
        rss: RssHeadlines | None = None,
        pair_allowlist: set[str] | None = None,
    ) -> None:
        self._enabled = enabled if enabled is not None else _env_flag("NEWS_STREAM_ENABLE", True)
        # SEMANTIC_SERVICE_URL is the microservice contract; NEWS_STREAM_BASE_URL is the
        # legacy name, still honoured so existing .env files keep working.
        self._base_url = (
            base_url
            or os.environ.get("SEMANTIC_SERVICE_URL")
            or os.environ.get("NEWS_STREAM_BASE_URL", "http://127.0.0.1:9000")
        ).rstrip("/")
        self._api_prefix = os.environ.get("SEMANTIC_API_PREFIX", DEFAULT_API_PREFIX).rstrip("/")
        self._timeout_ms = timeout_ms or int(
            os.environ.get("SEMANTIC_TIMEOUT_MS") or os.environ.get("NEWS_STREAM_TIMEOUT_MS", "1200")
        )
        self._feed_timeout_seconds = float(os.environ.get("NEWS_STREAM_FEED_TIMEOUT_SECONDS", "20"))
        self._live_cache_ttl_seconds = float(os.environ.get("NEWS_STREAM_LIVE_CACHE_TTL_SECONDS", "10"))
        self._pair_allowlist = pair_allowlist if pair_allowlist is not None else _resolve_pair_allowlist()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._root = Path(__file__).resolve().parents[2]
        runtime_override = os.environ.get("SEMANTIC_RUNTIME_DIR", "").strip()
        self._runtime_history_root = Path(runtime_override) if runtime_override else self._root / "sentiment_assets" / "runtime"
        self._history_cache: dict[Path, list[dict[str, object]]] = {}
        self._article_tape: dict[str, dict[str, dict[str, Any]]] = {}
        self._feed_items: dict[str, list[dict[str, Any]]] = {}
        self._analysis_history: dict[str, list[dict[str, Any]]] = {}
        self._analysis_by_event_id: dict[str, dict[str, Any]] = {}
        self._runtime_cache_loaded: set[str] = set()
        self._ingest_thread: threading.Thread | None = None
        self._ingest_stop = threading.Event()
        self._ingest_started = threading.Event()
        self._state_lock = threading.RLock()
        self._last_error: str | None = None
        self._last_latency_ms: float | None = None
        self._last_success_at: datetime | None = None
        # One job per headline: (pairs it is filed under, primary-pair event).
        self._analysis_queue: queue.Queue[tuple[tuple[str, ...], dict[str, Any]]] = queue.Queue(maxsize=512)
        self._analysis_worker: threading.Thread | None = None
        self._live_cache: dict[tuple[str, int], tuple[datetime, dict[str, Any]]] = {}
        # /health is polled only from get_status (the dashboard status chip), never
        # from the arbitrage hot path, and cached so a 15s UI poll is not 15s of probes.
        self._health_cache: tuple[datetime, dict[str, Any]] | None = None
        self._health_cache_ttl_seconds = float(os.environ.get("NEWS_STREAM_HEALTH_CACHE_TTL_SECONDS", "30"))
        # Direct RSS headlines when news_stream is not running (see news_fallback.py)
        self._rss = rss or RssHeadlines()

    def _rss_fallback(self, pair: str, limit: int = 12) -> list[dict[str, Any]]:
        if not RssHeadlines.enabled():
            return []
        try:
            return self._rss.for_pair(pair, limit=limit)
        except Exception as exc:
            self._rss.last_error = str(exc)
            return []

    def _rss_live_payload(self, pair: str, window_minutes: int) -> dict[str, Any]:
        """Live window built from direct RSS headlines (keyword direction, low confidence)."""
        payload = _neutral_live_payload(pair, window_minutes)
        items = self._rss_fallback(pair)
        if not items:
            return payload
        scores = [float(it["sentiment"]) for it in items]
        payload.update({
            "overall_sentiment": round(sum(scores) / len(scores), 4),
            "confidence": 0.3,   # keyword heuristic, not the news_stream model
            "bullish_count": sum(1 for x in scores if x > 0),
            "bearish_count": sum(1 for x in scores if x < 0),
            "neutral_count": sum(1 for x in scores if x == 0),
            "item_count": len(items),
            "top_drivers": [{"headline": it["headline"], "sentiment": it["sentiment"],
                             "source": it["source"], "category": it["category"]} for it in items],
            "status": "rss_fallback",
            "reason": "news_stream offline: headlines fetched directly from RSS, direction from a keyword heuristic",
        })
        return payload

    def start_live_ingest(self) -> None:
        if not self._enabled or not self._base_url:
            return
        self._ingest_stop.clear()
        # The analysis worker drains the /analyze queue off the SSE read path so a
        # bursty feed can't backpressure ingestion.
        if self._analysis_worker is None or not self._analysis_worker.is_alive():
            self._analysis_worker = threading.Thread(
                target=self._run_analysis_worker, name="SentimentBridgeAnalysis", daemon=True
            )
            self._analysis_worker.start()
        if self._ingest_thread is not None and self._ingest_thread.is_alive():
            return
        self._ingest_thread = threading.Thread(target=self._run_live_ingest_loop, name="SentimentBridgeLiveIngest", daemon=True)
        self._ingest_thread.start()

    def stop_live_ingest(self) -> None:
        self._ingest_stop.set()
        thread = self._ingest_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._ingest_thread = None
        worker = self._analysis_worker
        if worker is not None and worker.is_alive():
            worker.join(timeout=2.0)
        self._analysis_worker = None
        self._ingest_started.clear()

    def _record_success(self, latency_ms: float) -> None:
        with self._state_lock:
            self._last_latency_ms = latency_ms
            self._last_success_at = self._clock()
            self._last_error = None

    def _record_error(self, message: str) -> None:
        with self._state_lock:
            self._last_error = message

    def _record_stream_activity(self) -> None:
        """Mark the service live because an SSE event arrived.

        Deliberately does not touch ``_last_latency_ms``: that figure describes
        request/response round trips, and a streamed event has no comparable
        latency. It only needs to establish liveness and clear a stale error.
        """
        with self._state_lock:
            self._last_success_at = self._clock()
            self._last_error = None

    def get_health_snapshot(self) -> dict[str, Any]:
        """Cheap, network-free status for the server /health endpoint."""
        with self._state_lock:
            last_error = self._last_error
            last_success_at = self._last_success_at
        ingest = self._ingest_thread
        worker = self._analysis_worker
        return {
            "enabled": self._enabled,
            "base_url": self._base_url,
            "available": last_success_at is not None and last_error is None,
            "last_success_at": _isoformat(last_success_at),
            "last_error": last_error,
            "ingest_active": ingest is not None and ingest.is_alive(),
            "analysis_worker_active": worker is not None and worker.is_alive(),
            "analysis_queue_depth": self._analysis_queue.qsize(),
            "tracked_pairs": sorted(self._pair_allowlist),
        }

    def _service_block(self) -> dict[str, Any]:
        """Purely local view of upstream health. Does no I/O.

        Hot paths (get_window_bias, get_live_sentiment) embed this, so it must
        never make a network call -- that is what get_status's /health probe is for.
        """
        with self._state_lock:
            last_error = self._last_error
            last_success_at = self._last_success_at
            last_latency_ms = self._last_latency_ms
        return {
            "enabled": self._enabled,
            "base_url": self._base_url,
            "available": last_success_at is not None and last_error is None,
            "last_success_at": _isoformat(last_success_at),
            "last_error": last_error,
            "last_latency_ms": round(last_latency_ms, 2) if last_latency_ms is not None else None,
        }

    def get_status(self, pair: str) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        dataset = self.get_dataset_info(normalized_pair)
        runtime = self._runtime_flags()
        return {
            "pair": normalized_pair,
            "news_stream": self._service_block(),
            "runtime": runtime,
            "dataset": dataset,
        }

    def _runtime_flags(self) -> dict[str, Any]:
        """Resolve upstream feature flags, preferring what the service reports.

        The service resolves use_rag from env OR its own config.toml, so reading
        only our environment reported "rag off" while RAG was in fact running.
        /health now returns the resolved flags; env vars stay as the fallback for
        when the probe fails or the service predates that field.
        """
        env_rag = _env_flag("NEWS_STREAM_USE_RAG", False) or _env_flag("NEWS_STREAM_RAG_ENABLED", False)
        flags: dict[str, Any] = {
            "rag_enabled": env_rag,
            "window_minutes": int(os.environ.get("NEWS_STREAM_WINDOW_MINUTES", "30")),
            "flags_source": "environment",
        }
        health = self._probe_health()
        runtime = health.get("runtime") if isinstance(health.get("runtime"), dict) else None
        if runtime:
            flags["rag_enabled"] = bool(runtime.get("use_rag", env_rag))
            flags["sentence_transformer_enabled"] = bool(runtime.get("use_sentence_transformer", False))
            flags["finbert_enabled"] = bool(runtime.get("use_finbert", False))
            flags["flags_source"] = "news_stream/health"
        return flags

    def _probe_health(self) -> dict[str, Any]:
        """Cheap cached GET /health. Never raises; a dead service just returns {}."""
        if not self._enabled or not self._base_url:
            return {}
        now = self._clock()
        with self._state_lock:
            cached = self._health_cache
            if cached is not None and (now - cached[0]).total_seconds() <= self._health_cache_ttl_seconds:
                return dict(cached[1])
        try:
            payload = self._fetch_json("/health", {}, record=False)
        except Exception:
            return {}
        with self._state_lock:
            self._health_cache = (now, dict(payload))
        return payload

    def get_live_sentiment(self, pair: str, window_minutes: int) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        if not self._enabled or not self._base_url:
            payload = self._rss_live_payload(normalized_pair, window_minutes)
            payload["service"] = self._service_block()
            return payload

        cache_key = (normalized_pair, window_minutes)
        now = self._clock()
        with self._state_lock:
            cached = self._live_cache.get(cache_key)
            if cached is not None and (now - cached[0]).total_seconds() <= self._live_cache_ttl_seconds:
                return dict(cached[1])

        try:
            payload = self._fetch_json(
                "/sentiment/live",
                {
                    "pair": normalized_pair,
                    "window_minutes": window_minutes,
                },
            )
            fetch_succeeded = True
        except Exception:
            # news_stream unreachable: direct RSS headlines if available, else a
            # neutral window; the reason stays visible in service.last_error.
            payload = self._rss_live_payload(normalized_pair, window_minutes)
            fetch_succeeded = False
        payload["pair"] = normalize_pair(str(payload.get("pair", normalized_pair)))
        payload["service"] = self._service_block()
        # Only cache successful fetches so a transient outage recovers immediately.
        if fetch_succeeded:
            with self._state_lock:
                self._live_cache[cache_key] = (now, dict(payload))
        return payload

    def get_service_metrics(self) -> dict[str, Any]:
        """Proxy GET /metrics: upstream throughput and model telemetry."""
        return self._proxy_get("/metrics", {}, "metrics")

    def get_model_evaluation(self) -> dict[str, Any]:
        """Proxy GET /evaluation: the LIVE model's evaluation block.

        Preferred over reading the mirrored artifact, which silently goes stale
        the moment the service is retrained or pointed at a different artifact.
        """
        return self._proxy_get("/evaluation", {}, "evaluation")

    def get_correlation(self, offset: int = 0, limit: int = 200) -> dict[str, Any]:
        """Proxy GET /correlation, paginating to gather the full labelled set.

        Upstream caps a page at 100 rows and returns newest-first. The rows that
        carry a standardized_surprise are the *scheduled* calendar releases, which
        are the oldest in the table -- a single page is therefore all unscheduled
        headlines with a null surprise, and the surprise/reaction scatter comes back
        empty. Page through instead of trusting one request.
        """
        collected: list[dict[str, Any]] = []
        total = 0
        page_offset = max(0, offset)
        target = max(1, limit)
        for _ in range(MAX_CORRELATION_PAGES):
            page = self._proxy_get(
                "/correlation",
                {"offset": page_offset, "limit": CORRELATION_PAGE_SIZE},
                "correlation",
            )
            if not page.get("available"):
                return page if not collected else _correlation_payload(collected, total, offset, target, self._service_block())
            body = page.get("correlation") or {}
            items = body.get("items") if isinstance(body.get("items"), list) else []
            total = _safe_int(body.get("total")) or total
            if not items:
                break
            collected.extend(items)
            page_offset += len(items)
            if len(collected) >= target or page_offset >= total:
                break
        return _correlation_payload(collected[:target], total, offset, target, self._service_block())

    def get_benchmark_report(self) -> dict[str, Any]:
        """Read the model-selection benchmark for the shipped model, if one exists.

        Not an upstream call: the report is a build artifact read from disk, so it is
        available whether or not the service is running. The pooled multi-pair model's
        training_report.json (in services/semantic) comes first; an older
        baseline_report.json under sentiment_assets is the fallback. Paths outside the
        repository (the former sibling news_stream folder) are no longer consulted.
        """
        pooled = self._root / "services" / "semantic" / "artifacts" / "pooled_multipair" / "training_report.json"
        try:
            if pooled.exists():
                report = json.loads(pooled.read_text(encoding="utf-8"))
                if isinstance(report, dict) and isinstance(report.get("test"), dict):
                    return {"available": True, "path": str(pooled), "report": _summarize_pooled_report(report)}
        except (OSError, json.JSONDecodeError):
            pass
        candidates = [
            self._root / "sentiment_assets" / "benchmark" / "baseline_report.json",
        ]
        for path in candidates:
            try:
                if not path.exists():
                    continue
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(report, dict):
                return {"available": True, "path": str(path), "report": _summarize_benchmark(report)}
        return {
            "available": False,
            "path": None,
            "report": None,
            "detail": "No baseline_report.json found; run experiments/benchmark_reaction_models.py.",
        }

    def _proxy_get(self, path: str, query: dict[str, Any], label: str) -> dict[str, Any]:
        """Fetch an upstream JSON route, degrading to an explicit unavailable payload.

        Mirrors get_live_sentiment's contract: never raise at the REST boundary, so
        one dead upstream route cannot blank the panels that read from local disk.
        """
        if not self._enabled or not self._base_url:
            return {"available": False, label: None, "detail": "news_stream disabled.", "service": self._service_block()}
        try:
            payload = self._fetch_json(path, query)
        except Exception as exc:
            return {"available": False, label: None, "detail": str(exc), "service": self._service_block()}
        return {"available": True, label: payload, "service": self._service_block()}

    def fetch_live_window(self, pair: str, window_minutes: int) -> dict[str, Any]:
        """Raw /sentiment/live payload, raising on failure.

        For consumers that implement their own fallback (e.g. the semantic engine's
        NewsBias stale-cache path) and so need to distinguish success from failure.

        Honours the enable flag: NEWS_STREAM_ENABLE=false must mean *no* upstream
        traffic, and callers treat the raise as "unavailable" and fall back.
        """
        if not self._enabled or not self._base_url:
            raise RuntimeError("news_stream is disabled (NEWS_STREAM_ENABLE=false).")
        return self._fetch_json(
            "/sentiment/live",
            {"pair": normalize_pair(pair), "window_minutes": window_minutes},
        )

    def get_window_bias(self, pair: str, window_minutes: int) -> dict[str, Any]:
        """Aggregate locally mirrored analyses into a directional window bias.

        The reaction model's ``market_score`` is the directional term here, NOT the
        raw headline sentiment. FinBERT is trained on equity/corporate text, so for
        a quote like USD/INR it reads "rupee plunges" as strongly negative when the
        tradable implication is that the pair *rises*. The reaction model was trained
        on realised USD/INR returns and so already encodes that inversion.

        Returns a payload shaped like ``/sentiment/live`` so the semantic engine can
        consume either source, plus ``market_bias`` and ``source`` for provenance.
        """
        normalized_pair = normalize_pair(pair)
        self._ensure_runtime_cache_loaded(normalized_pair)
        resolved_window = max(1, window_minutes)
        now = self._clock()
        cutoff = now - timedelta(minutes=resolved_window)

        with self._state_lock:
            history = [dict(item) for item in self._analysis_history.get(normalized_pair, [])]

        total_weight = 0.0
        weighted_market = 0.0
        weighted_sentiment = 0.0
        weighted_confidence = 0.0
        bullish_count = 0
        bearish_count = 0
        neutral_count = 0
        in_window: list[tuple[float, dict[str, Any]]] = []

        for analysis in history:
            produced_at = _parse_iso_datetime(analysis.get("produced_at"))
            if produced_at is None:
                feed = analysis.get("feed") if isinstance(analysis.get("feed"), dict) else {}
                produced_at = _parse_iso_datetime(feed.get("timestamp"))
            if produced_at is None or produced_at < cutoff:
                continue

            confidence = _clamp(_safe_float(analysis.get("confidence")), 0.0, 1.0)
            age_minutes = max(0.0, (now - produced_at).total_seconds() / 60.0)
            # Same recency shape as news_stream's LiveSentimentAggregator.aggregate,
            # so switching sources cannot silently change the weighting.
            recency_weight = max(0.1, 1.0 - age_minutes / resolved_window)
            weight = recency_weight * confidence
            if weight <= 0.0:
                continue

            normalized_market = _clamp(
                _safe_float(analysis.get("market_score")) * MARKET_SCORE_NORMALIZER, -1.0, 1.0
            )
            sentiment_score = _clamp(_safe_float(analysis.get("sentiment_score")), -1.0, 1.0)

            weighted_market += normalized_market * weight
            weighted_sentiment += sentiment_score * weight
            weighted_confidence += confidence * weight
            total_weight += weight
            in_window.append((abs(normalized_market), analysis))

            if normalized_market > MARKET_DIRECTION_THRESHOLD:
                bullish_count += 1
            elif normalized_market < -MARKET_DIRECTION_THRESHOLD:
                bearish_count += 1
            else:
                neutral_count += 1

        if total_weight <= 0.0:
            payload = _neutral_live_payload(normalized_pair, resolved_window)
            payload["market_bias"] = 0.0
            payload["source"] = "local_analysis_history"
            payload["service"] = self._service_block()
            return payload

        in_window.sort(key=lambda entry: entry[0], reverse=True)
        top_drivers: list[dict[str, str]] = []
        for _, analysis in in_window[:5]:
            feed = analysis.get("feed") if isinstance(analysis.get("feed"), dict) else {}
            top_drivers.append(
                {
                    "headline": str(feed.get("headline", "")),
                    "sentiment": f"{_safe_float(analysis.get('sentiment_score')):+.2f}",
                    "market": f"{_safe_float(analysis.get('market_score')):+.5f}",
                    "signal": str(analysis.get("trading_signal", "")),
                    "source": str(feed.get("source", "unknown")),
                }
            )

        market_bias = _clamp(weighted_market / total_weight, -1.0, 1.0)
        # A thin window is a weak signal even when the few articles in it agree, so
        # the bias is scaled by how full the window is before a direction is called.
        # This MUST happen here rather than in each consumer: the dashboard was
        # deriving its direction from the raw market_bias while the arbitrage engine
        # applied the damped score, so the panel could read RISK OFF while the engine
        # saw NEUTRAL. semantic_score is now the single directional number.
        activity_factor = min(1.0, len(in_window) / ACTIVITY_SATURATION_COUNT)
        semantic_score = _clamp(market_bias * activity_factor, -1.0, 1.0)

        return {
            "pair": normalized_pair,
            "market_bias": round(market_bias, 4),
            "activity_factor": round(activity_factor, 4),
            "semantic_score": round(semantic_score, 4),
            "direction": _bias_direction(semantic_score),
            "overall_sentiment": round(_clamp(weighted_sentiment / total_weight, -1.0, 1.0), 4),
            "confidence": round(_clamp(weighted_confidence / total_weight, 0.0, 1.0), 4),
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "neutral_count": neutral_count,
            "item_count": len(in_window),
            "top_drivers": top_drivers,
            "updated_at": _isoformat(now),
            "window_minutes": resolved_window,
            "source": "local_analysis_history",
            "service": self._service_block(),
        }

    def get_live_articles(self, pair: str, window_minutes: int, limit: int = 12) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        live_payload = self.get_live_sentiment(normalized_pair, window_minutes)
        cache = self._article_tape.setdefault(normalized_pair, {})
        now = self._clock()
        active_ids: set[str] = set()
        current_window_confidence = _safe_float(live_payload.get("confidence"))
        current_window_item_count = max(0, _safe_int(live_payload.get("item_count")))
        activity_factor = min(1.0, current_window_item_count / 5.0) if current_window_item_count > 0 else 0.0

        for raw_driver in _top_drivers(live_payload.get("top_drivers"), limit=limit):
            headline = str(raw_driver.get("headline", "")).strip()
            if not headline:
                continue
            article_id = _article_id(normalized_pair, headline)
            active_ids.add(article_id)
            sentiment = _parse_signed_float(raw_driver.get("sentiment"))
            cached = cache.get(article_id)
            if cached is None:
                cache[article_id] = {
                    "id": article_id,
                    "headline": headline,
                    "source": str(raw_driver.get("source", "unknown")),
                    "category": str(raw_driver.get("category", "unknown")),
                    "sentiment": sentiment,
                    "first_seen_at": now,
                    "last_seen_at": now,
                    "observations": 1,
                }
                continue
            cached["source"] = str(raw_driver.get("source", cached.get("source", "unknown")))
            cached["category"] = str(raw_driver.get("category", cached.get("category", "unknown")))
            cached["sentiment"] = sentiment
            cached["last_seen_at"] = now
            cached["observations"] = int(cached.get("observations", 0)) + 1

        stale_before = now - timedelta(hours=12)
        stale_ids = [
            article_id
            for article_id, cached in cache.items()
            if _cached_timestamp(cached.get("last_seen_at")) < stale_before
        ]
        for article_id in stale_ids:
            cache.pop(article_id, None)

        sorted_items = sorted(
            cache.values(),
            key=lambda item: _cached_timestamp(item.get("last_seen_at")),
            reverse=True,
        )

        payload_items: list[dict[str, Any]] = []
        for cached in sorted_items[:limit]:
            sentiment = _safe_float(cached.get("sentiment"))
            impact_score = round(abs(sentiment) * current_window_confidence * activity_factor, 4)
            payload_items.append(
                {
                    "id": str(cached.get("id")),
                    "headline": str(cached.get("headline", "")),
                    "source": str(cached.get("source", "unknown")),
                    "category": str(cached.get("category", "unknown")),
                    "sentiment": round(sentiment, 4),
                    "impact_score": impact_score,
                    "impact_label": _impact_label(impact_score),
                    "impact_direction": _impact_direction(sentiment),
                    "active_in_window": str(cached.get("id")) in active_ids,
                    "observations": int(cached.get("observations", 0)),
                    "first_seen_at": _isoformat(_cached_timestamp(cached.get("first_seen_at"))),
                    "last_seen_at": _isoformat(_cached_timestamp(cached.get("last_seen_at"))),
                    "window_confidence": round(current_window_confidence, 4),
                    "window_item_count": current_window_item_count,
                }
            )

        return {
            "pair": normalized_pair,
            "window_minutes": window_minutes,
            "items": payload_items,
            "summary": {
                "current_window_confidence": round(current_window_confidence, 4),
                "current_window_item_count": current_window_item_count,
                "active_items": len(active_ids),
                "tracked_items": len(cache),
            },
            "service": live_payload.get("service", self._service_block()),
        }

    def get_feed_snapshot(self, pair: str, limit: int = 20) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        self._ensure_runtime_cache_loaded(normalized_pair)
        with self._state_lock:
            items = [dict(item) for item in self._feed_items.get(normalized_pair, [])[: max(1, limit)]]
        news_stream_down = not self._enabled or self._last_error is not None or self._last_success_at is None
        if news_stream_down:
            # today's headlines first, then whatever was cached from earlier news_stream runs
            fresh = [{
                "event_id": it["event_id"], "headline": it["headline"], "source": it["source"],
                "pair": normalized_pair, "category": it["category"], "url": it["url"],
                "timestamp": it["timestamp"].isoformat(), "live": True, "provider": "rss-direct",
                "provenance": "rss-direct", "feed_mode": "fallback", "upstream_status": "news_stream offline",
                "upstream_note": "Headlines fetched directly from RSS because news_stream is not running.",
            } for it in self._rss_fallback(normalized_pair, limit=max(1, limit))]
            known = {it["event_id"] for it in fresh}
            items = (fresh + [it for it in items if it.get("event_id") not in known])[: max(1, limit)]
        return {
            "pair": normalized_pair,
            "items": items,
            "summary": {
                "tracked_items": len(items),
                "ingest_active": self._ingest_thread is not None and self._ingest_thread.is_alive(),
                "ingest_started": self._ingest_started.is_set(),
            },
            "service": self._service_block(),
        }

    def get_analysis_history(
        self,
        pair: str,
        limit: int = 20,
        *,
        q: str | None = None,
        min_confidence: float | None = None,
        has_evidence: bool | None = None,
        source: str | None = None,
        event_category: str | None = None,
        from_timestamp: datetime | None = None,
        to_timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        self._ensure_runtime_cache_loaded(normalized_pair)
        with self._state_lock:
            items = [dict(item) for item in self._analysis_history.get(normalized_pair, [])]
        items = _filter_analysis_items(
            items,
            q=q,
            min_confidence=min_confidence,
            has_evidence=has_evidence,
            source=source,
            event_category=event_category,
            from_timestamp=from_timestamp,
            to_timestamp=to_timestamp,
        )[: max(1, limit)]
        return {
            "pair": normalized_pair,
            "items": items,
            "summary": {
                "tracked_items": len(items),
                "with_evidence": sum(1 for item in items if item.get("evidence")),
                "rag_enabled": self.get_status(normalized_pair)["runtime"]["rag_enabled"],
            },
            "service": self._service_block(),
        }

    def get_explanation(self, pair: str, event_id: str) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        self._ensure_runtime_cache_loaded(normalized_pair)
        with self._state_lock:
            # Pair-specific first: one headline is filed under several pairs, each
            # with its own score.
            analysis = dict(
                self._analysis_by_event_id.get(_pair_event_key(normalized_pair, event_id))
                or self._analysis_by_event_id.get(event_id, {})
            )
        if not analysis:
            fallback = self.get_analysis_history(normalized_pair, limit=1, q=None)
            for item in fallback.get("items", []):
                if str(item.get("event_id")) == event_id:
                    analysis = dict(item)
                    break
        if not analysis:
            return {
                "pair": normalized_pair,
                "event_id": event_id,
                "available": False,
                "analysis": None,
                "service": self._service_block(),
            }
        return {
            "pair": normalized_pair,
            "event_id": event_id,
            "available": True,
            "analysis": analysis,
            "service": self._service_block(),
        }

    def get_historical_bias(
        self,
        *,
        pair: str,
        from_timestamp: datetime,
        to_timestamp: datetime,
        aggregate_interval_minutes: int,
        horizon_minutes: int,
    ) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        rows = self._load_training_rows(normalized_pair)

        filtered_rows: list[tuple[datetime, dict[str, object]]] = []
        for row in rows:
            if str(row.get("pair", "")).upper() != normalized_pair:
                continue
            if _row_int(row, "horizon_minutes") != horizon_minutes:
                continue
            row_timestamp = _row_timestamp(row)
            if row_timestamp is None:
                continue
            if not (from_timestamp <= row_timestamp <= to_timestamp):
                continue
            filtered_rows.append((row_timestamp, row))

        if not filtered_rows:
            return {
                "pair": normalized_pair,
                "from_timestamp": _isoformat(from_timestamp),
                "to_timestamp": _isoformat(to_timestamp),
                "aggregate_interval_minutes": aggregate_interval_minutes,
                "horizon_minutes": horizon_minutes,
                "bins": [],
                "summary": {
                    "average_bias_score": 0.0,
                    "average_confidence": 0.0,
                    "average_reaction": 0.0,
                    "average_surprise": 0.0,
                    "total_items": 0,
                    "bullish_ratio": 0.0,
                    "bearish_ratio": 0.0,
                    "neutral_ratio": 0.0,
                },
                "dataset": self.get_dataset_info(normalized_pair),
            }

        bins: dict[datetime, list[dict[str, float | str]]] = {}
        for row_timestamp, row in filtered_rows:
            bin_timestamp = _bin_timestamp(row_timestamp, aggregate_interval_minutes)
            bins.setdefault(bin_timestamp, []).append(_historical_bias_row(row))

        sorted_bins = sorted(bins.items(), key=lambda item: item[0])
        payload_bins: list[dict[str, object]] = []
        total_items = 0
        total_bullish = 0
        total_bearish = 0
        total_neutral = 0
        weighted_bias_sum = 0.0
        weighted_confidence_sum = 0.0
        total_weight = 0.0
        reaction_sum = 0.0
        surprise_sum = 0.0

        for bin_timestamp, entries in sorted_bins:
            item_count = len(entries)
            bullish_count = sum(1 for entry in entries if float(entry["bias_score"]) > 0.05)
            bearish_count = sum(1 for entry in entries if float(entry["bias_score"]) < -0.05)
            neutral_count = item_count - bullish_count - bearish_count

            bin_weight = sum(float(entry["confidence"]) for entry in entries)
            if bin_weight > 0:
                bias_score = sum(float(entry["bias_score"]) * float(entry["confidence"]) for entry in entries) / bin_weight
            else:
                bias_score = sum(float(entry["bias_score"]) for entry in entries) / item_count

            avg_confidence = sum(float(entry["confidence"]) for entry in entries) / item_count
            avg_reaction = sum(float(entry["reaction_label"]) for entry in entries) / item_count
            avg_surprise = sum(float(entry["standardized_surprise"]) for entry in entries) / item_count

            payload_bins.append(
                {
                    "timestamp": _isoformat(bin_timestamp),
                    "bias_score": round(bias_score, 4),
                    "confidence": round(avg_confidence, 4),
                    "average_reaction": round(avg_reaction, 6),
                    "average_surprise": round(avg_surprise, 4),
                    "bullish_count": bullish_count,
                    "bearish_count": bearish_count,
                    "neutral_count": neutral_count,
                    "item_count": item_count,
                }
            )

            total_items += item_count
            total_bullish += bullish_count
            total_bearish += bearish_count
            total_neutral += neutral_count
            weighted_bias_sum += sum(float(entry["bias_score"]) * float(entry["confidence"]) for entry in entries)
            weighted_confidence_sum += sum(float(entry["confidence"]) for entry in entries)
            total_weight += bin_weight
            reaction_sum += sum(float(entry["reaction_label"]) for entry in entries)
            surprise_sum += sum(float(entry["standardized_surprise"]) for entry in entries)

        average_bias_score = weighted_bias_sum / total_weight if total_weight > 0 else 0.0
        average_confidence = weighted_confidence_sum / total_items if total_items > 0 else 0.0
        average_reaction = reaction_sum / total_items if total_items > 0 else 0.0
        average_surprise = surprise_sum / total_items if total_items > 0 else 0.0

        return {
            "pair": normalized_pair,
            "from_timestamp": _isoformat(from_timestamp),
            "to_timestamp": _isoformat(to_timestamp),
            "aggregate_interval_minutes": aggregate_interval_minutes,
            "horizon_minutes": horizon_minutes,
            "bins": payload_bins,
            "summary": {
                "average_bias_score": round(average_bias_score, 4),
                "average_confidence": round(average_confidence, 4),
                "average_reaction": round(average_reaction, 6),
                "average_surprise": round(average_surprise, 4),
                "total_items": total_items,
                "bullish_ratio": round(total_bullish / total_items, 4) if total_items > 0 else 0.0,
                "bearish_ratio": round(total_bearish / total_items, 4) if total_items > 0 else 0.0,
                "neutral_ratio": round(total_neutral / total_items, 4) if total_items > 0 else 0.0,
            },
            "dataset": self.get_dataset_info(normalized_pair),
        }

    def get_dataset_info(self, pair: str) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        training_table_path = self._training_table_path(normalized_pair)
        artifact_path = self._artifact_path(normalized_pair)
        rows = self._load_training_rows(normalized_pair)
        timestamps = [timestamp for timestamp in (_row_timestamp(row) for row in rows) if timestamp is not None]
        timestamps.sort()
        return {
            "pair": normalized_pair,
            "available": training_table_path.exists(),
            "training_table_path": str(training_table_path),
            "artifact_path": str(artifact_path),
            "row_count": len(rows),
            "first_timestamp": _isoformat(timestamps[0]) if timestamps else None,
            "last_timestamp": _isoformat(timestamps[-1]) if timestamps else None,
        }

    def _load_training_rows(self, pair: str) -> list[dict[str, object]]:
        training_table_path = self._training_table_path(pair)
        if training_table_path in self._history_cache:
            return self._history_cache[training_table_path]
        if not training_table_path.exists():
            self._history_cache[training_table_path] = []
            return self._history_cache[training_table_path]

        rows: list[dict[str, object]] = []
        for line in training_table_path.read_text(encoding="utf-8").splitlines():
            stripped_line = line.strip()
            if not stripped_line:
                continue
            try:
                raw_row = json.loads(stripped_line)
            except json.JSONDecodeError:
                continue
            if isinstance(raw_row, dict):
                rows.append(raw_row)
        rows.sort(key=lambda row: str(row.get("timestamp", "")), reverse=True)
        self._history_cache[training_table_path] = rows
        return rows

    def _training_table_path(self, pair: str) -> Path:
        pair_slug = _pair_slug(pair)
        return self._root / "sentiment_assets" / pair_slug / f"{pair_slug}_reaction_training_rows_targeted_expanded.jsonl"

    def _artifact_path(self, pair: str) -> Path:
        return self._root / "sentiment_assets" / _pair_slug(pair) / "reaction_model_artifact.json"

    def _fetch_json(
        self,
        path: str,
        query_params: dict[str, Any],
        *,
        record: bool = True,
    ) -> dict[str, Any]:
        """GET a JSON object from the news service.

        ``record=False`` performs the request without touching the shared
        success/error state. Used by the /health probe: that probe exists to read
        feature flags, and letting its outcome redefine "available" would mean a
        capability lookup could mark a service unavailable that the data endpoints
        are talking to perfectly well.
        """
        query = urlencode(query_params)
        url = f"{self._service_url(path)}?{query}" if query else self._service_url(path)
        request = Request(url, headers={"accept": "application/json"})
        started_at = time.perf_counter()
        try:
            with urlopen(request, timeout=self._timeout_ms / 1000.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            if record:
                self._record_error(str(exc))
            raise
        if record:
            self._record_success((time.perf_counter() - started_at) * 1000.0)
        if not isinstance(payload, dict):
            raise ValueError(f"Sentiment bridge expected a JSON object from {path}.")
        return payload

    def fetch_currency_index(self) -> dict[str, Any] | None:
        """GET /currency-index (per-currency bias, behind SEMANTIC_CURRENCY_BIAS).

        Returns None when the bridge is disabled or the service has the capability off
        (501). Uses record=False so an optional capability being off never marks the
        service unavailable for the data endpoints.
        """
        if not self._enabled or not self._base_url:
            return None
        try:
            payload = self._fetch_json("/currency-index", {}, record=False)
        except HTTPError as exc:
            if exc.code == 501:
                return None
            raise
        return payload if payload.get("enabled") else None

    def _service_url(self, path: str) -> str:
        """Absolute URL of a semantic-service route, e.g. '/analyze' -> base + '/v1/analyze'."""
        return f"{self._base_url}{self._api_prefix}{path}"

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            self._service_url(path),
            data=json.dumps(payload).encode("utf-8"),
            headers={"accept": "application/json", "content-type": "application/json"},
            method="POST",
        )
        started_at = time.perf_counter()
        try:
            with urlopen(request, timeout=self._timeout_ms / 1000.0) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            self._record_error(str(exc))
            raise
        self._record_success((time.perf_counter() - started_at) * 1000.0)
        if not isinstance(parsed, dict):
            raise ValueError(f"Sentiment bridge expected a JSON object from {path}.")
        return parsed

    def _run_live_ingest_loop(self) -> None:
        while not self._ingest_stop.is_set():
            try:
                self._stream_feed_once()
            except Exception as exc:
                self._record_error(str(exc))
                if self._ingest_stop.wait(3.0):
                    return

    def _stream_feed_once(self) -> None:
        request = Request(self._service_url("/feed"), headers={"accept": "text/event-stream"})
        with urlopen(request, timeout=self._feed_timeout_seconds) as response:
            self._ingest_started.set()
            while not self._ingest_stop.is_set():
                raw_line = response.readline()
                if not raw_line:
                    break
                line = raw_line.decode("utf-8").strip()
                if not line or not line.startswith("data: "):
                    continue
                try:
                    event = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                # A delivered SSE event is proof the service is alive. Without this
                # the status chip latched on one stale HTTP error and read "service
                # degraded" indefinitely while live headlines were arriving.
                self._record_stream_activity()
                self._handle_feed_event(event)

    def _handle_feed_event(self, event: dict[str, Any]) -> None:
        tracked_pairs = self._tracked_pairs_for_feed_event(event)
        normalized_by_pair: list[tuple[str, dict[str, Any]]] = []
        for tracked_pair in tracked_pairs:
            normalized_event = self._normalize_feed_event(tracked_pair, event)
            normalized_by_pair.append((tracked_pair, normalized_event))
            with self._state_lock:
                existing = self._feed_items.setdefault(tracked_pair, [])
                existing = [item for item in existing if str(item.get("event_id")) != normalized_event["event_id"]]
                existing.insert(0, normalized_event)
                self._feed_items[tracked_pair] = existing[:80]
                self._persist_runtime_record(self._feed_history_path(tracked_pair), normalized_event)
        if not normalized_by_pair:
            return
        # ONE analysis per headline, however many pairs it is filed under: the service
        # runs FinBERT once and scores every requested pair in the same call. The
        # upstream /analyze call goes to the worker thread so a slow response can't
        # stall the SSE read loop.
        try:
            self._analysis_queue.put_nowait(tuple(normalized_by_pair))
        except queue.Full:
            self._record_error("Sentiment analysis queue full; dropped an event.")

    def _run_analysis_worker(self) -> None:
        while not self._ingest_stop.is_set():
            try:
                job = self._analysis_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self._process_analysis_job(job)
            except Exception as exc:
                self._record_error(str(exc))
            finally:
                self._analysis_queue.task_done()

    def _process_analysis(self, tracked_pair: str, normalized_event: dict[str, Any]) -> None:
        """Single-pair form, kept for callers that analyse one pair directly."""
        self._process_analysis_job(((tracked_pair, normalized_event),))

    def _process_analysis_job(self, job: tuple[tuple[str, dict[str, Any]], ...]) -> None:
        """Analyse one headline for every pair in `job` and file each pair's own score.

        The first pair is primary: the service's article-level fields describe it. Every
        other pair gets its `pair_scores[pair]` entry. Pairs are never averaged together:
        EUR/USD falling and USD/JPY rising are the same dollar move, so a cross-pair mean
        would cancel a real signal.
        """
        if not job:
            return
        primary_pair, primary_event = job[0]
        pairs = [pair for pair, _ in job]
        analysis = (
            self._analyze_feed_event(primary_event, currency_pairs=pairs)
            if len(pairs) > 1
            else self._analyze_feed_event(primary_event)
        )
        if analysis is None:
            return
        for pair, normalized_event in job:
            record = _pair_analysis_view(analysis, pair, primary_pair)
            if record is None:
                continue
            record["event_id"] = normalized_event["event_id"]
            with self._state_lock:
                self._analysis_by_event_id[_pair_event_key(pair, record["event_id"])] = record
                if pair == primary_pair:
                    self._analysis_by_event_id[record["event_id"]] = record
                history = self._analysis_history.setdefault(pair, [])
                history = [item for item in history if str(item.get("event_id")) != record["event_id"]]
                history.insert(0, record)
                self._analysis_history[pair] = history[:80]
                self._persist_runtime_record(self._analysis_history_path(pair), record)
                self._persist_runtime_record(self._prediction_log_path(), _prediction_log_row(record, self._clock()))

    def _analyze_feed_event(
        self,
        event: dict[str, Any],
        *,
        currency_pairs: list[str] | None = None,
    ) -> dict[str, Any] | None:
        primary_pair = str(event.get("pair", "USD/INR"))
        payload = {
            "article_text": str(event.get("headline", "")).strip(),
            "headline": str(event.get("headline", "")).strip(),
            "external_id": str(event.get("event_id", "")).strip() or None,
            "currency_pair": primary_pair,
            "event_category": str(event.get("category", "general")),
            "source_url": event.get("url"),
            "published_at": event.get("timestamp"),
            "metadata": {
                "provider": str(event.get("provider", "unknown")),
                "provenance": str(event.get("provenance", "unknown")),
                "feed_mode": str(event.get("feed_mode", "unknown")),
                "upstream_status": str(event.get("upstream_status", "unknown")),
            },
        }
        extra_pairs = [pair for pair in (currency_pairs or []) if normalize_pair(pair) != normalize_pair(primary_pair)]
        if extra_pairs:
            payload["currency_pairs"] = extra_pairs
        try:
            response = self._post_json("/analyze", payload)
        except Exception:
            return None
        raw_result = response.get("result")
        if not isinstance(raw_result, dict):
            return None
        analysis = dict(raw_result)
        analysis["event_id"] = str(analysis.get("event_id") or event.get("event_id") or "")
        analysis["feed"] = {
            "provider": event.get("provider"),
            "provenance": event.get("provenance"),
            "feed_mode": event.get("feed_mode"),
            "source": event.get("source"),
            "timestamp": event.get("timestamp"),
            "url": event.get("url"),
            "headline": event.get("headline"),
            "upstream_status": event.get("upstream_status"),
            "upstream_note": event.get("upstream_note"),
        }
        return analysis

    def _normalize_feed_event(self, pair: str, event: dict[str, Any]) -> dict[str, Any]:
        headline = str(event.get("headline", "")).strip()
        timestamp = _coerce_iso_timestamp(event.get("timestamp"))
        event_id = str(event.get("event_id") or _article_id(pair, f"{headline}|{timestamp}"))
        return {
            "event_id": event_id,
            "headline": headline,
            "source": str(event.get("source", "RSS")),
            "pair": pair,
            "category": str(event.get("category", "general")),
            "url": str(event.get("url", "")) if event.get("url") is not None else None,
            "timestamp": timestamp,
            "live": bool(event.get("live", True)),
            "provider": str(event.get("provider", "unknown")),
            "provenance": str(event.get("provenance", "unknown")),
            "feed_mode": str(event.get("feed_mode", "unknown")),
            "upstream_status": str(event.get("upstream_status", "unknown")),
            "upstream_note": str(event.get("upstream_note", "")),
            "last_upstream_item_at": event.get("last_upstream_item_at"),
            "feed_checked_at": event.get("feed_checked_at"),
        }

    def _ensure_runtime_cache_loaded(self, pair: str) -> None:
        normalized_pair = normalize_pair(pair)
        with self._state_lock:
            if normalized_pair in self._runtime_cache_loaded:
                return
            feed_items = self._load_runtime_records(self._feed_history_path(normalized_pair), limit=80)
            analysis_items = self._load_runtime_records(self._analysis_history_path(normalized_pair), limit=80)
            # Merge, don't skip. These used to be guarded by "pair not in cache",
            # so a single item arriving from live ingest before the first request
            # discarded the whole persisted archive -- the panels then showed one
            # item instead of the last 80.
            if feed_items:
                self._feed_items[normalized_pair] = _merge_runtime_records(
                    self._feed_items.get(normalized_pair, []), feed_items, limit=80
                )
            if analysis_items:
                self._analysis_history[normalized_pair] = _merge_runtime_records(
                    self._analysis_history.get(normalized_pair, []), analysis_items, limit=80
                )
            for analysis in analysis_items:
                event_id = str(analysis.get("event_id", "")).strip()
                if event_id:
                    self._analysis_by_event_id[_pair_event_key(normalized_pair, event_id)] = analysis
                    self._analysis_by_event_id.setdefault(event_id, analysis)
            self._runtime_cache_loaded.add(normalized_pair)

    def _load_runtime_records(self, path: Path, *, limit: int) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

        records: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw_line in reversed(lines):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            event_id = str(payload.get("event_id", "")).strip()
            if not event_id or event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            records.append(payload)
            if len(records) >= limit:
                break
        records.reverse()
        records.sort(key=lambda item: str(item.get("timestamp") or item.get("produced_at") or ""), reverse=True)
        return records

    def _persist_runtime_record(self, path: Path, payload: dict[str, Any]) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=True))
                handle.write("\n")
        except OSError:
            self._record_error(f"Failed to persist runtime sentiment history at {path}")

    def _tracked_pairs_for_feed_event(self, event: dict[str, Any]) -> list[str]:
        """Map a raw feed event to the pairs it should be filed under.

        Macro/global (and bare-USD) events are a dollar shock to every study pair, so
        they are filed under all of STUDY_PAIRS that the allowlist permits, USD/INR
        first. They used to collapse to USD/INR alone, which starved the majors of
        live data. Everything else must match the configured allowlist, which drops
        the junk "pairs" the upstream feed regex emits from ordinary hyphenated words
        (e.g. wait-AND-SEE, MID-CAP).
        """
        raw_pair = str(event.get("pair") or "").strip().upper()
        if raw_pair in _MACRO_PAIR_TOKENS:
            return [pair for pair in STUDY_PAIRS if pair in self._pair_allowlist]
        normalized_pair = normalize_pair(raw_pair)
        if normalized_pair in self._pair_allowlist:
            return [normalized_pair]
        return []

    def _prediction_log_path(self) -> Path:
        """Append-only log of every live per-pair prediction, for prospective evaluation."""
        return self._runtime_history_root / "predictions" / "live_predictions.jsonl"

    def _feed_history_path(self, pair: str) -> Path:
        return self._runtime_history_root / _pair_slug(pair) / "live_feed.jsonl"

    def _analysis_history_path(self, pair: str) -> Path:
        return self._runtime_history_root / _pair_slug(pair) / "live_analysis.jsonl"


def _correlation_payload(
    items: list[dict[str, Any]],
    total: int,
    offset: int,
    limit: int,
    service: dict[str, Any],
) -> dict[str, Any]:
    """Shape paginated correlation rows like a single upstream page.

    ``plottable`` counts rows carrying both a surprise and a realised reaction --
    only scheduled releases have a consensus forecast, so this is well below
    ``total`` and the UI should say so rather than implying every row is charted.
    """
    plottable = sum(
        1
        for item in items
        if isinstance(item, dict)
        and item.get("standardized_surprise") is not None
        and item.get("reaction_label") is not None
    )
    return {
        "available": True,
        "correlation": {
            "total": total,
            "offset": offset,
            "limit": limit,
            "returned": len(items),
            "plottable": plottable,
            "items": items,
        },
        "service": service,
    }


def _merge_runtime_records(
    live: list[dict[str, Any]],
    persisted: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Union live and persisted records, newest first, deduped by event_id.

    Live records win on collision: they are the same event but may carry a newer
    analysis. Records without an event_id are kept, keyed by identity, rather than
    silently collapsing into one.
    """
    merged: dict[str, dict[str, Any]] = {}
    for index, record in enumerate([*live, *persisted]):
        event_id = str(record.get("event_id", "")).strip() or f"__anon_{index}"
        if event_id not in merged:
            merged[event_id] = record
    ordered = sorted(
        merged.values(),
        key=lambda record: str(record.get("produced_at") or record.get("timestamp") or ""),
        reverse=True,
    )
    return ordered[:limit]


def _summarize_benchmark(report: dict[str, Any]) -> dict[str, Any]:
    """Flatten the benchmark report into a comparison table the UI can render.

    Keeps only what a reader needs to judge model selection: which family, how
    many rows it was evaluated on, and the four headline metrics. The raw report
    is several hundred KB of per-slice breakdowns.
    """
    datasets: list[dict[str, Any]] = []
    for dataset in report.get("datasets", []) if isinstance(report.get("datasets"), list) else []:
        if not isinstance(dataset, dict):
            continue
        inventory = dataset.get("inventory") if isinstance(dataset.get("inventory"), dict) else {}
        models: list[dict[str, Any]] = []
        for benchmark in dataset.get("benchmarks", []) if isinstance(dataset.get("benchmarks"), list) else []:
            if not isinstance(benchmark, dict):
                continue
            evaluation = benchmark.get("evaluation") if isinstance(benchmark.get("evaluation"), dict) else {}
            summary = benchmark.get("artifact_summary") if isinstance(benchmark.get("artifact_summary"), dict) else {}
            confusion = evaluation.get("confusion_matrix")
            holdout = 0
            if isinstance(confusion, list):
                holdout = sum(sum(row) for row in confusion if isinstance(row, list))
            models.append(
                {
                    "model_family": str(benchmark.get("model_family", "unknown")),
                    "training_rows": _safe_int(summary.get("training_rows")),
                    "holdout_rows": holdout,
                    "direction_accuracy": round(_safe_float(evaluation.get("direction_accuracy")), 4),
                    "direction_macro_f1": round(_safe_float(evaluation.get("direction_macro_f1")), 4),
                    "magnitude_mae": round(_safe_float(evaluation.get("magnitude_mae")), 6),
                    "calibration_score": round(_safe_float(evaluation.get("calibration_score")), 4),
                }
            )
        models.sort(key=lambda model: model["direction_accuracy"], reverse=True)
        datasets.append(
            {
                "dataset_name": str(dataset.get("dataset_name", "unknown")),
                "row_count": _safe_int(inventory.get("row_count")),
                "time_range": inventory.get("time_range") if isinstance(inventory.get("time_range"), dict) else None,
                "horizons": inventory.get("horizons") if isinstance(inventory.get("horizons"), dict) else None,
                "models": models,
                "best_model": models[0]["model_family"] if models else None,
            }
        )
    datasets.sort(key=lambda dataset: dataset["row_count"], reverse=True)
    return {"generated_at": report.get("generated_at"), "datasets": datasets}


def _summarize_pooled_report(report: dict[str, Any]) -> dict[str, Any]:
    """The pooled model's training_report.json in the same table shape as the UI expects.

    One dataset per holdout view the live service serves ("<pair>|live" calendar view and
    the USD/INR archive; horizon sub-slices are skipped). Rows: the pooled linear model,
    the majority-class and surprise-word baselines, and the k-NN / SVM benchmarks. Metrics
    a family does not report are None, never 0, so the table never shows a fake score.
    """
    benchmarks: dict[str, dict] = {}
    for block in (report.get("benchmarks") or {}).values():
        if isinstance(block, dict):
            benchmarks.update({k: v for k, v in block.items() if isinstance(v, dict)})
    datasets: list[dict[str, Any]] = []
    for block in (report.get("test") or {}).values():
        if not isinstance(block, dict):
            continue
        for view, metrics in block.items():
            if not isinstance(metrics, dict) or not (view.endswith("|live") or view.endswith("|archive")):
                continue
            rows = _safe_int(metrics.get("rows"))
            mae = metrics.get("magnitude_mae_bps")

            def row(family: str, accuracy: Any, f1: Any = None, mae_bps: Any = None) -> dict[str, Any] | None:
                if not isinstance(accuracy, (int, float)):
                    return None
                return {"model_family": family, "training_rows": None, "holdout_rows": rows,
                        "direction_accuracy": round(float(accuracy), 4),
                        "direction_macro_f1": round(float(f1), 4) if isinstance(f1, (int, float)) else None,
                        "magnitude_mae": round(float(mae_bps) * 1e-4, 6) if isinstance(mae_bps, (int, float)) else None,
                        "calibration_score": None}

            bench = benchmarks.get(view, {})
            models = [m for m in (
                row("pooled linear (shipped)", metrics.get("direction_accuracy"), metrics.get("direction_macro_f1"), mae),
                row("majority class", metrics.get("majority_baseline_accuracy")),
                row("surprise-word rule", metrics.get("surprise_word_rule_accuracy")),
                row("k-NN", bench.get("knn_accuracy")),
                row("SVM (tf-idf)", bench.get("svm_tfidf_accuracy")),
            ) if m is not None]
            models.sort(key=lambda model: model["direction_accuracy"], reverse=True)
            datasets.append({"dataset_name": view, "row_count": rows, "time_range": None, "horizons": None,
                             "models": models, "best_model": models[0]["model_family"] if models else None})
    datasets.sort(key=lambda dataset: dataset["row_count"], reverse=True)
    return {"generated_at": report.get("generated_at"), "datasets": datasets}


def _pair_event_key(pair: str, event_id: str) -> str:
    return f"{normalize_pair(pair)}|{event_id}"


def _pair_signal_label(market_score: float) -> str:
    """Direction label for a secondary pair, from its reaction-model score alone.

    The service's trading_signal (with its risk-flag gating) is computed for the
    primary pair only, so it must not be copied onto other pairs.
    """
    normalized = market_score * MARKET_SCORE_NORMALIZER
    if normalized > MARKET_DIRECTION_THRESHOLD:
        return "BUY"
    if normalized < -MARKET_DIRECTION_THRESHOLD:
        return "SELL"
    return "HOLD"


def _pair_analysis_view(analysis: dict[str, Any], pair: str, primary_pair: str) -> dict[str, Any] | None:
    """The analysis as filed under `pair`: that pair's own score, never another pair's.

    Returns None for a secondary pair the service produced no score for, rather than
    filing the primary pair's number under it.
    """
    view = dict(analysis)
    view["pair"] = normalize_pair(pair)
    view["primary_pair"] = normalize_pair(primary_pair)
    raw_scores = analysis.get("pair_scores")
    scores = raw_scores if isinstance(raw_scores, dict) else {}
    pair_score = scores.get(normalize_pair(pair))
    if normalize_pair(pair) == normalize_pair(primary_pair):
        view["signal_scope"] = "article"
        if isinstance(pair_score, dict):
            view["pair_score"] = dict(pair_score)
        return view
    if not isinstance(pair_score, dict):
        return None
    market_score = _safe_float(pair_score.get("market_score"))
    view["market_score"] = market_score
    view["confidence"] = _clamp(_safe_float(pair_score.get("confidence")), 0.0, 1.0)
    view["trading_signal"] = _pair_signal_label(market_score)
    view["signal_scope"] = "pair_score"
    view["pair_score"] = dict(pair_score)
    return view


def _prediction_log_row(record: dict[str, Any], logged_at: datetime) -> dict[str, Any]:
    """Compact, self-contained row: enough to score the prediction against later prices."""
    feed = record.get("feed") if isinstance(record.get("feed"), dict) else {}
    pair_score = record.get("pair_score") if isinstance(record.get("pair_score"), dict) else {}
    return {
        "logged_at": _isoformat(logged_at),
        "event_id": record.get("event_id"),
        "pair": record.get("pair"),
        "primary_pair": record.get("primary_pair"),
        "signal_scope": record.get("signal_scope"),
        "headline": feed.get("headline"),
        "published_at": feed.get("timestamp"),
        "produced_at": record.get("produced_at"),
        "market_score": _safe_float(record.get("market_score")),
        "confidence": _safe_float(record.get("confidence")),
        "sentiment_score": _safe_float(record.get("sentiment_score")),
        "trading_signal": record.get("trading_signal"),
        "direction": pair_score.get("direction"),
        "magnitude": pair_score.get("magnitude"),
        "horizon_minutes": pair_score.get("horizon_minutes"),
        "source": feed.get("source"),
        "provider": feed.get("provider"),
    }


def _pair_slug(pair: str) -> str:
    normalized = normalize_pair(pair)
    if normalized == "USD/INR":
        return "usdinr"
    return normalized.replace("/", "").replace("-", "").lower()


def _article_id(pair: str, headline: str) -> str:
    return hashlib.sha256(f"{pair}:{headline.strip().casefold()}".encode("utf-8")).hexdigest()[:16]


def _top_drivers(value: object, *, limit: int) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    drivers: list[dict[str, str]] = []
    for item in value[:limit]:
        if not isinstance(item, dict):
            continue
        drivers.append({str(key): str(val) for key, val in item.items()})
    return drivers


def _neutral_live_payload(pair: str, window_minutes: int) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "pair": pair,
        "overall_sentiment": 0.0,
        "confidence": 0.0,
        "bullish_count": 0,
        "bearish_count": 0,
        "neutral_count": 0,
        "item_count": 0,
        "top_drivers": [],
        "updated_at": now.isoformat(),
        "window_minutes": window_minutes,
    }


def _impact_label(score: float) -> str:
    if score >= 0.25:
        return "high"
    if score >= 0.1:
        return "medium"
    return "low"


def _impact_direction(sentiment: float) -> str:
    if sentiment > 0.05:
        return "bullish"
    if sentiment < -0.05:
        return "bearish"
    return "neutral"


def _cached_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime.now(UTC)


def _parse_signed_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    try:
        return float(value.strip())
    except ValueError:
        return 0.0


def _safe_float(value: object) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    return 0.0


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return 0
    return 0


def _env_flag(name: str, default: bool) -> bool:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "no", "off"}


def _coerce_iso_timestamp(value: Any) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return datetime.now(UTC).isoformat()


def _row_timestamp(row: dict[str, object]) -> datetime | None:
    raw_timestamp = row.get("timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _row_float(row: dict[str, object], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0.0
        try:
            return float(stripped)
        except ValueError:
            return 0.0
    return 0.0


def _row_int(row: dict[str, object], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            return int(float(stripped))
        except ValueError:
            return 0
    return 0


def _row_bool(row: dict[str, object], key: str) -> bool:
    value = row.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
    return False


def _bin_timestamp(timestamp: datetime, interval_minutes: int) -> datetime:
    midnight = timestamp.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed_minutes = int((timestamp - midnight).total_seconds() // 60)
    bucket_minutes = (elapsed_minutes // interval_minutes) * interval_minutes
    return midnight + timedelta(minutes=bucket_minutes)


def _historical_bias_row(row: dict[str, object]) -> dict[str, float | str]:
    reaction_label = _row_float(row, "reaction_label")
    surprise_value = _row_float(row, "standardized_surprise")
    reaction_component = _clamp(reaction_label * 800.0, -1.0, 1.0)
    surprise_component = _clamp(surprise_value, -1.0, 1.0)
    scheduled_bonus = 0.1 if _row_bool(row, "scheduled") else 0.0
    confidence = _clamp(abs(reaction_component) * 0.6 + abs(surprise_component) * 0.25 + scheduled_bonus, 0.05, 1.0)
    bias_score = _clamp((reaction_component * 0.7) + (surprise_component * 0.3), -1.0, 1.0)
    return {
        "bias_score": bias_score,
        "confidence": confidence,
        "reaction_label": reaction_label,
        "standardized_surprise": surprise_value,
    }


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _filter_analysis_items(
    items: list[dict[str, Any]],
    *,
    q: str | None,
    min_confidence: float | None,
    has_evidence: bool | None,
    source: str | None,
    event_category: str | None,
    from_timestamp: datetime | None,
    to_timestamp: datetime | None,
) -> list[dict[str, Any]]:
    normalized_query = q.strip().casefold() if isinstance(q, str) and q.strip() else None
    normalized_source = source.strip().casefold() if isinstance(source, str) and source.strip() else None
    normalized_category = event_category.strip().casefold() if isinstance(event_category, str) and event_category.strip() else None
    filtered: list[dict[str, Any]] = []

    for item in items:
        confidence = _safe_float(item.get("confidence"))
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
        feed = item.get("feed") if isinstance(item.get("feed"), dict) else {}
        produced_at = _parse_iso_datetime(item.get("produced_at"))

        if min_confidence is not None and confidence < min_confidence:
            continue
        if has_evidence is not None and bool(evidence) != has_evidence:
            continue
        if from_timestamp is not None and produced_at is not None and produced_at < from_timestamp.astimezone(UTC):
            continue
        if to_timestamp is not None and produced_at is not None and produced_at > to_timestamp.astimezone(UTC):
            continue

        if normalized_source is not None:
            feed_source = str(feed.get("source", "")).strip().casefold()
            evidence_sources = [str(entry.get("source", "")).strip().casefold() for entry in evidence if isinstance(entry, dict)]
            if normalized_source != feed_source and normalized_source not in evidence_sources:
                continue

        if normalized_category is not None:
            feed_category = str(feed.get("category", item.get("event_category", ""))).strip().casefold()
            evidence_categories = [str(entry.get("event_category", "")).strip().casefold() for entry in evidence if isinstance(entry, dict)]
            if normalized_category != feed_category and normalized_category not in evidence_categories:
                continue

        if normalized_query is not None:
            haystacks = [
                str(item.get("event_id", "")).casefold(),
                str(item.get("signal_rationale", "")).casefold(),
                str(feed.get("headline", "")).casefold(),
                str(feed.get("source", "")).casefold(),
            ]
            key_drivers = item.get("key_drivers") if isinstance(item.get("key_drivers"), list) else []
            haystacks.extend(str(driver.get("label", "")).casefold() for driver in key_drivers if isinstance(driver, dict))
            haystacks.extend(str(driver.get("rationale", "")).casefold() for driver in key_drivers if isinstance(driver, dict))
            haystacks.extend(str(entry.get("title", "")).casefold() for entry in evidence if isinstance(entry, dict))
            haystacks.extend(str(entry.get("snippet", "")).casefold() for entry in evidence if isinstance(entry, dict))
            if not any(normalized_query in haystack for haystack in haystacks):
                continue

        filtered.append(item)

    return filtered


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)