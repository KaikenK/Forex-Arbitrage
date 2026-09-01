"""Options-implied forward — put-call parity synthetic future (retail-arb mode)."""

from datetime import date, datetime, timezone

import pytest

from backend.core.normalization import (
    InstrumentNormalizer,
    discount_factor,
    implied_forward,
    implied_forward_from_chain,
)


def test_discount_factor():
    assert discount_factor(0) == 1.0
    assert discount_factor(365, 0.065) == pytest.approx(1 / 1.065)
    assert 0.99 < discount_factor(30, 0.065) < 1.0


def _consistent_option_pair(F, K, days, r, cspread=0.02):
    """Call/put mids consistent with forward F at strike K, plus a bid/ask."""
    df = discount_factor(days, r)
    cp = df * (F - K)                      # C - P
    c_mid, p_mid = 0.60, 0.60 - cp
    return (dict(bid=c_mid - cspread, ask=c_mid + cspread),
            dict(bid=p_mid - cspread, ask=p_mid + cspread))


def test_implied_forward_recovers_the_true_forward():
    F, K, days, r = 95.30, 95.00, 30, 0.065
    call, put = _consistent_option_pair(F, K, days, r)
    fb, fa = implied_forward(call["bid"], call["ask"], put["bid"], put["ask"], K,
                             days_to_expiry=days, rate_annual=r)
    assert fb < fa
    assert (fb + fa) / 2 == pytest.approx(F, abs=1e-6)   # mid recovers F


def test_implied_forward_rejects_crossed_quotes():
    with pytest.raises(ValueError):
        implied_forward(0.6, 0.5, 0.3, 0.32, 95.0, days_to_expiry=30)   # call ask < bid


def test_from_chain_picks_atm_and_averages():
    F, days, r = 95.30, 30, 0.065
    chain = {}
    for K in (94.75, 95.00, 95.25, 95.50, 96.00):
        c, p = _consistent_option_pair(F, K, days, r)
        chain[K] = {"call": c, "put": p}
    q = implied_forward_from_chain(chain, ref_price=F, days_to_expiry=days,
                                   rate_annual=r, n_strikes=3)
    assert q is not None
    assert q.strikes_used == (95.00, 95.25, 95.50)      # 3 nearest 95.30
    assert q.mid == pytest.approx(F, abs=1e-6)


def test_from_chain_returns_none_when_no_complete_quote():
    chain = {95.0: {"call": {"bid": 0.5}, "put": {}}}   # incomplete
    assert implied_forward_from_chain(chain, 95.0, days_to_expiry=30) is None


def test_synthetic_forward_vs_future_basis_through_normalizer():
    T = date(2026, 9, 28)
    norm = InstrumentNormalizer(target_expiry=T, carry_rate_annual=0.019)
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    days = (T - now.date()).days

    # options chain consistent with a synthetic forward of 95.34
    chain = {}
    for K in (95.25, 95.50):
        c, p = _consistent_option_pair(95.34, K, days, 0.065)
        chain[K] = {"call": c, "put": p}
    q = implied_forward_from_chain(chain, 95.34, days_to_expiry=days, rate_annual=0.065)

    syn = norm.to_common_forward(q.bid, q.ask, leg="onshore_opt",
                                 instrument_kind="options_forward", source_id="nse_opt",
                                 quote_ts=now.timestamp(), quote_expiry=T)
    fut = norm.to_common_forward(95.30, 95.31, leg="onshore_fut",
                                 instrument_kind="future", source_id="nse_fut",
                                 quote_ts=now.timestamp(), quote_expiry=T)

    assert syn.comparison_basis == "options_implied"
    assert fut.comparison_basis == "futures"
    basis = InstrumentNormalizer.basis_pips(syn, fut)     # synthetic minus future
    assert basis == pytest.approx((95.34 - 95.305) / 0.01, abs=0.5)   # ~3.5 pips rich
