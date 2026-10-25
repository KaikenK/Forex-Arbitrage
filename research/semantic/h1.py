"""H1 under the LOCKED PROTOCOL (RESEARCH_TRACKER §6, 2026-10-25, approved before any H1 result).

Locked clauses implemented here (quoted where wording matters):

  Unit       deduplicated GDELT event x study pair it is tagged to.
  Target     majors: "absolute log return from the first HistData M1 bar at/after (GDELT
             timestamp - 15 min) to the first bar at/after (GDELT timestamp + 60 min); entry
             lag <= 2 min, exit lag <= 5 min, fail closed." USD/INR (secondary): absolute
             daily return, R2 design.
  Exclusion  (1) "any event whose window [t-45 min, t+90 min] contains a high-impact,
             exact-time calendar release of either currency in the pair";
             (2) market-wrap headlines (`MARKET_WRAP_FX_TERMS` x `MARKET_WRAP_MOVE_TERMS`).
  Controls   "mean absolute return over the same 75-minute clock window on the previous 20
             trading days for that pair, plus pair fixed effects."
  Baseline   FinBERT absolute polarity (lexicon reported as robustness only).
  Embedder   sentence-transformer is the sole primary embedder; TF-IDF and hashing reported,
             no decision weight.
  Pass       both (i) novelty coefficient > 0 with p < 0.05 in the nested model (controls +
             FinBERT baseline + novelty), SEs clustered by UTC date; and (ii) "expanding window
             refit by calendar month, out-of-sample MSE of the novelty model below the baseline
             model's over the pooled test months". Otherwise not supported.
  Kill       "< 30 events after exclusions = underpowered".

Clauses the locked text leaves open are in `UNRESOLVED`. They are `None` on purpose: the
coordinator instructed "if anything is ambiguous or unimplementable, stop and report rather
than choosing", so `require_resolved()` refuses to run H1 until each is set in a §6 entry.
Tests exercise every option.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from research.data.tagging import ISO_CODES

from .walkforward import MonthlyComparison, clustered_ols, monthly_expanding_comparison

EMBEDDING_BACKENDS: tuple[str, ...] = ("sentence_transformer", "tfidf_svd", "hashing")
PRIMARY_EMBEDDER = "sentence_transformer"
MAJORS: tuple[str, ...] = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF")
SECONDARY_PAIR = "USD/INR"

TARGET_START_OFFSET_MIN = -15
TARGET_END_OFFSET_MIN = 60
MAX_ENTRY_LAG_MIN = 2
MAX_EXIT_LAG_MIN = 5
RELEASE_WINDOW_BEFORE_MIN = 45
RELEASE_WINDOW_AFTER_MIN = 90
CONTROL_TRADING_DAYS = 20
ALPHA = 0.05
MIN_EVENTS = 30

# Quoted verbatim from the locked protocol (§6, 2026-10-25), exclusion (2).
MARKET_WRAP_FX_TERMS = r"dollar|greenback|euro|yen|sterling|pound|franc|rupee|forex|fx|currenc(y|ies)"
MARKET_WRAP_MOVE_TERMS = (
    r"rise[sn]?|rose|rising|gain(s|ed)?|climb(s|ed)?|jump(s|ed)?|surge[sd]?|soar(s|ed)?|"
    r"rall(y|ies|ied)|fall(s|en)?|fell|drop(s|ped)?|slip(s|ped)?|slide[sd]?|slid|tumble[sd]?|"
    r"plunge[sd]?|sink(s)?|sank|weaken(s|ed)?|strengthen(s|ed)?|firm(s|ed)?|ease[sd]?|steady|"
    r"steadies|edge[sd]? (higher|lower|up|down)|(hit|hits|touch(es)?) .{0,20}(high|low)|"
    r"record (high|low)|(week|month|year)-?(high|low)"
)
# "or a pair code such as `EUR/USD`, `USDJPY`": two ISO codes of the project's currency
# universe (`data/tagging.ISO_CODES`), with or without a slash.
_ISO = "|".join(sorted(ISO_CODES))
MARKET_WRAP_PAIR_CODE = rf"(?:{_ISO})/?(?:{_ISO})"

# Open clauses. See module docstring; set only by a logged §6 decision.
# Set 2026-10-25 by CLARIFICATIONS C1-C5 in RESEARCH_TRACKER §6, before any H1 result.
UNRESOLVED: dict[str, object] = {
    # The locked regex has no word boundaries. Read literally, "ease" matches "release" and
    # "increase", "rise" matches "surprise", "gain" matches "against", "euro" matches
    # "Europe", "fx" matches "effects": most macro headlines become "market wraps".
    # True = match whole words only; False = the literal substring reading.
    "market_wrap_word_boundaries": True,     # C1
    # "study pair it is tagged to": GDELT has always used usd_policy="none" (a USD-only
    # headline has no pair); the 2026-10-24 "study" policy maps USD to all four majors. The
    # choice changes n materially. "none" | "study".
    "gdelt_usd_policy": "study",             # C2
    # "previous 20 trading days": the 20 previous weekdays, averaging those whose window is
    # computable ("weekdays"), or the 20 most recent weekdays whose window is computable
    # ("valid_days", looking back at most 40 weekdays).
    "control_day_rule": "weekdays",          # C3
    # Minimum number of computable prior days for a control value (int >= 1).
    "control_min_valid_days": 10,            # C4
    # "expanding window refit by calendar month": how many leading months are training-only
    # before the first test month (int >= 1).
    "walk_forward_min_train_months": 12,     # C5
}


class ProtocolUnresolved(RuntimeError):
    """A locked-protocol clause needs a logged decision before H1 can run."""


def require_resolved(settings: Mapping[str, object] = UNRESOLVED) -> None:
    missing = [name for name, value in settings.items() if value is None]
    if missing:
        raise ProtocolUnresolved(
            "H1 cannot run: these clauses of the locked protocol need a logged §6 decision: "
            + ", ".join(missing))


# ---------------------------------------------------------------- exclusion (2): market wraps

def _wrap_patterns(word_boundaries: bool) -> tuple[re.Pattern, re.Pattern]:
    if word_boundaries:
        fx = rf"\b(?:{MARKET_WRAP_FX_TERMS}|{MARKET_WRAP_PAIR_CODE})\b"
        move = rf"\b(?:{MARKET_WRAP_MOVE_TERMS})\b"
    else:
        fx = rf"(?:{MARKET_WRAP_FX_TERMS}|{MARKET_WRAP_PAIR_CODE})"
        move = rf"(?:{MARKET_WRAP_MOVE_TERMS})"
    return re.compile(fx, re.IGNORECASE), re.compile(move, re.IGNORECASE)


def market_wrap_mask(headlines: Sequence[str], *, word_boundaries: bool) -> np.ndarray:
    """True where a headline contains an FX term (or pair code) AND a move term."""
    fx, move = _wrap_patterns(word_boundaries)
    return np.array([bool(fx.search(str(h))) and bool(move.search(str(h))) for h in headlines], dtype=bool)


# ---------------------------------------------------------------- exclusion (1): releases

def _epoch_seconds(values) -> np.ndarray:
    return pd.to_datetime(pd.Series(list(values)), utc=True).astype("int64").to_numpy() // 10**9


def release_window_mask(events: pd.DataFrame, calendar: pd.DataFrame) -> np.ndarray:
    """True where [t-45, t+90] (inclusive) holds a high-impact exact-time release of either
    currency in the pair. Exact time = `timestamp_precision_minutes == 0`, which includes
    release times filled from validated agency clocks (§6, 2026-10-24)."""
    releases = calendar[(calendar["importance"] == "high") & (calendar["timestamp_precision_minutes"] == 0)]
    by_currency: dict[str, np.ndarray] = {}
    for currency, group in releases.groupby(releases["currencies"].astype(str)):
        by_currency[currency] = np.sort(_epoch_seconds(group["timestamp"]))
    times = _epoch_seconds(events["timestamp"])
    excluded = np.zeros(len(events), dtype=bool)
    for i, (t, pair) in enumerate(zip(times, events["pair"])):
        lo, hi = t - RELEASE_WINDOW_BEFORE_MIN * 60, t + RELEASE_WINDOW_AFTER_MIN * 60
        for currency in str(pair).split("/"):
            stamps = by_currency.get(currency)
            if stamps is not None and np.searchsorted(stamps, lo, "left") < np.searchsorted(stamps, hi, "right"):
                excluded[i] = True
                break
    return excluded


# ---------------------------------------------------------------- target and control

def window_abs_log_returns(
    bar_times: np.ndarray, prices: np.ndarray, starts: np.ndarray, ends: np.ndarray,
    *, max_entry_lag_s: int = MAX_ENTRY_LAG_MIN * 60, max_exit_lag_s: int = MAX_EXIT_LAG_MIN * 60,
) -> np.ndarray:
    """|log(P_exit / P_entry)|: entry = first bar at/after `start`, exit = first bar at/after
    `end` (epoch seconds; bars stamped at close). NaN when a lag is exceeded, a bar is
    missing, or exit is not after entry: fail closed, never imputed."""
    n = len(bar_times)
    out = np.full(len(starts), np.nan)
    if n == 0:
        return out
    i = np.searchsorted(bar_times, starts, "left")
    j = np.searchsorted(bar_times, ends, "left")
    ok = (i < n) & (j < n)
    ii, jj = np.where(ok, i, 0), np.where(ok, j, 0)
    ok &= (bar_times[ii] - starts <= max_entry_lag_s) & (bar_times[jj] - ends <= max_exit_lag_s) & (jj > ii)
    ok &= (prices[ii] > 0) & (prices[jj] > 0)
    out[ok] = np.abs(np.log(prices[jj][ok] / prices[ii][ok]))
    return out


def event_targets(bar_times: np.ndarray, prices: np.ndarray, event_times: np.ndarray) -> np.ndarray:
    return window_abs_log_returns(bar_times, prices, event_times + TARGET_START_OFFSET_MIN * 60,
                                  event_times + TARGET_END_OFFSET_MIN * 60)


def _previous_weekday_offsets(event_times: np.ndarray, count: int) -> np.ndarray:
    """(n_events, count) day offsets d >= 1 such that the UTC date of t - d days is a weekday."""
    weekday = ((np.asarray(event_times) // 86400) + 3) % 7      # 1970-01-01 was a Thursday
    offsets = np.zeros((len(weekday), count), dtype=np.int64)
    for row, wd in enumerate(weekday):
        found, d = 0, 0
        while found < count:
            d += 1
            if (wd - d) % 7 < 5:
                offsets[row, found] = d
                found += 1
    return offsets


def prior_window_control(
    bar_times: np.ndarray, prices: np.ndarray, event_times: np.ndarray,
    *, rule: str, min_valid_days: int, trading_days: int = CONTROL_TRADING_DAYS,
) -> np.ndarray:
    """Mean |log return| over the same UTC clock window on previous trading days."""
    if rule not in ("weekdays", "valid_days"):
        raise ValueError(f"unknown control_day_rule {rule!r}")
    lookback = trading_days if rule == "weekdays" else 2 * trading_days
    offsets = _previous_weekday_offsets(event_times, lookback)
    shifted = np.asarray(event_times)[:, None] - offsets * 86400
    values = event_targets(bar_times, prices, shifted.ravel()).reshape(shifted.shape)
    if rule == "valid_days":
        valid = np.isfinite(values)
        values = np.where(valid & (np.cumsum(valid, axis=1) <= trading_days), values, np.nan)
    counts = np.isfinite(values).sum(axis=1)
    sums = np.nansum(values, axis=1)
    means = np.full(len(counts), np.nan)
    enough = counts >= max(1, min_valid_days)
    means[enough] = sums[enough] / counts[enough]
    return means


def hours_for_events(event_times: np.ndarray, *, trading_days: int = 2 * CONTROL_TRADING_DAYS) -> list:
    """UTC hours HistData must load to compute targets and controls for these events."""
    import datetime as dt

    event_times = np.asarray(event_times)
    offsets = np.concatenate([np.zeros((len(event_times), 1), dtype=np.int64),
                              _previous_weekday_offsets(event_times, trading_days)], axis=1)
    base = event_times[:, None] - offsets * 86400
    starts = (base + TARGET_START_OFFSET_MIN * 60).ravel() // 3600
    ends = (base + (TARGET_END_OFFSET_MIN + MAX_EXIT_LAG_MIN) * 60).ravel() // 3600
    hours: set[int] = set()
    for s, e in zip(starts, ends):
        hours.update(range(int(s), int(e) + 1))
    return [dt.datetime.fromtimestamp(h * 3600, tz=dt.UTC) for h in sorted(hours)]


# ---------------------------------------------------------------- the locked test

@dataclass(frozen=True)
class H1Result:
    backend: str
    n_rows: int
    n_events: int
    n_dates: int
    novelty_beta: float | None
    novelty_se: float | None
    novelty_p: float | None
    walk_forward: MonthlyComparison | None
    verdict: str                     # "supported" | "not_supported" | "underpowered"
    decision_weight: bool            # False for TF-IDF / hashing (reported only)

    def to_row(self) -> dict:
        w = self.walk_forward
        return {
            "backend": self.backend, "decision_weight": self.decision_weight,
            "n_rows": self.n_rows, "n_events": self.n_events, "n_dates": self.n_dates,
            "novelty_beta": self.novelty_beta, "novelty_se": self.novelty_se, "novelty_p": self.novelty_p,
            "oos_mse_baseline": w.mse_a if w else None, "oos_mse_novelty": w.mse_b if w else None,
            "oos_test_rows": w.n_test if w else None, "oos_test_months": w.n_months if w else None,
            "verdict": self.verdict,
        }


def h1_locked_test(
    frame: pd.DataFrame,
    *,
    backend: str,
    target: str,
    controls: Sequence[str],
    baseline: Sequence[str],
    min_train_months: int,
    novelty: str = "novelty",
    event_col: str = "event_id",
    timestamp_col: str = "timestamp",
    alpha: float = ALPHA,
    min_events: int = MIN_EVENTS,
) -> H1Result:
    """Nested model with UTC-date-clustered SEs + monthly expanding walk-forward MSE."""
    full = [*controls, *baseline, novelty]
    data = frame[list(dict.fromkeys([target, event_col, timestamp_col, *full]))].dropna().copy()
    stamps = pd.to_datetime(data[timestamp_col], utc=True)
    data["_utc_date"] = stamps.dt.strftime("%Y-%m-%d")
    data["_month"] = stamps.dt.strftime("%Y-%m")
    n_events = int(data[event_col].nunique())
    weight = backend == PRIMARY_EMBEDDER
    if n_events < min_events:
        return H1Result(backend, len(data), n_events, int(data["_utc_date"].nunique()), None, None, None,
                        None, "underpowered", weight)
    ols = clustered_ols(data, target=target, features=full, cluster_col="_utc_date")
    coef = ols.coefficients.get(novelty) if ols else None
    walk = monthly_expanding_comparison(data, target=target, features_a=[*controls, *baseline],
                                        features_b=full, month_col="_month",
                                        min_train_months=min_train_months)
    coefficient_pass = coef is not None and coef.beta > 0 and coef.p < alpha
    oos_pass = walk is not None and walk.n_test > 0 and walk.mse_b < walk.mse_a
    verdict = "supported" if (coefficient_pass and oos_pass) else "not_supported"
    return H1Result(backend, len(data), n_events, int(data["_utc_date"].nunique()),
                    coef.beta if coef else None, coef.se if coef else None, coef.p if coef else None,
                    walk, verdict, weight)


def h1_decision(results: Mapping[str, H1Result]) -> str:
    """Only the primary embedder decides; the others are reported without weight."""
    primary = results.get(PRIMARY_EMBEDDER)
    return primary.verdict if primary is not None else "incomplete"
