"""Tests for the surprise-response regression and cluster-robust inference."""
from __future__ import annotations

import numpy as np

from research.eventstudy.surprise import surprise_response


def test_recovers_known_beta():
    rng = np.random.default_rng(0)
    surprises = rng.normal(0, 1, 500)
    returns = 0.0001 + 0.0020 * surprises + rng.normal(0, 1e-5, 500)

    result = surprise_response(returns, surprises)
    assert result is not None
    assert abs(result.beta - 0.0020) < 1e-4
    assert abs(result.alpha - 0.0001) < 1e-4
    assert result.beta_p < 0.001
    assert result.r_squared > 0.95


def test_no_relationship_is_not_significant():
    rng = np.random.default_rng(1)
    surprises = rng.normal(0, 1, 400)
    returns = rng.normal(0, 0.001, 400)          # independent of surprise

    result = surprise_response(returns, surprises)
    assert result is not None
    assert result.beta_p > 0.05


def test_clustering_widens_standard_errors_for_correlated_releases():
    """Same-timestamp releases share both their surprise and their shock.

    This mirrors the real data: Non-Farm Employment Change, Unemployment Rate and Average
    Hourly Earnings all print from one jobs report, so their surprises co-move *and* they
    face a common price shock. Clustering the slope's standard error only matters when the
    regressor is correlated within cluster as well as the residual — a pure intercept shock
    leaves the slope's precision untouched.
    """
    rng = np.random.default_rng(2)
    n_clusters, per_cluster = 60, 4
    cluster_ids, surprises, returns = [], [], []
    for cluster in range(n_clusters):
        common_surprise = rng.normal(0, 1)        # one jobs report drives all its prints
        shock = rng.normal(0, 0.002)              # shared, unmodelled price shock
        for _ in range(per_cluster):
            s = common_surprise + rng.normal(0, 0.15)
            cluster_ids.append(cluster)
            surprises.append(s)
            returns.append(0.0005 * s + shock + rng.normal(0, 1e-4))

    naive = surprise_response(returns, surprises)
    clustered = surprise_response(returns, surprises, clusters=cluster_ids)
    assert naive is not None and clustered is not None
    assert clustered.n_clusters == n_clusters
    assert clustered.beta_se > naive.beta_se     # the whole point of clustering
    # point estimate is unchanged; only inference differs
    assert abs(clustered.beta - naive.beta) < 1e-12


def test_controls_absorb_a_confounder():
    rng = np.random.default_rng(3)
    surprises = rng.normal(0, 1, 400)
    confounder = rng.normal(0, 1, 400)
    # returns driven by the confounder, not by surprise
    returns = 0.003 * confounder + rng.normal(0, 1e-5, 400)

    without = surprise_response(returns, surprises)
    with_control = surprise_response(returns, surprises, controls={"dxy": confounder})
    assert without is not None and with_control is not None
    assert abs(with_control.beta) < 1e-3
    assert with_control.beta_p > 0.05
    assert "dxy" in with_control.control_betas
    assert abs(with_control.control_betas["dxy"] - 0.003) < 1e-3


def test_returns_none_when_underpowered():
    assert surprise_response([0.1, 0.2], [1.0, 2.0]) is None


def test_confidence_interval_brackets_beta():
    rng = np.random.default_rng(4)
    surprises = rng.normal(0, 1, 300)
    returns = 0.001 * surprises + rng.normal(0, 1e-4, 300)
    result = surprise_response(returns, surprises)
    assert result is not None
    low, high = result.beta_ci95
    assert low < result.beta < high
