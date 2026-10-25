"""Modular expected-return models for abnormal-return (event-study) analysis.

Abnormal return = observed − expected. The *expected* model is pluggable so the research can
compare which definition is most predictive rather than hard-coding one:

  - ConstantMeanModel   : E[r] = mean of pair returns over the estimation window (Brown-Warner).
  - MarketModel         : E[r] = alpha + beta * factor   (single factor, e.g. DXY).
  - MultiFactorModel    : E[r] = alpha + sum_k beta_k * factor_k   (DXY, VIX, oil, yields...).

All are fit on a pre-event estimation window (default 60 trading days) so nothing leaks. The
table interface is untouched: `add_abnormal_returns` only appends ar_<model>_<h> columns.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

import numpy as np


class ExpectedReturnModel(Protocol):
    name: str

    def fit(self, pair_returns: Sequence[float], factor_returns: dict[str, Sequence[float]]) -> "ExpectedReturnModel":
        ...

    def expected(self, factor_row: dict[str, float]) -> float:
        ...


@dataclass
class ConstantMeanModel:
    name: str = "const"
    mean_: float = 0.0

    def fit(self, pair_returns, factor_returns=None):
        values = [r for r in pair_returns if r is not None and np.isfinite(r)]
        self.mean_ = float(np.mean(values)) if values else 0.0
        return self

    def expected(self, factor_row=None) -> float:
        return self.mean_


@dataclass
class MarketModel:
    factors: tuple[str, ...] = ("dxy",)
    name: str = "market"
    alpha_: float = 0.0
    betas_: dict[str, float] = field(default_factory=dict)

    def fit(self, pair_returns, factor_returns):
        y = np.asarray(pair_returns, dtype=float)
        columns = [np.asarray(factor_returns[f], dtype=float) for f in self.factors]
        design = np.column_stack([np.ones(len(y))] + columns)
        mask = np.isfinite(y) & np.all(np.isfinite(design), axis=1)
        if mask.sum() <= len(self.factors) + 1:
            self.alpha_, self.betas_ = 0.0, {f: 0.0 for f in self.factors}
            return self
        coeffs, *_ = np.linalg.lstsq(design[mask], y[mask], rcond=None)
        self.alpha_ = float(coeffs[0])
        self.betas_ = {factor: float(coeffs[i + 1]) for i, factor in enumerate(self.factors)}
        return self

    def expected(self, factor_row: dict[str, float]) -> float:
        return self.alpha_ + sum(self.betas_.get(f, 0.0) * float(factor_row.get(f, 0.0)) for f in self.factors)


def MultiFactorModel(factors=("dxy", "vix", "oil", "yield_diff")) -> MarketModel:
    """Multi-factor expected-return model (a MarketModel over several factors)."""
    return MarketModel(factors=tuple(factors), name="factor")


def abnormal_return(observed: float, model: ExpectedReturnModel, factor_row: dict[str, float] | None = None) -> float:
    return observed - model.expected(factor_row or {})


def fit_expected_model(
    model: ExpectedReturnModel,
    pair_returns: Sequence[float],
    factor_returns: dict[str, Sequence[float]] | None = None,
) -> ExpectedReturnModel:
    """Fit any model on an estimation window (caller supplies the pre-event slice, e.g. 60 days)."""
    return model.fit(pair_returns, factor_returns or {})
