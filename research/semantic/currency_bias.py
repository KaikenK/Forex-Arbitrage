"""H6: a news-derived currency bias forecasts pair returns beyond a sentiment-only baseline.

LOCKED in RESEARCH_TRACKER §6 (entry "H6 PRE-REGISTERED", 2026-10-25). The signal formula is
`currency_bias_core.py` (shared byte-for-byte with the live service); this module assembles
the research panel and runs the locked tests:

  H6a (decides)  daily, four majors + USD/INR, signal at 21:00 UTC, target = the pair's log
                 return over the next 24 h. Nested model: return ~ baseline + pair bias + pair
                 FE, SEs clustered by UTC date. PASS = (i) pair-bias coefficient > 0, p < 0.05
                 AND (ii) expanding monthly walk-forward (12 training-only months) pooled OOS
                 MSE of baseline + pair bias below baseline alone.
  Reported only  OOS hit rate and rank IC; ablations (news only, event only); intraday 60 and
                 240 min on the majors sampled hourly, BH-corrected; USD/INR alone.
  Horizon rule   the live signal's horizon = the longest tested horizon whose OOS IC is > 0
                 with q <= 0.05, else "none established".

Clauses the locked text leaves open are in `UNRESOLVED` (None on purpose, A2's H1 pattern):
`require_resolved()` refuses until each is set by a §6 entry. Tests exercise every option.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
from scipy import stats

from research.data.tagging import currencies_to_pairs
from research.eventstudy.stats import benjamini_hochberg

from . import currency_bias_core as core
from .walkforward import _design, clustered_ols

H6_CURRENCIES: tuple[str, ...] = core.CURRENCIES
H6_PAIRS: tuple[str, ...] = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/INR")
MAJORS: tuple[str, ...] = H6_PAIRS[:4]
USDINR = "USD/INR"
SIGNAL_HOUR_UTC = 21
PRIMARY_HORIZON_MIN = 1440
SECONDARY_HORIZONS_MIN: tuple[int, ...] = (60, 240)
MIN_TRAIN_MONTHS = 12            # "12 training-only months, as C5"
ALPHA = 0.05
WINSOR_SIGMA = 5.0               # Option A surprises, winsorized as everywhere else

# Open clauses. Set only by a logged §6 decision.
# Set 2026-10-25 by H6 CLARIFICATIONS C6-C19 in RESEARCH_TRACKER §6, before any H6 data.
UNRESOLVED: dict[str, object] = {
    # E_c,t has no window in the locked text (B uses "the same 24 h window"). Hours (float).
    "event_window_hours": 24.0,                       # C6
    # B or E with zero total weight in its window: "raw_zero" (no news = neutral raw value),
    # "z_zero" (standardized component = 0) or "drop_row".
    "no_data_value": "z_zero",                        # C7
    # Novelty is undefined with < 3 prior same-currency stories (semantic/novelty.py):
    # "drop_headline" or "as_novel" (novelty 1.0).
    "novelty_missing": "drop_headline",               # C8
    # A tagged currency with no attribution term found in the headline text:
    # "drop_headline" or "indirect" (relevance 0.5).
    "relevance_unmatched": "indirect",                # C9
    # "z-scored on its own training-window history": "expanding_causal" (each signal time
    # uses only strictly earlier values; one z for both criteria and live) or
    # "training_months" (walk-forward: stats from the training months; criterion (i), an
    # in-sample test, then uses full-sample stats).
    "zscore_scheme": "training_months",               # C10
    # Minimum earlier values before an expanding z exists (int >= 2; ignored otherwise).
    "zscore_min_history": 30,                         # C11
    # "headlines tagged directly to the pair": usd_policy for USD-only headlines —
    # "none" (H5 baseline convention) or "study" (H1 C2 convention).
    "baseline_usd_policy": "study",                   # C12
    # HistData targets: (max entry lag, max exit lag) in minutes, fail closed.
    "intraday_entry_exit_lag_minutes": (2, 5),        # C13
    # USD/INR "dated as in the R1 re-run": "next_bar" = log(c[d+1]/c[d]) for a signal at
    # 21:00 UTC on bar date d; "skip_one_bar" = log(c[d+2]/c[d+1]), leak-safe if the Yahoo
    # close of date d is observed before 21:00 UTC (R1 re-run finding).
    "usdinr_target_bar": "skip_one_bar",              # C14
    # What "out-of-sample IC" is computed on: "signal" (pair bias vs return on test-month
    # rows) or "model_prediction" (full-model OOS prediction vs return).
    "ic_of": "model_prediction",                      # C15
    # p-value of the OOS IC: "date_clustered_rank_regression" or "monthly_ic_ttest".
    "ic_inference": "date_clustered_rank_regression",  # C16
    # Signal times whose news window touches a GDELT window missing from the freeze:
    # "drop_signal_time" (fail closed) or "treat_as_no_news".
    "missing_window_rule": "drop_signal_time",        # C17
}

H6B_UNRESOLVED: dict[str, object] = {
    # "detector-only vs + risk channel vs + direction channel": "cumulative"
    # (detector, detector+risk, detector+risk+direction) or "separate" (each channel alone).
    "model_nesting": "cumulative",                    # C18
    # Next poll where a leg has no two-sided quote: "not_crossed" or "drop".
    "one_sided_next_quote": "drop",                   # C19
}


class ProtocolUnresolved(RuntimeError):
    """A locked-protocol clause needs a logged §6 decision before H6 can run."""


def require_resolved(settings: Mapping[str, object] = UNRESOLVED) -> None:
    missing = [name for name, value in settings.items() if value is None]
    if missing:
        raise ProtocolUnresolved(
            "H6 cannot run: these clauses of the locked protocol need a logged §6 decision: "
            + ", ".join(missing))


# ---------------------------------------------------------------- streams

@dataclass
class CurrencyStream:
    """Headlines attributed to one currency, sorted by timestamp (epoch seconds)."""
    timestamps: list[float] = field(default_factory=list)
    polarities: list[float] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)
    novelties: list[float | None] = field(default_factory=list)
    relevances: list[float | None] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)


def _epoch(stamp) -> float:
    ts = pd.Timestamp(stamp)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.timestamp()


def build_currency_streams(rows: pd.DataFrame, currencies: Sequence[str] = H6_CURRENCIES) -> dict[str, CurrencyStream]:
    """rows: event_id, timestamp, headline, news_currency, sentiment, sentiment_confidence, novelty."""
    streams = {c: CurrencyStream() for c in currencies}
    frame = rows.assign(_t=rows["timestamp"].map(_epoch)).sort_values(["_t", "event_id"])
    for row in frame.itertuples():
        stream = streams.get(row.news_currency)
        if stream is None:
            continue
        novelty = getattr(row, "novelty", None)
        novelty = None if novelty is None or (isinstance(novelty, float) and math.isnan(novelty)) else float(novelty)
        stream.timestamps.append(float(row._t))
        stream.polarities.append(float(row.sentiment))
        stream.confidences.append(float(row.sentiment_confidence))
        stream.novelties.append(novelty)
        stream.relevances.append(core.relevance(str(row.headline), row.news_currency))
        stream.event_ids.append(str(row.event_id))
    return streams


def build_release_streams(calendar: pd.DataFrame, currencies: Sequence[str] = H6_CURRENCIES) -> dict[str, tuple[list[float], list[float]]]:
    """Sign-oriented Option A surprises of high-impact, exact-time releases, per currency."""
    from research.eventstudy.sign_conventions import CURRENCY_FOR_COUNTRY, news_sign

    frame = calendar[(calendar["importance"] == "high")
                     & (calendar["timestamp_precision_minutes"] == 0)
                     & calendar["standardized_surprise"].notna()]
    items: dict[str, list[tuple[float, float]]] = {c: [] for c in currencies}
    for row in frame.to_dict("records"):
        currency = CURRENCY_FOR_COUNTRY.get(str(row.get("country", "")))
        if currency not in items:
            continue
        sign = news_sign(str(row["country"]), str(row["event_name"]))
        if sign is None:
            continue
        z = float(np.clip(row["standardized_surprise"], -WINSOR_SIGMA, WINSOR_SIGMA))
        items[currency].append((_epoch(row["timestamp"]), sign * z))
    out = {}
    for c, values in items.items():
        values.sort()
        out[c] = ([t for t, _ in values], [v for _, v in values])
    return out


# ---------------------------------------------------------------- signal times

def signal_times(start: date, end: date, *, hourly: bool) -> np.ndarray:
    """Weekday signal times (epoch s): 21:00 UTC daily, or every UTC hour."""
    times = []
    day = start
    while day <= end:
        if day.weekday() < 5:
            hours = range(24) if hourly else (SIGNAL_HOUR_UTC,)
            for hour in hours:
                times.append(datetime(day.year, day.month, day.day, hour, tzinfo=UTC).timestamp())
        day += timedelta(days=1)
    return np.asarray(times, dtype=float)


def apply_missing_window_rule(times: np.ndarray, missing_dates: Sequence[str], rule: str) -> np.ndarray:
    """Drop signal times whose news window (t-24 h, t-15 min] touches a missing GDELT day."""
    if rule == "treat_as_no_news":
        return times
    if rule != "drop_signal_time":
        raise ValueError(f"unknown missing_window_rule {rule!r}")
    missing = {date.fromisoformat(d) for d in missing_dates}
    keep = []
    for t in times:
        first = datetime.fromtimestamp(t - core.NEWS_WINDOW_HOURS * 3600, tz=UTC).date()
        last = datetime.fromtimestamp(t - core.GDELT_LAG_MINUTES * 60, tz=UTC).date()
        days = {first + timedelta(days=k) for k in range((last - first).days + 1)}
        keep.append(not (days & missing))
    return times[np.asarray(keep, dtype=bool)] if len(times) else times


# ---------------------------------------------------------------- raw components

def raw_signal_frame(
    streams: Mapping[str, CurrencyStream],
    releases: Mapping[str, tuple[list[float], list[float]]],
    times: np.ndarray,
    settings: Mapping[str, object],
) -> pd.DataFrame:
    """Index = signal time; B_<c>, E_<c> raw (NaN = no data in the window)."""
    data: dict[str, list[float]] = {}
    for c, stream in streams.items():
        b_values, e_values = [], []
        rel_t, rel_v = releases.get(c, ([], []))
        for t in times:
            comp = core.news_component(
                stream.timestamps, stream.polarities, stream.confidences, stream.novelties,
                stream.relevances, float(t), novelty_missing=str(settings["novelty_missing"]),
                relevance_unmatched=str(settings["relevance_unmatched"]))
            b_values.append(np.nan if comp.value is None else comp.value)
            e = core.event_component(rel_t, rel_v, float(t), window_hours=float(settings["event_window_hours"]))
            e_values.append(np.nan if e is None else e)
        data[f"B_{c}"] = b_values
        data[f"E_{c}"] = e_values
    return pd.DataFrame(data, index=pd.Index(times, name="time_s"))


@dataclass
class PairStream:
    timestamps: list[float] = field(default_factory=list)
    polarities: list[float] = field(default_factory=list)
    confidences: list[float] = field(default_factory=list)


def build_pair_streams(events: pd.DataFrame, pairs: Sequence[str], usd_policy: str) -> dict[str, PairStream]:
    """events: event_id, timestamp, currencies (';'), polarity, confidence (canonical only)."""
    streams = {p: PairStream() for p in pairs}
    frame = events.assign(_t=events["timestamp"].map(_epoch)).sort_values(["_t", "event_id"])
    for row in frame.itertuples():
        codes = [c for c in str(row.currencies).split(";") if c]
        for pair in currencies_to_pairs(codes, usd_policy=usd_policy):
            stream = streams.get(pair)
            if stream is not None:
                stream.timestamps.append(float(row._t))
                stream.polarities.append(float(row.polarity))
                stream.confidences.append(float(row.confidence))
    return streams


def baseline_frame(pair_streams: Mapping[str, PairStream], times: np.ndarray) -> pd.DataFrame:
    """Sentiment-only baseline: confidence-weighted mean polarity over the same 24 h window."""
    import bisect

    data = {}
    for pair, s in pair_streams.items():
        values = []
        for t in times:
            lo = bisect.bisect_right(s.timestamps, t - core.NEWS_WINDOW_HOURS * 3600)
            hi = bisect.bisect_right(s.timestamps, t - core.GDELT_LAG_MINUTES * 60)
            w = np.asarray(s.confidences[lo:hi], dtype=float)
            values.append(float(np.dot(w, s.polarities[lo:hi]) / w.sum()) if hi > lo and w.sum() > 0 else np.nan)
        data[pair] = values
    return pd.DataFrame(data, index=pd.Index(times, name="time_s"))


# ---------------------------------------------------------------- standardization

def _prepare(raw: pd.Series, no_data_value: str) -> pd.Series:
    """raw_zero: no data is a raw 0 and enters the history; otherwise NaN stays missing."""
    return raw.fillna(0.0) if no_data_value == "raw_zero" else raw


def _finish(z: pd.Series, raw: pd.Series, no_data_value: str) -> pd.Series:
    if no_data_value == "z_zero":
        return z.where(raw.notna(), 0.0)
    return z           # raw_zero already filled; drop_row leaves NaN


def zscore_expanding(raw: pd.Series, *, min_history: int, no_data_value: str) -> pd.Series:
    """z_t from strictly earlier values only (no look-ahead)."""
    x = _prepare(raw, no_data_value)
    prior = x.shift(1)
    mean = prior.expanding(min_periods=min_history).mean()
    std = prior.expanding(min_periods=min_history).std(ddof=1)
    z = (x - mean) / std.where(std > 0)
    return _finish(z, raw, no_data_value)


def zscore_from_mask(raw: pd.Series, train_mask: np.ndarray, *, no_data_value: str) -> pd.Series:
    x = _prepare(raw, no_data_value)
    train = x[np.asarray(train_mask, dtype=bool)].dropna()
    std = float(train.std(ddof=1)) if len(train) > 1 else float("nan")
    z = (x - float(train.mean())) / std if std and std > 0 else x * np.nan
    return _finish(z, raw, no_data_value)


def standardized_frame(raw_frame: pd.DataFrame, settings: Mapping[str, object],
                       train_mask: np.ndarray | None = None) -> pd.DataFrame:
    scheme = settings["zscore_scheme"]
    no_data = str(settings["no_data_value"])
    out = {}
    for column in raw_frame.columns:
        if scheme == "expanding_causal":
            out[f"z{column}"] = zscore_expanding(raw_frame[column], min_history=int(settings["zscore_min_history"]),
                                                 no_data_value=no_data)
        elif scheme == "training_months":
            mask = np.ones(len(raw_frame), dtype=bool) if train_mask is None else train_mask
            out[f"z{column}"] = zscore_from_mask(raw_frame[column], mask, no_data_value=no_data)
        else:
            raise ValueError(f"unknown zscore_scheme {scheme!r}")
    return pd.DataFrame(out, index=raw_frame.index)


def pair_features(zframe: pd.DataFrame, pairs: Sequence[str]) -> dict[str, pd.DataFrame]:
    """Per pair: pair_bias (locked), news_only and event_only (ablations)."""
    out = {}
    for pair in pairs:
        base, _, quote = pair.partition("/")
        bias_base = zframe[f"zB_{base}"] + zframe[f"zE_{base}"]
        bias_quote = zframe[f"zB_{quote}"] + zframe[f"zE_{quote}"]
        out[pair] = pd.DataFrame({
            "pair_bias": bias_base - bias_quote,
            "news_only": zframe[f"zB_{base}"] - zframe[f"zB_{quote}"],
            "event_only": zframe[f"zE_{base}"] - zframe[f"zE_{quote}"],
        }, index=zframe.index)
    return out


# ---------------------------------------------------------------- targets

def forward_log_returns(bar_times: np.ndarray, prices: np.ndarray, times: np.ndarray, *,
                        horizon_minutes: float, entry_lag_minutes: float, exit_lag_minutes: float) -> np.ndarray:
    """log(P[first bar >= t + h] / P[first bar >= t]); NaN when a lag guard fails."""
    bar_times = np.asarray(bar_times, dtype=float)
    out = np.full(len(times), np.nan)
    if len(bar_times) == 0:
        return out
    entry = np.searchsorted(bar_times, times, side="left")
    exit_ = np.searchsorted(bar_times, times + horizon_minutes * 60, side="left")
    for k, (i, j) in enumerate(zip(entry, exit_)):
        if i >= len(bar_times) or j >= len(bar_times):
            continue
        if bar_times[i] - times[k] > entry_lag_minutes * 60:
            continue
        if bar_times[j] - (times[k] + horizon_minutes * 60) > exit_lag_minutes * 60:
            continue
        out[k] = math.log(prices[j] / prices[i])
    return out


def usdinr_daily_returns(closes: pd.Series, times: np.ndarray, mode: str) -> np.ndarray:
    """closes indexed by bar date (ascending). A signal at 21:00 UTC on date d."""
    dates = list(closes.index)
    position = {d: i for i, d in enumerate(dates)}
    out = np.full(len(times), np.nan)
    for k, t in enumerate(times):
        d = datetime.fromtimestamp(t, tz=UTC).date()
        i = position.get(d)
        if i is None:
            continue
        a, b = (i, i + 1) if mode == "next_bar" else (i + 1, i + 2) if mode == "skip_one_bar" else (None, None)
        if a is None:
            raise ValueError(f"unknown usdinr_target_bar {mode!r}")
        if b < len(dates):
            out[k] = math.log(float(closes.iloc[b]) / float(closes.iloc[a]))
    return out


# ---------------------------------------------------------------- panel

def assemble_panel(times: np.ndarray, targets: Mapping[str, np.ndarray], baseline: pd.DataFrame,
                   settings: Mapping[str, object]) -> pd.DataFrame:
    """One row per (signal time, pair): target (bps), baseline, calendar keys, pair FE."""
    no_data = settings["no_data_value"]
    frames = []
    for pair, target in targets.items():
        base = baseline[pair].reindex(times).to_numpy()
        if no_data in ("raw_zero", "z_zero"):
            base = np.where(np.isnan(base), 0.0, base)
        stamps = pd.to_datetime(times, unit="s", utc=True)
        frames.append(pd.DataFrame({
            "time_s": times, "pair": pair, "target": np.asarray(target) * 1e4, "baseline": base,
            "utc_date": stamps.strftime("%Y-%m-%d"), "month": stamps.strftime("%Y-%m"),
        }))
    panel = pd.concat(frames, ignore_index=True)
    pairs = sorted(panel["pair"].unique())
    for pair in pairs[1:]:
        panel[f"fe_{pair}"] = (panel["pair"] == pair).astype(float)
    return panel


def fe_columns(panel: pd.DataFrame) -> list[str]:
    return [c for c in panel.columns if c.startswith("fe_")]


def attach_features(panel: pd.DataFrame, features: Mapping[str, pd.DataFrame]) -> pd.DataFrame:
    out = panel.copy()
    for name in ("pair_bias", "news_only", "event_only"):
        out[name] = np.nan
    for pair, frame in features.items():
        rows = out["pair"] == pair
        aligned = frame.reindex(out.loc[rows, "time_s"].to_numpy())
        for name in ("pair_bias", "news_only", "event_only"):
            out.loc[rows, name] = aligned[name].to_numpy()
    return out


# ---------------------------------------------------------------- tests

@dataclass
class WalkForward:
    n_test: int
    n_months: int
    mse_baseline: float
    mse_full: float
    hit_rate_baseline: float | None
    hit_rate_full: float | None
    ic: float | None
    ic_p: float | None
    ic_of: str

    @property
    def improves(self) -> bool:
        return self.mse_full < self.mse_baseline


def _hit_rate(y: np.ndarray, prediction: np.ndarray) -> float | None:
    mask = (y != 0) & (prediction != 0) & np.isfinite(prediction)
    return float(np.mean(np.sign(y[mask]) == np.sign(prediction[mask]))) if mask.any() else None


def ic_test(signal: np.ndarray, y: np.ndarray, dates: np.ndarray, months: np.ndarray,
            method: str) -> tuple[float | None, float | None]:
    """Rank IC and its two-sided p-value."""
    mask = np.isfinite(signal) & np.isfinite(y)
    signal, y, dates, months = signal[mask], y[mask], dates[mask], months[mask]
    if len(y) < 3 or np.std(signal) == 0 or np.std(y) == 0:
        return None, None
    ic = float(stats.spearmanr(signal, y).statistic)
    if method == "date_clustered_rank_regression":
        frame = pd.DataFrame({"ry": stats.rankdata(y) / len(y), "rs": stats.rankdata(signal) / len(y),
                              "date": dates})
        fit = clustered_ols(frame, target="ry", features=["rs"], cluster_col="date")
        return ic, (None if fit is None else fit.coefficients["rs"].p)
    if method == "monthly_ic_ttest":
        per_month = []
        for month in np.unique(months):
            m = months == month
            if m.sum() >= 10 and np.std(signal[m]) > 0 and np.std(y[m]) > 0:
                per_month.append(stats.spearmanr(signal[m], y[m]).statistic)
        if len(per_month) < 3:
            return ic, None
        return ic, float(stats.ttest_1samp(per_month, 0.0).pvalue)
    raise ValueError(f"unknown ic_inference {method!r}")


def criterion_i(panel: pd.DataFrame, feature: str = "pair_bias") -> dict:
    fit = clustered_ols(panel, target="target", features=["baseline", feature, *fe_columns(panel)],
                        cluster_col="utc_date")
    if fit is None:
        return {"n": 0, "passes": False}
    coef = fit.coefficients[feature]
    return {"n": fit.n, "n_dates": fit.n_clusters, "beta": coef.beta, "se": coef.se, "p": coef.p,
            "passes": bool(coef.beta > 0 and coef.p < ALPHA)}


def walk_forward(
    panel: pd.DataFrame,
    raw_frame: pd.DataFrame,
    settings: Mapping[str, object],
    *,
    pairs: Sequence[str],
    feature: str = "pair_bias",
    min_train_months: int = MIN_TRAIN_MONTHS,
    static_features: Mapping[str, pd.DataFrame] | None = None,
) -> WalkForward | None:
    """Expanding monthly refit; standardization follows `zscore_scheme` inside each fold."""
    months = sorted(panel["month"].unique())
    raw_months = pd.to_datetime(raw_frame.index.to_numpy(), unit="s", utc=True).strftime("%Y-%m").to_numpy()
    fe = fe_columns(panel)
    ys, pa, pb, sig, dts, mos = [], [], [], [], [], []
    used_months = 0
    for month in months[min_train_months:]:
        if settings["zscore_scheme"] == "training_months":
            z = standardized_frame(raw_frame, settings, train_mask=raw_months < month)
            fold = attach_features(panel, pair_features(z, pairs))
        else:
            fold = attach_features(panel, static_features)
        fold = fold.dropna(subset=["target", "baseline", feature])
        train, test = fold[fold["month"] < month], fold[fold["month"] == month]
        if test.empty or len(train) <= len(fe) + 3:
            continue
        design_a = _design(train, ["baseline", *fe])
        design_b = _design(train, ["baseline", feature, *fe])
        coef_a, *_ = np.linalg.lstsq(design_a, train["target"].to_numpy(), rcond=None)
        coef_b, *_ = np.linalg.lstsq(design_b, train["target"].to_numpy(), rcond=None)
        ys.append(test["target"].to_numpy())
        pa.append(_design(test, ["baseline", *fe]) @ coef_a)
        pb.append(_design(test, ["baseline", feature, *fe]) @ coef_b)
        sig.append(test[feature].to_numpy())
        dts.append(test["utc_date"].to_numpy())
        mos.append(test["month"].to_numpy())
        used_months += 1
    if not ys:
        return None
    y, a, b = np.concatenate(ys), np.concatenate(pa), np.concatenate(pb)
    signal = np.concatenate(sig) if settings["ic_of"] == "signal" else b
    ic, ic_p = ic_test(signal, y, np.concatenate(dts), np.concatenate(mos), str(settings["ic_inference"]))
    return WalkForward(n_test=len(y), n_months=used_months, mse_baseline=float(np.mean((y - a) ** 2)),
                       mse_full=float(np.mean((y - b) ** 2)), hit_rate_baseline=_hit_rate(y, a),
                       hit_rate_full=_hit_rate(y, b), ic=ic, ic_p=ic_p, ic_of=str(settings["ic_of"]))


def full_sample_features(raw_frame: pd.DataFrame, settings: Mapping[str, object],
                         pairs: Sequence[str]) -> dict[str, pd.DataFrame]:
    """Features for criterion (i): expanding z, or full-sample stats under training_months."""
    return pair_features(standardized_frame(raw_frame, settings), pairs)


def h6_test(panel: pd.DataFrame, raw_frame: pd.DataFrame, settings: Mapping[str, object], *,
            pairs: Sequence[str], feature: str = "pair_bias") -> dict:
    """Both locked criteria for one horizon/sample. Verdict only for the primary call."""
    static = full_sample_features(raw_frame, settings, pairs)
    full = attach_features(panel, static).dropna(subset=["target", "baseline", feature])
    crit_i = criterion_i(full, feature)
    wf = walk_forward(panel, raw_frame, settings, pairs=pairs, feature=feature,
                      static_features=static if settings["zscore_scheme"] == "expanding_causal" else None)
    crit_ii = None if wf is None else {
        "n_test": wf.n_test, "n_months": wf.n_months, "mse_baseline": wf.mse_baseline,
        "mse_full": wf.mse_full, "passes": wf.improves,
        "hit_rate_baseline": wf.hit_rate_baseline, "hit_rate_full": wf.hit_rate_full,
        "ic": wf.ic, "ic_p": wf.ic_p, "ic_of": wf.ic_of}
    passed = bool(crit_i.get("passes") and crit_ii is not None and crit_ii["passes"])
    return {"feature": feature, "criterion_i": crit_i, "criterion_ii": crit_ii, "passes": passed}


def bh_secondaries(results: Mapping[int, dict]) -> dict[int, float | None]:
    """BH over the intraday secondaries' criterion-(i) p-values."""
    keys = [h for h, r in results.items() if r["criterion_i"].get("p") is not None]
    corrections = benjamini_hochberg([results[h]["criterion_i"]["p"] for h in keys])
    q = {h: None for h in results}
    for h, correction in zip(keys, corrections):
        q[h] = correction.q_value
    return q


def horizon_output(ic_by_horizon: Mapping[int, tuple[float | None, float | None]]) -> dict:
    """Longest tested horizon whose OOS IC > 0 with BH q <= 0.05; else none established."""
    tested = [h for h, (ic, p) in ic_by_horizon.items() if ic is not None and p is not None]
    corrections = benjamini_hochberg([ic_by_horizon[h][1] for h in tested])
    q = {h: c.q_value for h, c in zip(tested, corrections)}
    passing = [h for h in tested if ic_by_horizon[h][0] > 0 and q[h] <= ALPHA]
    if not passing:
        return {"horizon_minutes": None, "label": "none established", "q_values": q}
    best = max(passing)
    return {"horizon_minutes": best, "label": f"{best} min", "q_values": q}
