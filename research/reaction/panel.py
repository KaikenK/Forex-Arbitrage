"""Release × pair panel: signed log returns (bps) around each release plus same-clock controls.

Conventions (RQ1–RQ5 pre-registration, clarifications C20–C23):
  - Bars are HistData M1 stamped at close. The reference price is the first bar at/after the
    release time (entry lag ≤ 2 min), i.e. the close of the minute ending at the release when it
    exists — the same alignment as H2 and the positive control. Exit = first bar at/after the
    horizon end (lag ≤ 5 min). Fail closed: a missing bar gives NaN, never an imputed price.
  - Release-time returns r(0, h); arrival-time returns r(a, a + h) with a = 15 min.
  - Control v = mean |log return| over the same UTC clock window on the previous 20 weekdays,
    NaN unless ≥ 10 of them are computable (as H1 C3/C4).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import pandas as pd

RELEASE_HORIZONS = (1, 5, 15, 30, 60, 120)
ARRIVAL_OFFSET_MIN = 15
ARRIVAL_HORIZONS = (15, 45, 105)          # r(15, 30), r(15, 60), r(15, 120)
PRIMARY_RELEASE_H = 15
PRIMARY_ARRIVAL_H = 45
MAX_ENTRY_LAG_MIN = 2
MAX_EXIT_LAG_MIN = 5
CONTROL_DAYS = 20
CONTROL_MIN_VALID = 10
WINSOR_SIGMA = 5.0
STUDY_PAIRS = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF")
STUDY_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF")


def signed_log_returns(bar_times: np.ndarray, prices: np.ndarray, starts: np.ndarray,
                       ends: np.ndarray) -> np.ndarray:
    """log(P_exit / P_entry) in bps; entry = first bar at/after start, exit = first bar at/after
    end (epoch seconds, bars stamped at close). NaN when a lag is exceeded or a bar is missing."""
    out = np.full(len(starts), np.nan)
    n = len(bar_times)
    if n == 0 or len(starts) == 0:
        return out
    i = np.searchsorted(bar_times, starts, "left")
    j = np.searchsorted(bar_times, ends, "left")
    ok = (i < n) & (j < n)
    ii, jj = np.where(ok, i, 0), np.where(ok, j, 0)
    ok &= (bar_times[ii] - starts <= MAX_ENTRY_LAG_MIN * 60)
    ok &= (bar_times[jj] - ends <= MAX_EXIT_LAG_MIN * 60) & (jj > ii)
    ok &= (prices[ii] > 0) & (prices[jj] > 0)
    out[ok] = np.log(prices[jj][ok] / prices[ii][ok]) * 1e4
    return out


def previous_weekdays(event_times: np.ndarray, count: int = CONTROL_DAYS) -> np.ndarray:
    """(n, count) day offsets d ≥ 1 such that the UTC date of t − d days is a weekday."""
    weekday = ((np.asarray(event_times, dtype=np.int64) // 86400) + 3) % 7   # 1970-01-01 = Thursday
    offsets = np.zeros((len(weekday), count), dtype=np.int64)
    for row, wd in enumerate(weekday):
        found, d = 0, 0
        while found < count:
            d += 1
            if (wd - d) % 7 < 5:
                offsets[row, found] = d
                found += 1
    return offsets


def same_clock_control(bar_times: np.ndarray, prices: np.ndarray, event_times: np.ndarray,
                       start_min: int, end_min: int) -> np.ndarray:
    """Mean |r(start, end)| (bps) at the same UTC clock time on the previous 20 weekdays."""
    event_times = np.asarray(event_times, dtype=np.int64)
    shifted = event_times[:, None] - previous_weekdays(event_times) * 86400
    flat = shifted.ravel()
    values = np.abs(signed_log_returns(bar_times, prices, flat + start_min * 60,
                                       flat + end_min * 60)).reshape(shifted.shape)
    counts = np.isfinite(values).sum(axis=1)
    means = np.full(len(event_times), np.nan)
    enough = counts >= CONTROL_MIN_VALID
    means[enough] = np.nansum(values, axis=1)[enough] / counts[enough]
    return means


def hours_needed(event_times: np.ndarray, arrival_offset_min: int = ARRIVAL_OFFSET_MIN) -> list[datetime]:
    """UTC hours of bars needed for every horizon and every control day of these events."""
    event_times = np.asarray(event_times, dtype=np.int64)
    offsets = np.concatenate([np.zeros((len(event_times), 1), dtype=np.int64),
                              previous_weekdays(event_times)], axis=1)
    base = event_times[:, None] - offsets * 86400
    last = max(RELEASE_HORIZONS[-1], arrival_offset_min + ARRIVAL_HORIZONS[-1]) + MAX_EXIT_LAG_MIN
    hours: set[int] = set()
    for s, e in zip((base - 60).ravel() // 3600, (base + last * 60).ravel() // 3600):
        hours.update(range(int(s), int(e) + 1))
    return [datetime.fromtimestamp(h * 3600, tz=UTC) for h in sorted(hours)]


@dataclass(frozen=True)
class Release:
    release_id: str
    timestamp: datetime
    country: str
    currency: str
    indicator: str
    category: str
    z: float                       # winsorized at ±5σ
    news_sign: int | None          # sign_conventions.news_sign; None = ambiguous


def releases_from_calendar(frame: pd.DataFrame, news_sign_fn) -> list[Release]:
    """High-impact exact-time releases with z ≠ 0 in the study currencies (RQ unit)."""
    out: list[Release] = []
    for row in frame.to_dict("records"):
        currency = str(row.get("currencies") or "").split(";")[0]
        categories = [c for c in str(row.get("categories") or "").split(";") if c]
        z = row.get("standardized_surprise")
        if currency not in STUDY_CURRENCIES or not categories or z is None or not np.isfinite(z) or z == 0:
            continue
        timestamp = pd.Timestamp(row["timestamp"]).to_pydatetime()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        out.append(Release(
            release_id=str(row["event_id"]), timestamp=timestamp.astimezone(UTC),
            country=str(row["country"]), currency=currency, indicator=str(row["event_name"]),
            category=categories[0], z=float(np.clip(z, -WINSOR_SIGMA, WINSOR_SIGMA)),
            news_sign=news_sign_fn(str(row["country"]), str(row["event_name"]))))
    return out


def pair_side(pair: str, currency: str) -> int | None:
    base, _, quote = pair.partition("/")
    return 1 if currency == base else -1 if currency == quote else None


def panel_for_pair(releases: Sequence[Release], pair: str, bar_times: np.ndarray,
                   prices: np.ndarray, arrival_offset_min: int = ARRIVAL_OFFSET_MIN) -> pd.DataFrame:
    """One row per release for `pair`: returns at every horizon, controls, metadata.

    `arrival_offset_min` is when an article is assumed to arrive after its release (15 in the
    pre-registered RQ2; the arrival-latency study re-tests at the observed delay)."""
    t = np.array([int(r.timestamp.timestamp()) for r in releases], dtype=np.int64)
    rows = {
        "release_id": [r.release_id for r in releases],
        "moment": [r.timestamp.isoformat() for r in releases],
        "month": [r.timestamp.strftime("%Y-%m") for r in releases],
        "timestamp": [r.timestamp for r in releases],
        "currency": [r.currency for r in releases],
        "country": [r.country for r in releases],
        "indicator": [r.indicator for r in releases],
        "category": [r.category for r in releases],
        "z": [r.z for r in releases],
        "news_sign": [r.news_sign for r in releases],
        "pair": pair,
        "side": [pair_side(pair, r.currency) for r in releases],
    }
    for h in RELEASE_HORIZONS:
        rows[f"r{h}"] = signed_log_returns(bar_times, prices, t, t + h * 60)
    a = int(arrival_offset_min)
    rows["arrival_offset_min"] = a
    for h in ARRIVAL_HORIZONS:
        rows[f"a{h}"] = signed_log_returns(bar_times, prices, t + a * 60, t + (a + h) * 60)
    rows["v_release"] = same_clock_control(bar_times, prices, t, 0, PRIMARY_RELEASE_H)
    rows["v_arrival"] = same_clock_control(bar_times, prices, t, a, a + PRIMARY_ARRIVAL_H)
    return pd.DataFrame(rows)
