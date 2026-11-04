"""Window bias v2 (RESEARCH_TRACKER §6, C35; maths revised in C38 before any v2 result existed):
the 30-minute per-pair news bias, rebuilt on the per-article outputs the research supports.
SHADOW: logged next to the live window in each opportunity's `semantic_snapshot`; it adjusts
nothing.

Differences from the live window (`SentimentBridge.get_window_bias`):
  - one item per headline (restarts and repeated feed items re-analyse the same headline);
  - an item's time is the publisher's when known and not ahead of our analysis (+5 min skew),
    so a headline that reached us an hour late is not counted as fresh news;
  - every item contributes its EXPECTED signed direction d in [-1, 1]:
      release  d = side * (2p - 1), p = the calibrated p_direction, so a 50/50 call adds nothing
               (C35 counted it as a full +-1 vote weighted by p);
      news     d = +sentiment if the headline names the base currency only, -sentiment if the
               quote only (the rule of the historical window-30 test, `research/run_window30`);
               headlines naming both or neither count as activity only;
  - items are weighted by recency r alone. Confidence already shapes d (FinBERT's expected
    polarity, 2p - 1), so weighting by it again counted it twice;
  - the score is shrunk toward 0 by the amount of evidence, S = sum(r d) / (sum(r) + KAPPA): one
    fresh item gives half its direction, a full window almost all of it. This replaces the
    min(1, n/5) item-count factor, under which four headlines about other currencies gave one
    oriented headline full weight;
  - a release's expected move is carried only while it can still matter for execution, up to
    15 min after the release (volatility stays raised about 15 min; Ederington & Lee 1993).
r = max(0.1, 1 - age / window), the live window's recency shape. The payload has the shape of
`/sentiment/live`, so `LiveNewsBiasClient._to_bias` turns it into the same penalty formula as the
live window and the two can be compared like for like (RQ5).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Iterable, Mapping

try:                                        # same tagger as the historical tests
    from research.data.tagging import tag_currencies
except ImportError:                         # pragma: no cover - research package absent
    def tag_currencies(text: str, *, require_context: bool = True) -> list[str]:
        return []

PUBLISHER_SKEW = timedelta(minutes=5)
RECENCY_FLOOR = 0.1
ACTIVITY_SATURATION_COUNT = 5.0             # as sentiment_bridge; reported, no longer in the score
DIRECTION_THRESHOLD = 0.1
KAPPA = 1.0                                 # prior weight at zero: one fresh item's worth
RELEASE_MOVE_MINUTES = 15.0


def headline_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


@lru_cache(maxsize=4096)
def _currencies(headline: str) -> tuple[str, ...]:
    """The tagger runs ~57 regexes per headline; the window re-reads the same 80 every 30 s."""
    return tuple(tag_currencies(headline))


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


def _produced(record: Mapping[str, Any]) -> datetime | None:
    feed = record.get("feed") if isinstance(record.get("feed"), dict) else {}
    return _parse(record.get("produced_at")) or _parse(feed.get("timestamp"))


def item_time(record: Mapping[str, Any]) -> tuple[datetime | None, bool]:
    """(event time, whether it is the publisher's time)."""
    feed = record.get("feed") if isinstance(record.get("feed"), dict) else {}
    produced = _produced(record)
    published = _parse(feed.get("publisher_time"))
    if published is not None and (produced is None or published <= produced + PUBLISHER_SKEW):
        return published, True
    return produced, False


def item_direction(record: Mapping[str, Any], pair: str) -> tuple[str, float | None, float]:
    """(kind, expected signed direction d in [-1, 1] or None, confidence c in [0, 1])."""
    score = record.get("pair_score") if isinstance(record.get("pair_score"), dict) else {}
    release_direction = score.get("release_direction")
    if score.get("basis") == "release" and release_direction not in (None, 0):
        p = min(1.0, max(0.0, _float(score.get("p_direction"), 0.5)))
        side = 1.0 if _float(release_direction) > 0 else -1.0
        return "release", side * (2.0 * p - 1.0), p
    feed = record.get("feed") if isinstance(record.get("feed"), dict) else {}
    sign = orientation(pair, _currencies(str(feed.get("headline") or "")))
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
    # Oldest analysis first, by parsed time (string order breaks on mixed 'Z'/'+00:00' stamps). A
    # record with no analysis time cannot be placed before or after `now`, so it is left out.
    timed = [(produced, record) for record in history if (produced := _produced(record)) is not None]
    timed.sort(key=lambda pair_: pair_[0])
    seen: set[str] = set()
    duplicates = late = 0
    items: list[dict[str, Any]] = []
    for produced_at, record in timed:
        if produced_at > now:
            continue                                        # not analysed yet at `now` (replays)
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
            late += int(from_publisher and produced_at >= start)
            continue
        kind, d, c = item_direction(record, pair)
        age = max(0.0, (now - stamp).total_seconds())
        r = max(RECENCY_FLOOR, 1.0 - age / window.total_seconds())
        move, release_age = None, None
        if kind == "release":
            score = record.get("pair_score") or {}
            released = _parse(score.get("release_utc")) or stamp
            release_age = max(0.0, (now - released).total_seconds() / 60.0)
            if release_age <= RELEASE_MOVE_MINUTES and _float(score.get("expected_move_bps")) > 0:
                move = _float(score.get("expected_move_bps"))
        items.append({"kind": kind, "d": d, "c": c, "r": r, "headline": headline, "move": move,
                      "release_age": release_age, "source": str(feed.get("source") or "unknown")})

    directional = [i for i in items if i["d"] is not None]
    total = sum(i["r"] for i in directional)
    signed = sum(i["r"] * i["d"] for i in directional)
    bias = signed / total if total > 0 else 0.0
    semantic_score = max(-1.0, min(1.0, signed / (total + KAPPA)))
    activity_factor = min(1.0, len(items) / ACTIVITY_SATURATION_COUNT)
    moving = [i for i in items if i["move"] is not None]
    largest = max(moving, key=lambda i: i["move"]) if moving else None
    drivers = sorted(directional, key=lambda i: abs(i["d"]) * i["r"], reverse=True)[:5]
    return {
        "pair": pair,
        "source": "window_v2",
        "market_bias": round(bias, 4),
        "activity_factor": round(activity_factor, 4),
        "semantic_score": round(semantic_score, 4),
        "overall_sentiment": round(bias, 4),
        "confidence": round(sum(i["r"] * i["c"] for i in directional) / total, 4) if total > 0 else 0.0,
        "evidence_weight": round(total, 4),
        "bullish_count": sum(i["d"] > DIRECTION_THRESHOLD for i in directional),
        "bearish_count": sum(i["d"] < -DIRECTION_THRESHOLD for i in directional),
        "neutral_count": len(items) - sum(abs(i["d"]) > DIRECTION_THRESHOLD for i in directional),
        "item_count": len(items),
        "release_items": sum(i["kind"] == "release" for i in items),
        "news_items": sum(i["kind"] == "news" for i in items),
        "unoriented_items": sum(i["kind"] == "unoriented" for i in items),
        "duplicates_dropped": duplicates,
        "late_dropped": late,
        "expected_move_bps": round(largest["move"], 3) if largest else None,
        "release_age_minutes": round(largest["release_age"], 2) if largest else None,
        "top_drivers": [{"headline": i["headline"], "sentiment": f"{i['d']:+.2f}", "kind": i["kind"],
                         "source": i["source"]} for i in drivers],
        "updated_at": now.isoformat(),
        "window_minutes": int(window.total_seconds() // 60),
    }
