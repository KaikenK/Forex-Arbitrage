"""Tests for exchange-contract selection (near-month + NSE/BSE common expiry)."""

from datetime import date

import pytest

from backend.core.data_sources.exchange_contracts import (
    FutureContract,
    NoCommonExpiryError,
    pick_near_month,
    select_common_expiry,
)


def _c(exchange, expiry, key=None, weekly=False):
    return FutureContract(
        exchange=exchange,
        instrument_key=key or f"{exchange}|{expiry.isoformat()}",
        trading_symbol=f"USDINR FUT {expiry:%d %b %y}",
        expiry=expiry,
        lot_size=1,
        contract_size=1000.0,
        weekly=weekly,
    )


AS_OF = date(2026, 10, 9)


def test_near_month_skips_expired_and_too_close():
    contracts = [
        _c("NSE", date(2026, 10, 8)),   # expired
        _c("NSE", date(2026, 10, 10)),  # 1 day left -> too close (min 2)
        _c("NSE", date(2026, 10, 28)),
        _c("NSE", date(2026, 11, 26)),
    ]
    near = pick_near_month(contracts, as_of=AS_OF)
    assert near.expiry == date(2026, 10, 28)


def test_near_month_prefers_monthly_over_weekly():
    contracts = [
        _c("NSE", date(2026, 10, 16), weekly=True),
        _c("NSE", date(2026, 10, 28)),
    ]
    assert pick_near_month(contracts, as_of=AS_OF).expiry == date(2026, 10, 28)


def test_near_month_raises_when_nothing_live():
    with pytest.raises(LookupError):
        pick_near_month([_c("NSE", date(2026, 9, 28))], as_of=AS_OF)


def test_common_expiry_picks_nearest_shared_monthly():
    nse = [_c("NSE", date(2026, 10, 28)), _c("NSE", date(2026, 11, 26))]
    bse = [_c("BSE", date(2026, 10, 28)), _c("BSE", date(2026, 11, 26))]
    picked = select_common_expiry({"NSE": nse, "BSE": bse}, as_of=AS_OF)
    assert {c.exchange for c in picked.values()} == {"NSE", "BSE"}
    assert {c.expiry for c in picked.values()} == {date(2026, 10, 28)}


def test_common_expiry_skips_month_missing_on_one_exchange():
    nse = [_c("NSE", date(2026, 10, 28)), _c("NSE", date(2026, 11, 26))]
    bse = [_c("BSE", date(2026, 11, 26))]
    picked = select_common_expiry({"NSE": nse, "BSE": bse}, as_of=AS_OF)
    assert picked["NSE"].expiry == picked["BSE"].expiry == date(2026, 11, 26)


def test_common_expiry_raises_when_no_overlap():
    nse = [_c("NSE", date(2026, 10, 28))]
    bse = [_c("BSE", date(2026, 11, 26))]
    with pytest.raises(NoCommonExpiryError):
        select_common_expiry({"NSE": nse, "BSE": bse}, as_of=AS_OF)
