"""C43 shadow engine (RESEARCH_TRACKER §6): the Phase 2 switches computed live, recorded apart.

Behind ARBEX_SEMANTIC_SHADOW=1 (default off). For every detected opportunity it writes one row to
`<ARBEX_SHADOW_DIR>/<UTC date>/semantic_shadow.jsonl`. The rows hold what the engine WOULD record
with the Phase 2 switches on: the risk-adjusted confidence, the currency-index window direction,
and the FX-filtered window. They are written outside the recordings root, on purpose. The
pre-registered loaders treat every `*.jsonl` in a day directory as quotes and every directory
under the root as a day, so nothing they read changes. `opportunities.jsonl`, the semantic
snapshot and the prediction log are not touched.
"""
from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from backend.core.currency_bias_channels import MIN_DIRECTION_CONFIDENCE
from backend.core.semantic_apply import apply_to_row, currency_index_direction

SHADOW_ENV = "ARBEX_SEMANTIC_SHADOW"
SHADOW_DIR_ENV = "ARBEX_SHADOW_DIR"
SHADOW_FILE = "semantic_shadow.jsonl"
ACTIVITY_SATURATION_COUNT = 5.0          # as the live window (sentiment_bridge)


def shadow_enabled() -> bool:
    return (os.environ.get(SHADOW_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


def shadow_root() -> Path:
    override = os.environ.get(SHADOW_DIR_ENV)
    if override:
        return Path(override)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "arbex" / "shadow"


def pair_shadow(bridge, currency_payload: Optional[Mapping[str, Any]], pair: str, window_minutes: int,
                *, clock=time.time) -> Dict[str, Any]:
    """Per-pair inputs refreshed with the snapshot cache (every 30 s): the FX-filtered window and the
    currency-index direction. Network-free: the bridge reads its local analysis history."""
    filtered = bridge.get_window_bias(pair, window_minutes, fx_filter=True)
    items = int(filtered.get("item_count") or 0)
    return {
        "computed_at": clock(),
        "window_filtered": {"item_count": items,
                            "activity_factor": round(min(1.0, items / ACTIVITY_SATURATION_COUNT), 4),
                            "fx_filter_dropped": int(filtered.get("fx_filter_dropped") or 0),
                            "semantic_score": filtered.get("semantic_score"),
                            "direction": filtered.get("direction")},
        "currency_index": currency_index_direction(currency_payload, min_confidence=MIN_DIRECTION_CONFIDENCE),
    }


def shadow_row(row: Mapping[str, Any], snapshot: Optional[Mapping[str, Any]], pair: str,
               pair_cache: Optional[Mapping[str, Any]], *, older_leg_age_ms: Optional[float],
               clock=time.time) -> Dict[str, Any]:
    """One shadow row for a detected opportunity; the inputs are copied, never modified."""
    adjusted_row = {"confidence_score": row.get("confidence_score")}
    applied = apply_to_row(adjusted_row, copy.deepcopy(snapshot) if snapshot else None,
                           older_leg_age_ms=older_leg_age_ms)
    window = (snapshot or {}).get("window_bias") or {}
    now = clock()
    cache = pair_cache or {}
    return {
        "id": row.get("id"),
        "timestamp_ms": row.get("timestamp_ms"),
        "pair": pair,
        "type": row.get("type"),
        "confidence_score": row.get("confidence_score"),
        "adjusted_confidence_score": adjusted_row["confidence_score"],
        "risk_penalty": applied["risk_penalty"],
        "risk_unavailable": applied["risk_unavailable"],
        "stale_caution": applied["stale_caution"],
        "older_leg_age_ms": older_leg_age_ms,
        "window_direction_v1": window.get("direction"),
        "window_item_count": window.get("item_count"),
        "window_direction_currency_index": (cache.get("currency_index") or {}).get("direction"),
        "currency_index": cache.get("currency_index"),
        "window_filtered": cache.get("window_filtered"),
        "shadow_inputs_age_seconds": (None if cache.get("computed_at") is None
                                      else round(now - float(cache["computed_at"]), 1)),
        "written_at": now,
    }


class ShadowWriter:
    """Appends shadow rows under `root/<UTC date of the opportunity>/semantic_shadow.jsonl`."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = root or shadow_root()
        self._lock = threading.Lock()
        self.rows_written = 0

    def write(self, shadow: Mapping[str, Any]) -> Path:
        ts = shadow.get("timestamp_ms")
        stamp = datetime.fromtimestamp(float(ts) / 1000, tz=UTC) if ts is not None else datetime.now(UTC)
        day = self._root / stamp.date().isoformat()
        with self._lock:
            day.mkdir(parents=True, exist_ok=True)
            path = day / SHADOW_FILE
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(shadow, default=str) + "\n")
            self.rows_written += 1
        return path
