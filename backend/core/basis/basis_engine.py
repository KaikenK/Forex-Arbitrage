"""
Basis dislocation detection + persistence tracking (Option A).

`BasisArbitrageEngine.detect()` takes one aligned set of normalised forwards
(`InstrumentNormalizer` output, keyed by leg) and emits `BasisEvent`s for every
enabled leg pair whose *executable* basis exceeds the threshold.

`BasisPersistenceTracker` turns a stream of detections into a persistence class:
  - mode="duration" (live tick path): seconds thresholds from config;
  - mode="count"    (EOD path): consecutive-observation thresholds
                     (1 = ephemeral, 2-3 = flickering, 4+ = persistent).

Both reuse the frozen `BasisEvent` schema so the tick and EOD tracks are
directly comparable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from backend.config import BASIS_DETECTION_CONFIG, BasisDetectionConfig
from backend.core.basis.basis_event import BasisEvent
from backend.core.normalization import InstrumentNormalizer, NormalizedForward

logger = logging.getLogger(__name__)

_PIP = 0.01
_PAIR_LEGS = {
    "onshore_offshore": ("onshore", "offshore"),
    "onshore_otc": ("onshore", "otc"),
    "offshore_otc": ("offshore", "otc"),
    # retail-arbitrage mode (all on NSE)
    "future_options": ("future", "options"),   # NSE future vs put-call-parity synthetic
    "future_far": ("future", "far"),           # calendar: near vs far month
}


@dataclass
class BasisArbitrageEngine:
    config: BasisDetectionConfig = field(default_factory=lambda: BASIS_DETECTION_CONFIG)
    _detections: int = 0

    def detect(
        self,
        forwards: Dict[str, NormalizedForward],
        *,
        now_ts: Optional[float] = None,
        target_expiry: Optional[date] = None,
        cadence: str = "tick",
        staleness_ms: Optional[Dict[str, int]] = None,
    ) -> List[BasisEvent]:
        now_ts = now_ts if now_ts is not None else time.time()
        staleness_ms = staleness_ms or {}
        t_star = (target_expiry
                  or next((f.target_expiry for f in forwards.values()), None))
        events: List[BasisEvent] = []

        for pair in self.config.enabled_leg_pairs:
            legs = _PAIR_LEGS.get(pair)
            if not legs or legs[0] not in forwards or legs[1] not in forwards:
                continue
            a, b = forwards[legs[0]], forwards[legs[1]]

            # executable basis both ways: buy at ask, sell at bid
            buy_a_sell_b = (b.bid - a.ask) / _PIP   # buy legs[0], sell legs[1]
            buy_b_sell_a = (a.bid - b.ask) / _PIP
            if buy_a_sell_b >= buy_b_sell_a:
                basis, buy_leg, sell_leg, buy_fwd, sell_fwd = (
                    buy_a_sell_b, legs[0], legs[1], a, b)
            else:
                basis, buy_leg, sell_leg, buy_fwd, sell_fwd = (
                    buy_b_sell_a, legs[1], legs[0], b, a)

            if basis < self.config.min_basis_threshold_pips:
                continue

            self._detections += 1
            events.append(BasisEvent.new(
                ts=now_ts,
                leg_pair=pair,
                buy_leg=buy_leg,
                sell_leg=sell_leg,
                buy_source_id=buy_fwd.source_id,
                sell_source_id=sell_fwd.source_id,
                basis_pips=round(basis, 3),
                comparison_basis=self.config.comparison_basis,
                target_expiry=t_star,
                carry_adjustment_pips=round(
                    abs(a.carry_adjustment_pips) + abs(b.carry_adjustment_pips), 3),
                buy_quote_ts=buy_fwd.quote_ts,
                sell_quote_ts=sell_fwd.quote_ts,
                offshore_staleness_ms=int(
                    staleness_ms.get("offshore", 0) if "offshore" in (buy_leg, sell_leg) else 0),
                raw_score=round(basis, 3),
                cadence=cadence,
            ))
        return events

    def get_stats(self) -> dict:
        return {"detections": self._detections,
                "min_basis_threshold_pips": self.config.min_basis_threshold_pips,
                "enabled_leg_pairs": list(self.config.enabled_leg_pairs)}


# session weights for USD/INR: onshore NSE hours are the liquid window
_PERSISTENCE_BONUS = {"ephemeral": 0.0, "flickering": 6.0, "persistent": 15.0}
_VERDICT_BONUS = {"viable": 12.0, "risky": 4.0, "unlikely": -6.0, "unknown": 0.0}


def score_basis_event(ev: BasisEvent) -> float:
    """
    Composite 0-100+ score for ranking a BasisEvent. Rewards a big gross basis
    and persistence / feasibility; penalises stale offshore data and the carry
    modelling adjustment (the larger it is, the less certain the basis).

        score = 8*gross_pips
              + persistence_bonus + verdict_bonus
              - 0.5*carry_adjustment_pips
              - staleness_penalty        (up to -20 as offshore data ages to 30 min)
    """
    score = 8.0 * ev.basis_pips
    score += _PERSISTENCE_BONUS.get(ev.persistence_class, 0.0)
    score += _VERDICT_BONUS.get(ev.execution_verdict, 0.0)
    score -= 0.5 * abs(ev.carry_adjustment_pips)
    if ev.offshore_staleness_ms > 0:
        score -= min(20.0, 20.0 * ev.offshore_staleness_ms / 1_800_000)
    return round(max(0.0, score), 2)


@dataclass
class _Track:
    first_ts: float
    last_ts: float
    count: int = 1
    cumulative_active: float = 0.0


class BasisPersistenceTracker:
    """Classifies a stream of detections (per leg_pair+direction) into a persistence class."""

    def __init__(
        self,
        config: Optional[BasisDetectionConfig] = None,
        *,
        mode: str = "duration",
        gap_tolerance_s: float = 2.0,
    ):
        self.config = config or BASIS_DETECTION_CONFIG
        if mode not in ("duration", "count"):
            raise ValueError("mode must be 'duration' or 'count'")
        self.mode = mode
        self.gap_tolerance_s = gap_tolerance_s
        self._active: Dict[str, _Track] = {}
        self._ended: Dict[str, str] = {}   # key -> final class (for stats)

    @staticmethod
    def _key(ev: BasisEvent) -> str:
        return f"{ev.leg_pair}|{ev.buy_leg}"

    def observe(self, ev: BasisEvent) -> str:
        """Record a detection, return the current persistence class for it."""
        k = self._key(ev)
        tr = self._active.get(k)
        if tr is None:
            self._active[k] = _Track(first_ts=ev.ts, last_ts=ev.ts)
        else:
            gap = ev.ts - tr.last_ts
            if self.mode == "duration" and gap <= self.gap_tolerance_s:
                tr.cumulative_active += gap
            tr.last_ts = ev.ts
            tr.count += 1
        return self.classify(k)

    def classify(self, key: str) -> str:
        tr = self._active.get(key)
        if tr is None:
            return "ephemeral"
        if self.mode == "count":
            if tr.count >= 4:
                return "persistent"
            return "flickering" if tr.count >= 2 else "ephemeral"
        dur = tr.cumulative_active
        if dur >= self.config.persistence_flickering_max_s:
            return "persistent"
        if dur >= self.config.persistence_ephemeral_max_s:
            return "flickering"
        return "ephemeral"

    def tick(self, now_ts: float) -> None:
        """Expire tracks with no observation within the gap tolerance (duration mode)."""
        if self.mode != "duration":
            return
        for k in list(self._active):
            if now_ts - self._active[k].last_ts > self.gap_tolerance_s * 3:
                self._ended[k] = self.classify(k)
                del self._active[k]

    def stamp(self, ev: BasisEvent) -> BasisEvent:
        ev.persistence_class = self.observe(ev)
        return ev

    def get_stats(self) -> dict:
        dist = {"ephemeral": 0, "flickering": 0, "persistent": 0}
        for k in self._active:
            dist[self.classify(k)] += 1
        for c in self._ended.values():
            dist[c] += 1
        return {"mode": self.mode, "active": len(self._active),
                "distribution": dist}
