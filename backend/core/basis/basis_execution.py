"""
Execution-feasibility filter for basis events (Phase 3, T2.3).

A basis trade is "buy the cheap leg, sell the rich leg" at one common expiry T*.
`BasisEvent.basis_pips` is the *touch-to-touch* executable basis
(`sell.bid - buy.ask`). To act on size you also walk down each leg's order book,
which costs extra pips. This module prices that:

  expected_slippage_pips = buy-leg book walk + sell-leg book walk
  net_basis_pips         = basis_pips - expected_slippage_pips - offshore staleness haircut
  execution_verdict      = viable / risky / unlikely   (from net_basis_pips)

The onshore leg (Upstox) has a real 5-level book; the offshore future and OTC
spot have no retail L2, so they use the assumed-depth profile in
`OFFSHORE_FEASIBILITY_CONFIG` — `assumed_depth` on the event is set True whenever
either leg fell back to that profile (honest: for onshore<->offshore it is
always True, which matches the "partial / sensitivity-only" position in the
novelty matrix).

Slippage is computed on the raw quote book; the carry factor from a near-month
future to T* is ~1.0001, so raw pips ≈ normalised pips to 4 d.p.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.config import (
    BASIS_EXECUTION_CONFIG,
    OFFSHORE_FEASIBILITY_CONFIG,
    BasisExecutionConfig,
)
from backend.core.basis.basis_event import BasisEvent

_PIP = 0.01

# leg -> which side of that leg's book you take
#   you BUY the buy-leg  -> lift its asks
#   you SELL the sell-leg -> hit its bids
_BUY_SIDE = "asks"
_SELL_SIDE = "bids"


def _profile_from_book(
    levels: List[Dict[str, float]],
    side: str,
    *,
    contract_size_usd: float,
) -> List[Tuple[float, float]]:
    """
    Real L2 -> [(price_offset_pips_from_touch, cumulative_notional_usd), ...].
    `levels` is [{"price":.., "qty":..}] in *lots*; touch = levels[0].
    """
    if not levels:
        return []
    touch = float(levels[0]["price"])
    out: List[Tuple[float, float]] = []
    cum = 0.0
    for lv in levels:
        try:
            price = float(lv["price"])
            qty = float(lv.get("qty", 0) or 0)
        except (KeyError, TypeError, ValueError):
            continue
        cum += qty * contract_size_usd
        out.append((abs(price - touch) / _PIP, cum))
    return out


def _assumed_profile() -> List[Tuple[float, float]]:
    return [(off, m * 1_000_000.0)
            for off, m in OFFSHORE_FEASIBILITY_CONFIG.assumed_depth_levels]


def slippage_pips(
    profile: List[Tuple[float, float]],
    target_notional_usd: float,
    *,
    thin_book_penalty: float,
) -> float:
    """
    Notional-weighted average price offset (in pips) to fill `target_notional_usd`
    against a cumulative depth `profile`. If the book is too thin, the unfilled
    remainder is charged at the worst level's offset x `thin_book_penalty`.
    """
    if not profile or target_notional_usd <= 0:
        return 0.0
    prev_cum = 0.0
    weighted = 0.0
    for off, cum in profile:
        take = min(cum, target_notional_usd) - prev_cum
        if take > 0:
            weighted += off * take
            prev_cum = min(cum, target_notional_usd)
        if prev_cum >= target_notional_usd:
            break
    if prev_cum < target_notional_usd:      # book exhausted
        gap = target_notional_usd - prev_cum
        weighted += profile[-1][0] * thin_book_penalty * gap
        prev_cum = target_notional_usd
    return weighted / target_notional_usd


@dataclass
class BasisExecutionAssessment:
    expected_slippage_pips: float
    execution_verdict: str
    net_basis_pips: float
    assumed_depth: bool
    buy_slippage_pips: float
    sell_slippage_pips: float
    staleness_haircut_pips: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "expected_slippage_pips": round(self.expected_slippage_pips, 3),
            "execution_verdict": self.execution_verdict,
            "net_basis_pips": round(self.net_basis_pips, 3),
            "assumed_depth": self.assumed_depth,
            "buy_slippage_pips": round(self.buy_slippage_pips, 3),
            "sell_slippage_pips": round(self.sell_slippage_pips, 3),
            "staleness_haircut_pips": round(self.staleness_haircut_pips, 3),
        }


@dataclass
class BasisExecutionFilter:
    """Prices the fill for a basis event and stamps it with a verdict."""

    config: BasisExecutionConfig = field(default_factory=lambda: BASIS_EXECUTION_CONFIG)

    def _leg_slippage(
        self, book: Optional[Dict[str, List[Dict[str, float]]]], take_side: str,
    ) -> Tuple[float, bool]:
        floor = self.config.min_leg_cost_pips
        if book and book.get(take_side):
            prof = _profile_from_book(
                book[take_side], take_side,
                contract_size_usd=self.config.contract_size_usd)
            if prof:
                walk = slippage_pips(
                    prof, self.config.target_notional_usd,
                    thin_book_penalty=self.config.thin_book_penalty)
                return max(walk, floor), False
        # no real book for this leg -> assumed profile
        walk = slippage_pips(
            _assumed_profile(), self.config.target_notional_usd,
            thin_book_penalty=self.config.thin_book_penalty)
        return max(walk, floor), True

    def assess(
        self,
        ev: BasisEvent,
        books: Optional[Dict[str, Dict[str, List[Dict[str, float]]]]] = None,
        *,
        staleness_ms: Optional[Dict[str, int]] = None,
    ) -> BasisExecutionAssessment:
        books = books or {}
        buy_slip, buy_assumed = self._leg_slippage(books.get(ev.buy_leg), _BUY_SIDE)
        sell_slip, sell_assumed = self._leg_slippage(books.get(ev.sell_leg), _SELL_SIDE)

        stale_ms = (staleness_ms or {}).get("offshore", ev.offshore_staleness_ms) or 0
        haircut = 0.0
        if "offshore" in (ev.buy_leg, ev.sell_leg):
            haircut = (stale_ms / 60_000.0) * self.config.offshore_staleness_haircut_pips_per_min

        slippage = buy_slip + sell_slip
        net = ev.basis_pips - slippage - haircut

        if net <= 0:
            verdict = "unlikely"
        elif net < self.config.min_viable_net_pips:
            verdict = "risky"
        else:
            verdict = "viable"

        return BasisExecutionAssessment(
            expected_slippage_pips=round(slippage, 3),
            execution_verdict=verdict,
            net_basis_pips=round(net, 3),
            assumed_depth=buy_assumed or sell_assumed,
            buy_slippage_pips=buy_slip,
            sell_slippage_pips=sell_slip,
            staleness_haircut_pips=haircut,
        )

    def stamp(
        self,
        ev: BasisEvent,
        books: Optional[Dict[str, Dict[str, List[Dict[str, float]]]]] = None,
        *,
        staleness_ms: Optional[Dict[str, int]] = None,
    ) -> BasisEvent:
        """assess() + write the result onto the event in place; returns it."""
        a = self.assess(ev, books, staleness_ms=staleness_ms)
        ev.expected_slippage_pips = a.expected_slippage_pips
        ev.execution_verdict = a.execution_verdict
        ev.assumed_depth = a.assumed_depth
        return ev
