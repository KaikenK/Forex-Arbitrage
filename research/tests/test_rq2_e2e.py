"""RQ2-E2E plumbing on synthetic headlines, releases and bars (no network, no freeze).

The GDELT sample only stores headlines the research tagger attributes to a currency, so a release
headline is a candidate only if it also names the currency or its central bank ("Dollar climbs as
US consumer prices rise 0.5%"); plain "US CPI rises 0.5%" never reaches the sample."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from research import run_rq2_e2e as e2e

REL = datetime(2024, 3, 12, 12, 30, tzinfo=UTC)


def _releases():
    return pd.DataFrame([
        {"event_id": "r1", "timestamp": pd.Timestamp(REL), "currency": "USD", "category": "inflation",
         "country": "United States", "event_name": "CPI m/m", "forecast": 0.3, "actual": 0.5,
         "standardized_surprise": 1.5},
        {"event_id": "r2", "timestamp": pd.Timestamp(REL), "currency": "USD", "category": "inflation",
         "country": "United States", "event_name": "CPI y/y", "forecast": 3.1, "actual": 3.2,
         "standardized_surprise": 0.7},
        {"event_id": "r3", "timestamp": pd.Timestamp(REL - timedelta(hours=3)), "currency": "USD",
         "category": "employment", "country": "United States", "event_name": "Unemployment Claims",
         "forecast": 220000.0, "actual": 230000.0, "standardized_surprise": 0.5},
    ])


def _headlines():
    return pd.DataFrame([
        {"event_id": "h1", "timestamp": pd.Timestamp(REL + timedelta(minutes=15)),
         "headline": "Dollar climbs as US consumer prices rise 0.5% in February, inflation above forecasts"},
        {"event_id": "h2", "timestamp": pd.Timestamp(REL + timedelta(minutes=30)),
         "headline": "Dollar inflation fears ease as markets rally"},                # no number: no match
        {"event_id": "h3", "timestamp": pd.Timestamp(REL + timedelta(hours=2)),
         "headline": "Dollar firm as US consumer prices rise 0.5%, inflation data show"},  # too late
    ])


def test_forecast_strings_carry_the_indicator_unit():
    assert e2e.forecast_string("CPI m/m", 0.3) == "0.3%"
    assert e2e.forecast_string("Non-Farm Employment Change", 180000.0) == "180K"
    assert e2e.forecast_string("ISM Manufacturing PMI", 49.5) == "49.5"


def test_candidates_need_same_currency_category_and_window():
    cands = e2e.candidates(_headlines(), _releases())
    assert list(cands["event_id"]) == ["h1", "h2"]
    assert set(cands.iloc[0]["release_events"]) == {"CPI m/m", "CPI y/y"}


def test_extraction_correctness_and_directions():
    extractor = e2e._service_modules()
    sigma = {("USD", "CPI m/m"): 0.13, ("USD", "CPI y/y"): 0.14}
    extracted = e2e.extract(e2e.candidates(_headlines(), _releases()), _releases(), sigma, extractor)
    first, second = extracted.iloc[0], extracted.iloc[1]
    assert first["matched"] and first["matched_event"] == "CPI m/m" and first["correct_actual"]
    assert not second["matched"] and not second["correct_actual"]
    rows = e2e.pair_rows(extracted)
    assert sorted(rows[rows["event_id"] == "h1"]["pair"]) == ["EUR/USD", "GBP/USD", "USD/CHF", "USD/JPY"]
    assert e2e.theory_direction("USD", "CPI m/m", 1.5, "EUR/USD") == -1.0      # USD up: EUR/USD down
    assert e2e.theory_direction("USD", "Unemployment Rate", 1.0, "USD/JPY") == -1.0
    assert np.isnan(e2e.theory_direction("USD", None, None, "EUR/USD"))


def test_returns_and_summary_on_synthetic_bars():
    extractor = e2e._service_modules()
    # Both CPI rows need a σ: without one the y/y row cannot be ruled implausible and the
    # extractor (correctly) abstains between two equally good rows.
    extracted = e2e.extract(e2e.candidates(_headlines(), _releases()), _releases(),
                            {("USD", "CPI m/m"): 0.13, ("USD", "CPI y/y"): 0.14}, extractor)
    rows = e2e.pair_rows(extracted)

    def bars(pair, hours):                      # USD rallies after the release
        start = int(REL.timestamp()) - 3600
        times = start + 60 * np.arange(1, 4 * 60)
        drift = (-1e-4 if pair.endswith("/USD") else 1e-4) * np.maximum(0, times - int(REL.timestamp())) / 60
        return times.astype(np.int64), np.exp(drift)

    rows = e2e.attach_returns(rows, bars)
    rows["dir_structured"] = [e2e.theory_direction(r.currency, r.matched_event, r.z, r.pair)
                              for r in rows.itertuples(index=False)]
    rows["dir_finbert"] = np.nan
    rows["dir_v1_text_model"] = np.nan
    summary = e2e.evaluate(rows)
    assert summary["article_time_r60"]["structured"]["accuracy"] == pytest.approx(1.0)
    assert summary["release_time_r15"]["structured"]["n"] == 4
    assert summary["article_time_r60"]["finbert"]["n"] == 0
