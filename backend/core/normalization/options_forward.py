"""
Options-implied forward — put-call parity (retail-arbitrage mode).

For European, cash-settled NSE USD/INR options at expiry ``T``, put-call parity is

    C - P = DF(T) * (F_T - K)          =>    F_T = K + (C - P) / DF(T)

where C, P are the call/put prices for strike K, ``F_T`` is the USD/INR forward
for delivery at T, and ``DF(T) = 1 / (1 + r * days/365)`` discounts from expiry.

The synthetic forward is directly comparable to the NSE USD/INR **future** at the
same expiry — any persistent gap is the conversion / reversal (box) arbitrage a
retail trader can execute entirely on NSE. Feed the returned ``(bid, ask)`` into
``InstrumentNormalizer.to_common_forward(..., instrument_kind="options_forward",
quote_expiry=T)``.

Sign convention for the bid/ask:
  * synthetic **long** forward = long call + short put -> you "buy" the forward at
    ``K + (call_ask - put_bid) / DF``   -> this is the **ask**
  * synthetic **short** forward = short call + long put -> you "sell" it at
    ``K + (call_bid - put_ask) / DF``   -> this is the **bid**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

_DAYS_PER_YEAR = 365.0
DEFAULT_RATE_ANNUAL = 0.065          # INR money-market rate, ~1-month


def discount_factor(days_to_expiry: float, rate_annual: float = DEFAULT_RATE_ANNUAL) -> float:
    d = max(0.0, float(days_to_expiry))
    return 1.0 / (1.0 + rate_annual * (d / _DAYS_PER_YEAR))


def implied_forward(
    call_bid: float, call_ask: float,
    put_bid: float, put_ask: float,
    strike: float,
    *,
    days_to_expiry: float,
    rate_annual: float = DEFAULT_RATE_ANNUAL,
) -> Tuple[float, float]:
    """One strike -> (forward_bid, forward_ask). Raises on a crossed/invalid quote."""
    for name, v in (("call_bid", call_bid), ("call_ask", call_ask),
                    ("put_bid", put_bid), ("put_ask", put_ask)):
        if v is None or v < 0:
            raise ValueError(f"bad option quote {name}={v}")
    if call_ask < call_bid or put_ask < put_bid:
        raise ValueError("crossed option bid/ask")
    df = discount_factor(days_to_expiry, rate_annual)
    fwd_ask = strike + (call_ask - put_bid) / df     # price to synthetically buy
    fwd_bid = strike + (call_bid - put_ask) / df     # price to synthetically sell
    if fwd_ask < fwd_bid:                            # pathological chain -> widen symmetrically
        mid = (fwd_bid + fwd_ask) / 2.0
        fwd_bid, fwd_ask = mid - 0.005, mid + 0.005
    return fwd_bid, fwd_ask


@dataclass(frozen=True)
class ImpliedForwardQuote:
    bid: float
    ask: float
    strikes_used: Tuple[float, ...]
    days_to_expiry: float
    rate_annual: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    def to_dict(self) -> dict:
        return {
            "bid": round(self.bid, 5), "ask": round(self.ask, 5),
            "mid": round(self.mid, 5),
            "strikes_used": list(self.strikes_used),
            "days_to_expiry": round(self.days_to_expiry, 3),
            "rate_annual": self.rate_annual,
        }


# chain: {strike: {"call": {"bid": .., "ask": ..}, "put": {"bid": .., "ask": ..}}}
Chain = Dict[float, Dict[str, Dict[str, float]]]


def implied_forward_from_chain(
    chain: Chain,
    ref_price: float,
    *,
    days_to_expiry: float,
    rate_annual: float = DEFAULT_RATE_ANNUAL,
    n_strikes: int = 3,
) -> Optional[ImpliedForwardQuote]:
    """
    Average the implied forward across the ``n_strikes`` strikes nearest
    ``ref_price`` (the current future/spot). ATM strikes give the tightest
    option spreads and the cleanest parity. Returns None if no strike has a
    complete call+put quote.
    """
    usable: List[Tuple[float, float, float, float]] = []   # (dist, strike, fwd_bid, fwd_ask)
    for k, legs in chain.items():
        c, p = legs.get("call") or {}, legs.get("put") or {}
        if not all(x in c for x in ("bid", "ask")) or not all(x in p for x in ("bid", "ask")):
            continue
        try:
            fb, fa = implied_forward(
                c["bid"], c["ask"], p["bid"], p["ask"], float(k),
                days_to_expiry=days_to_expiry, rate_annual=rate_annual)
        except ValueError:
            continue
        usable.append((abs(float(k) - ref_price), float(k), fb, fa))
    if not usable:
        return None
    usable.sort(key=lambda t: t[0])
    picked = usable[:max(1, n_strikes)]
    bid = sum(fb for _, _, fb, _ in picked) / len(picked)
    ask = sum(fa for _, _, _, fa in picked) / len(picked)
    return ImpliedForwardQuote(
        bid=bid, ask=ask,
        strikes_used=tuple(sorted(k for _, k, _, _ in picked)),
        days_to_expiry=days_to_expiry, rate_annual=rate_annual,
    )
