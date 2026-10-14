"""
Exchange-listed USD/INR futures contracts: selection helpers.

Cross-exchange arbitrage (NSE vs BSE) is only meaningful when both legs are the
*same* contract — same underlying, same contract size and, crucially, the same
expiry. Comparing an NSE October future with a BSE November future would report
the calendar carry as a "dislocation". `select_common_expiry` enforces that rule:
it picks the nearest monthly expiry listed on every exchange, or refuses.

Provider-agnostic: Upstox and Kite resolvers both produce `FutureContract`s.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class FutureContract:
    exchange: str            # "NSE" | "BSE"
    instrument_key: str      # what the provider's quote API takes
    trading_symbol: str
    expiry: date
    lot_size: int
    contract_size: float     # USD per lot (1000 for USDINR)
    weekly: bool = False
    provider: str = ""

    def to_dict(self) -> dict:
        return {
            "exchange": self.exchange,
            "instrument_key": self.instrument_key,
            "trading_symbol": self.trading_symbol,
            "expiry": self.expiry.isoformat(),
            "lot_size": self.lot_size,
            "contract_size": self.contract_size,
            "weekly": self.weekly,
            "provider": self.provider,
        }


class NoCommonExpiryError(LookupError):
    """No monthly expiry is listed on every requested exchange."""


def _live(contracts: Iterable[FutureContract], as_of: date,
          min_days_to_expiry: int) -> List[FutureContract]:
    return sorted(
        (c for c in contracts if (c.expiry - as_of).days >= min_days_to_expiry),
        key=lambda c: c.expiry,
    )


def pick_near_month(
    contracts: Iterable[FutureContract],
    *,
    as_of: date,
    min_days_to_expiry: int = 2,
    prefer_monthly: bool = True,
) -> FutureContract:
    """Nearest unexpired contract; monthly preferred over weekly when present."""
    live = _live(contracts, as_of, min_days_to_expiry)
    if not live:
        raise LookupError(f"no USDINR future with >= {min_days_to_expiry} days to expiry as of {as_of}")
    if prefer_monthly:
        monthly = [c for c in live if not c.weekly]
        if monthly:
            return monthly[0]
    return live[0]


def select_common_expiry(
    by_exchange: Dict[str, Iterable[FutureContract]],
    *,
    as_of: date,
    min_days_to_expiry: int = 2,
) -> Dict[str, FutureContract]:
    """
    One contract per exchange, all with the same (nearest shared) monthly expiry.

    Raises NoCommonExpiryError if the exchanges share no live monthly expiry.
    """
    live: Dict[str, Dict[date, FutureContract]] = {}
    for exchange, contracts in by_exchange.items():
        monthly = [c for c in _live(contracts, as_of, min_days_to_expiry) if not c.weekly]
        live[exchange] = {}
        for c in monthly:
            live[exchange].setdefault(c.expiry, c)

    shared: Optional[set] = None
    for expiries in live.values():
        shared = set(expiries) if shared is None else shared & set(expiries)
    if not shared:
        detail = {ex: sorted(e.isoformat() for e in exps) for ex, exps in live.items()}
        raise NoCommonExpiryError(f"no common monthly USDINR expiry across exchanges: {detail}")

    expiry = min(shared)
    return {exchange: live[exchange][expiry] for exchange in live}


def select_best_expiry(
    by_group: Dict[str, Iterable[FutureContract]],
    *,
    as_of: date,
    min_days_to_expiry: int = 2,
) -> Tuple[Dict[str, FutureContract], Dict[str, str]]:
    """
    Like select_common_expiry, but tolerant: picks the nearest monthly expiry
    listed by the MOST groups (at least two) and returns the groups that lack
    it as {group: reason} instead of failing. One provider with a stale or
    empty instrument list (e.g. Kite's BSE list) then drops out on its own.
    """
    live: Dict[str, Dict[date, FutureContract]] = {}
    for group, contracts in by_group.items():
        live[group] = {}
        for c in _live(contracts, as_of, min_days_to_expiry):
            if not c.weekly:
                live[group].setdefault(c.expiry, c)
    counts: Dict[date, int] = {}
    for expiries in live.values():
        for e in expiries:
            counts[e] = counts.get(e, 0) + 1
    best = max(counts.values(), default=0)
    if best < 2:
        detail = {g: sorted(e.isoformat() for e in exps) for g, exps in live.items()}
        raise NoCommonExpiryError(f"no monthly expiry is listed by two sources: {detail}")
    expiry = min(e for e, n in counts.items() if n == best)
    picked = {g: exps[expiry] for g, exps in live.items() if expiry in exps}
    dropped = {g: (f"does not list the {expiry} contract" if exps else "no unexpired monthly contracts listed")
               for g, exps in live.items() if expiry not in exps}
    return picked, dropped
