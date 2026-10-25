"""The impact atlas: per-category, per-horizon event-study statistics.

For each event category and horizon it reports mean (abnormal) return, the impact
magnitude (mean |return|), a one-sample t-test of the mean against zero, the directional
hit-rate, and the reaction half-life (horizon by which |mean| decays to half its peak).

v1 abnormal returns default to *sample-mean adjustment*: AR = raw - mean(raw over all events
at that horizon), i.e. "does this category deviate from the average event?". A covariate
market-model (DXY / oil / yield-diff) is the planned M2 upgrade and plugs in by populating
`AlignedObservation.abnormal_returns` before calling `build_atlas(..., use_abnormal=True)`.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from scipy import stats

from .schema import AlignedObservation


@dataclass(frozen=True)
class HorizonImpact:
    horizon_minutes: int
    n: int
    mean_return_bps: float
    mean_abs_return_bps: float
    t_stat: float
    p_value: float
    hit_rate: float          # share of same-sign-as-mean reactions


@dataclass
class CategoryImpact:
    category: str
    horizons: dict[int, HorizonImpact] = field(default_factory=dict)
    half_life_minutes: float | None = None


def _returns_for(observation: AlignedObservation, horizon: int, use_abnormal: bool) -> float | None:
    source = observation.abnormal_returns if use_abnormal else observation.horizon_returns
    return source.get(horizon)


def _one_sample_stats(values: list[float]) -> tuple[float, float]:
    """t-stat and two-sided p-value of the mean vs 0, with degenerate-variance guards."""
    n = len(values)
    if n < 2:
        return 0.0, 1.0
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    if variance == 0.0:
        return (0.0, 1.0) if mean == 0.0 else (float("inf"), 0.0)
    result = stats.ttest_1samp(values, 0.0)
    return float(result.statistic), float(result.pvalue)


def build_atlas(
    observations: list[AlignedObservation],
    horizons_minutes: list[int],
    *,
    use_abnormal: bool = False,
    mean_adjust: bool = True,
) -> dict[str, CategoryImpact]:
    # Sample mean per horizon for mean-adjustment (skipped when using a market model).
    sample_mean: dict[int, float] = {}
    if mean_adjust and not use_abnormal:
        for horizon in horizons_minutes:
            pool = [r for o in observations if (r := _returns_for(o, horizon, False)) is not None]
            sample_mean[horizon] = sum(pool) / len(pool) if pool else 0.0

    by_category: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
    for observation in observations:
        for category in observation.event.categories:
            for horizon in horizons_minutes:
                value = _returns_for(observation, horizon, use_abnormal)
                if value is None:
                    continue
                by_category[category][horizon].append(value - sample_mean.get(horizon, 0.0))

    atlas: dict[str, CategoryImpact] = {}
    for category, horizon_values in by_category.items():
        impact = CategoryImpact(category=category)
        for horizon in horizons_minutes:
            values = horizon_values.get(horizon, [])
            if not values:
                continue
            n = len(values)
            mean = sum(values) / n
            mean_abs = sum(abs(v) for v in values) / n
            t_stat, p_value = _one_sample_stats(values)
            hit_sign = 1.0 if mean >= 0 else -1.0
            hit_rate = sum(1 for v in values if (v >= 0) == (hit_sign >= 0)) / n
            impact.horizons[horizon] = HorizonImpact(
                horizon_minutes=horizon,
                n=n,
                mean_return_bps=mean * 1e4,
                mean_abs_return_bps=mean_abs * 1e4,
                t_stat=t_stat,
                p_value=p_value,
                hit_rate=hit_rate,
            )
        impact.half_life_minutes = _half_life(impact)
        atlas[category] = impact
    return atlas


def _half_life(impact: CategoryImpact) -> float | None:
    """Horizon at which |mean return| first falls to <= half its peak across horizons."""
    if not impact.horizons:
        return None
    ordered = sorted(impact.horizons.values(), key=lambda h: h.horizon_minutes)
    peak = max(abs(h.mean_return_bps) for h in ordered)
    if peak == 0.0:
        return None
    threshold = peak / 2.0
    peaked = False
    for h in ordered:
        if abs(h.mean_return_bps) >= peak:
            peaked = True
            continue
        if peaked and abs(h.mean_return_bps) <= threshold:
            return float(h.horizon_minutes)
    return None
