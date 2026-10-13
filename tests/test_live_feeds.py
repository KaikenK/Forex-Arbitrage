"""Tests for the live-feed factory (provider x exchange legs, common expiry)."""

import time
from datetime import date

import pytest

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.live_feeds import (
    LiveFeedUnavailable,
    build_live_exchange_sources,
    build_replay_sources,
)
from backend.core.data_sources.quote_hub import PolledQuoteHub, Quote, QuoteAuthError
from backend.core.data_sources.tick_recorder import QuoteRecorder

AS_OF = date(2026, 10, 9)


def _contracts(provider, exchange, expiries):
    return [FutureContract(exchange=exchange, instrument_key=f"{provider}:{exchange}:{e}",
                           trading_symbol=f"USDINR {e}", expiry=e, lot_size=1,
                           contract_size=1000.0, provider=provider) for e in expiries]


OCT, NOV = date(2026, 10, 28), date(2026, 11, 26)


def _loader(table):
    def load(provider, exchange):
        value = table[(provider, exchange)]
        if isinstance(value, Exception):
            raise value
        return value
    return load


def _hubs(fail=()):
    def make(provider):
        if provider in fail:
            raise QuoteAuthError(f"{provider} creds missing")
        return PolledQuoteHub(provider, lambda keys: {}, poll_interval_s=1.0)
    return make


def test_upstox_nse_and_bse_legs_on_the_same_expiry():
    setup = build_live_exchange_sources(
        providers=["upstox"], exchanges=["NSE", "BSE"], as_of=AS_OF,
        contract_loader=_loader({("upstox", "NSE"): _contracts("upstox", "NSE", [OCT, NOV]),
                                 ("upstox", "BSE"): _contracts("upstox", "BSE", [OCT, NOV])}),
        hub_factory=_hubs(),
    )
    ids = sorted(s.source_id for s in setup.sources)
    assert ids == ["upstox_bse_usdinr", "upstox_nse_usdinr"]
    assert {s.contract.expiry for s in setup.sources} == {OCT}
    # both legs share one hub -> one request -> same observation timestamp
    assert len(setup.hubs) == 1


def test_provider_without_credentials_is_skipped_with_a_reason():
    setup = build_live_exchange_sources(
        providers=["upstox", "kite"], exchanges=["NSE", "BSE"], as_of=AS_OF,
        contract_loader=_loader({("upstox", "NSE"): _contracts("upstox", "NSE", [OCT]),
                                 ("upstox", "BSE"): _contracts("upstox", "BSE", [OCT])}),
        hub_factory=_hubs(fail=("kite",)),
    )
    assert len(setup.sources) == 2
    assert "creds missing" in setup.skipped["kite"]


def test_kite_adds_a_second_provider_on_the_same_contract():
    setup = build_live_exchange_sources(
        providers=["upstox", "kite"], exchanges=["NSE", "BSE"], as_of=AS_OF,
        contract_loader=_loader({("upstox", "NSE"): _contracts("upstox", "NSE", [OCT]),
                                 ("upstox", "BSE"): _contracts("upstox", "BSE", [OCT]),
                                 ("kite", "NSE"): _contracts("kite", "NSE", [OCT]),
                                 ("kite", "BSE"): RuntimeError("BCD not enabled")}),
        hub_factory=_hubs(),
    )
    assert sorted(s.source_id for s in setup.sources) == [
        "kite_nse_usdinr", "upstox_bse_usdinr", "upstox_nse_usdinr"]
    assert "BCD not enabled" in setup.skipped["kite:BSE"]


def test_fewer_than_two_legs_is_an_error():
    with pytest.raises(LiveFeedUnavailable) as err:
        build_live_exchange_sources(
            providers=["upstox"], exchanges=["NSE", "BSE"], as_of=AS_OF,
            contract_loader=_loader({("upstox", "NSE"): _contracts("upstox", "NSE", [OCT]),
                                     ("upstox", "BSE"): RuntimeError("network down")}),
            hub_factory=_hubs(),
        )
    assert "network down" in str(err.value)


def test_recorder_is_attached_to_every_hub(tmp_path):
    setup = build_live_exchange_sources(
        providers=["upstox"], exchanges=["NSE", "BSE"], as_of=AS_OF, record_dir=tmp_path,
        contract_loader=_loader({("upstox", "NSE"): _contracts("upstox", "NSE", [OCT]),
                                 ("upstox", "BSE"): _contracts("upstox", "BSE", [OCT])}),
        hub_factory=_hubs(),
    )
    assert isinstance(setup.recorder, QuoteRecorder)
    assert setup.hubs["upstox"]._listeners


def test_replay_builds_one_leg_per_recorded_instrument(tmp_path):
    rec = QuoteRecorder(tmp_path)
    for c in _contracts("upstox", "NSE", [OCT]) + _contracts("upstox", "BSE", [OCT]):
        rec.register_contract(c)
    rec.on_poll("upstox", {
        "upstox:NSE:2026-10-28": Quote(bid=88.70, ask=88.71, recv_ts_ms=1_800_000_000_000),
        "upstox:BSE:2026-10-28": Quote(bid=88.69, ask=88.72, recv_ts_ms=1_800_000_000_000),
    })
    setup = build_replay_sources(rec.session_dir, speed=1.0)
    assert sorted(s.source_id for s in setup.sources) == [
        "replay_upstox_bse_usdinr", "replay_upstox_nse_usdinr"]
    assert setup.replay_dir == rec.session_dir


def test_cross_spreads_report_the_best_executable_direction_in_paise():
    from backend.core.data_sources.live_feeds import cross_spreads
    rows = cross_spreads({
        "upstox_nse_usdinr": Quote(bid=88.7075, ask=88.7100),
        "upstox_bse_usdinr": Quote(bid=88.7150, ask=88.7200),
        "kite_nse_usdinr": Quote(bid=None, ask=88.7100),        # one-sided: skipped
    })
    assert len(rows) == 1
    row = rows[0]
    assert (row["buy"], row["sell"]) == ("upstox_nse_usdinr", "upstox_bse_usdinr")
    assert row["executable_paise"] == pytest.approx(0.5)     # 88.7150 - 88.7100
    assert row["crossed"] is True


def test_uncrossed_books_have_negative_executable_spread():
    from backend.core.data_sources.live_feeds import cross_spreads
    row = cross_spreads({
        "a": Quote(bid=88.7000, ask=88.7100),
        "b": Quote(bid=88.7050, ask=88.7300),
    })[0]
    assert row["crossed"] is False
    assert row["executable_paise"] == pytest.approx(-0.5)    # best: buy a @88.71, sell b @88.705


def test_record_dir_off_disables_recording():
    setup = build_live_exchange_sources(
        providers=["upstox"], exchanges=["NSE", "BSE"], as_of=AS_OF, record_dir="off",
        contract_loader=_loader({("upstox", "NSE"): _contracts("upstox", "NSE", [OCT]),
                                 ("upstox", "BSE"): _contracts("upstox", "BSE", [OCT])}),
        hub_factory=_hubs(),
    )
    assert setup.recorder is None
    assert not setup.hubs["upstox"]._listeners


def test_every_leg_is_registered_before_the_hub_first_polls():
    """Regression: the hub started on the first leg's connect() and its first
    request fetched only that leg; all legs must be in the very first request."""
    seen = []
    hub = PolledQuoteHub("upstox", lambda keys: seen.append(sorted(keys)) or {}, poll_interval_s=60)
    setup = build_live_exchange_sources(
        providers=["upstox"], exchanges=["NSE", "BSE"], as_of=AS_OF, record_dir="off",
        contract_loader=_loader({("upstox", "NSE"): _contracts("upstox", "NSE", [OCT]),
                                 ("upstox", "BSE"): _contracts("upstox", "BSE", [OCT])}),
        hub_factory=lambda provider: hub,
    )
    setup.sources[0].connect()          # starts the hub thread -> first poll
    deadline = time.time() + 2
    while not seen and time.time() < deadline:
        time.sleep(0.01)
    hub.stop()
    assert seen[0] == sorted(s.contract.instrument_key for s in setup.sources)
