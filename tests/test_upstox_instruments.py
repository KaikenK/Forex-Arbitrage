"""Tests for the Upstox instrument-master resolver (no network — uses a fixture)."""

from datetime import date, datetime, timezone

import pytest

from backend.core.data_sources.upstox_instruments import (
    StaleMasterError,
    resolve_near_month,
    usdinr_futures,
)


def _ms(y, m, d):
    return int(datetime(y, m, d, 14, 30, tzinfo=timezone.utc).timestamp() * 1000)


FIXTURE = [
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "FUT", "weekly": True,
     "instrument_key": "NCD_FO|2674", "trading_symbol": "USDINR FUT 04 SEP 26",
     "expiry": _ms(2026, 9, 4), "lot_size": 1, "qty_multiplier": 1000.0},
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "FUT", "weekly": True,
     "instrument_key": "NCD_FO|4563", "trading_symbol": "USDINR FUT 25 SEP 26",
     "expiry": _ms(2026, 9, 25), "lot_size": 1, "qty_multiplier": 1000.0},
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "FUT", "weekly": False,
     "instrument_key": "NCD_FO|1769", "trading_symbol": "USDINR FUT 28 SEP 26",
     "expiry": _ms(2026, 9, 28), "lot_size": 1, "qty_multiplier": 1000.0},
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "FUT", "weekly": False,
     "instrument_key": "NCD_FO|1584", "trading_symbol": "USDINR FUT 26 NOV 26",
     "expiry": _ms(2026, 11, 26), "lot_size": 1, "qty_multiplier": 1000.0},
    # noise that must be filtered out
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "CE", "weekly": True,
     "instrument_key": "NCD_FO|14223", "trading_symbol": "USDINR 101.5 CE 13 NOV 26",
     "expiry": _ms(2026, 11, 13), "strike_price": 101.5},
    {"segment": "NSE_FO", "name": "NIFTY", "instrument_type": "FUT", "weekly": False,
     "instrument_key": "NSE_FO|53001", "trading_symbol": "NIFTY FUT 25 SEP 26",
     "expiry": _ms(2026, 9, 25)},
    {"segment": "NCD_FO", "name": "EURINR", "instrument_type": "FUT", "weekly": False,
     "instrument_key": "NCD_FO|9000", "trading_symbol": "EURINR FUT 28 SEP 26",
     "expiry": _ms(2026, 9, 28)},
]


def test_usdinr_futures_filters_segment_type_and_underlying():
    futs = usdinr_futures(master_json=FIXTURE)
    assert [c.instrument_key for c in futs] == [
        "NCD_FO|2674", "NCD_FO|4563", "NCD_FO|1769", "NCD_FO|1584"]  # sorted by expiry
    assert futs[0].expiry == date(2026, 9, 4)
    assert futs[0].weekly is True
    assert futs[2].weekly is False
    assert futs[0].qty_multiplier == 1000.0


def test_resolve_near_month_prefers_the_monthly_contract():
    nm = resolve_near_month(as_of=date(2026, 8, 31), master_json=FIXTURE)
    assert nm.instrument_key == "NCD_FO|1769"     # 28 Sep monthly, not the 4 Sep weekly
    assert nm.weekly is False


def test_resolve_near_month_any_takes_nearest_weekly():
    nm = resolve_near_month(as_of=date(2026, 8, 31), prefer_monthly=False, master_json=FIXTURE)
    assert nm.instrument_key == "NCD_FO|2674"     # 4 Sep weekly


def test_resolve_near_month_rolls_past_expiring_monthly():
    # 27 Sep: the 28 Sep monthly is inside min_days_to_expiry -> roll to 26 Nov
    nm = resolve_near_month(as_of=date(2026, 9, 27), min_days_to_expiry=2, master_json=FIXTURE)
    assert nm.instrument_key == "NCD_FO|1584"


def test_resolve_raises_when_all_expired():
    with pytest.raises(StaleMasterError):
        resolve_near_month(as_of=date(2027, 6, 1), master_json=FIXTURE)
