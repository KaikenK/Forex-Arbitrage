"""Display-only system state for the dashboard's Health view (GET /system/health) and the shadow block
on the dashboard's scored messages. Nothing here writes to the recorder, the semantic snapshot or the
prediction log; it only watches hub polls and keeps the latest C43 shadow rows in memory."""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Any, Dict, Iterable, Mapping, Optional

GAP_THRESHOLD_MS = 5_000          # same rule as C40's gap detection: a silence over 5 s in every hub
SHADOW_BLOCK_FIELDS = ("confidence_score", "adjusted_confidence_score", "risk_penalty", "risk_unavailable",
                       "stale_caution", "older_leg_age_ms", "window_direction_v1",
                       "window_direction_currency_index", "window_filtered")


class GapTracker:
    """Recorder silences since this process started: gaps over `threshold_ms` between successive
    quote receipts across all hubs (the newest receipt time per poll is what counts)."""

    def __init__(self, threshold_ms: int = GAP_THRESHOLD_MS, keep: int = 50) -> None:
        self.threshold_ms = threshold_ms
        self.gaps: "deque[Dict[str, Any]]" = deque(maxlen=keep)
        self.gap_count = 0
        self.last_recv_ms: Optional[int] = None
        self._lock = threading.Lock()

    def observe(self, recv_ms: Iterable[Optional[int]]) -> None:
        newest = max((int(t) for t in recv_ms if t is not None), default=None)
        if newest is None:
            return
        with self._lock:
            last = self.last_recv_ms
            if last is not None and newest - last > self.threshold_ms:
                self.gaps.append({"start_ms": last, "end_ms": newest, "seconds": round((newest - last) / 1000, 1)})
                self.gap_count += 1
            if last is None or newest > last:
                self.last_recv_ms = newest


class ShadowCache:
    """The newest C43 shadow rows by opportunity id, bounded, for the dashboard's breakdown."""

    def __init__(self, keep: int = 500) -> None:
        self._rows: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._keep = keep
        self._lock = threading.Lock()
        self.last_row_ms: Optional[int] = None

    def put(self, row: Mapping[str, Any]) -> None:
        key = str(row.get("id") or "")
        if not key:
            return
        block = {k: row.get(k) for k in SHADOW_BLOCK_FIELDS}
        with self._lock:
            self._rows[key] = block
            self._rows.move_to_end(key)
            while len(self._rows) > self._keep:
                self._rows.popitem(last=False)
            self.last_row_ms = int(row.get("timestamp_ms") or 0) or self.last_row_ms

    def get(self, opportunity_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            block = self._rows.get(opportunity_id)
            return dict(block) if block is not None else None


def system_health(*, started_at: float, mode: str, gaps: GapTracker, recorder: Any, shadow_enabled: bool,
                  shadow_writer: Any, shadow_cache: ShadowCache, shadow_root: str, legs: Iterable[Mapping[str, Any]],
                  semantic: Mapping[str, Any], now: Optional[float] = None) -> Dict[str, Any]:
    now = time.time() if now is None else now
    legs = list(legs)
    return {
        "backend": {"started_at": started_at, "uptime_s": round(now - started_at, 1), "mode": mode},
        "recorder": {
            "enabled": recorder is not None,
            "rows_written": getattr(recorder, "rows_written", None),
            "session_dir": str(getattr(recorder, "session_dir", None) or "") or None,
            "last_quote_recv_ms": gaps.last_recv_ms,
            "gap_threshold_s": gaps.threshold_ms / 1000,
            "gaps_since_start": gaps.gap_count,
            "recent_gaps": list(gaps.gaps)[-10:],
        },
        "shadow": {
            "enabled": shadow_enabled,
            "dir": shadow_root,
            "rows_written": getattr(shadow_writer, "rows_written", None),
            "last_row_ms": shadow_cache.last_row_ms,
        },
        "legs": {"total": len(legs), "healthy": sum(1 for leg in legs if leg.get("healthy")),
                 "stale": [leg.get("id") for leg in legs if not leg.get("healthy")]},
        "semantic_service": {k: semantic.get(k) for k in ("available", "last_success_at", "last_error", "last_latency_ms")},
    }
