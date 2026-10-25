"""H1 and H5 machinery on synthetic data: no look-ahead, sign identification, decision rules."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from research.features import attach_semantic_features, compute_event_semantics, pair_news_currency
from research.semantic.currency_index import (
    STUDY_CURRENCIES,
    ScoredHeadline,
    daily_currency_sentiment,
    fx_day,
    implied_pair_sentiment,
    pair_tagged_sentiment,
    rolling_currency_sentiment,
    surprise_index,
    walk_forward_market_component,
)
from research.semantic.embeddings import HashingEmbedder
from research.semantic.h5 import H5TestA, H5TestB, backend_verdict, h5_decision, h5_test_a, h5_test_b
from research.semantic.sentiment import LexiconSentiment
from research.semantic.walkforward import expanding_date_folds

T0 = datetime(2019, 1, 1, 12, tzinfo=UTC)


# ---------------------------------------------------------------- walk-forward

def test_folds_never_train_on_or_after_the_test_block():
    dates = [T0 + timedelta(days=i // 3) for i in range(300)]          # 3 rows per day
    folds = expanding_date_folds(dates, n_folds=4, min_train_fraction=0.5, embargo_days=2)
    assert len(folds) == 4
    day = pd.to_datetime(pd.Series(dates)).dt.normalize()
    for train, test in folds:
        assert day[train].max() < day[test].min() - pd.Timedelta(days=2), "embargo respected"
        assert not (train & test).any()


# (The locked-protocol H1 test, superseding the §2 draft rule, is tested in test_h1_locked.py.)


# ---------------------------------------------------------------- semantic join

def test_semantics_drop_syndicated_copies_and_join_via_the_news_currency():
    rows = [
        {"event_id": "a", "timestamp": T0, "headline": "ECB raises rates as euro jumps", "currencies": "EUR", "domain": "x"},
        {"event_id": "a2", "timestamp": T0 + timedelta(minutes=15), "headline": "ECB raises rates as euro jumps",
         "currencies": "EUR", "domain": "y"},
    ]
    rows += [{"event_id": f"n{i}", "timestamp": T0 + timedelta(hours=2 + i),
              "headline": f"euro story number {i} about trade {i * 7}", "currencies": "EUR", "domain": "z"}
             for i in range(6)]
    semantics = compute_event_semantics(pd.DataFrame(rows), embedder_factory=HashingEmbedder,
                                        scorer=LexiconSentiment(), currencies=["EUR"])
    by_id = semantics.set_index("event_id")
    assert "a2" not in by_id.index and by_id.loc["a", "coverage_count"] == 2
    assert pd.isna(by_id.loc["a", "novelty"]), "no prior context for the first story"
    assert pd.notna(by_id.loc["n5", "novelty"]), "enough prior context by the sixth story"
    assert by_id.loc["a", "sentiment"] > 0

    table = pd.DataFrame({"event_id": ["n5", "n5"], "pair": ["EUR/USD", "USD/JPY"]})
    joined = attach_semantic_features(table, semantics)
    assert joined.loc[0, "novelty"] == pytest.approx(by_id.loc["n5", "novelty"])
    assert pd.isna(joined.loc[1, "novelty"]), "a JPY row never borrows EUR-stream scores"
    assert pair_news_currency("USD/INR") == "INR" and pair_news_currency("GBP/USD") == "GBP"


# ---------------------------------------------------------------- currency index

def test_fx_day_follows_the_21utc_close_and_rolls_weekends():
    assert fx_day(datetime(2019, 1, 2, 20, 59, tzinfo=UTC)) == date(2019, 1, 2)
    assert fx_day(datetime(2019, 1, 2, 21, 0, tzinfo=UTC)) == date(2019, 1, 3)
    assert fx_day(datetime(2019, 1, 5, 10, tzinfo=UTC)) == date(2019, 1, 7), "Saturday -> Monday"
    assert fx_day(datetime(2019, 1, 4, 22, tzinfo=UTC)) == date(2019, 1, 7), "Friday after close -> Monday"


def test_currency_index_and_base_minus_quote_algebra():
    day = datetime(2019, 1, 2, 10, tzinfo=UTC)
    headlines = [
        ScoredHeadline(day, ("EUR",), 0.6),
        ScoredHeadline(day, ("EUR", "USD"), -0.2),
        ScoredHeadline(day, ("JPY",), 0.4),
    ]
    index = daily_currency_sentiment(headlines)
    assert index.loc[date(2019, 1, 2), "S_EUR"] == pytest.approx(0.2)
    assert index.loc[date(2019, 1, 2), "S_USD"] == pytest.approx(-0.2)
    assert index.loc[date(2019, 1, 2), "n_GBP"] == 0 and index.loc[date(2019, 1, 2), "S_GBP"] == 0.0
    implied = implied_pair_sentiment(index, "EUR/USD").iloc[0]
    assert implied == pytest.approx(0.4)
    assert implied_pair_sentiment(index, "USD/JPY").iloc[0] == pytest.approx(-0.2 - 0.4)
    # Baseline: EUR-tagged headlines only (USD-only headlines have no pair under policy "none").
    assert pair_tagged_sentiment(headlines, "EUR/USD").iloc[0] == pytest.approx(0.2)


def test_rolling_index_matches_the_live_window():
    now = datetime(2019, 1, 2, 12, tzinfo=UTC)
    headlines = [ScoredHeadline(now - timedelta(hours=30), ("EUR",), 1.0),
                 ScoredHeadline(now - timedelta(hours=2), ("EUR",), -0.5),
                 ScoredHeadline(now - timedelta(hours=1), ("USD",), 0.5)]
    out = rolling_currency_sentiment(headlines, now, window_hours=24)
    assert out["currency_sentiment"]["EUR"] == pytest.approx(-0.5), "30h-old story is outside"
    assert out["implied_pair_sentiment"]["EUR/USD"] == pytest.approx(-1.0)


def _factor_index(n_days: int, seed: int = 0) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    factor = rng.normal(0, 1, n_days)
    loads = {"USD": 0.8, "EUR": 0.9, "GBP": 0.7, "INR": 0.6, "JPY": -0.5, "CHF": -0.4}
    data = {f"S_{c}": loads[c] * factor + rng.normal(0, 0.3, n_days) for c in STUDY_CURRENCIES}
    days = [d.date() for d in pd.bdate_range("2019-01-01", periods=n_days)]
    return pd.DataFrame(data, index=pd.Index(days, name="fx_day")), factor


def test_market_component_sign_is_risk_on_and_identified():
    index, factor = _factor_index(400)
    component = walk_forward_market_component(index, min_train_days=60)["market_component"]
    valid = component.notna().to_numpy()
    assert np.corrcoef(component[valid], factor[valid])[0, 1] > 0.9


def test_market_component_has_no_look_ahead():
    index, _ = _factor_index(200)
    before = walk_forward_market_component(index, min_train_days=60)["market_component"]
    shocked = index.copy()
    shocked.iloc[150:] = shocked.iloc[150:] * 50 + 7     # rewrite the future
    after = walk_forward_market_component(shocked, min_train_days=60)["market_component"]
    pd.testing.assert_series_equal(before.iloc[:150], after.iloc[:150])
    assert before.iloc[:60].isna().all()


def test_surprise_index_orients_signs_decays_and_excludes_the_pandemic_window():
    calendar = pd.DataFrame([
        {"timestamp": datetime(2019, 1, 4, 13, 30, tzinfo=UTC), "country": "United States",
         "event_name": "Unemployment Rate", "importance": "high", "standardized_surprise": 2.0,
         "timestamp_precision_minutes": 0.0, "in_pandemic_window": False},
        {"timestamp": datetime(2020, 5, 8, 12, 30, tzinfo=UTC), "country": "United States",
         "event_name": "Non-Farm Employment Change", "importance": "high", "standardized_surprise": 9.0,
         "timestamp_precision_minutes": 0.0, "in_pandemic_window": True},
    ])
    days = [date(2019, 1, 3), date(2019, 1, 4), date(2019, 2, 4), date(2020, 5, 8)]
    si = surprise_index(calendar, days)
    assert si.loc[date(2019, 1, 3), "SI_USD"] == 0.0, "nothing released yet"
    assert si.loc[date(2019, 1, 4), "SI_USD"] == pytest.approx(-2.0), "higher unemployment is bad news"
    assert si.loc[date(2020, 5, 8), "SI_USD"] == pytest.approx(5.0), "winsorized at 5 sigma"
    robust = surprise_index(calendar, days, exclude_pandemic_window=True)
    assert robust.loc[date(2020, 5, 8), "SI_USD"] == 0.0
    assert si.loc[date(2019, 1, 4), "SI_EUR"] == 0.0


# ---------------------------------------------------------------- H5 tests

def test_h5_a_prefers_the_decomposed_measure_when_returns_are_currency_driven():
    rng = np.random.default_rng(1)
    n_days = 400
    days = pd.to_datetime(pd.bdate_range("2019-01-01", periods=n_days))
    rows = []
    for pair in ("EUR/USD", "GBP/USD", "USD/JPY"):
        s_base, s_quote = rng.normal(0, 1, n_days), rng.normal(0, 1, n_days)
        implied = s_base - s_quote
        tagged = (s_quote if pair.startswith("USD") else s_base) + rng.normal(0, 1, n_days)
        rows.append(pd.DataFrame({"fx_day": days, "ret_bps": 20 * implied + rng.normal(0, 20, n_days),
                                  "tagged_sentiment": tagged, "implied_sentiment": implied,
                                  "si_diff": rng.normal(0, 1, n_days)}))
    test_a = h5_test_a(pd.concat(rows, ignore_index=True))
    assert test_a.passed and test_a.comparison.delta_oos_r2 > 0


def _daily_b(beta_vix: float, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = 500
    m = rng.normal(0, 1, n)
    si = rng.normal(0, 1, n)
    return pd.DataFrame({
        "fx_day": pd.bdate_range("2019-01-01", periods=n), "market_component": m, "SI_USD": si,
        "vix_change": beta_vix * m + rng.normal(0, 1, n),
        "ret_USD/JPY": 5 * m + rng.normal(0, 10, n), "ret_USD/CHF": 4 * m + rng.normal(0, 10, n),
    })


def test_h5_b_requires_every_predicted_sign():
    assert h5_test_b(_daily_b(-0.5), controls=["SI_USD"]).passed
    wrong = h5_test_b(_daily_b(+0.5), controls=["SI_USD"])
    assert not wrong.passed and wrong.outcome_passes("ret_USD/JPY") and not wrong.outcome_passes("vix_change")


def test_h5_decision_rule():
    passing_b = h5_test_b(_daily_b(-0.5), controls=["SI_USD"])
    failing_a = H5TestA(comparison=None, implied_ols=None)
    assert backend_verdict(failing_a, passing_b) == "partial"
    assert backend_verdict(failing_a, H5TestB()) == "neither"
    assert h5_decision({"lexicon": "partial"}) == "incomplete"
    assert h5_decision({"lexicon": "neither", "finbert": "neither"}) == "rejected"
    assert h5_decision({"lexicon": "neither", "finbert": "partial"}) == "partial"
    assert h5_decision({"lexicon": "supported"}) == "supported"
