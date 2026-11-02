"""Window-30 (C35, RESEARCH_TRACKER §6, 2026-11-03): does the news of the last 30 minutes say
anything about a pair's next 30-60 minutes?

The product's semantic layer is a per-pair bias over a rolling 30-minute window that the
detector uses to rank opportunities. H6 tests a 24 h news bias; this tests the 30-minute window
on the frozen GKG sample, with the same aggregation the live window v2 uses
(`backend/core/sentiment_bridge.window_bias_v2`). Exploratory: no decision weight; BH over the
six tests below. Pre-registered before the freeze, before any GKG headline text was read.

Per major pair and signal time t (weekdays, every 30 min, 2019-10-01..2025-04-30):
  window   headlines with GKG timestamp in (t - 45 min, t - 15 min] (30 min, lagged 15 min for
           GKG's batch clock, as H6), canonical events only (dedup stage).
  bias     sum(r * c * d) / sum(r * c) over headlines whose currencies name exactly one of the
           pair's two currencies: d = FinBERT polarity, sign-flipped for the quote currency;
           c = FinBERT confidence; r = max(0.1, 1 - age / 30 min), age from t - 15 min.
  activity number of headlines naming the base or the quote currency in the window.
  caution  |bias| * min(1, activity / 5): the magnitude the live window turns into a penalty.
Targets: r_h = log return from t to t + h, h in {30, 60} min (HistData M1, entry/exit lags as
C13); abnormal move a_h = |r_h| / median |r_h| at the same clock time on the previous 20
weekdays (at least 10). Release flag: a high-impact exact-time release of the base or quote
currency in (t - 45 min, t + h].

Tests, each at h = 30 and 60 (six p-values, BH):
  T1 direction  rank IC of bias vs r_h (rows with a bias), date-clustered rank regression;
                hit rate reported; also on rows without a release.
  T2 activity   OLS of log a_h on log(1 + activity), release flag and pair dummies, SEs
                clustered by UTC date; coefficient on activity.
  T3 caution    rank IC of caution vs a_h, date-clustered; also on rows without a release.
Signal times whose window touches a missing day of the freeze are dropped (C17).

  python -m research.run_window30
"""
from __future__ import annotations

import bisect
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from research.eventstudy.stats import benjamini_hochberg
from research.semantic.currency_bias import MAJORS, forward_log_returns, ic_test
from research.semantic.walkforward import clustered_ols

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CALENDAR = ARTIFACTS / "calendar_events.parquet"
RESULTS = ARTIFACTS / "window30_results.json"

WINDOW_MINUTES = 30.0
LAG_MINUTES = 15.0                      # GKG batch clock, as H6 (core.GDELT_LAG_MINUTES)
STEP_MINUTES = 30
HORIZONS_MIN: tuple[int, ...] = (30, 60)
RECENCY_FLOOR = 0.1                     # live aggregator's recency shape
ACTIVITY_SATURATION = 5.0               # backend ACTIVITY_SATURATION_COUNT
BASELINE_DAYS = 20
BASELINE_MIN = 10
ENTRY_EXIT_LAG_MINUTES = (2, 5)         # C13
IC_METHOD = "date_clustered_rank_regression"   # C16


def orientation(pair: str, currencies: Sequence[str]) -> int:
    """+1 if the headline names the base currency only, -1 the quote only, else 0."""
    base, quote = pair.split("/")
    has_base, has_quote = base in currencies, quote in currencies
    if has_base == has_quote:
        return 0
    return 1 if has_base else -1


@dataclass
class PairNews:
    """Time-sorted headlines attributed to a pair: every one counts as activity; the oriented
    ones (exactly one of the two currencies named) carry a direction."""
    times: list[float] = field(default_factory=list)
    directions: list[float] = field(default_factory=list)     # oriented polarity; NaN if unoriented
    confidences: list[float] = field(default_factory=list)


def build_pair_news(events: pd.DataFrame, pairs: Sequence[str] = MAJORS) -> dict[str, PairNews]:
    """events: timestamp, currencies (';'-joined), polarity, confidence (canonical events only)."""
    out = {p: PairNews() for p in pairs}
    frame = events.assign(epoch=pd.to_datetime(events["timestamp"], utc=True).map(lambda s: s.timestamp()))
    for row in frame.sort_values("epoch").itertuples():
        codes = [c for c in str(row.currencies).split(";") if c]
        for pair, news in out.items():
            base, quote = pair.split("/")
            if base not in codes and quote not in codes:
                continue
            sign = orientation(pair, codes)
            news.times.append(float(row.epoch))
            news.directions.append(sign * float(row.polarity) if sign else float("nan"))
            news.confidences.append(float(row.confidence))
    return out


def window_signal(news: PairNews, times: np.ndarray, *, window_minutes: float = WINDOW_MINUTES,
                  lag_minutes: float = LAG_MINUTES) -> tuple[np.ndarray, np.ndarray]:
    """(bias, activity) per signal time; bias is NaN when no oriented headline is in the window."""
    bias = np.full(len(times), np.nan)
    activity = np.zeros(len(times), dtype=int)
    stamps = news.times
    for k, t in enumerate(times):
        end = t - lag_minutes * 60
        lo = bisect.bisect_right(stamps, end - window_minutes * 60)
        hi = bisect.bisect_right(stamps, end)
        activity[k] = hi - lo
        if hi == lo:
            continue
        d = np.asarray(news.directions[lo:hi], dtype=float)
        c = np.asarray(news.confidences[lo:hi], dtype=float)
        age = end - np.asarray(stamps[lo:hi], dtype=float)
        r = np.maximum(RECENCY_FLOOR, 1.0 - age / (window_minutes * 60))
        oriented = np.isfinite(d)
        w = (r * c)[oriented]
        if oriented.any() and w.sum() > 0:
            bias[k] = float(np.dot(w, d[oriented]) / w.sum())
    return bias, activity


def caution(bias: np.ndarray, activity: np.ndarray) -> np.ndarray:
    return np.abs(np.nan_to_num(bias)) * np.minimum(1.0, activity / ACTIVITY_SATURATION)


def signal_times_30(start: date, end: date, step_minutes: int = STEP_MINUTES) -> np.ndarray:
    times, day = [], start
    while day <= end:
        if day.weekday() < 5:
            midnight = datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp()
            times.extend(midnight + m * 60 for m in range(0, 24 * 60, step_minutes))
        day += timedelta(days=1)
    return np.asarray(times, dtype=float)


def drop_missing(times: np.ndarray, missing_dates: Sequence[str]) -> np.ndarray:
    """C17: drop signal times whose window (t - 45 min, t - 15 min] touches a missing day."""
    missing = {date.fromisoformat(d) for d in missing_dates}
    if not missing or not len(times):
        return times
    first = [datetime.fromtimestamp(t - (WINDOW_MINUTES + LAG_MINUTES) * 60, tz=UTC).date() for t in times]
    last = [datetime.fromtimestamp(t - LAG_MINUTES * 60, tz=UTC).date() for t in times]
    keep = np.array([a not in missing and b not in missing for a, b in zip(first, last)], dtype=bool)
    return times[keep]


def abnormal_move(times: np.ndarray, returns: np.ndarray) -> np.ndarray:
    """|r| over the median |r| at the same UTC clock time on the previous 20 weekdays (>= 10)."""
    frame = pd.DataFrame({"t": times, "abs": np.abs(returns)})
    frame["clock"] = (frame["t"] % 86400).astype(int)
    frame = frame.sort_values("t")
    frame["base"] = frame.groupby("clock")["abs"].transform(
        lambda s: s.shift(1).rolling(BASELINE_DAYS, min_periods=BASELINE_MIN).median())
    frame = frame.sort_index()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = (frame["abs"] / frame["base"]).to_numpy()
    ratio[~np.isfinite(ratio)] = np.nan
    return ratio


def release_flags(release_times: Mapping[str, Sequence[float]], pair: str, times: np.ndarray,
                  horizon_minutes: float) -> np.ndarray:
    """A high-impact exact-time release of the base or quote currency in (t - 45 min, t + h]."""
    stamps = sorted(t for c in pair.split("/") for t in release_times.get(c, ()))
    out = np.zeros(len(times), dtype=bool)
    for k, t in enumerate(times):
        i = bisect.bisect_right(stamps, t - (WINDOW_MINUTES + LAG_MINUTES) * 60)
        out[k] = i < len(stamps) and stamps[i] <= t + horizon_minutes * 60
    return out


def release_times_from_calendar(calendar: pd.DataFrame) -> dict[str, list[float]]:
    from research.eventstudy.sign_conventions import CURRENCY_FOR_COUNTRY

    frame = calendar[(calendar["importance"] == "high") & (calendar["timestamp_precision_minutes"] == 0)]
    out: dict[str, list[float]] = {}
    for row in frame.to_dict("records"):
        currency = CURRENCY_FOR_COUNTRY.get(str(row.get("country", "")))
        if currency:
            out.setdefault(currency, []).append(pd.Timestamp(row["timestamp"]).timestamp())
    return {c: sorted(v) for c, v in out.items()}


def build_panel(times: np.ndarray, news: Mapping[str, PairNews], returns: Mapping[int, Mapping[str, np.ndarray]],
                release_times: Mapping[str, Sequence[float]]) -> pd.DataFrame:
    """One row per (pair, signal time, horizon)."""
    frames = []
    for pair, pair_news in news.items():
        bias, activity = window_signal(pair_news, times)
        for h, by_pair in returns.items():
            r = by_pair[pair]
            frames.append(pd.DataFrame({
                "pair": pair, "time_s": times, "horizon": h, "bias": bias, "activity": activity,
                "caution": caution(bias, activity), "ret": r, "abnormal": abnormal_move(times, r),
                "release": release_flags(release_times, pair, times, h)}))
    panel = pd.concat(frames, ignore_index=True)
    stamps = pd.to_datetime(panel["time_s"], unit="s", utc=True)
    panel["utc_date"] = stamps.dt.date.astype(str)
    panel["month"] = stamps.dt.strftime("%Y-%m")
    return panel


def _ic(frame: pd.DataFrame, signal: str, target: str) -> dict:
    data = frame[np.isfinite(frame[signal]) & np.isfinite(frame[target])]
    ic, p = ic_test(data[signal].to_numpy(), data[target].to_numpy(), data["utc_date"].to_numpy(),
                    data["month"].to_numpy(), IC_METHOD)
    return {"n": int(len(data)), "ic": ic, "p": p}


def direction_test(panel: pd.DataFrame) -> dict:
    data = panel[np.isfinite(panel["bias"]) & np.isfinite(panel["ret"])]
    hit = data[(data["bias"] != 0) & (data["ret"] != 0)]
    out = {**_ic(data, "bias", "ret"),
           "hit_rate": float(np.mean(np.sign(hit["bias"]) == np.sign(hit["ret"]))) if len(hit) else None,
           "no_release": _ic(data[~data["release"]], "bias", "ret")}
    return out


def activity_test(panel: pd.DataFrame) -> dict:
    data = panel[np.isfinite(panel["abnormal"]) & (panel["abnormal"] > 0)].copy()
    data["log_abnormal"] = np.log(data["abnormal"])
    data["log_activity"] = np.log1p(data["activity"])
    data["release_flag"] = data["release"].astype(float)
    dummies = []
    for pair in sorted(data["pair"].unique())[1:]:
        name = f"fe_{pair.replace('/', '')}"
        data[name] = (data["pair"] == pair).astype(float)
        dummies.append(name)
    fit = clustered_ols(data, target="log_abnormal", features=["log_activity", "release_flag", *dummies],
                        cluster_col="utc_date")
    if fit is None:
        return {"n": 0, "beta": None, "p": None}
    coef = fit.coefficients["log_activity"]
    return {"n": fit.n, "n_dates": fit.n_clusters, "beta": coef.beta, "se": coef.se, "p": coef.p,
            "release_beta": fit.coefficients["release_flag"].beta}


def caution_test(panel: pd.DataFrame) -> dict:
    data = panel[np.isfinite(panel["abnormal"])]
    return {**_ic(data, "caution", "abnormal"), "no_release": _ic(data[~data["release"]], "caution", "abnormal")}


def run_tests(panel: pd.DataFrame) -> dict:
    results: dict[str, dict] = {}
    for h in sorted(panel["horizon"].unique()):
        sub = panel[panel["horizon"] == h]
        results[f"T1_direction_{h}"] = direction_test(sub)
        results[f"T2_activity_{h}"] = activity_test(sub)
        results[f"T3_caution_{h}"] = caution_test(sub)
    keys = [k for k, r in results.items() if r.get("p") is not None]
    for key, correction in zip(keys, benjamini_hochberg([results[k]["p"] for k in keys])):
        results[key]["q_bh"] = correction.q_value
    coverage = panel[panel["horizon"] == panel["horizon"].min()]
    return {"tests": results, "coverage": {
        "signal_rows": int(len(coverage)), "rows_with_activity": int((coverage["activity"] > 0).sum()),
        "rows_with_bias": int(np.isfinite(coverage["bias"]).sum()),
        "rows_with_release": int(coverage["release"].sum())}}


def _hours(times: np.ndarray, horizon_minutes: float) -> list[datetime]:
    hours: set[datetime] = set()
    for t in times:
        for anchor in (t, t + horizon_minutes * 60):
            stamp = datetime.fromtimestamp(anchor, tz=UTC).replace(minute=0, second=0, microsecond=0)
            hours.update((stamp, stamp + timedelta(hours=1)))
    return sorted(hours)


def _returns(times: np.ndarray) -> dict[int, dict[str, np.ndarray]]:
    from research.data.cache_paths import histdata_cache_dir
    from research.data.histdata import load_windowed_series

    entry_lag, exit_lag = ENTRY_EXIT_LAG_MINUTES
    out: dict[int, dict[str, np.ndarray]] = {h: {} for h in HORIZONS_MIN}
    hours = _hours(times, max(HORIZONS_MIN))
    for pair in MAJORS:
        series, missing = load_windowed_series(pair, hours, histdata_cache_dir())
        if missing:
            print(f"  {pair}: HistData years missing {missing} -> those rows fail closed")
        bars = np.array([t.timestamp() for t in series.times], dtype=float)
        prices = np.asarray(series.prices, dtype=float)
        for h in HORIZONS_MIN:
            out[h][pair] = forward_log_returns(bars, prices, times, horizon_minutes=h,
                                               entry_lag_minutes=entry_lag, exit_lag_minutes=exit_lag)
        del series, bars, prices
    return out


def main() -> None:
    from research.freeze_gdelt_sample import SAMPLE_END, SAMPLE_START, load_freeze, load_frozen_events
    from research.score_archive import load_scores, require_frozen_scores

    freeze = load_freeze()
    events = load_frozen_events(freeze)
    for stage in ("dedup", "finbert"):
        require_frozen_scores(stage, freeze["sha256"])
    dedup = load_scores("dedup")
    canonical = set(dedup.loc[dedup["canonical"], "event_id"].astype(str))
    finbert = load_scores("finbert")[["event_id", "polarity", "confidence"]].assign(
        event_id=lambda f: f["event_id"].astype(str))
    meta = events.assign(event_id=events["event_id"].astype(str))[["event_id", "timestamp", "currencies"]]
    scored = meta[meta["event_id"].isin(canonical)].merge(finbert, on="event_id")
    news = build_pair_news(scored)
    times = drop_missing(signal_times_30(SAMPLE_START, SAMPLE_END), freeze["missing_windows"])
    panel = build_panel(times, news, _returns(times), release_times_from_calendar(pd.read_parquet(CALENDAR)))
    report = {"freeze_sha256": freeze["sha256"], "generated_at": datetime.now(UTC).isoformat(),
              "framing": "exploratory (C35); no decision weight; BH over six tests",
              "settings": {"window_minutes": WINDOW_MINUTES, "lag_minutes": LAG_MINUTES, "step_minutes": STEP_MINUTES,
                           "horizons_min": list(HORIZONS_MIN), "pairs": list(MAJORS),
                           "sample": [SAMPLE_START.isoformat(), SAMPLE_END.isoformat()]},
              **run_tests(panel)}
    RESULTS.write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    print(json.dumps(report, indent=2, default=float))


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
