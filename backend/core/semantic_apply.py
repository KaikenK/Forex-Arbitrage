"""Phase 2 engine switches (RESEARCH_TRACKER §6). All default OFF; with every switch off the backend
records exactly what it recorded before (same opportunity rows, snapshot and window fields).

  ARBEX_APPLY_SEMANTIC=risk            the risk channel (and a stale-leg caution) lowers the recorded
                                       `confidence_score`; the detector's own value is kept as
                                       `raw_confidence_score`. Direction is never applied: H6b and
                                       RQ2-E2E found no direction value.
  SEMANTIC_DIRECTION_SOURCE=currency_index
                                       the 30-minute window's direction label follows the per-pair
                                       currency index (H6 relative bias) instead of V1 `market_bias`.
  SEMANTIC_FX_RELEVANCE_FILTER=1       a headline enters a pair's window only when it is tagged with one
                                       of the pair's currencies.

Switched on after 2026-11-18 00:00 IST, when the RQ5 extension and C40 samples have closed.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

APPLY_ENV = "ARBEX_APPLY_SEMANTIC"
DIRECTION_SOURCE_ENV = "SEMANTIC_DIRECTION_SOURCE"
FX_FILTER_ENV = "SEMANTIC_FX_RELEVANCE_FILTER"

# Stale-leg caution: the detector already drops a leg older than its max staleness (5 s,
# ExchangeLegSource). Within that, a leg more than one 1 Hz poll old is a weaker basis for the
# cross; the caution rises linearly from 0 at STALE_FREE_MS to STALE_CAUTION_MAX at STALE_FULL_MS.
# Engineering default; C40-2 (18 Nov) measures whether quote age predicts a vanishing cross.
STALE_FREE_MS = 1_000.0
STALE_FULL_MS = 5_000.0
STALE_CAUTION_MAX = 0.05


def _flag(name: str) -> str:
    return (os.environ.get(name) or "").strip().lower()


def apply_mode() -> str:
    """'risk' when ARBEX_APPLY_SEMANTIC=risk, else 'off' (any other value is ignored)."""
    return "risk" if _flag(APPLY_ENV) == "risk" else "off"


def direction_source() -> str:
    """'currency_index' when SEMANTIC_DIRECTION_SOURCE=currency_index, else 'v1'."""
    return "currency_index" if _flag(DIRECTION_SOURCE_ENV) == "currency_index" else "v1"


def fx_relevance_filter_enabled() -> bool:
    return _flag(FX_FILTER_ENV) in {"1", "true", "yes", "on"}


def stale_caution(older_leg_age_ms: Optional[float]) -> float:
    if older_leg_age_ms is None:
        return 0.0
    span = STALE_FULL_MS - STALE_FREE_MS
    return STALE_CAUTION_MAX * min(1.0, max(0.0, (float(older_leg_age_ms) - STALE_FREE_MS) / span))


def older_leg_age_ms(row: Mapping[str, Any], latest_recv_ms: Mapping[str, Optional[int]]) -> Optional[float]:
    """Detection time minus the receipt time of each leg's latest quote (the older leg), as C40-2."""
    detected = row.get("timestamp_ms")
    ages = []
    for side in ("buy_source", "sell_source"):
        recv = latest_recv_ms.get(str(row.get(side)))
        if recv is None or detected is None:
            return None
        ages.append(max(0.0, float(detected) - float(recv)))
    return max(ages)


def apply_to_row(row: Dict[str, Any], snapshot: Optional[Dict[str, Any]], *,
                 older_leg_age_ms: Optional[float]) -> Dict[str, Any]:
    """Lower the row's confidence by the risk-channel penalty and the stale-leg caution (in place)."""
    raw = float(row.get("confidence_score") or 0.0)
    risk = ((snapshot or {}).get("risk_channel") or {}).get("penalty")
    risk_penalty = float(risk) if risk is not None else 0.0
    caution = stale_caution(older_leg_age_ms)
    row["raw_confidence_score"] = raw
    row["confidence_score"] = round(min(1.0, max(0.0, raw - risk_penalty - caution)), 3)
    applied = {"mode": "risk", "risk_penalty": risk_penalty, "stale_caution": round(caution, 4),
               "older_leg_age_ms": older_leg_age_ms,
               "risk_unavailable": risk is None}
    row["semantic_applied"] = applied
    if snapshot is not None:
        snapshot["applied"] = True
    return applied


def currency_index_direction(pair_payload: Optional[Mapping[str, Any]], *, min_confidence: float) -> Dict[str, Any]:
    """The window's direction label from the per-pair currency index (relative bias = base - quote).

    'risk_on' (pair up) for a bullish relative bias, 'risk_off' for bearish, 'neutral' when the
    index is neutral, unavailable, or below the direction channel's minimum confidence."""
    if not pair_payload or pair_payload.get("relative_bias") is None:
        return {"direction": "neutral", "reason": "currency index unavailable", "relative_bias": None,
                "label": None, "confidence": None}
    label = str(pair_payload.get("label") or "neutral")
    confidence = float(pair_payload.get("confidence") or 0.0)
    bias = float(pair_payload["relative_bias"])
    if label == "neutral" or confidence < min_confidence:
        direction, reason = "neutral", "currency index neutral or low confidence"
    else:
        direction = "risk_on" if label == "bullish" else "risk_off" if label == "bearish" else "neutral"
        reason = f"currency index {label} (relative bias {bias:+.2f}, confidence {confidence:.2f})"
    return {"direction": direction, "reason": reason, "relative_bias": round(bias, 4), "label": label,
            "confidence": round(confidence, 4)}
