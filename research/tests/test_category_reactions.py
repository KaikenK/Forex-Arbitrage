"""H2/H3 statistics on synthetic reaction curves. No network, no price data."""
from __future__ import annotations

import numpy as np
import pytest

from research.eventstudy.atlas import CategoryImpact, HorizonImpact, _half_life
from research.eventstudy.category_reactions import (
    bootstrap_speed,
    build_panels,
    clustered_mean,
    decay_half_life,
    disjoint_pairs,
    orient,
    time_to_half_peak,
)

HORIZONS = [1, 5, 15, 30, 60, 120]


def test_orientation_turns_bad_news_and_quote_side_into_one_direction() -> None:
    # Strong payrolls (+z, sign +1), EUR/USD falls (USD is quote: side -1) -> positive.
    assert orient(-3.0, 1.2, 1, -1) == pytest.approx(3.0)
    # Higher unemployment (+z, sign -1), USD/JPY falls (USD is base: side +1) -> positive.
    assert orient(-2.0, 0.8, -1, 1) == pytest.approx(2.0)
    assert orient(5.0, 0.0, 1, 1) is None, "no surprise, no direction"


def test_clustered_mean_matches_iid_when_each_obs_is_its_own_cluster() -> None:
    rng = np.random.default_rng(1)
    values = rng.normal(1.0, 2.0, 400)
    fit = clustered_mean(values, list(range(400)))
    iid_se = values.std(ddof=1) / np.sqrt(400)
    assert fit.se == pytest.approx(iid_se, rel=0.01)
    assert fit.p < 1e-6


def test_clustering_widens_the_se_when_a_release_is_seen_in_four_pairs() -> None:
    rng = np.random.default_rng(2)
    shocks = rng.normal(0.3, 2.0, 200)
    values = np.repeat(shocks, 4) + rng.normal(0, 0.1, 800)     # 4 near-identical pair obs
    clusters = np.repeat(np.arange(200), 4)
    clustered = clustered_mean(values, clusters)
    naive = clustered_mean(values, np.arange(800))
    assert clustered.se > 1.8 * naive.se
    assert clustered.clusters == 200


def test_decay_half_life_equals_the_atlas_definition() -> None:
    for curve in ([1, 4, 6, 5, 2, 1], [1, 2, 3, 4, 5, 6], [6, 1, 1, 1, 1, 1], [0.5, 3, 2, 1.4, 1.6, 2]):
        impact = CategoryImpact(category="x", horizons={
            h: HorizonImpact(h, 10, m, abs(m), 0.0, 1.0, 0.5) for h, m in zip(HORIZONS, curve)})
        assert decay_half_life(HORIZONS, curve) == _half_life(impact)


def test_permanent_reaction_has_no_half_life_but_a_time_to_half_peak() -> None:
    building = [1.0, 2.0, 2.5, 3.0, 3.5, 4.0]
    assert decay_half_life(HORIZONS, building) is None
    assert time_to_half_peak(HORIZONS, building) == 5.0
    assert decay_half_life(HORIZONS, [4.0, 3.0, 1.5, 1.0, 0.5, 0.2]) == 15.0


def _obs(category: str, curve: list[float], clusters: int, noise: float, rng) -> list[dict]:
    out = []
    for c in range(clusters):
        row = {"categories": [category], "cluster": f"{category}-{c}"}
        for h, m in zip(HORIZONS, curve):
            row[f"o{h}"] = m + rng.normal(0, noise)
        out.append(row)
    return out


def test_bootstrap_separates_a_fast_decay_from_a_slow_one() -> None:
    rng = np.random.default_rng(3)
    fast = _obs("fast", [6, 2, 1, 0.5, 0.3, 0.2], 300, 0.5, rng)   # half-life 5m
    slow = _obs("slow", [1, 3, 5, 6, 4, 2.5], 300, 0.5, rng)       # half-life 120m
    panels = build_panels(fast + slow, HORIZONS)
    est = {name: bootstrap_speed(panels[name], decay_half_life, draws=300, rng=np.random.default_rng(4))
           for name in ("fast", "slow")}
    assert est["fast"].point == 5.0 and est["slow"].point == 120.0
    assert disjoint_pairs(est) == [("fast", "slow")]


def test_noisy_categories_do_not_produce_disjoint_intervals() -> None:
    rng = np.random.default_rng(5)
    a = _obs("a", [2, 2, 2, 2, 2, 2], 40, 6.0, rng)
    b = _obs("b", [2, 2, 2, 2, 2, 2], 40, 6.0, rng)
    panels = build_panels(a + b, HORIZONS)
    est = {n: bootstrap_speed(panels[n], decay_half_life, draws=300, rng=np.random.default_rng(6)) for n in "ab"}
    assert disjoint_pairs(est) == []


def test_panel_curve_is_the_pooled_mean() -> None:
    rows = [{"categories": ["x"], "cluster": "d1", "o1": 1.0, "o5": 2.0},
            {"categories": ["x"], "cluster": "d1", "o1": 3.0, "o5": None},
            {"categories": ["x"], "cluster": "d2", "o1": 5.0, "o5": 4.0}]
    panel = build_panels(rows, [1, 5])["x"]
    assert list(panel.curve()) == pytest.approx([3.0, 3.0])
