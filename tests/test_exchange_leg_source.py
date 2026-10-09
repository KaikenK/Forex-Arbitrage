"""Tests for PolledQuoteHub + ExchangeLegSource (live exchange legs)."""

import time
from datetime import date

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.exchange_leg_source import ExchangeLegSource
from backend.core.data_sources.quote_hub import PolledQuoteHub, Quote, QuoteAuthError
from backend.core.interfaces.data_source import DataSourceConfig


def _contract(exchange, key):
    return FutureContract(exchange=exchange, instrument_key=key,
                          trading_symbol="USDINR FUT 28 OCT 26", expiry=date(2026, 10, 28),
                          lot_size=1, contract_size=1000.0, provider="upstox")


class FakeClock:
    def __init__(self, t=1_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _hub(quotes, clock):
    return PolledQuoteHub("fake", lambda keys: {k: quotes[k] for k in keys if k in quotes},
                          poll_interval_s=0.01, clock=clock)


def _leg(hub, exchange, key, clock):
    cfg = DataSourceConfig(source_id=f"upstox_{exchange.lower()}_usdinr", source_type="exchange_future")
    return ExchangeLegSource(cfg, hub=hub, contract=_contract(exchange, key),
                             max_staleness_ms=5_000, clock=clock)


def test_both_legs_share_one_observation_timestamp():
    clock = FakeClock()
    hub = _hub({"N": Quote(bid=88.70, ask=88.71, bid_qty=5, ask_qty=7, exchange_ts_ms=42),
                "B": Quote(bid=88.69, ask=88.72)}, clock)
    nse, bse = _leg(hub, "NSE", "N", clock), _leg(hub, "BSE", "B", clock)
    hub.register("N")
    hub.register("B")
    hub.poll_once()

    a, b = nse.get_tick("USDINR"), bse.get_tick("USDINR")
    assert a.timestamp_ms == b.timestamp_ms == 1_000_000
    assert (a.bid, a.ask, a.symbol) == (88.70, 88.71, "USDINR")
    assert a.extra["exchange"] == "NSE" and b.extra["exchange"] == "BSE"
    assert a.extra["expiry"] == "2026-10-28"
    assert a.extra["exchange_ts_ms"] == 42
    assert a.extra["bid_qty"] == 5 and a.extra["provider"] == "upstox"


def test_one_sided_book_emits_no_tick():
    clock = FakeClock()
    hub = _hub({"B": Quote(bid=None, ask=88.72, last=88.70)}, clock)
    leg = _leg(hub, "BSE", "B", clock)
    hub.register("B")
    hub.poll_once()
    assert leg.get_tick("USDINR") is None


def test_stale_quote_emits_no_tick():
    clock = FakeClock()
    hub = _hub({"N": Quote(bid=88.70, ask=88.71)}, clock)
    leg = _leg(hub, "NSE", "N", clock)
    hub.register("N")
    hub.poll_once()
    assert leg.get_tick("USDINR") is not None
    clock.t += 6.0          # > 5 s staleness gate, no new poll
    assert leg.get_tick("USDINR") is None
    assert leg.is_healthy() is False


def test_unknown_symbol_returns_none():
    clock = FakeClock()
    hub = _hub({"N": Quote(bid=88.70, ask=88.71)}, clock)
    leg = _leg(hub, "NSE", "N", clock)
    hub.register("N")
    hub.poll_once()
    assert leg.get_tick("EURUSD") is None


def test_auth_error_stops_the_hub_thread():
    def fetch(keys):
        raise QuoteAuthError("401")

    hub = PolledQuoteHub("fake", fetch, poll_interval_s=0.01)
    hub.register("N")
    hub.start()
    deadline = time.time() + 2
    while hub.running and time.time() < deadline:
        time.sleep(0.01)
    assert hub.auth_failed and not hub.running


def test_connect_registers_and_disconnect_releases():
    clock = FakeClock()
    hub = _hub({"N": Quote(bid=88.70, ask=88.71)}, clock)
    leg = _leg(hub, "NSE", "N", clock)
    assert leg.connect() is True
    assert "N" in hub.stats()["instruments"]
    leg.disconnect()
    assert "N" not in hub.stats()["instruments"]
    assert leg.is_connected is False
