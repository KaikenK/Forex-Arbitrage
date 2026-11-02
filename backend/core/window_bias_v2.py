"""Window bias v2 (RESEARCH_TRACKER §6, C35): the 30-minute per-pair news bias, rebuilt on the
per-article outputs the research supports. SHADOW: logged next to the live window in each
opportunity's `semantic_snapshot`; it adjusts nothing.

Differences from the live window (`SentimentBridge.get_window_bias`):
  - one item per headline (restarts and repeated feed items re-analyse the same headline);
  - an item's time is the publisher's when known and not ahead of our analysis (+5 min skew),
    so a headline that reached us an hour late is not counted as fresh news;
  - a matched release contributes its calibrated direction: d = release direction, weight
    r * p_direction, and its expected move is carried as `expected_move_bps`;
  - other headlines contribute their sentiment oriented to the pair: d = +sentiment if the
    headline names the base currency only, -sentiment if the quote only (the same rule as the
    historical window-30 test, `research/run_window30.orientation`), weight r * confidence;
    headlines naming both or neither count as activity only.
r = max(0.1, 1 - age / window), the live window's recency shape. The payload has the shape of
`/sentiment/live`, so `LiveNewsBiasClient._to_bias` turns it into the same penalty formula as the
live window and the two can be compared like for like (RQ5).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping

try:                                        # same tagger as the historical tests
    from research.data.tagging import tag_currencies
except ImportError:                         # pragma: no cover - research package absent
    def tag_currencies(text: str, *, require_context: bool = True) -> list[str]:
        return []

PUBLISHER_SKEW = timedelta(minutes=5)
RECENCY_FLOOR = 0.1
ACTIVITY_SATURATION_COUNT = 5.0             # as sentiment_bridge
DIRECTION_THRESHOLD = 0.1


def headline_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def orientation(pair: str, currencies: Iterable[str]) -> int:
    base, _, quote = pair.partition("/")
    codes = set(currencies)
    has_base, has_quote = base in codes, quote in codes
    if has_base == has_quote:
        return 0
    return 1 if has_base else -1


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def item_time(record: Mapping[str, Any]) -> tuple[datetime | None, bool]:
    """(event time, whether it is the publisher's time)."""
    feed = record.get("feed") if isinstance(record.get("feed"), dict) else {}
    produced = _parse(record.get("produced_at")) or _parse(feed.get("timestamp"))
    published = _parse(feed.get("publisher_time"))
    if published is not None and (produced is None or published <= produced + PUBLISHER_SKEW):
        return published, True
    return produced, False


def item_direction(record: Mapping[str, Any], pair: str) -> tuple[str, float | None, float]:
    """(kind, direction d in [-1, 1] or None, weight factor c)."""
    score = record.get("pair_score") if isinstance(record.get("pair_score"), dict) else {}
    release_direction = score.get("release_direction")
    if score.get("basis") == "release" and release_direction not in (None, 0):
        p = min(1.0, max(0.0, _float(score.get("p_direction"), 0.5)))
        return "release", (1.0 if _float(release_direction) > 0 else -1.0), p
    feed = record.get("feed") if isinstance(record.get("feed"), dict) else {}
    sign = orientation(pair, tag_currencies(str(feed.get("headline") or "")))
    if sign == 0:
        return "unoriented", None, 0.0
    sentiment = min(1.0, max(-1.0, _float(record.get("sentiment_score"))))
    confidence = min(1.0, max(0.0, _float(record.get("confidence"))))
    return "news", sign * sentiment, confidence


def window_bias_v2(history: Iterable[Mapping[str, Any]], pair: str, now: datetime,
                   window_minutes: int = 30) -> dict[str, Any]:
    """`history`: the analyses filed under `pair` (any order)."""
    window = timedelta(minutes=max(1, window_minutes))
    start = now - window
    records = sorted(history, key=lambda r: str(r.get("produced_at") or ""))
    seen: set[str] = set()
    duplicates = late = 0
    items: list[dict[str, Any]] = []
    for record in records:                                  # oldest first: keep the first analysis
        feed = record.get("feed") if isinstance(record.get("feed"), dict) else {}
        headline = str(feed.get("headline") or "")
        key = headline_key(headline)
        if key and key in seen:
            duplicates += 1
            continue
        if key:
            seen.add(key)
        stamp, from_publisher = item_time(record)
        if stamp is None or stamp > now + PUBLISHER_SKEW:
            continue
        if stamp < start:
            produced = _parse(record.get("produced_at"))
            late += int(from_publisher and produced is not None and produced >= start)
            continue
        kind, d, c = item_direction(record, pair)
        age = max(0.0, (now - stamp).total_seconds())
        r = max(RECENCY_FLOOR, 1.0 - age / window.total_seconds())
        items.append({"kind": kind, "d": d, "c": c, "w": r * c, "headline": headline,
                      "expected_move_bps": (_float((record.get("pair_score") or {}).get("expected_move_bps"), 0.0)
                                            if kind == "release" else None),
                      "source": str(feed.get("source") or "unknown")})

    directional = [i for i in items if i["d"] is not None and i["w"] > 0]
    total = sum(i["w"] for i in directional)
    bias = sum(i["w"] * i["d"] for i in directional) / total if total > 0 else 0.0
    activity_factor = min(1.0, len(items) / ACTIVITY_SATURATION_COUNT)
    semantic_score = max(-1.0, min(1.0, bias * activity_factor))
    moves = [i["expected_move_bps"] for i in items if i["expected_move_bps"]]
    drivers = sorted(directional, key=lambda i: abs(i["d"]) * i["w"], reverse=True)[:5]
    return {
        "pair": pair,
        "source": "window_v2",
        "market_bias": round(bias, 4),
        "activity_factor": round(activity_factor, 4),
        "semantic_score": round(semantic_score, 4),
        "overall_sentiment": round(bias, 4),
        "confidence": round(sum(i["w"] * i["c"] for i in directional) / total, 4) if total > 0 else 0.0,
        "bullish_count": sum(i["d"] > DIRECTION_THRESHOLD for i in directional),
        "bearish_count": sum(i["d"] < -DIRECTION_THRESHOLD for i in directional),
        "neutral_count": len(items) - sum(abs(i["d"]) > DIRECTION_THRESHOLD for i in directional),
        "item_count": len(items),
        "release_items": sum(i["kind"] == "release" for i in items),
        "news_items": sum(i["kind"] == "news" for i in items),
        "unoriented_items": sum(i["kind"] == "unoriented" for i in items),
        "duplicates_dropped": duplicates,
        "late_dropped": late,
        "expected_move_bps": round(max(moves), 3) if moves else None,
        "top_drivers": [{"headline": i["headline"], "sentiment": f"{i['d']:+.2f}", "kind": i["kind"],
                         "source": i["source"]} for i in drivers],
        "updated_at": now.isoformat(),
        "window_minutes": int(window.total_seconds() // 60),
    }
