"""Walk-forward model comparison and cluster-robust OLS, shared by the H1 and H5 tests.

Out-of-sample by construction: folds are contiguous blocks of *dates*, each model is fitted
only on dates strictly before the block it predicts (expanding window), and an optional
embargo drops the last `embargo_days` of training data so a multi-day target cannot overlap
the test block. Out-of-sample R² is measured against the training-mean forecast
(Campbell & Thompson 2008), so it is negative when a model does worse than "predict the
historical average".

Inference reuses the CR1 cluster-robust covariance of `eventstudy/surprise.py`, so H1/H5 and
the event study use one estimator.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from research.eventstudy.surprise import _clustered_covariance


@dataclass(frozen=True)
class Coefficient:
    beta: float
    se: float
    t: float
    p: float


@dataclass(frozen=True)
class OlsResult:
    n: int
    n_clusters: int
    r_squared: float
    coefficients: dict[str, Coefficient] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelComparison:
    """Model B against model A on identical out-of-sample rows."""
    n_test: int
    n_folds: int
    oos_r2_a: float
    oos_r2_b: float
    spearman_a: float
    spearman_b: float
    # Diebold-Mariano-style test on squared-error differences (A - B), clustered by date.
    # Positive = B has lower loss. Reported for information; the pre-registered rules use
    # the R² / Spearman comparison.
    loss_diff_mean: float
    loss_diff_p: float

    @property
    def delta_oos_r2(self) -> float:
        return self.oos_r2_b - self.oos_r2_a

    @property
    def delta_spearman(self) -> float:
        return self.spearman_b - self.spearman_a

    @property
    def b_improves(self) -> bool:
        """The pre-registered criterion: better out-of-sample R² *or* Spearman."""
        return self.delta_oos_r2 > 0 or self.delta_spearman > 0


@dataclass(frozen=True)
class MonthlyComparison:
    """Pooled out-of-sample MSE of two models, refit at the start of every test month."""
    n_test: int
    n_months: int
    mse_a: float
    mse_b: float


def monthly_expanding_comparison(
    frame: pd.DataFrame,
    *,
    target: str,
    features_a: Sequence[str],
    features_b: Sequence[str],
    month_col: str,
    min_train_months: int,
) -> MonthlyComparison | None:
    """Expanding window refit by calendar month (H1 locked protocol, criterion ii).

    Months are sorted; the first `min_train_months` are training-only. Each later month is
    predicted by both models fitted on every row from strictly earlier months; squared errors
    are pooled over all test months.
    """
    if min_train_months < 1:
        raise ValueError("min_train_months must be >= 1")
    data = frame.reset_index(drop=True)
    y = data[target].to_numpy(dtype=float)
    design_a, design_b = _design(data, features_a), _design(data, features_b)
    months = data[month_col].astype(str).to_numpy()
    ordered = sorted(set(months))
    errors_a: list[np.ndarray] = []
    errors_b: list[np.ndarray] = []
    for month in ordered[min_train_months:]:
        train, test = months < month, months == month
        if train.sum() <= design_b.shape[1]:
            continue
        coef_a, *_ = np.linalg.lstsq(design_a[train], y[train], rcond=None)
        coef_b, *_ = np.linalg.lstsq(design_b[train], y[train], rcond=None)
        errors_a.append((y[test] - design_a[test] @ coef_a) ** 2)
        errors_b.append((y[test] - design_b[test] @ coef_b) ** 2)
    if not errors_a:
        return None
    pooled_a, pooled_b = np.concatenate(errors_a), np.concatenate(errors_b)
    return MonthlyComparison(n_test=len(pooled_a), n_months=len(errors_a),
                             mse_a=float(pooled_a.mean()), mse_b=float(pooled_b.mean()))


def expanding_date_folds(
    dates: Sequence,
    *,
    n_folds: int = 5,
    min_train_fraction: float = 0.5,
    embargo_days: int = 0,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """(train_mask, test_mask) pairs over rows, split on unique dates.

    The first `min_train_fraction` of dates is training-only; the rest is cut into
    `n_folds` contiguous test blocks. Rows sharing a date are never split across train/test.
    """
    day = pd.to_datetime(pd.Series(list(dates))).dt.normalize()
    unique = np.sort(day.unique())
    if len(unique) < n_folds + 2:
        return []
    first_test = max(1, int(len(unique) * min_train_fraction))
    blocks = np.array_split(unique[first_test:], n_folds)
    embargo = pd.Timedelta(days=embargo_days)
    folds = []
    for block in blocks:
        if len(block) == 0:
            continue
        test_start = pd.Timestamp(block[0])
        train = (day < test_start - embargo).to_numpy()
        test = day.isin(block).to_numpy()
        if train.sum() > 0 and test.sum() > 0:
            folds.append((train, test))
    return folds


def _design(frame: pd.DataFrame, features: Sequence[str]) -> np.ndarray:
    columns = [np.ones(len(frame))] + [frame[name].to_numpy(dtype=float) for name in features]
    return np.column_stack(columns)


def walk_forward_predictions(
    frame: pd.DataFrame,
    features: Sequence[str],
    target: str,
    folds: list[tuple[np.ndarray, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(predictions, training-mean benchmark, tested mask), each over all rows."""
    y = frame[target].to_numpy(dtype=float)
    design = _design(frame, features)
    predictions = np.full(len(frame), np.nan)
    benchmark = np.full(len(frame), np.nan)
    for train, test in folds:
        coefficients, *_ = np.linalg.lstsq(design[train], y[train], rcond=None)
        predictions[test] = design[test] @ coefficients
        benchmark[test] = y[train].mean()
    return predictions, benchmark, np.isfinite(predictions)


def oos_r2(y: np.ndarray, prediction: np.ndarray, benchmark: np.ndarray) -> float:
    denominator = float(np.sum((y - benchmark) ** 2))
    return 1.0 - float(np.sum((y - prediction) ** 2)) / denominator if denominator > 0 else 0.0


def _spearman(y: np.ndarray, prediction: np.ndarray) -> float:
    if len(y) < 3 or np.std(prediction) == 0 or np.std(y) == 0:
        return 0.0
    return float(stats.spearmanr(prediction, y).statistic)


def clustered_mean_test(values: np.ndarray, clusters: np.ndarray) -> tuple[float, float]:
    """Mean of `values` and its two-sided p-value with cluster-robust SE."""
    design = np.ones((len(values), 1))
    mean = float(values.mean())
    covariance = _clustered_covariance(design, values - mean, clusters)
    se = float(np.sqrt(max(covariance[0, 0], 0.0)))
    if se == 0:
        return mean, 1.0
    dof = max(len(np.unique(clusters)) - 1, 1)
    return mean, float(2 * stats.t.sf(abs(mean / se), dof))


def compare_models(
    frame: pd.DataFrame,
    *,
    target: str,
    features_a: Sequence[str],
    features_b: Sequence[str],
    date_col: str,
    n_folds: int = 5,
    min_train_fraction: float = 0.5,
    embargo_days: int = 0,
) -> ModelComparison | None:
    """Walk-forward comparison of two linear models on the same rows and folds."""
    used = list(dict.fromkeys([target, date_col, *features_a, *features_b]))
    data = frame[used].dropna().reset_index(drop=True)
    folds = expanding_date_folds(data[date_col], n_folds=n_folds,
                                 min_train_fraction=min_train_fraction, embargo_days=embargo_days)
    if not folds:
        return None
    pred_a, bench, tested = walk_forward_predictions(data, features_a, target, folds)
    pred_b, _, _ = walk_forward_predictions(data, features_b, target, folds)
    y = data[target].to_numpy(dtype=float)[tested]
    pa, pb, bm = pred_a[tested], pred_b[tested], bench[tested]
    loss_diff = (y - pa) ** 2 - (y - pb) ** 2
    dates = pd.to_datetime(data[date_col]).dt.normalize().to_numpy()[tested]
    diff_mean, diff_p = clustered_mean_test(loss_diff, dates)
    return ModelComparison(
        n_test=int(tested.sum()), n_folds=len(folds),
        oos_r2_a=oos_r2(y, pa, bm), oos_r2_b=oos_r2(y, pb, bm),
        spearman_a=_spearman(y, pa), spearman_b=_spearman(y, pb),
        loss_diff_mean=diff_mean, loss_diff_p=diff_p,
    )


def clustered_ols(
    frame: pd.DataFrame,
    *,
    target: str,
    features: Sequence[str],
    cluster_col: str,
) -> OlsResult | None:
    """Full-sample OLS with CR1 cluster-robust SEs and t(G-1) p-values."""
    data = frame[list(dict.fromkeys([target, cluster_col, *features]))].dropna()
    n, k = len(data), len(features) + 1
    if n <= k + 1:
        return None
    y = data[target].to_numpy(dtype=float)
    design = _design(data, features)
    coefficients, *_ = np.linalg.lstsq(design, y, rcond=None)
    residuals = y - design @ coefficients
    clusters = data[cluster_col].astype(str).to_numpy()
    covariance = _clustered_covariance(design, residuals, clusters)
    n_clusters = len(np.unique(clusters))
    dof = max(n_clusters - 1, 1)
    result: dict[str, Coefficient] = {}
    for index, name in enumerate(features, start=1):
        se = float(np.sqrt(max(covariance[index, index], 0.0)))
        beta = float(coefficients[index])
        t = beta / se if se > 0 else 0.0
        p = float(2 * stats.t.sf(abs(t), dof)) if se > 0 else 1.0
        result[name] = Coefficient(beta=beta, se=se, t=t, p=p)
    total = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum(residuals ** 2)) / total if total > 0 else 0.0
    return OlsResult(n=n, n_clusters=n_clusters, r_squared=r2, coefficients=result)
