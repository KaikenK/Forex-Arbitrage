"""Evaluation metrics with release-moment clustering (RQ1–RQ4)."""
from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np
from scipy import stats
from sklearn.metrics import roc_auc_score

BOOTSTRAP_DRAWS = 1000
ECE_BINS = 10


def cluster_bootstrap_ci(clusters: Sequence, statistic: Callable[[np.ndarray], float], *,
                         draws: int = BOOTSTRAP_DRAWS, seed: int = 20261102,
                         alpha: float = 0.05) -> tuple[float, float] | None:
    """Percentile CI of `statistic(row_indices)` resampling whole clusters with replacement."""
    clusters = np.asarray(clusters)
    labels, inverse = np.unique(clusters, return_inverse=True)
    if len(labels) < 2:
        return None
    members = [np.flatnonzero(inverse == k) for k in range(len(labels))]
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(draws):
        pick = rng.integers(0, len(labels), len(labels))
        idx = np.concatenate([members[k] for k in pick])
        value = statistic(idx)
        if value is not None and np.isfinite(value):
            values.append(value)
    if len(values) < draws // 2:
        return None
    lo, hi = np.quantile(values, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi)


def accuracy(pred_sign: np.ndarray, real: np.ndarray) -> float | None:
    mask = np.isfinite(pred_sign) & np.isfinite(real) & (real != 0) & (pred_sign != 0)
    return float(np.mean(np.sign(pred_sign[mask]) == np.sign(real[mask]))) if mask.any() else None


def auc(score: np.ndarray, real: np.ndarray) -> float | None:
    mask = np.isfinite(score) & np.isfinite(real) & (real != 0)
    y = (real[mask] > 0).astype(int)
    if mask.sum() < 2 or y.min() == y.max():
        return None
    return float(roc_auc_score(y, score[mask]))


def _equal_mass_bins(p: np.ndarray, bins: int) -> np.ndarray:
    """Bin index per value from quantile edges, so tied probabilities share a bin."""
    edges = np.unique(np.quantile(p, np.linspace(0, 1, bins + 1)[1:-1]))
    return np.searchsorted(edges, p, side="right")


def ece(prob: np.ndarray, correct: np.ndarray, bins: int = ECE_BINS) -> float | None:
    """Expected calibration error over (approximately) equal-mass bins."""
    mask = np.isfinite(prob) & np.isfinite(correct)
    p, c = prob[mask], correct[mask]
    if len(p) < bins:
        return None
    idx = _equal_mass_bins(p, bins)
    total = sum(np.sum(idx == k) * abs(p[idx == k].mean() - c[idx == k].mean())
                for k in np.unique(idx))
    return float(total / len(p))


def reliability(prob: np.ndarray, correct: np.ndarray, bins: int = ECE_BINS) -> list[dict]:
    mask = np.isfinite(prob) & np.isfinite(correct)
    p, c = prob[mask], correct[mask]
    if not len(p):
        return []
    idx = _equal_mass_bins(p, bins)
    return [{"mean_p": float(p[idx == k].mean()), "hit_rate": float(c[idx == k].mean()),
             "n": int(np.sum(idx == k))} for k in np.unique(idx)]


def brier(prob: np.ndarray, correct: np.ndarray) -> float | None:
    mask = np.isfinite(prob) & np.isfinite(correct)
    return float(np.mean((prob[mask] - correct[mask]) ** 2)) if mask.any() else None


def mae(pred: np.ndarray, real: np.ndarray) -> float | None:
    mask = np.isfinite(pred) & np.isfinite(real)
    return float(np.mean(np.abs(pred[mask] - real[mask]))) if mask.any() else None


def spearman(pred: np.ndarray, real: np.ndarray) -> float | None:
    mask = np.isfinite(pred) & np.isfinite(real)
    if mask.sum() < 3:
        return None
    rho = stats.spearmanr(pred[mask], real[mask]).statistic
    return float(rho) if np.isfinite(rho) else None


def clustered_mean_diff(diff: np.ndarray, clusters: Sequence) -> dict | None:
    """Mean of `diff` with a cluster-robust SE (sums by cluster, small-sample adjusted)."""
    diff = np.asarray(diff, dtype=float)
    clusters = np.asarray(clusters)
    mask = np.isfinite(diff)
    diff, clusters = diff[mask], clusters[mask]
    labels, inverse = np.unique(clusters, return_inverse=True)
    g, n = len(labels), len(diff)
    if g < 2:
        return None
    mean = float(diff.mean())
    sums = np.bincount(inverse, weights=diff - mean)
    se = float(np.sqrt(g / (g - 1) * np.sum(sums ** 2)) / n)
    t = mean / se if se > 0 else float("nan")
    p = float(2 * stats.t.sf(abs(t), g - 1)) if np.isfinite(t) else float("nan")
    return {"mean": mean, "se": se, "t": t, "p": p, "n": n, "clusters": g}


def interval_coverage(lo: np.ndarray, hi: np.ndarray, real_abs: np.ndarray) -> dict | None:
    mask = np.isfinite(lo) & np.isfinite(hi) & np.isfinite(real_abs)
    if not mask.any():
        return None
    inside = (real_abs[mask] >= lo[mask]) & (real_abs[mask] <= hi[mask])
    return {"coverage": float(inside.mean()), "mean_width_bps": float(np.mean(hi[mask] - lo[mask])),
            "n": int(mask.sum())}
