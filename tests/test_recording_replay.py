"""Tests for quote recording and replay of a recorded live session."""

import json
from datetime import date

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.quote_hub import PolledQuoteHub, Quote
from backend.core.data_sources.tick_recorder import QuoteRecorder
from backend.core.data_sources.replay_hub import ReplayQuoteHub, load_recorded_contracts


def _contract(exchange, key, provider="upstox"):
    return FutureContract(exchange=exchange, instrument_key=key,
                          trading_symbol="USDINR FUT 28 OCT 26", expiry=date(2026, 10, 28),
                          lot_size=1, contract_size=1000.0, provider=provider)


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def _record_session(tmp_path):
    clock = Clock(1_800_000_000.0)            # 2027-01-15 08:00 UTC
    quotes = {"N": Quote(bid=88.70, ask=88.71, depth={"bids": [{"price": 88.70, "qty": 3}], "asks": []}),
              "B": Quote(bid=None, ask=88.72)}  # one-sided BSE book
    hub = PolledQuoteHub("upstox", lambda keys: {k: quotes[k] for k in keys}, clock=clock)
    rec = QuoteRecorder(tmp_path)
    rec.register_contract(_contract("NSE", "N"))
    rec.register_contract(_contract("BSE", "B"))
    hub.add_listener(rec.on_poll)
    hub.register("N")
    hub.register("B")
    hub.poll_once()
    clock.t += 1.0
    quotes["B"] = Quote(bid=88.69, ask=88.72)
    hub.poll_once()
    return rec


def test_recorder_writes_every_observation_including_one_sided(tmp_path):
    rec = _record_session(tmp_path)
    files = list(rec.session_dir.glob("*.jsonl"))
    assert [f.name for f in files] == ["upstox.jsonl"]
    rows = [json.loads(l) for l in files[0].read_text().splitlines()]
    assert len(rows) == 4
    first_bse = next(r for r in rows if r["instrument_key"] == "B")
    assert first_bse["bid"] is None and first_bse["exchange"] == "BSE"
    assert first_bse["expiry"] == "2026-10-28"
    nse = next(r for r in rows if r["instrument_key"] == "N")
    assert nse["depth"]["bids"][0]["price"] == 88.70


def test_replay_reproduces_quotes_on_a_shared_clock(tmp_path):
    rec = _record_session(tmp_path)
    contracts = load_recorded_contracts(rec.session_dir)
    assert {(c.provider, c.exchange) for c in contracts} == {("upstox", "NSE"), ("upstox", "BSE")}

    wall = Clock(2_000_000_000.0)
    hub = ReplayQuoteHub(rec.session_dir, speed=1.0, clock=wall)
    hub.register("N")
    hub.register("B")
    hub.poll_once()                     # replay starts: first observation due
    n1, b1 = hub.latest("N"), hub.latest("B")
    assert n1.bid == 88.70 and not b1.two_sided
    assert n1.recv_ts_ms == b1.recv_ts_ms == 2_000_000_000_000

    wall.t += 1.0
    hub.poll_once()
    n2, b2 = hub.latest("N"), hub.latest("B")
    assert b2.two_sided and b2.bid == 88.69
    assert n2.recv_ts_ms == b2.recv_ts_ms == 2_000_000_001_000
    assert hub.finished


def test_replay_ignores_the_opportunity_log_in_the_session_folder(tmp_path):
    """Regression: live mode writes opportunities.jsonl next to the quote files;
    replay must not try to read those rows as quotes."""
    rec = _record_session(tmp_path)
    (rec.session_dir / "opportunities.jsonl").write_text(
        json.dumps({"type": "cross_source", "buy_source": "upstox_nse_usdinr"}) + "\n")
    assert len(load_recorded_contracts(rec.session_dir)) == 2
    hub = ReplayQuoteHub(rec.session_dir, speed=1.0, clock=Clock(2_000_000_000.0))
    hub.register("N")
    hub.poll_once()
    assert hub.latest("N").bid == 88.70
