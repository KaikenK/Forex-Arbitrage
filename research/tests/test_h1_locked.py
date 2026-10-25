"""H1 under the locked protocol (§6, 2026-10-25): target, exclusions, controls, pass rule. No network."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from research.eventstudy.alignment import align_event
from research.eventstudy.schema import Event, PriceSeries
from research.features import compute_event_semantics
from research.run_h1 import apply_exclusions, build_records
from research.semantic.currency_index import surprise_index
from research.semantic.embeddings import HashingEmbedder
from research.semantic.h1 import (
    MARKET_WRAP_FX_TERMS,
    MARKET_WRAP_MOVE_TERMS,
    PRIMARY_EMBEDDER,
    UNRESOLVED,
    H1Result,
    ProtocolUnresolved,
    event_targets,
    h1_decision,
    h1_locked_test,
    hours_for_events,
    market_wrap_mask,
    prior_window_control,
    release_window_mask,
    require_resolved,
    window_abs_log_returns,
)
from research.semantic.sentiment import LexiconSentiment
from research.semantic.walkforward import monthly_expanding_comparison

T = datetime(2019, 3, 6, 12, 0, tzinfo=UTC)          # a Wednesday


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


# ---------------------------------------------------------------- open clauses

def test_h1_refuses_to_run_until_every_open_clause_is_decided():
    open_clauses = {name: None for name in UNRESOLVED}
    with pytest.raises(ProtocolUnresolved) as error:
        require_resolved(open_clauses)
    for name in UNRESOLVED:
        assert name in str(error.value)
    require_resolved({name: 1 for name in UNRESOLVED})


def test_open_clauses_hold_the_logged_clarifications():
    """C1-C5, RESEARCH_TRACKER §6 2026-10-25. A change here needs a new §6 entry."""
    assert UNRESOLVED == {
        "market_wrap_word_boundaries": True,
        "gdelt_usd_policy": "study",
        "control_day_rule": "weekdays",
        "control_min_valid_days": 10,
        "walk_forward_min_train_months": 12,
    }
    require_resolved(UNRESOLVED)


# ---------------------------------------------------------------- exclusion (2)

def test_market_wrap_patterns_quote_the_locked_text():
    assert MARKET_WRAP_FX_TERMS.startswith("dollar|greenback|euro|yen|sterling|pound|franc|rupee|forex|fx|")
    assert "(hit|hits|touch(es)?) .{0,20}(high|low)" in MARKET_WRAP_MOVE_TERMS
    assert MARKET_WRAP_MOVE_TERMS.endswith("(week|month|year)-?(high|low)")


WRAP_SAMPLES = [
    ("Dollar rises after strong payrolls", True, True),
    ("EUR/USD hits two-week high on ECB bets", True, True),
    ("USDJPY slides as yields fall", True, True),
    ("Sterling steadies ahead of BoE decision", True, True),
    ("Yen edges higher in quiet trade", True, True),
    ("Fed holds rates, signals patience", False, False),
    ("ECB release raises euro outlook", True, False),        # "ease" inside "release"
    ("Europe stocks gain against expectations", True, False),  # "euro" in "Europe" + "gain"
    ("France consumer confidence rises", True, False),        # "franc" inside "France"
    ("India rupee outlook ahead of RBI meeting", False, False),
]


@pytest.mark.parametrize("boundaries", [True, False])
def test_market_wrap_mask_both_readings(boundaries):
    expected = [literal if not boundaries else bounded for _, literal, bounded in WRAP_SAMPLES]
    got = market_wrap_mask([h for h, _, _ in WRAP_SAMPLES], word_boundaries=boundaries)
    assert list(got) == expected


# ---------------------------------------------------------------- exclusion (1)

def _calendar(rows):
    return pd.DataFrame(rows, columns=["timestamp", "currencies", "importance", "timestamp_precision_minutes"])


def test_release_window_is_inclusive_and_per_pair_currency():
    calendar = _calendar([
        (T - timedelta(minutes=45), "USD", "high", 0.0),    # edge: included
        (T + timedelta(minutes=91), "EUR", "high", 0.0),    # outside
        (T + timedelta(minutes=10), "GBP", "high", 0.0),    # GBP: only GBP pairs
        (T + timedelta(minutes=5), "JPY", "medium", 0.0),   # not high impact
        (T + timedelta(minutes=5), "CHF", "high", 1440.0),  # no exact time
    ])
    events = pd.DataFrame({"timestamp": [T, T, T, T], "pair": ["EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF"]})
    assert list(release_window_mask(events, calendar)) == [True, True, True, True]  # all have USD
    no_usd = calendar[calendar["currencies"] != "USD"]
    assert list(release_window_mask(events, no_usd)) == [False, True, False, False]
    plus_90 = _calendar([(T + timedelta(minutes=90), "EUR", "high", 0.0)])
    assert list(release_window_mask(events.iloc[:1], plus_90)) == [True]


def test_exclusion_counts_on_synthetic_data():
    """The counts the report quotes: each exclusion, their overlap, and what is left."""
    events = pd.DataFrame([
        {"event_id": "a", "timestamp": T, "headline": "Dollar rises after strong payrolls", "currencies": "USD;EUR"},
        {"event_id": "b", "timestamp": T + timedelta(hours=5), "headline": "ECB release raises euro outlook", "currencies": "EUR"},
        {"event_id": "c", "timestamp": T + timedelta(hours=9), "headline": "Bank of Japan holds policy", "currencies": "JPY"},
        {"event_id": "d", "timestamp": T + timedelta(hours=9, minutes=30), "headline": "Yen edges higher in quiet trade", "currencies": "JPY"},
        {"event_id": "e", "timestamp": T + timedelta(days=1), "headline": "SNB keeps franc policy", "currencies": "CHF"},
        {"event_id": "f", "timestamp": T + timedelta(days=1), "headline": "Fed minutes show caution", "currencies": "USD"},
    ])
    calendar = _calendar([(T + timedelta(minutes=30), "USD", "high", 0.0),
                          (T + timedelta(hours=9, minutes=20), "JPY", "high", 0.0)])
    canonical = {e: 1 for e in events["event_id"]}
    counts = {}
    for policy in ("none", "study"):
        records = build_records(events, canonical, {"EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF"}, policy)
        for boundaries in (True, False):
            _, counts[(policy, boundaries)] = apply_exclusions(records, calendar, word_boundaries=boundaries)
    # policy "none": a -> EUR/USD, b -> EUR/USD, c, d -> USD/JPY, e -> USD/CHF; f has no pair.
    none_wb = counts[("none", True)]
    assert none_wb["rows_before"] == 5
    assert none_wb["dropped_release_window"] == 3          # a (USD 12:30), c and d (JPY 21:20)
    assert none_wb["dropped_market_wrap"] == 2             # a, d
    assert none_wb["dropped_both"] == 2 and none_wb["rows_after"] == 2
    assert counts[("none", False)]["dropped_market_wrap"] == 3, "literal reading also drops b"
    # policy "study": USD-tagged a and f fan out to all four majors.
    assert counts[("study", True)]["rows_before"] == 5 + 3 + 4
    print("synthetic exclusion counts:", counts)


# ---------------------------------------------------------------- target

def _bars(start: datetime, n_minutes: int, *, gap: tuple[int, int] | None = None, drift: float = 1e-5):
    times, prices = [], []
    for k in range(n_minutes):
        if gap and gap[0] <= k < gap[1]:
            continue
        times.append(start + timedelta(minutes=k))
        prices.append(1.10 * (1 + drift) ** k)
    return times, prices


def _arrays(times, prices):
    return np.array([_epoch(t) for t in times], dtype=np.int64), np.asarray(prices, dtype=float)


def test_target_spans_t_minus_15_to_t_plus_60_measured_from_the_timestamp():
    times, prices = _bars(T - timedelta(minutes=30), 120)
    bt, px = _arrays(times, prices)
    got = event_targets(bt, px, np.array([_epoch(T)]))[0]
    entry = prices[times.index(T - timedelta(minutes=15))]
    exit_ = prices[times.index(T + timedelta(minutes=60))]
    assert got == pytest.approx(abs(np.log(exit_ / entry)))


def test_entry_and_exit_lags_fail_closed():
    """Window [0, 75] minutes. Remove bars just after each edge: lag 2 / 5 pass, 3 / 6 fail."""
    start, end = 0, 75 * 60
    for missing, ok in [(range(0, 2), True), (range(0, 3), False),
                        (range(75, 80), True), (range(75, 81), False)]:
        minutes = [m for m in range(0, 120) if m not in missing]
        bt = np.array([m * 60 for m in minutes], dtype=np.int64)
        px = 1.1 * np.exp(1e-5 * np.array(minutes, dtype=float))
        value = window_abs_log_returns(bt, px, np.array([start]), np.array([end]))[0]
        assert np.isfinite(value) == ok, list(missing)


def test_exit_targets_t_plus_60_not_entry_plus_75():
    """With a 2-minute entry lag the exit is still the first bar at/after t+60."""
    times, prices = _bars(T - timedelta(minutes=13), 120)          # first bar 2 min late
    bt, px = _arrays(times, prices)
    got = event_targets(bt, px, np.array([_epoch(T)]))[0]
    expected = abs(np.log(prices[times.index(T + timedelta(minutes=60))] / prices[0]))
    assert got == pytest.approx(expected)
    via_align = align_event(Event(event_id="x", timestamp=T - timedelta(minutes=15), pair="EUR/USD"),
                            PriceSeries(pair="EUR/USD", times=times, prices=prices), [75],
                            max_entry_lag_minutes=2, max_exit_lag_minutes=5)
    assert abs(np.log1p(via_align.horizon_returns[75])) != pytest.approx(expected), \
        "align_event measures from the entry bar, which the locked target does not"


# ---------------------------------------------------------------- control

def _flat_days(days_back: int, per_day_move: float):
    """Bars around 11:45-13:05 UTC on each of the previous `days_back` calendar days."""
    times, prices = [], []
    for d in range(days_back, -1, -1):
        day = T - timedelta(days=d)
        for k in range(0, 85):
            times.append(day - timedelta(minutes=15) + timedelta(minutes=k))
            prices.append(1.0 * np.exp(per_day_move * k / 75))
    return _arrays(times, prices)


def test_control_averages_the_same_clock_window_on_previous_weekdays():
    bt, px = _flat_days(40, 0.002)
    for rule in ("weekdays", "valid_days"):
        control = prior_window_control(bt, px, np.array([_epoch(T)]), rule=rule, min_valid_days=1)[0]
        assert control == pytest.approx(0.002, rel=1e-3)


def test_control_skips_weekends_and_respects_the_minimum():
    bt, px = _flat_days(3, 0.002)              # only Sun, Mon, Tue before Wed T have data
    weekdays = prior_window_control(bt, px, np.array([_epoch(T)]), rule="weekdays", min_valid_days=2)[0]
    assert weekdays == pytest.approx(0.002, rel=1e-3), "Mon + Tue valid"
    assert np.isnan(prior_window_control(bt, px, np.array([_epoch(T)]), rule="weekdays", min_valid_days=3)[0])
    with pytest.raises(ValueError):
        prior_window_control(bt, px, np.array([_epoch(T)]), rule="calendar", min_valid_days=1)


def test_hours_cover_target_and_control_windows():
    hours = hours_for_events(np.array([_epoch(T)]), trading_days=2)
    assert datetime(2019, 3, 6, 11, tzinfo=UTC) in hours and datetime(2019, 3, 6, 13, tzinfo=UTC) in hours
    assert datetime(2019, 3, 5, 11, tzinfo=UTC) in hours and datetime(2019, 3, 4, 13, tzinfo=UTC) in hours


# ---------------------------------------------------------------- pass rule

def _frame(n_events: int, effect: float, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    novelty = rng.uniform(0.2, 0.9, n_events)
    sentiment = rng.uniform(0, 1, n_events)
    control = rng.uniform(3, 8, n_events)
    stamps = [datetime(2018, 1, 1, tzinfo=UTC) + timedelta(hours=37 * i) for i in range(n_events)]
    return pd.DataFrame({
        "event_id": [f"e{i}" for i in range(n_events)], "timestamp": stamps,
        "target": 2 + control + 2 * sentiment + effect * novelty + rng.normal(0, 3, n_events),
        "control_prior20": control, "abs_sentiment": sentiment, "novelty": novelty,
    })


def _test(frame, backend=PRIMARY_EMBEDDER):
    return h1_locked_test(frame, backend=backend, target="target", controls=["control_prior20"],
                          baseline=["abs_sentiment"], min_train_months=3)


def test_monthly_walk_forward_never_trains_on_the_test_month():
    frame = _frame(400, 12.0)
    frame["_month"] = pd.to_datetime(frame["timestamp"]).dt.strftime("%Y-%m")
    walk = monthly_expanding_comparison(frame, target="target", features_a=["control_prior20"],
                                        features_b=["control_prior20", "novelty"], month_col="_month",
                                        min_train_months=3)
    assert walk.n_months == frame["_month"].nunique() - 3
    assert walk.mse_b < walk.mse_a
    with pytest.raises(ValueError):
        monthly_expanding_comparison(frame, target="target", features_a=[], features_b=["novelty"],
                                     month_col="_month", min_train_months=0)


def test_supported_requires_positive_significant_coefficient_and_lower_oos_mse():
    result = _test(_frame(600, 12.0))
    assert result.verdict == "supported" and result.novelty_beta > 0 and result.novelty_p < 0.05
    assert result.walk_forward.mse_b < result.walk_forward.mse_a


def test_a_significant_negative_novelty_effect_is_not_support():
    result = _test(_frame(600, -12.0))
    assert result.novelty_p < 0.05 and result.novelty_beta < 0
    assert result.verdict == "not_supported"


def test_null_novelty_is_rarely_supported():
    verdicts = [_test(_frame(500, 0.0, seed=s)).verdict for s in range(20)]
    assert verdicts.count("supported") <= 2


def test_underpowered_below_30_events_after_exclusions():
    assert _test(_frame(29, 12.0)).verdict == "underpowered"


def test_only_the_primary_embedder_decides():
    def r(backend, verdict):
        return H1Result(backend, 100, 100, 50, None, None, None, None, verdict, backend == PRIMARY_EMBEDDER)

    assert h1_decision({"tfidf_svd": r("tfidf_svd", "supported")}) == "incomplete"
    assert h1_decision({"tfidf_svd": r("tfidf_svd", "supported"),
                        PRIMARY_EMBEDDER: r(PRIMARY_EMBEDDER, "not_supported")}) == "not_supported"
    assert not _test(_frame(200, 12.0), backend="hashing").decision_weight


# ---------------------------------------------------------------- shared unit, H5 guard

def test_external_canonical_set_fixes_the_unit_across_embedders():
    rows = [{"event_id": f"n{i}", "timestamp": T + timedelta(hours=i), "domain": "x", "currencies": "EUR",
             "headline": f"euro story {i} on trade talks {i * 3}"} for i in range(8)]
    canonical = {f"n{i}": 1 for i in range(0, 8, 2)}
    semantics = compute_event_semantics(pd.DataFrame(rows), embedder_factory=HashingEmbedder,
                                        scorer=LexiconSentiment(), currencies=["EUR"], canonical=canonical)
    assert set(semantics["event_id"]) == set(canonical)


def test_pandemic_robustness_refuses_a_calendar_without_the_flag():
    calendar = pd.DataFrame([{"timestamp": T, "country": "United States", "event_name": "CPI m/m",
                              "importance": "high", "standardized_surprise": 1.0,
                              "timestamp_precision_minutes": 0.0}])
    surprise_index(calendar, [date(2019, 3, 6)])
    with pytest.raises(KeyError):
        surprise_index(calendar, [date(2019, 3, 6)], exclude_pandemic_window=True)
