"""Tests for the release → per-pair reaction model (research/reaction). Synthetic data, no network."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from research.reaction import metrics
from research.reaction.model import fit_fold, fit_logistic, predict
from research.reaction.panel import same_clock_control, signed_log_returns

T0 = int(datetime(2024, 3, 6, 13, 30, tzinfo=UTC).timestamp())   # a Wednesday


def _bars(start: int, minutes: int, price0: float = 1.0, step: float = 0.0):
    times = start + 60 * np.arange(1, minutes + 1)
    prices = price0 * np.exp(step * np.arange(minutes))
    return times.astype(np.int64), prices


def test_signed_log_returns_and_fail_closed_lags():
    times, prices = _bars(T0 - 600, 200, step=1e-4)            # +1 bp per minute
    r = signed_log_returns(times, prices, np.array([T0]), np.array([T0 + 15 * 60]))
    assert r[0] == pytest.approx(15.0, abs=1e-6)
    gap = times[(times < T0) | (times > T0 + 10 * 60)]           # 10-minute hole after entry
    gp = prices[(times < T0) | (times > T0 + 10 * 60)]
    assert np.isnan(signed_log_returns(gap, gp, np.array([T0 + 60]), np.array([T0 + 120]))[0])


def test_same_clock_control_needs_ten_valid_days():
    days = []
    for d in range(1, 31):
        start = T0 - d * 86400 - 600
        days.append(_bars(start, 40, step=2e-4))
    times = np.concatenate([t for t, _ in days])
    prices = np.concatenate([p for _, p in days])
    order = np.argsort(times)
    v = same_clock_control(times[order], prices[order], np.array([T0]), 0, 15)
    assert v[0] == pytest.approx(30.0, abs=1e-3)               # 2 bp/min × 15 min
    few = same_clock_control(times[order][:40 * 5], prices[order][:40 * 5], np.array([T0]), 0, 15)
    assert np.isnan(few[0])


def _synthetic(n_months: int = 36, per_month: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    start = datetime(2018, 1, 1, tzinfo=UTC)
    for m in range(n_months):
        month_start = (start + timedelta(days=31 * m)).replace(day=1)
        for k in range(per_month):
            ts = month_start + timedelta(days=k % 27, hours=12, minutes=30)
            currency = "USD" if k % 3 else "EUR"
            z = float(np.clip(rng.normal(), -5, 5)) or 0.1
            # USD good news → EUR/USD down; EUR good news → EUR/USD up.
            beta = -5.0 if currency == "USD" else 6.0
            r = beta * z + rng.normal(0, 4)
            rows.append({"release_id": f"{m}-{k}", "moment": ts.isoformat(), "month": ts.strftime("%Y-%m"),
                         "currency": currency, "category": "inflation", "pair": "EUR/USD",
                         "side": -1 if currency == "USD" else 1, "z": z, "news_sign": 1,
                         "r15": r, "v_release": 3.0 + rng.uniform(0, 1)})
            # A cross pair (no direction expected for EUR releases on USD/JPY).
            rows.append({"release_id": f"{m}-{k}", "moment": ts.isoformat(), "month": ts.strftime("%Y-%m"),
                         "currency": currency, "category": "inflation", "pair": "USD/JPY",
                         "side": 1 if currency == "USD" else None, "z": z, "news_sign": 1,
                         "r15": (5.0 * z if currency == "USD" else 0.0) + rng.normal(0, 4),
                         "v_release": 3.0})
    return pd.DataFrame(rows)


def test_cells_keyed_by_currency_do_not_cancel_and_cross_pairs_abstain():
    data = _synthetic()
    months = sorted(data["month"].unique())
    train, test = data[data["month"] < months[24]], data[data["month"] >= months[24]]
    model = fit_fold(train, target="r15", control="v_release")
    assert model.beta_cell[("USD", "inflation", "EUR/USD")] < 0 < model.beta_cell[("EUR", "inflation", "EUR/USD")]
    pred = predict(model, test)
    own = test["side"].notna().to_numpy()
    acc = metrics.accuracy(pred["s_hat"].to_numpy()[own], test["r15"].to_numpy()[own])
    assert acc > 0.7          # r = β·z + N(0, 4): the noise bounds accuracy near 0.78
    cross = (test["currency"] == "EUR") & (test["pair"] == "USD/JPY")
    assert pred.loc[cross, "s_hat"].isna().all()
    assert pred.loc[cross, "expected_move_bps"].notna().all()


def test_confidence_rises_with_surprise_size_and_is_roughly_calibrated():
    data = _synthetic(n_months=48, per_month=40, seed=1)
    months = sorted(data["month"].unique())
    train, test = data[data["month"] < months[30]], data[data["month"] >= months[30]]
    model = fit_fold(train, target="r15", control="v_release")
    pred = predict(model, test)
    own = test["side"].notna().to_numpy()
    p = pred["p_dir"].to_numpy()[own]
    z = np.abs(test["z"].to_numpy()[own])
    assert np.corrcoef(p, z)[0, 1] > 0.5
    correct = (np.sign(pred["s_hat"].to_numpy()[own]) == np.sign(test["r15"].to_numpy()[own])).astype(float)
    assert metrics.ece(p, correct) < 0.08


def test_conformal_interval_covers_near_nominal():
    data = _synthetic(n_months=48, per_month=40, seed=2)
    months = sorted(data["month"].unique())
    train, test = data[data["month"] < months[36]], data[data["month"] >= months[36]]
    model = fit_fold(train, target="r15", control="v_release")
    assert set(model.conformal_q) == {0.8, 0.9}
    pred = predict(model, test)
    own = test["side"].notna().to_numpy()
    cov = metrics.interval_coverage(pred["lo90"].to_numpy()[own], pred["hi90"].to_numpy()[own],
                                    np.abs(test["r15"].to_numpy()[own]))
    assert 0.83 <= cov["coverage"] <= 0.97


def test_logistic_refuses_degenerate_and_metrics_basics():
    assert fit_logistic(np.ones((30, 2)), np.ones(30)) is None
    assert metrics.ece(np.full(100, 0.7), (np.arange(100) < 70).astype(float)) == pytest.approx(0.0, abs=1e-9)
    res = metrics.clustered_mean_diff(np.array([1.0, 1.0, 3.0, 3.0]), ["a", "a", "b", "b"])
    assert res["mean"] == pytest.approx(2.0) and res["clusters"] == 2
    ci = metrics.cluster_bootstrap_ci(["a", "b", "c", "d"] * 5,
                                      lambda idx: float(np.mean(np.arange(20)[idx] % 2)), draws=200)
    assert ci is not None and ci[0] <= 0.5 <= ci[1]
