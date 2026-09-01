"""
Carry-rate calibration from the live futures curve (Option A, v2).

`InstrumentNormalizer` carries every leg to the common expiry T* using an
annualised carry rate. v1 held that rate constant (`config.CARRY_RATE_ANNUAL`,
~1.9%) — a *stated assumption*. When it is wrong, the residual leaks straight
into the reported basis: e.g. if the true USD/INR forward premium is 3.8% but we
assume 1.9%, normalising the far-month NSE future back to T* removes only half
the real calendar spread and the leftover (~15 pips) is reported as a permanent
"dislocation" that is really just model error.

`CarryCalibrator` removes that assumption for any mode that has **two futures on
the same underlying at different expiries** (retail-arb: the near and far NSE
contracts). The market's own implied carry between two futures F_near @ T_near
and F_far @ T_far is

    carry_annual = (F_far / F_near - 1) * 365 / (T_far - T_near).days

which is just the annualised forward premium. Feeding that back into the
normaliser makes the near/far calendar basis collapse to ~0 *by construction* —
which is correct: you cannot detect calendar arbitrage from the very two
contracts whose ratio defines the fair carry. The calendar pair then becomes a
curve-consistency check, and the same-expiry `future_options` pair (which needs
no carry at all) is the real retail signal.

The estimate is EMA-smoothed (a single wide tick shouldn't swing it) and clamped
to a sane band; outside the band or with missing inputs it falls back to the
configured constant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

_DAYS_PER_YEAR = 365.0


@dataclass
class CarryCalibrator:
    """Live annualised carry rate inferred from a two-point futures curve."""

    fallback_annual: float
    ema_alpha: float = 0.15           # weight on each new sample
    min_annual: float = -0.03         # sanity band — reject samples outside it
    max_annual: float = 0.15
    _ema: Optional[float] = field(default=None, repr=False)
    samples: int = 0

    @staticmethod
    def implied_annual(
        near_mid: float, near_expiry: date,
        far_mid: float, far_expiry: date,
    ) -> Optional[float]:
        """Annualised forward premium between two futures. None if inputs unusable."""
        if near_mid <= 0 or far_mid <= 0:
            return None
        dt = (far_expiry - near_expiry).days
        if dt <= 0:
            return None
        return (far_mid / near_mid - 1.0) * _DAYS_PER_YEAR / dt

    def update(
        self, near_mid: float, near_expiry: date,
        far_mid: float, far_expiry: date,
    ) -> float:
        """Fold one observation into the EMA and return the current carry rate."""
        raw = self.implied_annual(near_mid, near_expiry, far_mid, far_expiry)
        if raw is None or not (self.min_annual <= raw <= self.max_annual):
            return self.current
        self._ema = raw if self._ema is None else (
            self.ema_alpha * raw + (1.0 - self.ema_alpha) * self._ema)
        self.samples += 1
        return self.current

    @property
    def current(self) -> float:
        """EMA once we have a sample, else the configured constant."""
        return self._ema if self._ema is not None else self.fallback_annual

    @property
    def source(self) -> str:
        return "calibrated" if self._ema is not None else "assumed"

    def to_dict(self) -> dict:
        return {
            "carry_rate_annual": round(self.current, 5),
            "carry_source": self.source,
            "carry_samples": self.samples,
        }
