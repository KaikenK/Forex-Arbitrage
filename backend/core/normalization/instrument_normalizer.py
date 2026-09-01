"""
Instrument Normalizer — Option A (futures <-> futures)

Converts every USD/INR leg (onshore NSE future, offshore CME/SGX future, OTC spot,
RBI reference) to a single comparable quantity: a normalised forward price at one
common target expiry ``T*`` (the NSE near-month contract's expiry). The basis
between two legs is then just the difference of their normalised forwards.

Maths — see docs/SPEC.md section 3.1. With ``carry`` = annualised INR-USD rate
differential and ``d(a, b)`` = calendar days from a to b:

    future (expiry T_q):   F* = F_q * (1 + carry * d(T_q, T*) / 365)
    spot / fix (time t):   F* = S  * (1 + carry * d(t,   T*) / 365)

``carry`` is held constant (config ``CARRY_RATE_ANNUAL``) in v1 and is flagged in
every output; v2 will infer it from the NSE forward curve.

Design Decisions:
- Stateless: every conversion is a pure function of its inputs.
- Contract expiries are supplied by the caller (the data source reads them from
  instrument metadata); this module never guesses an expiry except via the
  explicit ``month_end_expiry_estimate`` helper.
- Bid and ask receive the same multiplicative carry factor, so the spread is
  preserved in price terms and scaled only by the (tiny) carry ratio.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

PIP = 0.01  # USD/INR pip value
_DAYS_PER_YEAR = 365.0


def month_end_expiry_estimate(year: int, month: int) -> date:
    """
    Approximate NSE USD/INR futures expiry: two calendar days before the last
    day of the month. The real rule is "two working days prior to the last
    working day", which needs the exchange holiday calendar — the data source
    should pass the true expiry from instrument metadata and use this only as a
    fallback for offline tests / bootstrapping.
    """
    last_day = calendar.monthrange(year, month)[1]
    d = date(year, month, last_day).toordinal() - 2
    return date.fromordinal(d)


def _days(a: date, b: date) -> int:
    """Signed calendar days from a to b."""
    return (b - a).days


@dataclass(frozen=True)
class NormalizedForward:
    """A leg's quote expressed as a forward at the common target expiry T*."""
    leg: str                       # "onshore" | "offshore" | "otc" | "reference"
    source_id: str
    target_expiry: date
    bid: float                     # F*_bid
    ask: float                     # F*_ask
    carry_adjustment_pips: float   # (F*_mid - raw_mid) / PIP  — signed
    quote_ts: float                # epoch seconds of the underlying quote
    comparison_basis: str = "futures"

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pips(self) -> float:
        return (self.ask - self.bid) / PIP

    def to_dict(self) -> dict:
        return {
            "leg": self.leg,
            "source_id": self.source_id,
            "target_expiry": self.target_expiry.isoformat(),
            "bid": round(self.bid, 5),
            "ask": round(self.ask, 5),
            "mid": round(self.mid, 5),
            "carry_adjustment_pips": round(self.carry_adjustment_pips, 3),
            "quote_ts": self.quote_ts,
            "comparison_basis": self.comparison_basis,
        }


class InstrumentNormalizer:
    """Converts heterogeneous USD/INR quotes to a common forward at ``target_expiry``."""

    def __init__(self, target_expiry: date, carry_rate_annual: float = 0.045):
        if not isinstance(target_expiry, date):
            raise TypeError("target_expiry must be a datetime.date")
        self.target_expiry = target_expiry
        self.carry_rate_annual = float(carry_rate_annual)
        self._conversions = 0

    # -- core ----------------------------------------------------------------
    def _carry_factor(self, from_moment: date) -> float:
        """(1 + carry * days_to_T* / 365).  Can be < 1 if T* is in the past."""
        tau_days = _days(from_moment, self.target_expiry)
        return 1.0 + self.carry_rate_annual * (tau_days / _DAYS_PER_YEAR)

    def to_common_forward(
        self,
        bid: float,
        ask: float,
        *,
        leg: str,
        instrument_kind: str,
        source_id: str,
        quote_ts: float,
        quote_expiry: Optional[date] = None,
        quote_time: Optional[datetime] = None,
    ) -> NormalizedForward:
        """
        Normalise one quote to a forward at ``self.target_expiry``.

        Args:
            bid, ask: raw quote in USD/INR price terms.
            leg: leg label for provenance.
            instrument_kind: "future" (needs quote_expiry) | "spot" | "fix".
            source_id: data-source id.
            quote_ts: epoch seconds of the quote.
            quote_expiry: the contract's own expiry (required for futures).
            quote_time: the quote's wall-clock time (defaults to quote_ts;
                used for spot/fix carry).
        """
        if bid <= 0 or ask <= 0 or ask < bid:
            raise ValueError(f"invalid quote bid={bid} ask={ask}")

        # "options_forward" is a put-call-parity synthetic future (see
        # normalization/options_forward.py) — carried to T* exactly like a future.
        if instrument_kind in ("future", "options_forward"):
            if quote_expiry is None:
                raise ValueError("quote_expiry is required for a futures/options leg")
            from_moment = quote_expiry
        elif instrument_kind in ("spot", "fix"):
            if quote_time is None:
                quote_time = datetime.fromtimestamp(quote_ts, tz=timezone.utc)
            from_moment = quote_time.date()
        else:
            raise ValueError(f"unknown instrument_kind {instrument_kind!r}")
        _cmp_basis = "options_implied" if instrument_kind == "options_forward" else "futures"

        factor = self._carry_factor(from_moment)
        raw_mid = (bid + ask) / 2.0
        f_bid, f_ask = bid * factor, ask * factor
        adj_pips = ((f_bid + f_ask) / 2.0 - raw_mid) / PIP

        self._conversions += 1
        return NormalizedForward(
            leg=leg,
            source_id=source_id,
            target_expiry=self.target_expiry,
            bid=f_bid,
            ask=f_ask,
            carry_adjustment_pips=adj_pips,
            quote_ts=quote_ts,
            comparison_basis=_cmp_basis,
        )

    # -- basis & validation ------------------------------------------------
    @staticmethod
    def basis_pips(fwd_x: NormalizedForward, fwd_y: NormalizedForward) -> float:
        """Signed basis, mid-to-mid, in pips: positive => X richer than Y."""
        return (fwd_x.mid - fwd_y.mid) / PIP

    @staticmethod
    def executable_basis_pips(
        buy_leg: NormalizedForward, sell_leg: NormalizedForward
    ) -> float:
        """
        Basis actually capturable: buy at the buy leg's ask, sell at the sell
        leg's bid. Positive => a paper arbitrage exists (before costs).
        """
        return (sell_leg.bid - buy_leg.ask) / PIP

    def within_reference_band(
        self,
        fwd: NormalizedForward,
        reference_rate: float,
        band_pips: float,
        reference_time: Optional[datetime] = None,
    ) -> bool:
        """
        True if ``fwd`` sits within ``band_pips`` of the RBI reference rate
        carried forward to T* (the reference is a spot-like number).
        """
        if reference_time is None:
            reference_time = datetime.now(timezone.utc)
        ref_fwd = reference_rate * self._carry_factor(reference_time.date())
        return abs(fwd.mid - ref_fwd) / PIP <= band_pips

    # -- Option B (dashboard only) --------------------------------------
    def to_implied_spot(
        self,
        price: float,
        *,
        instrument_kind: str,
        quote_expiry: Optional[date] = None,
        quote_time: Optional[datetime] = None,
    ) -> float:
        """
        Remove the forward premium to get an implied spot: ``F / (1 + carry*tau)``.
        Secondary view for the 3-way dashboard chart only — NOT the headline basis.
        """
        if instrument_kind == "future":
            if quote_expiry is None:
                raise ValueError("quote_expiry required")
            factor = self._carry_factor(quote_expiry)
        else:
            when = (quote_time or datetime.now(timezone.utc)).date()
            factor = self._carry_factor(when)
        return price / factor if factor else price

    def get_stats(self) -> dict:
        return {
            "target_expiry": self.target_expiry.isoformat(),
            "carry_rate_annual": self.carry_rate_annual,
            "conversions": self._conversions,
        }
