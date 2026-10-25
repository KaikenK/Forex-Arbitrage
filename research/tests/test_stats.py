"""Tests for Benjamini-Hochberg FDR correction of the atlas."""
from __future__ import annotations

from research.eventstudy.stats import benjamini_hochberg, correct_atlas
from research.eventstudy.atlas import CategoryImpact, HorizonImpact


def test_bh_matches_known_values():
    # Classic BH example: p sorted, q_i = min over j>=i of p_j * n / j
    p = [0.001, 0.008, 0.039, 0.041, 0.042]
    result = benjamini_hochberg(p, alpha=0.05)
    q = [round(c.q_value, 4) for c in result]
    assert q[0] == 0.005      # 0.001 * 5/1
    assert q[1] == 0.02       # 0.008 * 5/2
    assert all(result[i].significant for i in range(len(p)))  # all q <= 0.05


def test_bh_is_monotone_and_preserves_order():
    p = [0.9, 0.01, 0.5]
    result = benjamini_hochberg(p)
    assert [c.p_value for c in result] == p          # input order preserved
    q = [c.q_value for c in result]
    # monotone in p: smallest p has smallest q
    assert q[1] <= q[2] <= q[0]


def test_bh_kills_marginal_false_positives():
    # 20 tests, one marginal p=0.04 -> should NOT survive FDR
    p = [0.04] + [0.6] * 19
    result = benjamini_hochberg(p, alpha=0.05)
    assert result[0].q_value > 0.05
    assert not result[0].significant


def test_correct_atlas_keys_and_min_n():
    def cell(n, p):
        return HorizonImpact(horizon_minutes=1440, n=n, mean_return_bps=1.0,
                             mean_abs_return_bps=1.0, t_stat=1.0, p_value=p, hit_rate=0.5)

    strong = CategoryImpact(category="trade", horizons={1440: cell(100, 0.0001)})
    weak = CategoryImpact(category="tiny", horizons={1440: cell(2, 0.01)})
    corrections = correct_atlas({"trade": strong, "tiny": weak}, alpha=0.05, min_n=20)

    assert ("trade", 1440) in corrections
    assert ("tiny", 1440) not in corrections     # excluded by min_n
    assert corrections[("trade", 1440)].significant
