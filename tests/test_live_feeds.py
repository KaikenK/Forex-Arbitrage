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


def _contracts(provider, exchange, expiries, symbol="USDINR"):
    return [FutureContract(exchange=exchange, instrument_key=f"{provider}:{exchange}:{symbol}:{e}",
                           trading_symbol=f"{symbol} {e}", expiry=e, lot_size=1,
                           contract_size=1000.0, provider=provider, symbol=symbol) for e in expiries]


OCT, NOV = date(2026, 10, 28), date(2026, 11, 26)


def _loader(table):
    def load(provider, exchange, symbols):
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
        "upstox:NSE:USDINR:2026-10-28": Quote(bid=88.70, ask=88.71, recv_ts_ms=1_800_000_000_000),
        "upstox:BSE:USDINR:2026-10-28": Quote(bid=88.69, ask=88.72, recv_ts_ms=1_800_000_000_000),
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


# -- several currency pairs ---------------------------------------------------

def test_every_requested_pair_gets_its_own_legs_on_one_hub_per_provider():
    seen = []
    hub = PolledQuoteHub("upstox", lambda keys: seen.append(sorted(keys)) or {}, poll_interval_s=60)
    table = {(p, ex): [c for sym in ("USDINR", "EURINR", "USDJPY") for c in _contracts(p, ex, [OCT, NOV], sym)]
             for p, ex in (("upstox", "NSE"), ("upstox", "BSE"))}
    setup = build_live_exchange_sources(
        symbols=["USDINR", "EURINR", "USDJPY", "AUDUSD"], providers=["upstox"], exchanges=["NSE", "BSE"],
        as_of=AS_OF, record_dir="off", contract_loader=_loader(table), hub_factory=lambda p: hub,
        spot_venues=[])                      # spot off: USDJPY and AUDUSD fall back to futures
    assert sorted(s.source_id for s in setup.sources) == [
        "upstox_bse_eurinr", "upstox_bse_usdinr", "upstox_bse_usdjpy",
        "upstox_nse_eurinr", "upstox_nse_usdinr", "upstox_nse_usdjpy"]
    assert {s.symbol for s in setup.sources} == {"USDINR", "EURINR", "USDJPY"}
    assert setup.skipped["AUDUSD"] == "no NSE/BSE futures or free spot venue for this pair"
    eur = next(s for s in setup.sources if s.source_id == "upstox_nse_eurinr")
    assert eur.get_supported_symbols() == ["EURINR"] and "EUR/INR" in eur.config.display_name
    # one request covers every pair, so all legs share an observation timestamp
    hub.poll_once()
    assert len(seen) == 1 and len(seen[0]) == 6


def test_a_pair_listed_on_only_one_exchange_is_skipped_not_fatal():
    table = {("upstox", "NSE"): _contracts("upstox", "NSE", [OCT]) + _contracts("upstox", "NSE", [OCT], "GBPINR"),
             ("upstox", "BSE"): _contracts("upstox", "BSE", [OCT])}
    setup = build_live_exchange_sources(
        symbols=["USDINR", "GBPINR"], providers=["upstox"], exchanges=["NSE", "BSE"], as_of=AS_OF,
        record_dir="off", contract_loader=_loader(table), hub_factory=_hubs())
    assert sorted(s.source_id for s in setup.sources) == ["upstox_bse_usdinr", "upstox_nse_usdinr"]
    assert setup.skipped == {"GBPINR": "needs two legs; listed on upstox:NSE"}


def test_cross_spreads_never_compare_different_pairs():
    from backend.core.data_sources.live_feeds import cross_spreads
    rows = cross_spreads({
        "upstox_nse_usdinr": Quote(bid=88.7075, ask=88.7100),
        "upstox_bse_usdinr": Quote(bid=88.7150, ask=88.7200),
        "upstox_nse_eurusd": Quote(bid=1.1600, ask=1.1601),
        "upstox_bse_eurusd": Quote(bid=1.1603, ask=1.1604),
    })
    by_symbol = {r["symbol"]: r for r in rows}
    assert len(rows) == 2
    assert by_symbol["EURUSD"]["executable_pips"] == pytest.approx(2.0)   # 1.1603 - 1.1601
    assert by_symbol["EURUSD"]["executable_paise"] is None
    assert by_symbol["USDINR"]["executable_paise"] == pytest.approx(0.5)


def test_replay_rebuilds_legs_per_pair_and_reads_old_usdinr_recordings(tmp_path):
    import json
    rec = QuoteRecorder(tmp_path)
    contracts = (_contracts("upstox", "NSE", [OCT], "EURINR") + _contracts("upstox", "BSE", [OCT], "EURINR")
                 + _contracts("upstox", "NSE", [OCT], "GBPINR"))          # GBPINR: one leg only
    for c in contracts:
        rec.register_contract(c)
    rec.on_poll("upstox", {c.instrument_key: Quote(bid=102.1, ask=102.11, recv_ts_ms=1_800_000_000_000)
                           for c in contracts})
    # a row written before recordings carried the pair
    with (rec.session_dir / "old.jsonl").open("w") as fh:
        for ex in ("NSE", "BSE"):
            fh.write(json.dumps({"symbol": "USDINR", "instrument_key": f"old:{ex}", "provider": "upstox",
                                 "exchange": ex, "trading_symbol": "USDINR FUT", "expiry": "2026-10-28",
                                 "bid": 88.7, "ask": 88.71, "recv_ts_ms": 1_800_000_000_000}) + "\n")
    setup = build_replay_sources(rec.session_dir)
    assert sorted(s.source_id for s in setup.sources) == [
        "replay_upstox_bse_eurinr", "replay_upstox_bse_usdinr",
        "replay_upstox_nse_eurinr", "replay_upstox_nse_usdinr"]
    assert setup.skipped == {"GBPINR": "only 1 recorded leg"}

def test_cross_spreads_skip_two_providers_on_the_same_exchange():
    from backend.core.data_sources.live_feeds import cross_spreads
    rows = cross_spreads({
        "upstox_nse_usdinr": Quote(bid=88.7075, ask=88.7100),
        "kite_nse_usdinr": Quote(bid=88.7150, ask=88.7175),
    })
    assert rows == []


def test_a_provider_with_a_stale_exchange_list_is_dropped_not_fatal():
    """Kite's BSE instrument list only has expired contracts: skip that leg, keep the rest."""
    old = date(2025, 2, 25)
    setup = build_live_exchange_sources(
        providers=["upstox", "kite"], exchanges=["NSE", "BSE"], as_of=AS_OF, record_dir="off",
        contract_loader=_loader({("upstox", "NSE"): _contracts("upstox", "NSE", [OCT, NOV]),
                                 ("upstox", "BSE"): _contracts("upstox", "BSE", [OCT, NOV]),
                                 ("kite", "NSE"): _contracts("kite", "NSE", [OCT, NOV]),
                                 ("kite", "BSE"): _contracts("kite", "BSE", [old])}),
        hub_factory=_hubs(),
    )
    assert sorted(s.source_id for s in setup.sources) == [
        "kite_nse_usdinr", "upstox_bse_usdinr", "upstox_nse_usdinr"]
    assert setup.skipped["kite:BSE:USDINR"] == "no unexpired monthly contracts listed"



# -- free spot venues for non-INR pairs ----------------------------------------

def test_non_inr_pairs_use_free_spot_venues_on_one_shared_hub():
    seen = []
    spot = PolledQuoteHub("spot", lambda keys: seen.append(sorted(keys)) or {}, poll_interval_s=60)
    table = {(p, ex): _contracts(p, ex, [OCT]) for p, ex in (("upstox", "NSE"), ("upstox", "BSE"))}
    setup = build_live_exchange_sources(
        symbols=["USDINR", "EURUSD", "GBPJPY"], providers=["upstox"], exchanges=["NSE", "BSE"], as_of=AS_OF,
        record_dir="off", contract_loader=_loader(table), hub_factory=_hubs(),
        spot_venues=["kraken", "bitstamp", "truefx"], spot_hub_factory=lambda: spot)
    ids = sorted(s.source_id for s in setup.sources)
    assert ids == ["spot_bitstamp_eurusd", "spot_kraken_eurusd", "spot_truefx_eurusd",
                   "upstox_bse_usdinr", "upstox_nse_usdinr"]
    assert setup.skipped["GBPJPY"] == "needs two spot venues; quoted on truefx"
    kraken = next(s for s in setup.sources if s.source_id == "spot_kraken_eurusd")
    assert kraken.contract.expiry is None and kraken.config.display_name == "Kraken EUR/USD spot"
    assert set(setup.hubs) == {"upstox", "spot"}
    spot.poll_once()                         # every spot venue in one poll -> one timestamp
    assert seen == [["bitstamp:eurusd", "kraken:ZEURZUSD", "truefx:EUR/USD"]]


def test_spot_only_live_feed_needs_no_broker_credentials():
    spot = PolledQuoteHub("spot", lambda keys: {}, poll_interval_s=60)
    setup = build_live_exchange_sources(
        symbols=["EURUSD", "GBPUSD"], providers=["upstox"], as_of=AS_OF, record_dir="off",
        hub_factory=_hubs(fail=("upstox",)), spot_venues=["kraken", "bitstamp"], spot_hub_factory=lambda: spot)
    assert len(setup.sources) == 4 and "upstox" not in setup.skipped   # broker never even tried


def test_spot_quotes_cross_across_venues_and_replay(tmp_path):
    from backend.core.data_sources.live_feeds import cross_spreads
    rows = cross_spreads({"spot_kraken_eurusd": Quote(bid=1.12020, ask=1.12022),
                          "spot_truefx_eurusd": Quote(bid=1.12025, ask=1.12028)})
    assert rows[0]["crossed"] and rows[0]["executable_pips"] == pytest.approx(0.3)
    # recorded spot legs (no expiry) replay as spot legs
    rec = QuoteRecorder(tmp_path)
    from backend.core.data_sources.spot_feeds import spot_contracts
    cs = spot_contracts(["EURUSD"], ["kraken", "truefx"])["EURUSD"]
    for c in cs:
        rec.register_contract(c)
    rec.on_poll("spot", {c.instrument_key: Quote(bid=1.12, ask=1.1201, recv_ts_ms=1_800_000_000_000) for c in cs})
    setup = build_replay_sources(rec.session_dir)
    assert sorted(s.source_id for s in setup.sources) == ["replay_spot_kraken_eurusd", "replay_spot_truefx_eurusd"]


def test_basis_tracker_separates_a_persistent_venue_gap_from_a_dislocation():
    from backend.core.data_sources.live_feeds import BasisTracker, cross_spreads
    t = BasisTracker(window=50)
    # Kraken's EUR/USD sits ~1.5 pips under TrueFX on every poll: a basis
    for _ in range(20):
        t.observe({"spot_kraken_eurusd": Quote(bid=1.12000, ask=1.12002),
                   "spot_truefx_eurusd": Quote(bid=1.12014, ask=1.12018)})
    usual = t.annotate(cross_spreads({"spot_kraken_eurusd": Quote(bid=1.12000, ask=1.12002),
                                      "spot_truefx_eurusd": Quote(bid=1.12014, ask=1.12018)}))[0]
    assert usual["crossed"] and usual["executable_pips"] == pytest.approx(1.2)
    assert usual["basis_pips"] == pytest.approx(1.5) and usual["beyond_basis_pips"] < 0   # just the basis
    jump = t.annotate(cross_spreads({"spot_kraken_eurusd": Quote(bid=1.12000, ask=1.12002),
                                     "spot_truefx_eurusd": Quote(bid=1.12040, ask=1.12044)}))[0]
    assert jump["beyond_basis_pips"] == pytest.approx(2.3)                               # a real dislocation
