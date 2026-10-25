"""Multiple-testing correction for the impact atlas.

The atlas tests many (category x horizon) cells simultaneously. At alpha=0.05 with 24 cells,
roughly one "significant" result is expected by chance alone, so uncorrected p-values overstate
the evidence. Benjamini-Hochberg controls the false discovery rate (the expected share of false
positives among rejections), which is the standard choice for exploratory event-study grids —
less conservative than Bonferroni, and appropriate when the goal is to flag which categories
deserve follow-up rather than to prove a single pre-registered hypothesis.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Correction:
    p_value: float
    q_value: float          # BH-adjusted p (FDR)
    significant: bool       # survives FDR at the given alpha


def benjamini_hochberg(p_values: list[float], alpha: float = 0.05) -> list[Correction]:
    """Return BH-adjusted q-values and rejection flags, in the input order."""
    n = len(p_values)
    if n == 0:
        return []
    order = sorted(range(n), key=lambda i: p_values[i])
    q_raw = [0.0] * n
    for rank, index in enumerate(order, start=1):
        q_raw[index] = min(1.0, p_values[index] * n / rank)

    # Enforce monotonicity: q is non-decreasing in p (step-up from the largest p).
    running_min = 1.0
    for index in reversed(order):
        running_min = min(running_min, q_raw[index])
        q_raw[index] = running_min

    return [
        Correction(p_value=p_values[i], q_value=q_raw[i], significant=q_raw[i] <= alpha)
        for i in range(n)
    ]


def correct_atlas(atlas: dict, alpha: float = 0.05, *, min_n: int = 1) -> dict[tuple[str, int], Correction]:
    """Apply BH-FDR across every (category, horizon) cell of an atlas.

    Returns a mapping keyed by (category, horizon_minutes). Cells with fewer than `min_n`
    observations are excluded from the correction family (they are noise, and including them
    only inflates the correction denominator).
    """
    keys: list[tuple[str, int]] = []
    p_values: list[float] = []
    for category, impact in atlas.items():
        for horizon, cell in impact.horizons.items():
            if cell.n < min_n:
                continue
            keys.append((category, horizon))
            p_values.append(cell.p_value)
    corrections = benjamini_hochberg(p_values, alpha=alpha)
    return dict(zip(keys, corrections))
