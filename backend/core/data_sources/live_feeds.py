"""
Builds the live USD/INR exchange legs for `MultiSourceStreamer`.

Legs are (provider x exchange) pairs on one common contract expiry:

    upstox_nse_usdinr   free        NSE near-month USDINR future
    upstox_bse_usdinr   free        BSE near-month USDINR future (same expiry)
    kite_nse_usdinr     ₹500/month  same NSE contract through a second provider
    kite_bse_usdinr     ₹500/month  same BSE contract through a second provider

Each provider gets one `PolledQuoteHub`, so all of its legs are fetched in one
request and share an observation timestamp. A provider or exchange that cannot
be set up (missing credentials, network, contract not listed) is skipped with a
reason; at least two legs are required, since arbitrage needs two prices.

Environment (all optional):
    ARBEX_LIVE_PROVIDERS   default "upstox,kite"
    ARBEX_LIVE_EXCHANGES   default "NSE,BSE"
    ARBEX_RECORD_DIR       where to record quotes (default research/recordings;
                           set to "off" to disable)
    ARBEX_LIVE_REPLAY      a recorded session directory to replay instead of live
    ARBEX_REPLAY_SPEED     replay speed multiplier (default 1.0)
    ARBEX_REPLAY_LOOP      "0" to play a recording once (default loops; counts then repeat)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Optional

from backend.core.data_sources.exchange_contracts import FutureContract, select_common_expiry
from backend.core.data_sources.exchange_leg_source import ExchangeLegSource
from backend.core.data_sources.quote_hub import PolledQuoteHub, Quote
from backend.core.data_sources.replay_hub import ReplayQuoteHub, load_recorded_contracts
from backend.core.data_sources.tick_recorder import QuoteRecorder
from backend.core.interfaces.data_source import DataSourceConfig

logger = logging.getLogger(__name__)

DEFAULT_RECORD_DIR = Path(__file__).resolve().parents[3] / "research" / "recordings"
PROVIDER_NAMES = {"upstox": "Upstox", "kite": "Kite"}


class LiveFeedUnavailable(RuntimeError):
    """Fewer than two live legs could be set up."""


@dataclass
class LiveFeedSetup:
    sources: List[ExchangeLegSource]
    hubs: Dict[str, PolledQuoteHub]
    skipped: Dict[str, str] = field(default_factory=dict)
    recorder: Optional[QuoteRecorder] = None
    replay_dir: Optional[Path] = None

    def describe(self) -> List[str]:
        lines = [f"{s.source_id}: {s.contract.trading_symbol} ({s.contract.instrument_key}, "
                 f"expiry {s.contract.expiry})" for s in self.sources]
        lines += [f"skipped {k}: {v}" for k, v in self.skipped.items()]
        return lines


def _default_contract_loader(provider: str, exchange: str) -> List[FutureContract]:
    if provider == "upstox":
        from backend.core.data_sources.upstox_feed import load_upstox_contracts
        return load_upstox_contracts(exchange)
    if provider == "kite":
        from backend.core.data_sources.kite_feed import load_kite_contracts
        return load_kite_contracts(exchange)
    raise ValueError(f"unknown provider {provider!r}")


def _default_hub_factory(provider: str) -> PolledQuoteHub:
    if provider == "upstox":
        from backend.core.data_sources.upstox_feed import make_upstox_hub
        return make_upstox_hub()
    if provider == "kite":
        from backend.core.data_sources.kite_feed import make_kite_hub
        return make_kite_hub()
    raise ValueError(f"unknown provider {provider!r}")


def _env_list(name: str, default: str) -> List[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


def _leg(source_id: str, display: str, hub: PolledQuoteHub, contract: FutureContract,
         priority: int, max_staleness_ms: int,
         latency_ms: Optional[float] = None) -> ExchangeLegSource:
    config = DataSourceConfig(
        source_id=source_id,
        source_type="exchange_future",
        display_name=display,
        priority=priority,
        # Retail polling: on average a quote is acted on one poll interval
        # after it was observed, split across the two legs of a trade.
        latency_estimate_ms=(latency_ms if latency_ms is not None
                             else hub.stats()["poll_interval_s"] * 1000 / 2),
        reliability_score=0.95,
        symbols=["USDINR"],
        extra_config={"exchange": contract.exchange, "provider": contract.provider,
                      "expiry": contract.expiry.isoformat()},
    )
    # Register now, not on connect(): the hub starts polling on the first leg's
    # connect(), and every leg must be in that first single request.
    hub.register(contract.instrument_key)
    return ExchangeLegSource(config, hub=hub, contract=contract, max_staleness_ms=max_staleness_ms)


def build_live_exchange_sources(
    *,
    providers: Optional[List[str]] = None,
    exchanges: Optional[List[str]] = None,
    as_of: Optional[date] = None,
    record_dir: Optional[Path | str] = None,
    max_staleness_ms: int = 5_000,
    contract_loader: Callable[[str, str], List[FutureContract]] = _default_contract_loader,
    hub_factory: Callable[[str], PolledQuoteHub] = _default_hub_factory,
) -> LiveFeedSetup:
    providers = providers or _env_list("ARBEX_LIVE_PROVIDERS", "upstox,kite")
    exchanges = exchanges or _env_list("ARBEX_LIVE_EXCHANGES", "NSE,BSE")
    as_of = as_of or date.today()
    skipped: Dict[str, str] = {}

    hubs: Dict[str, PolledQuoteHub] = {}
    for p in providers:
        try:
            hubs[p] = hub_factory(p)
        except Exception as e:
            skipped[p] = str(e)

    groups: Dict[str, List[FutureContract]] = {}
    for p in hubs:
        for ex in exchanges:
            try:
                contracts = contract_loader(p, ex)
            except Exception as e:
                skipped[f"{p}:{ex}"] = str(e)
                continue
            if contracts:
                groups[f"{p}:{ex}"] = contracts
            else:
                skipped[f"{p}:{ex}"] = "no USDINR futures listed"

    if len(groups) < 2:
        raise LiveFeedUnavailable(f"need at least two live legs, have {list(groups)}; skipped: {skipped}")

    picked = select_common_expiry(groups, as_of=as_of)
    sources = []
    for priority, (group, contract) in enumerate(sorted(picked.items()), start=1):
        provider, exchange = group.split(":")
        sources.append(_leg(
            f"{provider}_{exchange.lower()}_usdinr",
            f"{PROVIDER_NAMES.get(provider, provider)} {exchange} USD/INR FUT {contract.expiry:%d %b %y}",
            hubs[provider], contract, priority, max_staleness_ms))

    used = {s.contract.provider for s in sources}
    hubs = {p: h for p, h in hubs.items() if p in used}

    recorder = None
    if record_dir is None:
        record_dir = os.environ.get("ARBEX_RECORD_DIR", str(DEFAULT_RECORD_DIR))
    if str(record_dir).lower() != "off":
        recorder = QuoteRecorder(record_dir)
        for s in sources:
            recorder.register_contract(s.contract)
        for hub in hubs.values():
            hub.add_listener(recorder.on_poll)

    return LiveFeedSetup(sources=sources, hubs=hubs, skipped=skipped, recorder=recorder)


def build_replay_sources(session_dir: Path | str, *, speed: float = 1.0, loop: bool = True,
                         max_staleness_ms: int = 5_000) -> LiveFeedSetup:
    """Legs that replay a recorded session. Source ids are prefixed ``replay_``."""
    session_dir = Path(session_dir)
    contracts = load_recorded_contracts(session_dir)
    if len(contracts) < 2:
        raise LiveFeedUnavailable(f"{session_dir} has {len(contracts)} recorded instrument(s); need 2")
    hub = ReplayQuoteHub(session_dir, speed=speed, loop=loop)
    sources = [
        _leg(f"replay_{c.provider}_{c.exchange.lower()}_usdinr",
             f"Replay {PROVIDER_NAMES.get(c.provider, c.provider)} {c.exchange} USD/INR FUT",
             hub, c, priority, max_staleness_ms,
             latency_ms=500.0)   # recorded at 1 Hz: keep the live latency assumption
        for priority, c in enumerate(sorted(contracts, key=lambda c: (c.provider, c.exchange)), start=1)
    ]
    return LiveFeedSetup(sources=sources, hubs={"replay": hub}, replay_dir=session_dir)


def cross_spreads(quotes: Dict[str, Quote]) -> List[dict]:
    """
    Pairwise executable spread between legs, in paise (1 pip = 0.01 INR).

    For legs A and B the executable edge is max(bid_B - ask_A, bid_A - ask_B):
    buy at one venue's ask, sell at the other's bid. Positive means a crossed
    market (arbitrage before costs); negative is the distance to one.
    One-sided books are skipped.
    """
    legs = sorted((k, q) for k, q in quotes.items() if q is not None and q.two_sided)
    rows = []
    for i, (a, qa) in enumerate(legs):
        for b, qb in legs[i + 1:]:
            a_to_b, b_to_a = qb.bid - qa.ask, qa.bid - qb.ask
            buy, sell, edge = (a, b, a_to_b) if a_to_b >= b_to_a else (b, a, b_to_a)
            rows.append({
                "buy": buy,
                "sell": sell,
                "executable_paise": round(edge / 0.01, 4),
                "crossed": edge > 0,
            })
    return rows
