"""Tests for the Dhan scrip-master resolver (no network — uses a fixture)."""

from datetime import date

import pytest

from backend.core.data_sources.dhan_instruments import (
    resolve_near_month,
    usdinr_futures,
)

FIXTURE = """SEM_EXM_EXCH_ID,SEM_SEGMENT,SEM_SMST_SECURITY_ID,SEM_INSTRUMENT_NAME,SEM_EXPIRY_CODE,SEM_TRADING_SYMBOL,SEM_LOT_UNITS,SEM_CUSTOM_SYMBOL,SEM_EXPIRY_DATE,SEM_STRIKE_PRICE,SEM_OPTION_TYPE,SEM_TICK_SIZE
NSE,D,1229,FUTCUR,0,USDINR-29Jul2026-FUT,1.0,USDINR JUL FUT,2026-07-29 14:30:00,-0.01,XX,0.0025
NSE,D,3916,FUTCUR,0,USDINR-26Aug2026-FUT,1.0,USDINR AUG FUT,2026-08-26 14:30:00,-0.01,XX,0.0025
NSE,D,3146,FUTCUR,0,USDINR-29Sep2026-FUT,1.0,USDINR SEP FUT,2026-09-29 14:30:00,-0.01,XX,0.0025
BSE,D,9999,FUTCUR,0,USDINR-26Aug2026-FUT,1.0,USDINR AUG FUT,2026-08-26 14:30:00,-0.01,XX,0.0025
NSE,D,5000,OPTCUR,0,USDINR-26Aug2026-88-CE,1.0,USDINR CE,2026-08-26 14:30:00,88,CE,0.0025
NSE,D,7000,FUTIDX,0,NIFTY-26Aug2026-FUT,50,NIFTY FUT,2026-08-26 14:30:00,-0.01,XX,0.05
"""


def test_usdinr_futures_filters_exchange_and_instrument():
    futs = usdinr_futures("NSE", master_text=FIXTURE)
    assert [c.security_id for c in futs] == ["1229", "3916", "3146"]  # sorted by expiry
    assert all(c.exchange == "NSE" for c in futs)
    assert futs[0].expiry == date(2026, 7, 29)
    assert futs[0].exchange_segment == "NSE_CURRENCY"

    bse = usdinr_futures("BSE", master_text=FIXTURE)
    assert [c.security_id for c in bse] == ["9999"]


def test_resolve_near_month_picks_nearest_live():
    nm = resolve_near_month("NSE", as_of=date(2026, 8, 20), master_text=FIXTURE)
    assert nm.trading_symbol == "USDINR-26Aug2026-FUT"
    assert nm.security_id == "3916"


def test_resolve_near_month_rolls_when_front_is_expiring():
    # 2 days before Aug expiry -> Aug is within min_days_to_expiry, roll to Sep
    nm = resolve_near_month("NSE", as_of=date(2026, 8, 25),
                            min_days_to_expiry=2, master_text=FIXTURE)
    assert nm.security_id == "3146"      # Sep


def test_resolve_raises_when_master_stale():
    with pytest.raises(RuntimeError):
        resolve_near_month("NSE", as_of=date(2027, 1, 1), master_text=FIXTURE)
