"""Tests for context, feature-table assembly, and modular abnormal-return models."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np

from research.eventstudy.abnormal import (
    ConstantMeanModel,
    MarketModel,
    MultiFactorModel,
    abnormal_return,
)
from research.eventstudy.schema import PriceSeries
from research.features import build_feature_table, build_market_context_fn, session_features


def _minute_series(pair, start, prices):
    return PriceSeries(pair=pair, times=[start + timedelta(minutes=i) for i in range(len(prices))], prices=prices)


def _daily_series(pair, start, prices):
    return PriceSeries(pair=pair, times=[start + timedelta(days=i) for i in range(len(prices))], prices=prices)


# ---------------------------------------------------------------- context

def test_session_features_overlap():
    ts = datetime(2024, 1, 3, 14, 0, tzinfo=UTC)  # 14:00 UTC -> London/NY overlap
    feats = session_features(ts)
    assert feats["session"] == "London/NY"
    assert feats["overlap_flag"] is True
    assert feats["day_of_week"] == 2


def test_market_context_is_leak_safe_asof():
    start = datetime(2024, 1, 1, tzinfo=UTC)
    dxy = _daily_series("DXY", start, [100.0 + i for i in range(25)])
    ctx = build_market_context_fn({"DXY": dxy})
    # As-of day index 10 -> level 110, must not see later bars.
    row = ctx("EUR/USD", start + timedelta(days=10, hours=12))
    assert row["dxy_level"] == 110.0
    assert row["dxy_ret_1d"] is not None
    assert row["dxy_rvol_20d"] is not None


# ---------------------------------------------------------------- assemble

def test_build_feature_table_one_row_with_all_blocks():
    event_ts = datetime(2024, 1, 2, 13, 0, tzinfo=UTC)   # 13:00 UTC -> London/NY overlap
    origin = event_ts - timedelta(minutes=30)
    prices = [1.0] * 30 + [1.0, 1.001, 1.002, 1.003]      # index 30 == event_ts
    series = _minute_series("EUR/USD", origin, prices)
    series_by_pair = {"EUR/USD": series}
    dxy = _daily_series("DXY", datetime(2024, 1, 1, tzinfo=UTC), [100.0 + 0.1 * i for i in range(10)])
    context_fn = build_market_context_fn({"DXY": dxy}, series_by_pair)

    records = [{
        "event_id": "e1", "timestamp": event_ts, "pair": "EUR/USD",
        "categories": ("rate_decision",), "source": "calendar", "event_type": "scheduled",
        "surprise": 96000.0, "importance": "High",
    }]
    table = build_feature_table(records, series_by_pair, horizons_minutes=[1, 2],
                                context_fn=context_fn, max_entry_lag_minutes=5)

    assert len(table) == 1
    row = table.iloc[0]
    assert row["event_id"] == "e1"
    assert row["event_type"] == "scheduled"
    assert row["surprise_bucket"] == "beat"
    assert row["ret_1m"] > 0
    assert row["direction_1m"] == 1
    assert row["session"] == "London/NY"
    assert "dxy_level" in table.columns


# ---------------------------------------------------------------- abnormal models

def test_constant_mean_model():
    model = ConstantMeanModel().fit([0.01, 0.02, 0.03])
    assert abs(model.expected() - 0.02) < 1e-9
    assert abs(abnormal_return(0.05, model) - 0.03) < 1e-9


def test_market_model_recovers_beta():
    rng = np.random.default_rng(0)
    factor = rng.normal(0, 0.01, 400)
    pair = 0.0002 + 1.5 * factor + rng.normal(0, 1e-5, 400)  # alpha=2bp, beta=1.5, tiny noise
    model = MarketModel(factors=("dxy",)).fit(pair, {"dxy": factor})
    assert abs(model.betas_["dxy"] - 1.5) < 0.05
    assert abs(model.alpha_ - 0.0002) < 1e-3
    # abnormal = observed - (alpha + beta*factor)
    ar = abnormal_return(0.01, model, {"dxy": 0.005})
    assert abs(ar - (0.01 - (model.alpha_ + model.betas_["dxy"] * 0.005))) < 1e-12


def test_multifactor_model_is_market_model_over_factors():
    model = MultiFactorModel(("dxy", "vix"))
    assert model.factors == ("dxy", "vix")
    assert model.name == "factor"
