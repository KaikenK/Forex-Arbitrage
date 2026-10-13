"""Instrument registry: one place for pip size, tick size and contract data."""

import pytest

from backend.core.instruments import (
    REGISTRY, UnknownInstrumentError, get_instrument, normalize_symbol,
    parse_symbol_list, pip_size, to_pips,
)


@pytest.mark.parametrize("sym,pip", [
    ("USDINR", 0.01), ("EURINR", 0.01), ("GBPINR", 0.01), ("JPYINR", 0.01),
    ("EURUSD", 0.0001), ("GBPUSD", 0.0001), ("USDJPY", 0.01), ("EURJPY", 0.01),
])
def test_pip_sizes(sym, pip):
    assert pip_size(sym) == pip


def test_inr_futures_use_exchange_tick_and_lot():
    usdinr = get_instrument("USDINR")
    assert usdinr.tick_size == 0.0025 and usdinr.price_decimals == 4
    assert usdinr.contract_size == 1000
    jpyinr = get_instrument("JPYINR")
    assert jpyinr.quote_unit == 100 and jpyinr.contract_size == 100_000


@pytest.mark.parametrize("raw", ["USDINR", "usdinr", "USD/INR", "usd_inr", " USD-INR "])
def test_symbol_spellings_normalize(raw):
    assert normalize_symbol(raw) == "USDINR"
    assert get_instrument(raw) is REGISTRY["USDINR"]


def test_one_inr_tick_is_a_quarter_pip_without_float_noise():
    assert to_pips(95.4525 - 95.4500, "USDINR") == pytest.approx(0.25)


def test_unregistered_pairs_fall_back_by_quote_currency():
    assert pip_size("SGDINR") == 0.01
    assert pip_size("CHFJPY") == 0.01
    assert pip_size("AUDCAD") == 0.0001
    assert get_instrument("SGDINR").known is False


def test_garbage_symbols_are_rejected_but_pip_size_stays_safe():
    with pytest.raises(UnknownInstrumentError):
        get_instrument("GOLD")
    assert pip_size("") == 0.0001


def test_parse_symbol_list():
    assert parse_symbol_list("USDINR, eur/inr,,USDINR") == ["USDINR", "EURINR"]
    assert parse_symbol_list(["gbp_usd"]) == ["GBPUSD"]
    with pytest.raises(UnknownInstrumentError):
        parse_symbol_list("USDINR,XAU")


def test_resolve_symbols_keeps_supported_and_reports_the_rest():
    from backend.core.instruments import resolve_symbols
    assert resolve_symbols(["USDINR", "EURINR"], ["USDINR"], ["USDINR"]) == (["USDINR"], ["EURINR"])
    assert resolve_symbols(["EURUSD"], ["USDINR"], ["USDINR"]) == (["USDINR"], ["EURUSD"])
    assert resolve_symbols(["EURUSD", "USDJPY"], ["EURUSD", "GBPUSD", "USDJPY"], ["EURUSD"]) == (
        ["EURUSD", "USDJPY"], [])
