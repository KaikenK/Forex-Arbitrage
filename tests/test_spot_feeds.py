"""Parsers for the free spot venues (payload shapes captured from the live APIs)."""

import time

import pytest

from backend.core.data_sources.spot_feeds import SpotFetcher, parse_bitstamp, parse_kraken, parse_truefx


def test_kraken_ticker():
    payload = {"error": [], "result": {"ZEURZUSD": {"a": ["1.12022", "1", "1.000"], "b": ["1.12020", "3", "3.000"],
                                                    "c": ["1.12021", "50.0"]}}}
    q = parse_kraken(payload)["kraken:ZEURZUSD"]
    assert (q.bid, q.ask, q.bid_qty, q.ask_qty) == (1.12020, 1.12022, 3.0, 1.0) and q.two_sided


def test_kraken_error_raises():
    with pytest.raises(RuntimeError):
        parse_kraken({"error": ["EQuery:Unknown asset pair"]})


def test_truefx_big_figure_plus_points():
    now = int(time.time() * 1000)
    csv = (f"EUR/USD,{now},1.12,029,1.12,032,1.11875,1.12116,1.11991\n"
           f"USD/JPY,{now},158.,268,158.,276,158.111,158.529,158.368\n"
           f"GBP/USD,{now - 3_600_000},1.32,606,1.32,609,1,1,1\n")      # an hour old: stale, dropped
    q = parse_truefx(csv)
    assert q["truefx:EUR/USD"].bid == 1.12029 and q["truefx:EUR/USD"].ask == 1.12032
    assert q["truefx:USD/JPY"].bid == 158.268
    assert "truefx:GBP/USD" not in q


def test_bitstamp_ticker():
    q = parse_bitstamp("eurusd", {"timestamp": str(int(time.time())), "bid": "1.12022", "ask": "1.12032",
                                  "last": "1.12029"})["bitstamp:eurusd"]
    assert (q.bid, q.ask) == (1.12022, 1.12032)


def test_one_failing_venue_does_not_hide_the_others():
    now = int(time.time() * 1000)

    def get(url, timeout):
        if "kraken" in url:
            raise OSError("connection reset")
        return f"EUR/USD,{now},1.12,029,1.12,032,1,1,1".encode()

    fetch = SpotFetcher(get)
    out = fetch(["kraken:ZEURZUSD", "truefx:EUR/USD"])
    assert list(out) == ["truefx:EUR/USD"] and fetch.venue_errors["kraken"]
