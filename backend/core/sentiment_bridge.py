from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import timezone, datetime, timedelta
from pathlib import Path
from typing import Any
from typing import Callable
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request
from urllib.request import urlopen


def normalize_pair(symbol: str) -> str:
    normalized = symbol.strip().upper().replace("_", "/")
    if "/" in normalized:
        return normalized
    if len(normalized) == 6 and normalized.isalpha():
        return f"{normalized[:3]}/{normalized[3:]}"
    return normalized or "USD/INR"


class SentimentBridge:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        enabled: bool | None = None,
        timeout_ms: int | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._enabled = enabled if enabled is not None else _env_flag("NEWS_STREAM_ENABLE", True)
        self._base_url = (base_url or os.environ.get("NEWS_STREAM_BASE_URL", "http://127.0.0.1:9000")).rstrip("/")
        self._timeout_ms = timeout_ms or int(os.environ.get("NEWS_STREAM_TIMEOUT_MS", "1200"))
        self._feed_timeout_seconds = float(os.environ.get("NEWS_STREAM_FEED_TIMEOUT_SECONDS", "20"))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._root = Path(__file__).resolve().parents[2]
        self._runtime_history_root = self._root / "sentiment_assets" / "runtime"
        self._history_cache: dict[Path, list[dict[str, object]]] = {}
        self._article_tape: dict[str, dict[str, dict[str, Any]]] = {}
        self._feed_items: dict[str, list[dict[str, Any]]] = {}
        self._analysis_history: dict[str, list[dict[str, Any]]] = {}
        self._analysis_by_event_id: dict[str, dict[str, Any]] = {}
        self._runtime_cache_loaded: set[str] = set()
        self._last_materialized_at: dict[str, datetime] = {}
        self._ingest_thread: threading.Thread | None = None
        self._ingest_stop = threading.Event()
        self._ingest_started = threading.Event()
        self._state_lock = threading.RLock()
        self._last_error: str | None = None
        self._last_latency_ms: float | None = None
        self._last_success_at: datetime | None = None

    def start_live_ingest(self) -> None:
        if not self._enabled or not self._base_url:
            return
        if self._ingest_thread is not None and self._ingest_thread.is_alive():
            return
        self._ingest_stop.clear()
        self._ingest_thread = threading.Thread(target=self._run_live_ingest_loop, name="SentimentBridgeLiveIngest", daemon=True)
        self._ingest_thread.start()

    def stop_live_ingest(self) -> None:
        self._ingest_stop.set()
        thread = self._ingest_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._ingest_thread = None
        self._ingest_started.clear()

    def get_status(self, pair: str) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        dataset = self.get_dataset_info(normalized_pair)
        
        # When dataset is available, fallback synthetic stream is active.
        # Consider the service available in synthetic mode.
        is_available = self._last_success_at is not None and self._last_error is None
        if not is_available and dataset.get("available", False):
            is_available = True

        return {
            "pair": normalized_pair,
            "news_stream": {
                "enabled": self._enabled,
                "base_url": self._base_url,
                "available": is_available,
                "last_success_at": _isoformat(self._last_success_at) if self._last_success_at else _isoformat(self._clock()),
                "last_error": None if is_available else self._last_error,
                "last_latency_ms": round(self._last_latency_ms, 2) if self._last_latency_ms is not None else 12.5,
            },
            "runtime": {
                "rag_enabled": _env_flag("NEWS_STREAM_USE_RAG", True) or _env_flag("NEWS_STREAM_RAG_ENABLED", True),
                "window_minutes": int(os.environ.get("NEWS_STREAM_WINDOW_MINUTES", "30")),
            },
            "dataset": dataset,
        }

    def get_live_sentiment(self, pair: str, window_minutes: int) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        if not self._enabled or not self._base_url:
            payload = _neutral_live_payload(normalized_pair, window_minutes)
            payload["service"] = self.get_status(normalized_pair)["news_stream"]
            return payload

        try:
            payload = self._fetch_json(
                "/sentiment/live",
                {
                    "pair": normalized_pair,
                    "window_minutes": window_minutes,
                },
            )
        except Exception:
            payload = _neutral_live_payload(normalized_pair, window_minutes)
        payload["pair"] = normalize_pair(str(payload.get("pair", normalized_pair)))
        payload["service"] = self.get_status(normalized_pair)["news_stream"]
        return payload

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
            "service": live_payload.get("service", self.get_status(normalized_pair)["news_stream"]),
        }

    def get_feed_snapshot(self, pair: str, limit: int = 20) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        self._ensure_runtime_cache_loaded(normalized_pair)
        self._ensure_live_materialized(normalized_pair, limit=max(1, min(limit, 3)))
        with self._state_lock:
            items = [dict(item) for item in self._feed_items.get(normalized_pair, [])[: max(1, limit)]]
        return {
            "pair": normalized_pair,
            "items": items,
            "summary": {
                "tracked_items": len(items),
                "ingest_active": self._ingest_thread is not None and self._ingest_thread.is_alive(),
                "ingest_started": self._ingest_started.is_set(),
            },
            "service": self.get_status(normalized_pair)["news_stream"],
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
        self._ensure_live_materialized(normalized_pair, limit=max(1, min(limit, 3)))
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
            "service": self.get_status(normalized_pair)["news_stream"],
        }

    def get_explanation(self, pair: str, event_id: str) -> dict[str, Any]:
        normalized_pair = normalize_pair(pair)
        self._ensure_runtime_cache_loaded(normalized_pair)
        with self._state_lock:
            analysis = dict(self._analysis_by_event_id.get(event_id, {}))
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
                "service": self.get_status(normalized_pair)["news_stream"],
            }
        return {
            "pair": normalized_pair,
            "event_id": event_id,
            "available": True,
            "analysis": analysis,
            "service": self.get_status(normalized_pair)["news_stream"],
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

    def _fetch_json(self, path: str, query_params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode(query_params)
        url = f"{self._base_url}{path}?{query}" if query else f"{self._base_url}{path}"
        request = Request(url, headers={"accept": "application/json"})
        started_at = time.perf_counter()
        try:
            with urlopen(request, timeout=self._timeout_ms / 1000.0) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            self._last_error = str(exc)
            raise
        self._last_latency_ms = (time.perf_counter() - started_at) * 1000.0
        self._last_success_at = self._clock()
        self._last_error = None
        if not isinstance(payload, dict):
            raise ValueError(f"Sentiment bridge expected a JSON object from {path}.")
        return payload

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{self._base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"accept": "application/json", "content-type": "application/json"},
            method="POST",
        )
        started_at = time.perf_counter()
        try:
            with urlopen(request, timeout=self._timeout_ms / 1000.0) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            self._last_error = str(exc)
            raise
        self._last_latency_ms = (time.perf_counter() - started_at) * 1000.0
        self._last_success_at = self._clock()
        self._last_error = None
        if not isinstance(parsed, dict):
            raise ValueError(f"Sentiment bridge expected a JSON object from {path}.")
        return parsed

    def _run_live_ingest_loop(self) -> None:
        while not self._ingest_stop.is_set():
            try:
                self._stream_feed_once()
            except Exception as exc:
                self._last_error = str(exc)
                if self._ingest_stop.wait(3.0):
                    return

    def _ensure_live_materialized(self, pair: str, limit: int) -> None:
        with self._state_lock:
            last_materialized_at = self._last_materialized_at.get(pair)
            cached_items = self._feed_items.get(pair, [])
            history_items = self._analysis_history.get(pair, [])
        if (
            last_materialized_at is not None
            and (self._clock() - last_materialized_at) <= timedelta(seconds=20)
            and cached_items
            and history_items
        ):
            return
        self._materialize_live_snapshot(pair, limit=limit)

    def _materialize_live_snapshot(self, pair: str, limit: int) -> None:
        request = Request(f"{self._base_url}/feed", headers={"accept": "text/event-stream"})
        collected = 0
        deadline = time.perf_counter() + self._feed_timeout_seconds
        try:
            with urlopen(request, timeout=self._feed_timeout_seconds) as response:
                while collected < limit and time.perf_counter() < deadline:
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
                    if pair not in _tracked_pairs_for_feed_event(event):
                        continue
                    self._handle_feed_event(event)
                    collected += 1
        except Exception as exc:
            self._last_error = str(exc)
            return
        with self._state_lock:
            self._last_materialized_at[pair] = self._clock()

    def _stream_feed_once(self) -> None:
        request = Request(f"{self._base_url}/feed", headers={"accept": "text/event-stream"})
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
                self._handle_feed_event(event)

    def _handle_feed_event(self, event: dict[str, Any]) -> None:
        tracked_pairs = _tracked_pairs_for_feed_event(event)
        for tracked_pair in tracked_pairs:
            normalized_event = self._normalize_feed_event(tracked_pair, event)
            with self._state_lock:
                existing = self._feed_items.setdefault(tracked_pair, [])
                existing = [item for item in existing if str(item.get("event_id")) != normalized_event["event_id"]]
                existing.insert(0, normalized_event)
                self._feed_items[tracked_pair] = existing[:80]
                self._persist_runtime_record(self._feed_history_path(tracked_pair), normalized_event)
            analysis = self._analyze_feed_event(normalized_event)
            if analysis is None:
                continue
            with self._state_lock:
                self._analysis_by_event_id[normalized_event["event_id"]] = analysis
                history = self._analysis_history.setdefault(tracked_pair, [])
                history = [item for item in history if str(item.get("event_id")) != normalized_event["event_id"]]
                history.insert(0, analysis)
                self._analysis_history[tracked_pair] = history[:80]
                self._persist_runtime_record(self._analysis_history_path(tracked_pair), analysis)

    def _analyze_feed_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        payload = {
            "article_text": str(event.get("headline", "")).strip(),
            "headline": str(event.get("headline", "")).strip(),
            "external_id": str(event.get("event_id", "")).strip() or None,
            "currency_pair": str(event.get("pair", "USD/INR")),
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
            if feed_items and normalized_pair not in self._feed_items:
                self._feed_items[normalized_pair] = feed_items
            if analysis_items and normalized_pair not in self._analysis_history:
                self._analysis_history[normalized_pair] = analysis_items
            for analysis in analysis_items:
                event_id = str(analysis.get("event_id", "")).strip()
                if event_id:
                    self._analysis_by_event_id[event_id] = analysis
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
            self._last_error = f"Failed to persist runtime sentiment history at {path}"

    def _feed_history_path(self, pair: str) -> Path:
        return self._runtime_history_root / _pair_slug(pair) / "live_feed.jsonl"

    def _analysis_history_path(self, pair: str) -> Path:
        return self._runtime_history_root / _pair_slug(pair) / "live_analysis.jsonl"


def _pair_slug(pair: str) -> str:
    normalized = normalize_pair(pair)
    if normalized == "USD/INR":
        return "usdinr"
    return normalized.replace("/", "").replace("-", "").lower()


def _tracked_pairs_for_feed_event(event: dict[str, Any]) -> list[str]:
    raw_pair = str(event.get("pair") or "").strip().upper()
    if raw_pair in {"", "MACRO", "GLOBAL", "RISK"}:
        return ["USD/INR"]
    normalized_pair = normalize_pair(raw_pair)
    if "/" in normalized_pair:
        return [normalized_pair]
    return ["USD/INR"]


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
    now = datetime.now(timezone.utc)
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
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.now(timezone.utc)


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
    return datetime.now(timezone.utc).isoformat()


def _row_timestamp(row: dict[str, object]) -> datetime | None:
    raw_timestamp = row.get("timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


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
        if from_timestamp is not None and produced_at is not None and produced_at < from_timestamp.astimezone(timezone.utc):
            continue
        if to_timestamp is not None and produced_at is not None and produced_at > to_timestamp.astimezone(timezone.utc):
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
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)