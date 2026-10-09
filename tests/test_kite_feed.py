"""Tests for the Kite Connect feed: instruments CSV and quote parsing."""

from datetime import date

from backend.core.data_sources.kite_feed import parse_kite_instruments, parse_kite_quotes

INSTRUMENTS_CSV = (
    "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
    "tick_size,lot_size,instrument_type,segment,exchange\n"
    "412675,1612,USDINR26OCTFUT,USDINR,0,2026-10-28,0,0.0025,1,FUT,CDS-FUT,CDS\n"
    "412676,1613,USDINR26NOVFUT,USDINR,0,2026-11-26,0,0.0025,1,FUT,CDS-FUT,CDS\n"
    "412677,1614,USDINR26O16FUT,USDINR,0,2026-10-16,0,0.0025,1,FUT,CDS-FUT,CDS\n"
    "412678,1615,EURINR26OCTFUT,EURINR,0,2026-10-28,0,0.0025,1,FUT,CDS-FUT,CDS\n"
    "412679,1616,USDINR26OCT88CE,USDINR,0,2026-10-28,88,0.0025,1,CE,CDS-OPT,CDS\n"
)


def test_instruments_filter_usdinr_futures_and_flag_weeklies():
    contracts = parse_kite_instruments(INSTRUMENTS_CSV, exchange="NSE")
    by_symbol = {c.trading_symbol: c for c in contracts}
    assert set(by_symbol) == {"USDINR26OCTFUT", "USDINR26NOVFUT", "USDINR26O16FUT"}
    assert by_symbol["USDINR26OCTFUT"].instrument_key == "CDS:USDINR26OCTFUT"
    assert by_symbol["USDINR26OCTFUT"].expiry == date(2026, 10, 28)
    assert by_symbol["USDINR26OCTFUT"].weekly is False
    assert by_symbol["USDINR26O16FUT"].weekly is True
    assert all(c.provider == "kite" and c.exchange == "NSE" for c in contracts)


def test_instruments_for_bse_use_bcd_keys():
    csv_bse = INSTRUMENTS_CSV.replace(",CDS-FUT,CDS", ",BCD-FUT,BCD")
    contracts = parse_kite_instruments(csv_bse, exchange="BSE")
    assert "BCD:USDINR26OCTFUT" in {c.instrument_key for c in contracts}


def test_quotes_parse_depth_and_ist_trade_time():
    payload = {"status": "success", "data": {
        "CDS:USDINR26OCTFUT": {
            "last_price": 88.71,
            "last_trade_time": "2026-10-09 10:30:00",
            "depth": {
                "buy": [{"price": 88.7075, "quantity": 40, "orders": 3},
                        {"price": 0, "quantity": 0, "orders": 0}],
                "sell": [{"price": 88.7100, "quantity": 25, "orders": 2}],
            },
        },
    }}
    q = parse_kite_quotes(payload, recv_ts_ms=5)["CDS:USDINR26OCTFUT"]
    assert (q.bid, q.ask, q.bid_qty, q.ask_qty) == (88.7075, 88.71, 40, 25)
    assert len(q.depth["bids"]) == 1            # zero-price level dropped
    # 10:30 IST == 05:00 UTC
    assert q.exchange_ts_ms == 1791522000000
    assert q.two_sided
