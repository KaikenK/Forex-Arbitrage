"""
Builds the live exchange legs for `MultiSourceStreamer`.

Legs are (provider x exchange) pairs, per currency pair, on one common contract
expiry. For USDINR:

    upstox_nse_usdinr   free        NSE near-month USDINR future
    upstox_bse_usdinr   free        BSE near-month USDINR future (same expiry)
    kite_nse_usdinr     ₹500/month  same NSE contract through a second provider
    kite_bse_usdinr     ₹500/month  same BSE contract through a second provider

and likewise `upstox_nse_eurinr`, `upstox_bse_usdjpy`, ... for every pair in
ARBEX_SYMBOLS that has NSE/BSE futures (see backend/core/instruments.py).

Each provider gets one `PolledQuoteHub`, so all of its legs, across every pair,
are fetched in one request and share an observation timestamp. A provider,
exchange or pair that cannot be set up (missing credentials, network, contract
not listed, no common expiry) is skipped with a reason. A pair needs at least
two legs, since arbitrage needs two prices.

Environment (all optional):
    ARBEX_SYMBOLS          pairs to trade (default USDINR; "all" = every listed pair)
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
from backend.core.instruments import (
    EXCHANGE_FUTURES_SYMBOLS, REGISTRY, get_instrument, normalize_symbol, to_pips,
)
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


def _default_contract_loader(provider: str, exchange: str, symbols: List[str]) -> List[FutureContract]:
    if provider == "upstox":
        from backend.core.data_sources.upstox_feed import load_upstox_contracts
        return load_upstox_contracts(exchange, symbols)
    if provider == "kite":
        from backend.core.data_sources.kite_feed import load_kite_contracts
        return load_kite_contracts(exchange, symbols)
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
        symbols=[contract.symbol],
        extra_config={"exchange": contract.exchange, "provider": contract.provider,
                      "expiry": contract.expiry.isoformat()},
    )
    # Register now, not on connect(): the hub starts polling on the first leg's
    # connect(), and every leg must be in that first single request.
    hub.register(contract.instrument_key)
    return ExchangeLegSource(config, hub=hub, contract=contract, max_staleness_ms=max_staleness_ms)


def _leg_id(prefix: str, contract: FutureContract) -> str:
    return f"{prefix}{contract.provider}_{contract.exchange.lower()}_{contract.symbol.lower()}"


def _leg_name(prefix: str, contract: FutureContract) -> str:
    return (f"{prefix}{PROVIDER_NAMES.get(contract.provider, contract.provider)} {contract.exchange} "
            f"{get_instrument(contract.symbol).display} FUT {contract.expiry:%d %b %y}")


def build_live_exchange_sources(
    *,
    symbols: Optional[List[str]] = None,
    providers: Optional[List[str]] = None,
    exchanges: Optional[List[str]] = None,
    as_of: Optional[date] = None,
    record_dir: Optional[Path | str] = None,
    max_staleness_ms: int = 5_000,
    contract_loader: Callable[[str, str, List[str]], List[FutureContract]] = _default_contract_loader,
    hub_factory: Callable[[str], PolledQuoteHub] = _default_hub_factory,
) -> LiveFeedSetup:
    symbols = [normalize_symbol(s) for s in (symbols or ["USDINR"])]
    providers = providers or _env_list("ARBEX_LIVE_PROVIDERS", "upstox,kite")
    exchanges = exchanges or _env_list("ARBEX_LIVE_EXCHANGES", "NSE,BSE")
    as_of = as_of or date.today()
    skipped: Dict[str, str] = {}

    for sym in [s for s in symbols if s not in EXCHANGE_FUTURES_SYMBOLS]:
        skipped[sym] = "no NSE/BSE futures for this pair"
    symbols = [s for s in symbols if s in EXCHANGE_FUTURES_SYMBOLS]

    hubs: Dict[str, PolledQuoteHub] = {}
    for p in providers:
        try:
            hubs[p] = hub_factory(p)
        except Exception as e:
            skipped[p] = str(e)

    # {symbol: {"provider:EXCHANGE": [contracts]}}
    groups: Dict[str, Dict[str, List[FutureContract]]] = {s: {} for s in symbols}
    missing: Dict[str, List[str]] = {}   # symbol -> provider:EXCHANGE groups not listing it
    for p in hubs:
        for ex in exchanges:
            try:
                contracts = contract_loader(p, ex, symbols)
            except Exception as e:
                skipped[f"{p}:{ex}"] = str(e)
                continue
            for sym in symbols:
                listed = [c for c in contracts if c.symbol == sym]
                if listed:
                    groups[sym][f"{p}:{ex}"] = listed
                else:
                    missing.setdefault(sym, []).append(f"{p}:{ex}")

    sources: List[ExchangeLegSource] = []
    for sym in symbols:
        if len(groups[sym]) < 2:
            have = ", ".join(sorted(groups[sym])) or "no venue"
            skipped[sym] = f"needs two legs; listed on {have}"
            continue
        if missing.get(sym):
            skipped[sym] = f"not listed on {', '.join(missing[sym])}"
        try:
            picked = select_common_expiry(groups[sym], as_of=as_of)
        except LookupError as e:
            skipped[sym] = str(e)
            continue
        for group, contract in sorted(picked.items()):
            provider = group.split(":")[0]
            sources.append(_leg(_leg_id("", contract), _leg_name("", contract), hubs[provider],
                                contract, len(sources) + 1, max_staleness_ms))

    if len(sources) < 2:
        raise LiveFeedUnavailable(f"need at least two live legs for some pair; skipped: {skipped}")

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
    per_symbol: Dict[str, int] = {}
    for c in contracts:
        per_symbol[c.symbol] = per_symbol.get(c.symbol, 0) + 1
    skipped = {sym: f"only {n} recorded leg" for sym, n in per_symbol.items() if n < 2}
    contracts = [c for c in contracts if c.symbol not in skipped]
    if len(contracts) < 2:
        raise LiveFeedUnavailable(f"{session_dir} has no pair with two recorded legs ({per_symbol})")
    hub = ReplayQuoteHub(session_dir, speed=speed, loop=loop)
    sources = [
        _leg(_leg_id("replay_", c), _leg_name("Replay ", c), hub, c, priority, max_staleness_ms,
             latency_ms=500.0)   # recorded at 1 Hz: keep the live latency assumption
        for priority, c in enumerate(
            sorted(contracts, key=lambda c: (c.symbol, c.provider, c.exchange)), start=1)
    ]
    return LiveFeedSetup(sources=sources, hubs={"replay": hub}, skipped=skipped, replay_dir=session_dir)


def _symbol_from_id(leg_id: str) -> str:
    tail = leg_id.rsplit("_", 1)[-1].upper()
    return tail if tail in REGISTRY else "USDINR"


def cross_spreads(quotes: Dict[str, Quote], symbols: Optional[Dict[str, str]] = None) -> List[dict]:
    """
    Pairwise executable spread between legs of the same currency pair.

    For legs A and B the executable edge is max(bid_B - ask_A, bid_A - ask_B):
    buy at one venue's ask, sell at the other's bid. Positive means a crossed
    market (arbitrage before costs); negative is the distance to one.
    One-sided books are skipped. `symbols` maps leg id -> pair; by default the
    pair is read from the id suffix (``upstox_nse_eurinr``), else USDINR.

    `executable_pips` is in the pair's pips; for INR pairs a pip is one paisa,
    which is what `executable_paise` (kept for the /live page) reports.
    """
    symbols = symbols or {}
    legs = sorted((k, q) for k, q in quotes.items() if q is not None and q.two_sided)
    rows = []
    for i, (a, qa) in enumerate(legs):
        sym = symbols.get(a) or _symbol_from_id(a)
        for b, qb in legs[i + 1:]:
            if (symbols.get(b) or _symbol_from_id(b)) != sym:
                continue
            a_to_b, b_to_a = qb.bid - qa.ask, qa.bid - qb.ask
            buy, sell, edge = (a, b, a_to_b) if a_to_b >= b_to_a else (b, a, b_to_a)
            pips = round(to_pips(edge, sym), 4)
            rows.append({
                "symbol": sym,
                "buy": buy,
                "sell": sell,
                "executable_pips": pips,
                "executable_paise": pips if sym.endswith("INR") else None,
                "crossed": edge > 0,
            })
    return rows
