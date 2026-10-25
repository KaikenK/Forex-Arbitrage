"""H6 locked protocol: formula, no look-ahead, refusals, synthetic power. No network, no real data."""
from __future__ import annotations

import hashlib
import math
import os
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.data import tagging
from research.semantic import currency_bias as cb
from research.semantic import currency_bias_core as core

T0 = datetime(2019, 3, 6, 21, tzinfo=UTC).timestamp()
SETTINGS = {
    "event_window_hours": 24.0, "no_data_value": "raw_zero", "novelty_missing": "drop_headline",
    "relevance_unmatched": "drop_headline", "zscore_scheme": "expanding_causal", "zscore_min_history": 20,
    "baseline_usd_policy": "none", "intraday_entry_exit_lag_minutes": (2, 5),
    "usdinr_target_bar": "skip_one_bar", "ic_of": "signal", "ic_inference": "date_clustered_rank_regression",
    "missing_window_rule": "drop_signal_time",
}


# ---------------------------------------------------------------- open clauses / refusals

OPEN_H6 = {name: None for name in cb.UNRESOLVED}
OPEN_H6B = {name: None for name in cb.H6B_UNRESOLVED}


def test_an_undecided_clause_blocks_the_run():
    with pytest.raises(cb.ProtocolUnresolved) as error:
        cb.require_resolved(OPEN_H6)
    for name in cb.UNRESOLVED:
        assert name in str(error.value)
    cb.require_resolved(SETTINGS)


def test_open_clauses_hold_the_logged_clarifications():
    """C6-C19, RESEARCH_TRACKER §6 2026-10-25. A change here needs a new §6 entry."""
    assert cb.UNRESOLVED == {
        "event_window_hours": 24.0, "no_data_value": "z_zero", "novelty_missing": "drop_headline",
        "relevance_unmatched": "indirect", "zscore_scheme": "training_months", "zscore_min_history": 30,
        "baseline_usd_policy": "study", "intraday_entry_exit_lag_minutes": (2, 5),
        "usdinr_target_bar": "skip_one_bar", "ic_of": "model_prediction",
        "ic_inference": "date_clustered_rank_regression", "missing_window_rule": "drop_signal_time",
    }
    assert cb.H6B_UNRESOLVED == {"model_nesting": "cumulative", "one_sided_next_quote": "drop"}
    cb.require_resolved(cb.UNRESOLVED)
    cb.require_resolved(cb.H6B_UNRESOLVED)


def test_run_scripts_refuse_without_decisions(monkeypatch):
    import research.run_h6 as run_h6
    import research.run_h6b as run_h6b

    monkeypatch.setattr(run_h6, "UNRESOLVED", OPEN_H6)
    monkeypatch.setattr(run_h6b, "H6B_UNRESOLVED", OPEN_H6B)
    with pytest.raises(SystemExit, match="locked protocol"):
        run_h6.main()
    monkeypatch.setattr("sys.argv", ["run_h6b", "--recordings", "."])
    with pytest.raises(SystemExit, match="locked protocol"):
        run_h6b.main()


def test_run_h6_refuses_without_the_freeze(monkeypatch, tmp_path):
    import research.run_h6 as run_h6
    from research.freeze_gdelt_sample import FreezeError

    monkeypatch.setattr(run_h6, "UNRESOLVED", SETTINGS)
    monkeypatch.setattr("research.freeze_gdelt_sample.FREEZE_PATH", tmp_path / "none.json")
    monkeypatch.setattr(run_h6, "load_freeze", lambda: (_ for _ in ()).throw(FreezeError("no frozen sample")))
    with pytest.raises(FreezeError):
        run_h6.main()


# ---------------------------------------------------------------- formula

def _stream(ages_min, polarity=1.0, confidence=1.0, novelty=1.0, relevance=1.0):
    times = sorted(T0 - a * 60 for a in ages_min)
    n = len(times)
    return times, [polarity] * n, [confidence] * n, [novelty] * n, [relevance] * n


def _news(times, pol, conf, nov, rel, **kw):
    return core.news_component(times, pol, conf, nov, rel, T0, novelty_missing=kw.get("nm", "drop_headline"),
                               relevance_unmatched=kw.get("ru", "drop_headline"))


def test_weight_is_exactly_the_locked_product():
    assert core.headline_weight(12.0, 0.8, 0.5, 0.5) == pytest.approx(math.exp(-1) * 0.8 * 0.5 * 0.5)
    times = [T0 - 6 * 3600, T0 - 18 * 3600]
    comp = _news(times, [1.0, -1.0], [0.9, 0.6], [0.4, 0.7], [1.0, 0.5])
    w = [math.exp(-18 / 12) * 0.6 * 0.7 * 0.5, math.exp(-6 / 12) * 0.9 * 0.4 * 1.0]
    assert comp.value == pytest.approx((w[1] - w[0]) / (w[0] + w[1]))


def test_t_minus_15_minute_rule_and_24h_window():
    inside = _news(*_stream([15]))
    assert inside.n_headlines == 1, "a story exactly 15 min old is usable"
    assert _news(*_stream([14.9])).value is None, "a story < 15 min old is not yet usable"
    assert _news(*_stream([24 * 60 - 1])).n_headlines == 1
    assert _news(*_stream([24 * 60])).value is None, "the window is (t-24h, t-15m]"


def test_novelty_and_relevance_open_options():
    times, pol, conf, _, rel = _stream([60, 120])
    nov = [None, 0.5]
    assert _news(times, pol, conf, nov, rel, nm="drop_headline").n_headlines == 1
    assert _news(times, pol, conf, nov, rel, nm="as_novel").n_headlines == 2
    rel = [None, 1.0]
    assert _news(times, pol, conf, [1.0, 1.0], rel, ru="drop_headline").n_headlines == 1
    assert _news(times, pol, conf, [1.0, 1.0], rel, ru="indirect").n_headlines == 2


def test_relevance_explicit_name_vs_institution_term():
    assert core.relevance("Yen slides as BoJ holds rates", "JPY") == 1.0
    assert core.relevance("BoJ holds rates, signals patience", "JPY") == 0.5
    assert core.relevance("Fed's Powell flags more hikes", "USD") == 0.5
    assert core.relevance("Dollar weakens after jobs data", "USD") == 1.0
    assert core.relevance("USD firms on yields", "USD") == 1.0
    assert core.relevance("Eurozone inflation cools", "EUR") == 0.5
    assert core.relevance("Rupee hits record low", "INR") == 1.0
    assert core.relevance("Oil prices climb", "USD") is None


def test_core_attribution_tables_mirror_tagging():
    assert core.STRONG_CURRENCY_HINTS == tagging.STRONG_CURRENCY_HINTS
    assert core.WEAK_CURRENCY_HINTS == tagging.WEAK_CURRENCY_HINTS
    assert core.ISO_CODES == tagging.ISO_CODES
    assert core.FX_CONTEXT_PATTERNS == tagging.FX_CONTEXT_PATTERNS
    assert core.FALSE_FRIEND_PATTERNS == tagging.FALSE_FRIEND_PATTERNS
    assert core.EXPLICIT_CURRENCY_TERMS <= set(tagging.CURRENCY_HINTS)
    corpus = ["Rupee hits record low as RBI intervenes", "Dollar weakens; euro gains on ECB talk",
              "Five million euro lottery jackpot", "USD/JPY climbs as yen slips", "Swiss franc rallies",
              "Fed holds rates, sterling slides", "Bank of Canada cuts", "dollar amount donated"]
    for text in corpus:
        assert sorted(core.tag_currencies(text)) == sorted(tagging.tag_currencies(text)), text


def test_event_component_window_decay_and_inclusive_t():
    times = [T0 - 30 * 3600, T0 - 12 * 3600, T0]
    values = [5.0, 1.0, -1.0]
    e = core.event_component(times, values, T0, window_hours=24)
    w = [math.exp(-1), 1.0]
    assert e == pytest.approx((w[0] * 1.0 + w[1] * -1.0) / sum(w)), "release at t counts; 30 h old does not"
    assert core.event_component(times, values, T0 - 31 * 3600, window_hours=24) is None


def test_pair_bias_is_base_minus_quote():
    z = pd.DataFrame({"zB_USD": [1.0], "zE_USD": [0.5], "zB_EUR": [-1.0], "zE_EUR": [0.0],
                      "zB_JPY": [0.0], "zE_JPY": [0.0]})
    features = cb.pair_features(z, ["EUR/USD", "USD/JPY"])
    # USD bullish + EUR bearish => EUR/USD bearish, USD/JPY bullish.
    assert features["EUR/USD"]["pair_bias"].iloc[0] == pytest.approx(-2.5)
    assert features["USD/JPY"]["pair_bias"].iloc[0] == pytest.approx(1.5)
    assert features["EUR/USD"]["news_only"].iloc[0] == pytest.approx(-2.0)
    assert features["EUR/USD"]["event_only"].iloc[0] == pytest.approx(-0.5)
    assert core.pair_bias(core.currency_bias(-1.0, 0.0), core.currency_bias(1.0, 0.5)) == pytest.approx(-2.5)


# ---------------------------------------------------------------- standardization: no look-ahead

def test_expanding_z_never_uses_the_future():
    rng = np.random.default_rng(1)
    raw = pd.Series(rng.normal(size=200))
    z = cb.zscore_expanding(raw, min_history=20, no_data_value="raw_zero")
    changed = raw.copy()
    changed.iloc[150:] += 100.0
    z2 = cb.zscore_expanding(changed, min_history=20, no_data_value="raw_zero")
    pd.testing.assert_series_equal(z.iloc[:150], z2.iloc[:150])
    assert z.iloc[:20].isna().all()
    assert z.iloc[60] == pytest.approx((raw.iloc[60] - raw.iloc[:60].mean()) / raw.iloc[:60].std(ddof=1))


def test_training_mask_z_uses_training_rows_only():
    raw = pd.Series([1.0, 2.0, 3.0, 100.0])
    z = cb.zscore_from_mask(raw, np.array([True, True, True, False]), no_data_value="drop_row")
    assert z.iloc[0] == pytest.approx((1 - 2) / 1.0)
    assert z.iloc[3] == pytest.approx(98.0)


def test_no_data_value_options():
    raw = pd.Series([1.0, np.nan, 3.0, 2.0, np.nan] * 10)
    for mode in ("raw_zero", "z_zero", "drop_row"):
        z = cb.zscore_expanding(raw, min_history=3, no_data_value=mode)
        missing = raw.isna() & (np.arange(len(raw)) > 10)
        if mode == "z_zero":
            assert (z[missing] == 0.0).all()
        elif mode == "drop_row":
            assert z[missing].isna().all()
        else:
            assert z[missing].notna().all() and (z[missing] != 0).all()


# ---------------------------------------------------------------- targets and sample rules

def test_forward_returns_fail_closed_on_lags():
    bars = np.array([T0 + 60, T0 + 3600 * 24 + 120], dtype=float)
    prices = np.array([1.0, 1.01])
    ok = cb.forward_log_returns(bars, prices, np.array([T0]), horizon_minutes=1440, entry_lag_minutes=2, exit_lag_minutes=5)
    assert ok[0] == pytest.approx(math.log(1.01))
    late = cb.forward_log_returns(bars, prices, np.array([T0]), horizon_minutes=1440, entry_lag_minutes=0.5, exit_lag_minutes=5)
    assert np.isnan(late[0])
    gap = cb.forward_log_returns(bars, prices, np.array([T0]), horizon_minutes=1440, entry_lag_minutes=2, exit_lag_minutes=1)
    assert np.isnan(gap[0])


def test_usdinr_target_bar_modes():
    closes = pd.Series([1.0, 2.0, 4.0], index=[date(2019, 3, 6), date(2019, 3, 7), date(2019, 3, 8)])
    t = np.array([T0])
    assert cb.usdinr_daily_returns(closes, t, "next_bar")[0] == pytest.approx(math.log(2.0))
    assert cb.usdinr_daily_returns(closes, t, "skip_one_bar")[0] == pytest.approx(math.log(2.0))
    assert np.isnan(cb.usdinr_daily_returns(closes.iloc[:2], t, "skip_one_bar")[0])


def test_missing_window_rule_drops_touching_signal_times():
    times = cb.signal_times(date(2019, 3, 4), date(2019, 3, 8), hourly=False)
    kept = cb.apply_missing_window_rule(times, ["2019-03-06"], "drop_signal_time")
    days = {datetime.fromtimestamp(t, tz=UTC).date() for t in kept}
    # Window (t-24h, t-15m]: signals of the 5th (touches 6th? no) ... 6th and 7th touch the 6th.
    assert date(2019, 3, 6) not in days and date(2019, 3, 7) not in days
    assert date(2019, 3, 5) in days and date(2019, 3, 8) in days
    assert len(cb.apply_missing_window_rule(times, ["2019-03-06"], "treat_as_no_news")) == len(times)


# ---------------------------------------------------------------- synthetic power

def _synthetic(signal_strength: float, scheme: str, seed: int = 7):
    rng = np.random.default_rng(seed)
    times = cb.signal_times(date(2018, 1, 1), date(2019, 12, 31), hourly=False)
    raw = pd.DataFrame({f"{k}_{c}": rng.normal(size=len(times)) for c in ("USD", "EUR", "JPY") for k in ("B", "E")},
                       index=pd.Index(times, name="time_s"))
    settings = SETTINGS | {"zscore_scheme": scheme}
    z = cb.standardized_frame(raw, settings) if scheme == "expanding_causal" else cb.standardized_frame(raw, settings)
    features = cb.pair_features(z, ["EUR/USD", "USD/JPY"])
    targets = {}
    for pair in ("EUR/USD", "USD/JPY"):
        bias = features[pair]["pair_bias"].fillna(0.0).to_numpy()
        targets[pair] = (signal_strength * bias + rng.normal(scale=1.0, size=len(times))) * 1e-4
    baseline = pd.DataFrame({p: rng.normal(size=len(times)) for p in ("EUR/USD", "USD/JPY")}, index=raw.index)
    panel = cb.assemble_panel(times, targets, baseline, settings)
    return panel, raw, settings


@pytest.mark.parametrize("scheme", ["expanding_causal", "training_months"])
def test_h6_detects_a_real_currency_signal(scheme):
    panel, raw, settings = _synthetic(0.5, scheme)
    result = cb.h6_test(panel, raw, settings, pairs=["EUR/USD", "USD/JPY"])
    assert result["criterion_i"]["passes"] and result["criterion_ii"]["passes"] and result["passes"]
    assert result["criterion_ii"]["ic"] > 0 and result["criterion_ii"]["ic_p"] < 0.05
    assert result["criterion_ii"]["n_months"] == 12, "24 months minus 12 training-only months"


@pytest.mark.parametrize("scheme", ["expanding_causal", "training_months"])
def test_h6_rarely_passes_without_a_signal(scheme):
    passes = sum(cb.h6_test(*_synthetic(0.0, scheme, seed=s)[:2], _synthetic(0.0, scheme, seed=s)[2],
                            pairs=["EUR/USD", "USD/JPY"])["passes"] for s in range(10))
    assert passes <= 2


def test_ic_inference_methods_and_horizon_rule():
    rng = np.random.default_rng(3)
    x = rng.normal(size=600)
    y = 0.3 * x + rng.normal(size=600)
    dates = np.repeat(np.arange(300).astype(str), 2)
    months = np.repeat(np.arange(30).astype(str), 20)
    for method in ("date_clustered_rank_regression", "monthly_ic_ttest"):
        ic, p = cb.ic_test(x, y, dates, months, method)
        assert ic > 0.2 and p < 0.01
    out = cb.horizon_output({60: (0.05, 0.001), 240: (0.04, 0.01), 1440: (0.02, 0.6)})
    assert out["horizon_minutes"] == 240
    assert cb.horizon_output({1440: (-0.1, 0.001)})["label"] == "none established"


# ---------------------------------------------------------------- H6b

def test_h6b_leg_parsing_outcome_and_auc():
    from research import run_h6b as h

    assert h.leg_key("spot_kraken_eurusd") == ("kraken", "KRAKEN", "EURUSD")
    assert h.leg_key("upstox_nse_usdinr") == ("upstox", "NSE", "USDINR")
    assert h.leg_key("replay_spot_truefx_usdjpy") == ("truefx", "TRUEFX", "USDJPY")
    quotes = {("kraken", "KRAKEN", "EURUSD"): ([1000, 2000], [{"bid": 1.1, "ask": 1.1001}, {"bid": 1.1, "ask": 1.1001}]),
              ("truefx", "TRUEFX", "EURUSD"): ([1500], [{"bid": 1.1003, "ask": 1.1004}])}
    opp = {"timestamp_ms": 1200, "buy_source": "spot_kraken_eurusd", "sell_source": "spot_truefx_eurusd"}
    out = h.next_poll_outcome(opp, quotes, one_sided="drop")
    assert out["still_crossed"] == 1 and out["edge_pips"] == pytest.approx(2.0)
    quotes[("truefx", "TRUEFX", "EURUSD")] = ([1500], [{"bid": None, "ask": 1.1004}])
    assert h.next_poll_outcome(opp, quotes, one_sided="drop") is None
    assert h.next_poll_outcome(opp, quotes, one_sided="not_crossed")["still_crossed"] == 0
    assert h.auc(np.array([0.1, 0.4, 0.35, 0.8]), np.array([0, 0, 1, 1])) == pytest.approx(0.75)


def test_h6b_bootstrap_is_deterministic_and_nested():
    from research import run_h6b as h

    rows = [{"day": f"d{i % 6}", "confidence_score": 0.5 + 0.01 * i, "risk_penalty": 0.02 * (i % 3),
             "direction_penalty": 0.01 * (i % 2), "still_crossed": int(i % 3 != 0), "edge_pips": 1.0} for i in range(60)]
    scores = h.model_scores(rows, "cumulative")
    assert set(scores) == {"detector", "plus_risk", "plus_risk_direction"}
    assert set(h.model_scores(rows, "separate")) == {"detector", "plus_risk", "plus_direction"}
    a = h.day_bootstrap(rows, scores, draws=200)
    b = h.day_bootstrap(rows, scores, draws=200)
    assert a == b and a["n_days"] == 6


# ---------------------------------------------------------------- one implementation

def _service_core() -> Path | None:
    root = os.environ.get("ARBEX_SEMANTIC_ROOT")
    candidates = [Path(root)] if root else []
    candidates.append(Path(__file__).resolve().parents[3] / "Forex-Arbitrage-semantic")
    for base in candidates:
        path = base / "services" / "semantic" / "src" / "market" / "currency_bias_core.py"
        if path.exists():
            return path
    return None


def test_service_copy_of_the_formula_is_byte_identical():
    service = _service_core()
    if service is None:
        pytest.skip("semantic service tree not found next to this checkout")
    research = Path(core.__file__)
    assert hashlib.sha256(service.read_bytes()).hexdigest() == hashlib.sha256(research.read_bytes()).hexdigest()
