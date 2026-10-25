"""H5 constructs: per-currency news sentiment, implied pair sentiment, the market component,
and a Citi-style economic surprise index (RESEARCH_TRACKER §2, pre-registered 2026-10-24).

Everything here is a pure function of its inputs, so the live semantic service can call the
same code on a rolling window of scored headlines that the research batch uses on the archive.

Definitions (one index definition, fixed before any H5 data was examined):

  FX day       The daily bars are stamped at the 21:00 UTC New York close (`prices_yf`), so a
               headline belongs to the bar whose 24 h ends at the next 21:00 UTC. Weekend
               headlines roll to Monday's bar (the market is shut; Monday's return absorbs them).
  S_c,t        Mean polarity of the deduplicated headlines attributed to currency c on FX day
               t. Attribution follows `data/tagging.py`: a headline counts for every currency
               it is tagged with. A day with no headline for c has S_c,t = 0 (no news is
               neutral) and n_c,t = 0, which is reported alongside.
  Implied      S_base,t - S_quote,t.
  Pair-tagged  The baseline: mean polarity of headlines whose pair attribution
               (`currencies_to_pairs`, usd_policy="none") includes the pair; 0 on no-news days.
  Market       First principal component of the standardized currency indices, fitted
  component    walk-forward (date t uses only dates < t), sign fixed a priori — never from
               outcomes — so that the mean loading of the non-safe-haven currencies (USD, EUR,
               GBP, INR) is positive: higher = broadly positive news tone = risk-on.
  Surprise     Per currency, the exponentially weighted mean of sign-oriented standardized
  index        surprises (`eventstudy/sign_conventions.news_sign`; Option A σ, winsorized at
               ±5) over the trailing 90 days, half-life 30 days, high-impact releases with an
               exact time; 0 with no releases. Same-day releases are included, so it controls
               for macro news known by that day's close.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd

from research.data.tagging import currencies_to_pairs

STUDY_CURRENCIES: tuple[str, ...] = ("USD", "EUR", "JPY", "GBP", "CHF", "INR")
SAFE_HAVENS: frozenset[str] = frozenset({"JPY", "CHF"})
H5_PAIRS: tuple[str, ...] = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/INR")
FX_DAY_CLOSE_HOUR_UTC = 21
SURPRISE_WINDOW_DAYS = 90
SURPRISE_HALF_LIFE_DAYS = 30.0
WINSOR_SIGMA = 5.0


@dataclass(frozen=True)
class ScoredHeadline:
    timestamp: datetime
    currencies: tuple[str, ...]
    polarity: float
    event_id: str = ""


# ---------------------------------------------------------------- day alignment

def fx_day(timestamp: datetime) -> date:
    """The daily bar a timestamp's information belongs to (21:00 UTC close, weekends -> Mon)."""
    stamp = timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)
    stamp = stamp.astimezone(UTC)
    day = stamp.date()
    if stamp.hour >= FX_DAY_CLOSE_HOUR_UTC:
        day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


# ---------------------------------------------------------------- currency indices

def daily_currency_sentiment(
    headlines: Iterable[ScoredHeadline],
    currencies: Sequence[str] = STUDY_CURRENCIES,
    *,
    days: Sequence[date] | None = None,
) -> pd.DataFrame:
    """Index = FX day; columns S_<c> and n_<c> for every currency."""
    sums: dict[tuple[date, str], float] = {}
    counts: dict[tuple[date, str], int] = {}
    seen_days: set[date] = set()
    wanted = set(currencies)
    for headline in headlines:
        day = fx_day(headline.timestamp)
        seen_days.add(day)
        for code in set(headline.currencies) & wanted:
            sums[(day, code)] = sums.get((day, code), 0.0) + float(headline.polarity)
            counts[(day, code)] = counts.get((day, code), 0) + 1
    index = sorted(set(days) if days is not None else seen_days)
    data: dict[str, list[float]] = {}
    for code in currencies:
        n = [counts.get((d, code), 0) for d in index]
        data[f"S_{code}"] = [sums.get((d, code), 0.0) / c if c else 0.0 for d, c in zip(index, n)]
        data[f"n_{code}"] = n
    return pd.DataFrame(data, index=pd.Index(index, name="fx_day"))


def rolling_currency_sentiment(
    headlines: Iterable[ScoredHeadline],
    as_of: datetime,
    *,
    window_hours: float = 24.0,
    currencies: Sequence[str] = STUDY_CURRENCIES,
    pairs: Sequence[str] = H5_PAIRS,
) -> dict:
    """Live-service form: currency sentiment over (as_of - window, as_of] and implied pairs.

    Same definitions as the daily index, over a sliding window instead of an FX day, so a
    service can call it on every new article without re-reading the archive.
    """
    as_of = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
    start = as_of - timedelta(hours=window_hours)
    sums = {code: 0.0 for code in currencies}
    counts = {code: 0 for code in currencies}
    for headline in headlines:
        stamp = headline.timestamp if headline.timestamp.tzinfo else headline.timestamp.replace(tzinfo=UTC)
        if not (start < stamp <= as_of):
            continue
        for code in set(headline.currencies) & set(currencies):
            sums[code] += float(headline.polarity)
            counts[code] += 1
    index = {code: (sums[code] / counts[code] if counts[code] else 0.0) for code in currencies}
    implied = {}
    for pair in pairs:
        base, _, quote = pair.partition("/")
        if base in index and quote in index:
            implied[pair] = index[base] - index[quote]
    return {"as_of": as_of.isoformat(), "window_hours": window_hours,
            "currency_sentiment": index, "headline_counts": counts, "implied_pair_sentiment": implied}


def implied_pair_sentiment(index: pd.DataFrame, pair: str) -> pd.Series:
    base, _, quote = pair.partition("/")
    return (index[f"S_{base}"] - index[f"S_{quote}"]).rename(f"implied_{pair}")


def pair_tagged_sentiment(headlines: Iterable[ScoredHeadline], pair: str,
                          *, days: Sequence[date] | None = None) -> pd.Series:
    """Baseline: per-pair mean polarity of the headlines attributed to that pair."""
    sums: dict[date, float] = {}
    counts: dict[date, int] = {}
    seen: set[date] = set()
    for headline in headlines:
        day = fx_day(headline.timestamp)
        seen.add(day)
        if pair in currencies_to_pairs(list(headline.currencies)):
            sums[day] = sums.get(day, 0.0) + float(headline.polarity)
            counts[day] = counts.get(day, 0) + 1
    index = sorted(set(days) if days is not None else seen)
    values = [sums.get(d, 0.0) / counts[d] if counts.get(d) else 0.0 for d in index]
    return pd.Series(values, index=pd.Index(index, name="fx_day"), name=f"tagged_{pair}")


# ---------------------------------------------------------------- market component

@dataclass(frozen=True)
class MarketComponentFit:
    """A fitted PC1, serializable so a live service can apply it to today's indices."""
    currencies: tuple[str, ...]
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    loadings: tuple[float, ...]
    explained_variance_ratio: float
    n_train: int

    def project(self, values: Sequence[float]) -> float:
        x = (np.asarray(values, dtype=float) - np.asarray(self.mean)) / np.asarray(self.scale)
        return float(x @ np.asarray(self.loadings))

    def to_dict(self) -> dict:
        return {"currencies": list(self.currencies), "mean": list(self.mean), "scale": list(self.scale),
                "loadings": list(self.loadings), "explained_variance_ratio": self.explained_variance_ratio,
                "n_train": self.n_train}


def fit_market_component(train: np.ndarray, currencies: Sequence[str]) -> MarketComponentFit:
    """PC1 of standardized currency indices, sign anchored on the non-safe-haven loadings."""
    mean = train.mean(axis=0)
    scale = train.std(axis=0, ddof=1) if len(train) > 1 else np.ones(train.shape[1])
    scale = np.where(scale > 0, scale, 1.0)
    z = (train - mean) / scale
    _, singular, vt = np.linalg.svd(z, full_matrices=False)
    loadings = vt[0]
    anchor = [i for i, code in enumerate(currencies) if code not in SAFE_HAVENS]
    if np.mean(loadings[anchor]) < 0:
        loadings = -loadings
    variance = singular ** 2
    ratio = float(variance[0] / variance.sum()) if variance.sum() > 0 else 0.0
    return MarketComponentFit(tuple(currencies), tuple(mean), tuple(scale), tuple(loadings), ratio, len(train))


def walk_forward_market_component(
    index: pd.DataFrame,
    currencies: Sequence[str] = STUDY_CURRENCIES,
    *,
    min_train_days: int = 60,
    refit_every: int = 1,
) -> pd.DataFrame:
    """Market component per FX day using only strictly earlier days. NaN until min_train_days."""
    values = index[[f"S_{c}" for c in currencies]].to_numpy(dtype=float)
    component = np.full(len(index), np.nan)
    explained = np.full(len(index), np.nan)
    fit: MarketComponentFit | None = None
    for t in range(len(index)):
        if t < min_train_days:
            continue
        if fit is None or (t - min_train_days) % refit_every == 0:
            fit = fit_market_component(values[:t], currencies)
        component[t] = fit.project(values[t])
        explained[t] = fit.explained_variance_ratio
    return pd.DataFrame({"market_component": component, "pc1_explained": explained}, index=index.index)


# ---------------------------------------------------------------- surprise index

def surprise_index(
    calendar: pd.DataFrame,
    days: Sequence[date],
    *,
    currencies: Sequence[str] = STUDY_CURRENCIES,
    importance: Sequence[str] = ("high",),
    window_days: int = SURPRISE_WINDOW_DAYS,
    half_life_days: float = SURPRISE_HALF_LIFE_DAYS,
    exclude_pandemic_window: bool = False,
) -> pd.DataFrame:
    """Citi-style surprise index per currency on each FX day (columns SI_<c>)."""
    from research.eventstudy.sign_conventions import CURRENCY_FOR_COUNTRY, news_sign

    frame = calendar
    if importance:
        frame = frame[frame["importance"].isin(importance)]
    # Locked: "surprise index uses high-impact exact-time releases". Both columns are required,
    # so a store without them fails loudly instead of silently changing the definition.
    frame = frame[frame["timestamp_precision_minutes"] == 0]
    if exclude_pandemic_window:
        if "in_pandemic_window" not in frame:
            raise KeyError("pandemic-window robustness needs the calendar's `in_pandemic_window` column")
        frame = frame[~frame["in_pandemic_window"].fillna(False).astype(bool)]
    frame = frame[frame["standardized_surprise"].notna()]

    releases: dict[str, list[tuple[date, float]]] = {code: [] for code in currencies}
    for row in frame.to_dict("records"):
        currency = CURRENCY_FOR_COUNTRY.get(str(row.get("country", "")))
        if currency not in releases:
            continue
        sign = news_sign(str(row["country"]), str(row["event_name"]))
        if sign is None:
            continue
        stamp = pd.Timestamp(row["timestamp"]).to_pydatetime()
        z = float(np.clip(row["standardized_surprise"], -WINSOR_SIGMA, WINSOR_SIGMA))
        releases[currency].append((fx_day(stamp), sign * z))

    decay = np.log(2) / half_life_days
    data: dict[str, list[float]] = {}
    for code in currencies:
        items = sorted(releases[code])
        release_days = np.array([np.datetime64(d) for d, _ in items], dtype="datetime64[D]")
        values = np.array([v for _, v in items], dtype=float)
        column = []
        for day in days:
            if len(items) == 0:
                column.append(0.0)
                continue
            age = (np.datetime64(day) - release_days).astype(int)
            mask = (age >= 0) & (age < window_days)
            if not mask.any():
                column.append(0.0)
                continue
            weights = np.exp(-decay * age[mask])
            column.append(float(np.sum(weights * values[mask]) / np.sum(weights)))
        data[f"SI_{code}"] = column
    return pd.DataFrame(data, index=pd.Index(list(days), name="fx_day"))
