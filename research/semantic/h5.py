"""H5 — news sentiment is a currency-level factor (RESEARCH_TRACKER §2, pre-registered 2026-10-24).

Pre-registered rule, implemented literally:
  Test (a)   walk-forward comparison of implied pair sentiment S_base - S_quote against
             pair-tagged sentiment as a predictor of daily pair returns; SEs clustered by date.
  Test (b)   the market component against same-day VIX change and JPY and CHF safe-haven
             returns, controlling for the surprise index.
  Supported  (a) improves out-of-sample R² or Spearman over the pair-tagged baseline, AND
             (b) the market component is significant with the predicted sign
             (risk-on <-> VIX falls, JPY and CHF weaken).
  Rejected   neither (a) nor (b) holds with either sentiment backend.
  Stop       one index definition x the two existing backends (lexicon, FinBERT).

Both models in (a) carry the same surprise-index control, so (a) compares the two news
measures on equal terms. (b) is three regressions with predicted signs
  vix_change: beta < 0     USD/JPY return: beta > 0     USD/CHF return: beta > 0
(JPY or CHF weakening = USD/JPY or USD/CHF rising) and passes only if all three are
significant with the predicted sign.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd

from .walkforward import ModelComparison, OlsResult, clustered_ols, compare_models

SENTIMENT_BACKENDS: tuple[str, ...] = ("lexicon", "finbert")
PREDICTED_SIGNS: dict[str, int] = {"vix_change": -1, "ret_USD/JPY": +1, "ret_USD/CHF": +1}
DEFAULT_ALPHA = 0.05


@dataclass(frozen=True)
class H5TestA:
    comparison: ModelComparison | None
    implied_ols: OlsResult | None

    @property
    def passed(self) -> bool:
        return self.comparison is not None and self.comparison.b_improves


@dataclass(frozen=True)
class H5TestB:
    regressions: dict[str, OlsResult | None] = field(default_factory=dict)
    alpha: float = DEFAULT_ALPHA

    def outcome_passes(self, outcome: str) -> bool:
        result = self.regressions.get(outcome)
        coef = result.coefficients.get("market_component") if result else None
        if coef is None:
            return False
        return coef.p < self.alpha and coef.beta * PREDICTED_SIGNS[outcome] > 0

    @property
    def passed(self) -> bool:
        return all(self.outcome_passes(outcome) for outcome in PREDICTED_SIGNS)


def h5_test_a(
    panel: pd.DataFrame,
    *,
    target: str = "ret_bps",
    tagged: str = "tagged_sentiment",
    implied: str = "implied_sentiment",
    controls: Sequence[str] = ("si_diff",),
    date_col: str = "fx_day",
    n_folds: int = 5,
    min_train_fraction: float = 0.5,
) -> H5TestA:
    """Pooled panel of (pair, FX day) rows. Model A: tagged + controls. Model B: implied + controls."""
    comparison = compare_models(
        panel, target=target, features_a=[tagged, *controls], features_b=[implied, *controls],
        date_col=date_col, n_folds=n_folds, min_train_fraction=min_train_fraction,
    )
    ols = clustered_ols(panel, target=target, features=[implied, *controls], cluster_col=date_col)
    return H5TestA(comparison=comparison, implied_ols=ols)


def h5_test_b(
    daily: pd.DataFrame,
    *,
    controls: Sequence[str],
    date_col: str = "fx_day",
    alpha: float = DEFAULT_ALPHA,
) -> H5TestB:
    """One row per FX day: market_component, the outcomes in PREDICTED_SIGNS, and controls."""
    regressions = {
        outcome: clustered_ols(daily, target=outcome, features=["market_component", *controls],
                               cluster_col=date_col)
        for outcome in PREDICTED_SIGNS if outcome in daily
    }
    return H5TestB(regressions=regressions, alpha=alpha)


def backend_verdict(test_a: H5TestA, test_b: H5TestB) -> str:
    if test_a.passed and test_b.passed:
        return "supported"
    if test_a.passed or test_b.passed:
        return "partial"
    return "neither"


def h5_decision(verdicts: Mapping[str, str], backends: Sequence[str] = SENTIMENT_BACKENDS) -> str:
    """"supported" if (a) and (b) hold for at least one backend; "rejected" if neither holds
    for every backend; "partial" otherwise; "incomplete" until both backends are tested."""
    if any(v == "supported" for v in verdicts.values()):
        return "supported"
    if not all(b in verdicts for b in backends):
        return "incomplete"
    if all(v == "neither" for v in verdicts.values()):
        return "rejected"
    return "partial"
