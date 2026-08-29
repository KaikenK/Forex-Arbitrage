"""Tests for the Option-A InstrumentNormalizer (docs/SPEC.md section 3)."""

from datetime import date, datetime, timezone

import pytest

from backend.core.normalization import (
    InstrumentNormalizer,
    month_end_expiry_estimate,
)

CARRY = 0.045
TSTAR = date(2026, 9, 29)          # NSE Sep-2026 near-month (estimate)
NOW = datetime(2026, 9, 1, 6, 0, tzinfo=timezone.utc)
NOW_TS = NOW.timestamp()


@pytest.fixture
def norm():
    return InstrumentNormalizer(target_expiry=TSTAR, carry_rate_annual=CARRY)


def test_future_at_target_expiry_is_unchanged(norm):
    fwd = norm.to_common_forward(
        86.40, 86.45, leg="onshore", instrument_kind="future",
        source_id="kite_usdinr_fut", quote_ts=NOW_TS, quote_expiry=TSTAR,
    )
    assert fwd.bid == pytest.approx(86.40)
    assert fwd.ask == pytest.approx(86.45)
    assert fwd.carry_adjustment_pips == pytest.approx(0.0, abs=1e-6)


def test_offshore_future_5_days_early_gets_positive_carry(norm):
    # CME Sep contract expiring 5 days before NSE T*
    t_off = date(2026, 9, 24)
    fwd = norm.to_common_forward(
        86.40, 86.44, leg="offshore", instrument_kind="future",
        source_id="cme_usdinr_fut", quote_ts=NOW_TS, quote_expiry=t_off,
    )
    # hand calc: factor = 1 + 0.045 * 5/365 ; adj = mid*(factor-1)/PIP
    mid = 86.42
    factor = 1 + CARRY * 5 / 365
    expected_adj = mid * (factor - 1) / 0.01
    assert fwd.carry_adjustment_pips == pytest.approx(expected_adj, rel=1e-6)
    assert 4.5 < fwd.carry_adjustment_pips < 6.5   # ~5.3 pips over 5 days at 4.5%
    assert fwd.mid > mid


def test_spot_carried_to_tstar(norm):
    fwd = norm.to_common_forward(
        86.30, 86.36, leg="otc", instrument_kind="spot",
        source_id="otc_usdinr_spot", quote_ts=NOW_TS, quote_time=NOW,
    )
    days = (TSTAR - NOW.date()).days           # 28
    factor = 1 + CARRY * days / 365
    assert fwd.mid == pytest.approx(86.33 * factor, rel=1e-9)
    assert fwd.carry_adjustment_pips > 25       # ~32 pips over 28 days


def test_basis_and_executable_basis(norm):
    on = norm.to_common_forward(86.500, 86.520, leg="onshore",
                                instrument_kind="future", source_id="kite",
                                quote_ts=NOW_TS, quote_expiry=TSTAR)
    off = norm.to_common_forward(86.460, 86.475, leg="offshore",
                                 instrument_kind="future", source_id="cme",
                                 quote_ts=NOW_TS, quote_expiry=TSTAR)
    # onshore mid 86.510, offshore mid 86.4675 -> +4.25 pips
    assert InstrumentNormalizer.basis_pips(on, off) == pytest.approx(4.25, abs=0.01)
    # buy offshore ask 86.475, sell onshore bid 86.500 -> +2.5 pips capturable
    assert InstrumentNormalizer.executable_basis_pips(off, on) == pytest.approx(2.5, abs=0.01)
    # the reverse direction is not a paper arb
    assert InstrumentNormalizer.executable_basis_pips(on, off) < 0


def test_reference_band(norm):
    # a realistic NSE future quotes near spot + the ~28-day forward premium
    fair_fwd = 86.90 * (1 + CARRY * (TSTAR - NOW.date()).days / 365)   # ~87.20
    fwd = norm.to_common_forward(fair_fwd - 0.01, fair_fwd + 0.01, leg="onshore",
                                 instrument_kind="future", source_id="kite",
                                 quote_ts=NOW_TS, quote_expiry=TSTAR)
    assert norm.within_reference_band(fwd, 86.90, band_pips=25.0, reference_time=NOW)
    # a reference 40 pips away is outside the band
    assert not norm.within_reference_band(fwd, 86.50, band_pips=25.0, reference_time=NOW)


def test_implied_spot_inverts_carry(norm):
    t_off = date(2026, 9, 24)
    # strip 5 days of carry from a raw offshore future ask
    implied = norm.to_implied_spot(86.44, instrument_kind="future", quote_expiry=t_off)
    assert implied < 86.44
    assert implied == pytest.approx(86.44 / (1 + CARRY * 5 / 365), rel=1e-9)


def test_bad_quote_rejected(norm):
    with pytest.raises(ValueError):
        norm.to_common_forward(86.5, 86.4, leg="otc", instrument_kind="spot",
                               source_id="x", quote_ts=NOW_TS, quote_time=NOW)


def test_future_without_expiry_rejected(norm):
    with pytest.raises(ValueError):
        norm.to_common_forward(86.4, 86.45, leg="onshore", instrument_kind="future",
                               source_id="x", quote_ts=NOW_TS)


def test_month_end_expiry_estimate():
    assert month_end_expiry_estimate(2026, 9) == date(2026, 9, 28)
    assert month_end_expiry_estimate(2026, 2) == date(2026, 2, 26)
