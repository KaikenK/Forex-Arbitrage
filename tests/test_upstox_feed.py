"""Tests for the Upstox feed: instrument master (NSE + BSE) and quote parsing."""

from datetime import date, datetime, timedelta, timezone

from backend.core.data_sources.upstox_feed import (
    parse_upstox_master,
    parse_upstox_quotes,
)

IST = timezone(timedelta(hours=5, minutes=30))


def _expiry_ms(d: date) -> int:
    # Upstox stores expiry as an epoch-ms instant; use IST end-of-day like the
    # real master so a naive UTC conversion would land on the wrong date.
    return int(datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=IST).timestamp() * 1000)


MASTER = [
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "FUT",
     "instrument_key": "NCD_FO|1769", "trading_symbol": "USDINR FUT 28 OCT 26",
     "expiry": _expiry_ms(date(2026, 10, 28)), "lot_size": 1, "qty_multiplier": 1000, "weekly": False},
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "FUT",
     "instrument_key": "NCD_FO|1801", "trading_symbol": "USDINR FUT 16 OCT 26",
     "expiry": _expiry_ms(date(2026, 10, 16)), "lot_size": 1, "qty_multiplier": 1000, "weekly": True},
    {"segment": "NCD_FO", "name": "EURINR", "instrument_type": "FUT",
     "instrument_key": "NCD_FO|2001", "trading_symbol": "EURINR FUT 28 OCT 26",
     "expiry": _expiry_ms(date(2026, 10, 28)), "lot_size": 1, "qty_multiplier": 1000, "weekly": False},
    {"segment": "NCD_FO", "name": "USDINR", "instrument_type": "CE", "strike_price": 88.0,
     "instrument_key": "NCD_FO|3001", "trading_symbol": "USDINR 88 CE 28 OCT 26",
     "expiry": _expiry_ms(date(2026, 10, 28)), "lot_size": 1, "qty_multiplier": 1000, "weekly": False},
    {"segment": "BCD_FO", "name": "USDINR", "instrument_type": "FUT",
     "instrument_key": "BCD_FO|855", "trading_symbol": "USDINR FUT 28 OCT 26",
     "expiry": _expiry_ms(date(2026, 10, 28)), "lot_size": 1, "qty_multiplier": 1000, "weekly": False},
]


def test_master_filters_to_usdinr_futures_of_the_requested_exchange():
    nse = parse_upstox_master(MASTER, exchange="NSE")
    assert {c.instrument_key for c in nse} == {"NCD_FO|1769", "NCD_FO|1801"}
    assert all(c.exchange == "NSE" and c.provider == "upstox" for c in nse)

    bse = parse_upstox_master(MASTER, exchange="BSE")
    assert [c.instrument_key for c in bse] == ["BCD_FO|855"]


def test_master_expiry_is_read_in_ist_not_utc():
    nse = {c.instrument_key: c for c in parse_upstox_master(MASTER, exchange="NSE")}
    assert nse["NCD_FO|1769"].expiry == date(2026, 10, 28)
    assert nse["NCD_FO|1801"].weekly is True
    assert nse["NCD_FO|1769"].contract_size == 1000.0


def _node(token, bids, asks, ltp=88.71, ltt="1791523800000"):
    return {
        "instrument_token": token,
        "last_price": ltp,
        "last_trade_time": ltt,
        "depth": {
            "buy": [{"price": p, "quantity": q, "orders": 1} for p, q in bids],
            "sell": [{"price": p, "quantity": q, "orders": 1} for p, q in asks],
        },
    }


def test_quotes_are_keyed_by_instrument_key_for_multi_instrument_requests():
    payload = {"status": "success", "data": {
        "NCD_FO:USDINR26OCTFUT": _node("NCD_FO|1769", [(88.7075, 120), (88.7050, 50)],
                                       [(88.7100, 90), (88.7125, 30)]),
        "BCD_FO:USDINR26OCTFUT": _node("BCD_FO|855", [(88.7000, 10)], [(88.7200, 5)]),
    }}
    quotes = parse_upstox_quotes(payload, recv_ts_ms=1_000)
    nse, bse = quotes["NCD_FO|1769"], quotes["BCD_FO|855"]
    assert (nse.bid, nse.ask, nse.bid_qty, nse.ask_qty) == (88.7075, 88.7100, 120, 90)
    assert nse.exchange_ts_ms == 1791523800000
    assert nse.recv_ts_ms == bse.recv_ts_ms == 1_000
    assert len(nse.depth["bids"]) == 2
    assert nse.two_sided and bse.two_sided


def test_empty_book_is_not_two_sided_even_with_a_last_price():
    payload = {"data": {"BCD_FO:X": _node("BCD_FO|855", [(0, 0)], [(0, 0)], ltp=88.70)}}
    q = parse_upstox_quotes(payload, recv_ts_ms=1)["BCD_FO|855"]
    assert q.last == 88.70
    assert not q.two_sided


def test_locked_or_crossed_own_book_is_rejected():
    payload = {"data": {"X": _node("NCD_FO|1769", [(88.72, 1)], [(88.71, 1)])}}
    assert not parse_upstox_quotes(payload, recv_ts_ms=1)["NCD_FO|1769"].two_sided
