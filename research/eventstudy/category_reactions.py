"""H2/H3 statistics: per-category, sign-oriented reaction curves for scheduled releases.

Why oriented. A scheduled release can surprise either way, so the raw mean return of a
category is ~0 by construction: good and bad surprises cancel. Each observation is
therefore oriented by the direction its surprise predicts,

    o = sign(z * news_sign) * side * r,

where `news_sign` is the a-priori economic sign (sign_conventions.py) and `side` turns the
pair return into the releasing currency's return. o > 0 means the currency moved the way
the news said it should. Releases with z = 0 carry no direction and are dropped. Over 1-120
minutes the expected return is ~0, so the oriented raw return is the abnormal return; no
sample-mean adjustment is applied (it would remove the very effect being measured).

Inference clusters by release date: one US release is observed in four pairs, and several
indicators print at the same minute (payrolls, unemployment, earnings).

H3 speed measures:
  - `decay_half_life` is the pre-registered definition, identical to `atlas._half_life`:
    the first horizon after the peak at which |mean| falls to <= half the peak. If the
    reaction does not decay within the horizon grid it is undefined (censored).
  - `time_to_half_peak` (first horizon at which |mean| reaches half its peak) is reported
    as a secondary, *not* pre-registered, description of how fast the reaction builds.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class ClusteredMean:
    n: int
    clusters: int
    mean: float
    se: float
    t: float
    p: float


def clustered_mean(values: Sequence[float], clusters: Sequence) -> ClusteredMean | None:
    """Mean with a cluster-robust (CR1) standard error and a t(G-1) p-value."""
    y = np.asarray(values, dtype=float)
    groups = np.asarray(clusters)
    finite = np.isfinite(y)
    y, groups = y[finite], groups[finite]
    n = len(y)
    if n < 2:
        return None
    mean = float(y.mean())
    _, inverse = np.unique(groups, return_inverse=True)
    cluster_sums = np.bincount(inverse, weights=y - mean)
    g = len(cluster_sums)
    if g < 2:
        return None
    variance = (g / (g - 1)) * float(np.sum(cluster_sums ** 2)) / n ** 2
    se = variance ** 0.5
    if se == 0.0:
        return ClusteredMean(n, g, mean, 0.0, 0.0, 1.0)
    t = mean / se
    p = float(2 * stats.t.sf(abs(t), g - 1))
    return ClusteredMean(n, g, mean, se, t, p)


def orient(raw_return: float, surprise: float, news_sign: int, side: int) -> float | None:
    """The return in the direction the surprise predicts; None when there is no direction."""
    direction = np.sign(surprise * news_sign)
    if direction == 0:
        return None
    return float(direction * side * raw_return)


def decay_half_life(horizons: Sequence[int], means: Sequence[float]) -> float | None:
    """Pre-registered H3 measure; same rule as `atlas._half_life`."""
    if not horizons:
        return None
    ordered = sorted(zip(horizons, means))
    peak = max(abs(m) for _, m in ordered)
    if peak == 0.0:
        return None
    peaked = False
    for horizon, mean in ordered:
        if abs(mean) >= peak:
            peaked = True
            continue
        if peaked and abs(mean) <= peak / 2.0:
            return float(horizon)
    return None


def time_to_half_peak(horizons: Sequence[int], means: Sequence[float]) -> float | None:
    """Secondary (not pre-registered): first horizon where |mean| reaches half its peak."""
    ordered = sorted(zip(horizons, means))
    if not ordered:
        return None
    peak = max(abs(m) for _, m in ordered)
    if peak == 0.0:
        return None
    return float(next(h for h, m in ordered if abs(m) >= peak / 2.0))


@dataclass
class CategoryPanel:
    """Per-category cluster sums, so bootstrap resamples cost one matrix product each."""
    category: str
    horizons: list[int]
    cluster_ids: list
    sums: np.ndarray     # clusters x horizons, sum of oriented returns
    counts: np.ndarray   # clusters x horizons, number of observations

    def curve(self, weights: np.ndarray | None = None) -> np.ndarray:
        w = np.ones(len(self.cluster_ids)) if weights is None else weights
        totals, counts = w @ self.sums, w @ self.counts
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(counts > 0, totals / counts, np.nan)


def build_panels(observations: Sequence[dict], horizons: Sequence[int]) -> dict[str, CategoryPanel]:
    """`observations`: dicts with `categories` (iterable), `cluster`, and `o{h}` oriented returns."""
    by_category: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: np.zeros((2, len(horizons)))))
    for obs in observations:
        for category in obs["categories"]:
            cell = by_category[category][obs["cluster"]]
            for j, horizon in enumerate(horizons):
                value = obs.get(f"o{horizon}")
                if value is not None and np.isfinite(value):
                    cell[0, j] += value
                    cell[1, j] += 1
    panels = {}
    for category, clusters in by_category.items():
        ids = sorted(clusters)
        stacked = np.stack([clusters[c] for c in ids])
        panels[category] = CategoryPanel(category, list(horizons), ids, stacked[:, 0, :], stacked[:, 1, :])
    return panels


@dataclass(frozen=True)
class SpeedEstimate:
    point: float | None
    ci_low: float | None
    ci_high: float | None
    undefined_share: float     # bootstrap draws in which the measure is undefined (censored)


def bootstrap_speed(panel: CategoryPanel, measure, *, draws: int = 1000,
                    rng: np.random.Generator | None = None) -> SpeedEstimate:
    """Release-clustered bootstrap percentile CI. Undefined draws count as +inf (no decay
    within the grid), so a category that rarely decays gets an open upper bound."""
    rng = rng or np.random.default_rng(0)
    g = len(panel.cluster_ids)
    point = measure(panel.horizons, list(panel.curve()))
    values = []
    for _ in range(draws):
        weights = np.bincount(rng.integers(0, g, g), minlength=g).astype(float)
        curve = panel.curve(weights)
        if np.isnan(curve).any():
            values.append(np.inf)
            continue
        value = measure(panel.horizons, list(curve))
        values.append(np.inf if value is None else value)
    arr = np.asarray(values)
    # Order statistics, not interpolation: the measure lives on the discrete horizon grid
    # and censored draws are +inf, which interpolation would turn into NaN.
    low = np.percentile(arr, 2.5, method="lower")
    high = np.percentile(arr, 97.5, method="higher")
    return SpeedEstimate(
        point=point,
        ci_low=None if not np.isfinite(low) else float(low),
        ci_high=None if not np.isfinite(high) else float(high),
        undefined_share=float(np.mean(~np.isfinite(arr))),
    )


def disjoint_pairs(estimates: dict[str, SpeedEstimate]) -> list[tuple[str, str]]:
    """Category pairs whose CIs do not overlap. An open (undefined) bound is +inf."""
    def bounds(e: SpeedEstimate) -> tuple[float, float] | None:
        if e.ci_low is None:
            return None  # >= 2.5% of draws undefined at the low end: no usable interval
        return e.ci_low, (np.inf if e.ci_high is None else e.ci_high)

    names = sorted(estimates)
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            ba, bb = bounds(estimates[a]), bounds(estimates[b])
            if ba and bb and (ba[1] < bb[0] or bb[1] < ba[0]):
                pairs.append((a, b))
    return pairs
