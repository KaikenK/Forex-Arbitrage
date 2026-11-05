"""C41 (RESEARCH_TRACKER §6, 2026-11-05): rupee/RBI headlines reach USD/INR, USD-only headlines
stay with the four majors, and nothing changes for the majors. No network."""
import itertools

import pandas as pd

from research import run_h1
from research.data.tagging import currencies_to_pairs
from research.semantic.currency_bias import UNRESOLVED as H6_SETTINGS, build_pair_streams
from research.semantic.h1 import MAJORS, SECONDARY_PAIR, UNRESOLVED


def test_study_drops_inr_and_study_inr_keeps_it():
    assert currencies_to_pairs(["INR"], usd_policy="study") == []          # the defect
    assert currencies_to_pairs(["INR"], usd_policy="study_inr") == ["USD/INR"]
    assert currencies_to_pairs(["USD"], usd_policy="study_inr") == ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF"]
    assert currencies_to_pairs(["AUD", "CNY"], usd_policy="study_inr") == []


def test_study_inr_equals_study_on_the_majors():
    codes = ["USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD", "CNY", "INR"]
    for n in (1, 2, 3):
        for combo in itertools.combinations(codes, n):
            study = currencies_to_pairs(list(combo), usd_policy="study")
            widened = currencies_to_pairs(list(combo), usd_policy="study_inr")
            assert [p for p in widened if p in MAJORS] == study


def test_h1_inr_arm_records_are_rupee_headlines_only():
    ts = pd.Timestamp("2021-03-01 10:00", tz="UTC")
    events = pd.DataFrame({
        "event_id": ["a", "b", "c", "d"],
        "timestamp": [ts] * 4,
        "currencies": ["INR", "USD", "USD;INR", "EUR"],
        "headline": ["rupee slips", "dollar firm", "rupee and dollar", "euro up"],
    })
    canonical = {e: 1 for e in events["event_id"]}
    inr = run_h1.build_records(events, canonical, {SECONDARY_PAIR}, UNRESOLVED["inr_arm_usd_policy"])
    assert sorted(inr["event_id"]) == ["a", "c"]                      # USD-only "b" is not USD/INR news
    majors_old = run_h1.build_records(events, canonical, set(MAJORS), "study")
    majors_new = run_h1.build_records(events, canonical, set(MAJORS), UNRESOLVED["gdelt_usd_policy"])
    pd.testing.assert_frame_equal(majors_old, majors_new)            # C2 untouched


def test_h6_baseline_fills_usdinr_and_leaves_majors_unchanged():
    assert H6_SETTINGS["baseline_usd_policy"] == "study_inr"
    events = pd.DataFrame({
        "event_id": ["a", "b", "c"],
        "timestamp": pd.to_datetime(["2021-03-01 10:00", "2021-03-01 11:00", "2021-03-01 12:00"], utc=True),
        "currencies": ["INR", "USD", "EUR"],
        "polarity": [0.5, -0.2, 0.1], "confidence": [0.9, 0.8, 0.7],
    })
    pairs = ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF", "USD/INR"]
    old = build_pair_streams(events, pairs, "study")
    new = build_pair_streams(events, pairs, H6_SETTINGS["baseline_usd_policy"])
    assert old["USD/INR"].polarities == []                            # the defect
    assert new["USD/INR"].polarities == [0.5]
    for pair in pairs[:4]:
        assert new[pair] == old[pair]


def test_h6_stream_builders_run_on_dataframes():
    """Found with C41: both builders read an underscore column that pandas' itertuples renames
    (`_t` became `_6`), so H6 would have crashed before producing anything. Renamed `t_epoch`."""
    from research.semantic.currency_bias import build_currency_streams

    rows = pd.DataFrame({
        "event_id": ["b", "a"], "timestamp": pd.to_datetime(["2021-03-01 11:00", "2021-03-01 10:00"], utc=True),
        "headline": ["rupee slips", "dollar firm"], "news_currency": ["INR", "USD"],
        "sentiment": [-0.4, 0.3], "sentiment_confidence": [0.8, 0.9], "novelty": [0.5, float("nan")],
    })
    streams = build_currency_streams(rows, currencies=["USD", "INR"])
    assert streams["USD"].event_ids == ["a"] and streams["INR"].event_ids == ["b"]
    assert streams["USD"].timestamps == [pd.Timestamp("2021-03-01 10:00", tz="UTC").timestamp()]
    assert streams["USD"].novelties == [None]
