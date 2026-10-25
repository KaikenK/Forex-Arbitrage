"""Surprise-response regression: how strongly a pair reacts per unit of macro surprise.

This is the canonical scheduled-event design (Andersen, Bollerslev, Diebold & Vega 2003).
Rather than asking "did returns differ around this event category" (a mean test that any
contemporaneous shock can contaminate), it estimates

    r_i = alpha + beta * standardized_surprise_i + controls + e_i

where `beta` is the reaction sensitivity in return units per standard deviation of surprise.
Because release *timing* is scheduled and the surprise is measured against a pre-release
consensus, `beta` has a causal reading that a raw event-window mean does not.

Standard errors are **clustered by event date**. Several releases share a timestamp (NFP,
Unemployment Rate and Average Hourly Earnings all print together), so their residuals are
correlated; treating them as independent inflates t-statistics. Clustering is not optional here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy import stats


@dataclass(frozen=True)
class RegressionResult:
    n: int
    n_clusters: int
    alpha: float
    beta: float
    beta_se: float
    beta_t: float
    beta_p: float
    r_squared: float
    control_betas: dict[str, float] = field(default_factory=dict)

    @property
    def beta_ci95(self) -> tuple[float, float]:
        margin = 1.96 * self.beta_se
        return (self.beta - margin, self.beta + margin)


def _clustered_covariance(
    design: np.ndarray,
    residuals: np.ndarray,
    clusters: np.ndarray,
) -> np.ndarray:
    """Cluster-robust (CR1) sandwich covariance estimator."""
    n, k = design.shape
    xtx_inv = np.linalg.pinv(design.T @ design)

    meat = np.zeros((k, k))
    unique_clusters = np.unique(clusters)
    for cluster in unique_clusters:
        mask = clusters == cluster
        # Sum of scores within the cluster; residuals inside a cluster may covary freely.
        score = design[mask].T @ residuals[mask]
        meat += np.outer(score, score)

    n_clusters = len(unique_clusters)
    if n_clusters <= 1 or n <= k:
        return xtx_inv @ meat @ xtx_inv
    # Finite-sample correction (Cameron & Miller 2015).
    correction = (n_clusters / (n_clusters - 1)) * ((n - 1) / (n - k))
    return correction * (xtx_inv @ meat @ xtx_inv)


def surprise_response(
    returns: np.ndarray | list[float],
    surprises: np.ndarray | list[float],
    *,
    clusters: np.ndarray | list | None = None,
    controls: dict[str, np.ndarray | list[float]] | None = None,
) -> RegressionResult | None:
    """Regress returns on standardized surprise with cluster-robust inference.

    `clusters` should identify observations whose residuals may be correlated — event date
    is the natural choice. When omitted, each observation is its own cluster, which reduces
    to heteroskedasticity-robust (HC1) standard errors.
    """
    y = np.asarray(returns, dtype=float)
    x = np.asarray(surprises, dtype=float)
    control_names = list(controls or {})
    control_columns = [np.asarray(controls[name], dtype=float) for name in control_names]

    columns = [np.ones(len(y)), x] + control_columns
    design = np.column_stack(columns)

    finite = np.isfinite(y) & np.all(np.isfinite(design), axis=1)
    y, design = y[finite], design[finite]
    if clusters is None:
        cluster_ids = np.arange(len(y))
    else:
        cluster_ids = np.asarray(clusters)[finite]

    n, k = design.shape
    if n <= k + 1:
        return None

    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    residuals = y - design @ coefficients

    covariance = _clustered_covariance(design, residuals, cluster_ids)
    beta_se = float(np.sqrt(max(covariance[1, 1], 0.0)))
    beta = float(coefficients[1])

    n_clusters = int(len(np.unique(cluster_ids)))
    if beta_se > 0:
        beta_t = beta / beta_se
        # t distribution with G-1 degrees of freedom is the standard choice when clustering.
        degrees_of_freedom = max(n_clusters - 1, 1)
        beta_p = float(2 * stats.t.sf(abs(beta_t), degrees_of_freedom))
    else:
        beta_t, beta_p = 0.0, 1.0

    total_sum_squares = float(np.sum((y - y.mean()) ** 2))
    residual_sum_squares = float(np.sum(residuals ** 2))
    r_squared = 1.0 - residual_sum_squares / total_sum_squares if total_sum_squares > 0 else 0.0

    return RegressionResult(
        n=n,
        n_clusters=n_clusters,
        alpha=float(coefficients[0]),
        beta=beta,
        beta_se=beta_se,
        beta_t=float(beta_t),
        beta_p=beta_p,
        r_squared=r_squared,
        control_betas={
            name: float(coefficients[2 + index]) for index, name in enumerate(control_names)
        },
    )
