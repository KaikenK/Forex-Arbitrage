"""
EOD onshore-offshore USD/INR basis (Option A).

For each trading date on which >= 2 legs have a bar, convert every leg's daily
mark to a normalised forward at that day's target expiry T* (the onshore
near-month contract's expiry, or an estimate), then compute the pairwise basis.

This reuses `InstrumentNormalizer` verbatim — the same maths the live
`BasisPipeline` runs — so the EOD track and the eventual tick track produce
comparable numbers and identical output schema (daily vs sub-second granularity).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date
from statistics import median
from pathlib import Path
from typing import Dict, List, Optional

from backend.config import BASIS_DETECTION_CONFIG, CARRY_RATE_ANNUAL
from backend.core.basis.basis_execution import BasisExecutionFilter
from backend.core.basis.basis_engine import (
    BasisArbitrageEngine,
    BasisPersistenceTracker,
    score_basis_event,
)
from backend.core.basis.basis_event import BasisEvent
from backend.core.data_sources.eod_loader import EODBar
from backend.core.normalization import (
    CarryCalibrator,
    InstrumentNormalizer,
    month_end_expiry_estimate,
)

# carry outside this annualised band is rejected as a bad mark (same as the live
# CarryCalibrator default); falls back to the assumed constant that day.
_CARRY_MIN, _CARRY_MAX = -0.03, 0.15

logger = logging.getLogger(__name__)

_RESULTS_EOD = Path(__file__).resolve().parents[3] / "research" / "results" / "eod"
_PIP = 0.01
_LEG_ORDER = ("onshore", "offshore", "spot")


@dataclass
class EODBasisRow:
    trade_date: date
    target_expiry: date
    carry_rate_annual: float
    forwards: Dict[str, dict]           # leg -> NormalizedForward.to_dict()
    basis_pips: Dict[str, float]        # "onshore_offshore" -> pips
    carry_adjustment_pips: Dict[str, float]
    carry_source: str = "assumed"      # "calibrated" (from F_onshore/S) | "assumed"

    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date.isoformat(),
            "target_expiry": self.target_expiry.isoformat(),
            "carry_rate_annual": self.carry_rate_annual,
            "carry_source": self.carry_source,
            "forwards": self.forwards,
            "basis_pips": self.basis_pips,
            "carry_adjustment_pips": self.carry_adjustment_pips,
        }


_LEG_TO_ENGINE = {"onshore": "onshore", "offshore": "offshore", "spot": "otc"}


@dataclass
class EODBasisRunner:
    carry_rate_annual: float = CARRY_RATE_ANNUAL   # fallback only when calibrate_carry
    calibrate_carry: bool = True                   # infer carry per day from F_onshore/S
    rows: List[EODBasisRow] = field(default_factory=list)
    events: List[BasisEvent] = field(default_factory=list)
    _exec_filter: BasisExecutionFilter = field(default_factory=BasisExecutionFilter, repr=False)

    def run(
        self,
        onshore: List[EODBar],
        offshore: List[EODBar],
        spot: List[EODBar],
        reference: Optional[List[EODBar]] = None,
    ) -> List[EODBasisRow]:
        engine = BasisArbitrageEngine(config=BASIS_DETECTION_CONFIG)
        tracker = BasisPersistenceTracker(BASIS_DETECTION_CONFIG, mode="count")
        self.events = []
        by_leg = {
            "onshore": {b.trade_date: b for b in onshore},
            "offshore": {b.trade_date: b for b in offshore},
            "spot": {b.trade_date: b for b in spot},
        }
        ref_by_date = {b.trade_date: b for b in (reference or [])}

        all_dates = sorted(set().union(*(d.keys() for d in by_leg.values())))

        # -- carry calibration (Option A v2) -------------------------------
        # The onshore near future and same-day spot pin the market's own forward
        # premium: (F_onshore/S - 1)*365/days. Without it, a spot-involving pair
        # is carried the full ~1 month at an assumed rate and the (assumed-true)
        # gap (~8-15 pips) sits in the reported basis as pure model error.
        # onshore<->offshore is unaffected (both futures at T*, carry cancels).
        #
        # Near expiry the annualisation is unstable, so only days with >= 10 days
        # to T* seed the estimate; the median of those becomes the carry for the
        # remaining days (>> a flat 1.9% guess). Front-month can be an NSE weekly.
        seeds = []
        for d in all_dates:
            on, sp = by_leg["onshore"].get(d), by_leg["spot"].get(d)
            if not (on and sp):
                continue
            tx = self._target_expiry(on, d)
            if (tx - d).days < 10:
                continue
            imp = CarryCalibrator.implied_annual(sp.mark, d, on.mark, tx)
            if imp is not None and _CARRY_MIN <= imp <= _CARRY_MAX:
                seeds.append(imp)
        carry_floor = (median(seeds) if seeds and self.calibrate_carry
                       else self.carry_rate_annual)
        self._carry_seeds = len(seeds)

        self.rows = []
        for d in all_dates:
            present = {leg: by_leg[leg][d] for leg in _LEG_ORDER if d in by_leg[leg]}
            if len(present) < 2:
                continue
            t_star = self._target_expiry(present.get("onshore"), d)

            day_carry, carry_source = self.carry_rate_annual, "assumed"
            if self.calibrate_carry:
                day_carry, carry_source = carry_floor, "calibrated_pooled"
                if "onshore" in present and "spot" in present and (t_star - d).days >= 10:
                    implied = CarryCalibrator.implied_annual(
                        present["spot"].mark, d, present["onshore"].mark, t_star)
                    if implied is not None and _CARRY_MIN <= implied <= _CARRY_MAX:
                        day_carry, carry_source = implied, "calibrated"

            norm = InstrumentNormalizer(target_expiry=t_star,
                                        carry_rate_annual=day_carry)

            forwards, adj, fwd_objs = {}, {}, {}
            for leg, bar in present.items():
                kind = "future" if leg in ("onshore", "offshore") else "spot"
                # onshore: its own contract expiry (or T*). offshore: continuous
                # series has no expiry, so assume it is also the near-month ~ T*
                # (this makes the carry adjustment cancel for the onshore<->offshore
                # pair, per SPEC 3.1). spot: no expiry.
                if leg == "onshore":
                    q_expiry = bar.expiry or t_star
                elif leg == "offshore":
                    q_expiry = bar.expiry or t_star
                else:
                    q_expiry = None
                try:
                    fwd = norm.to_common_forward(
                        bar.mark - _PIP / 2, bar.mark + _PIP / 2,
                        leg=leg, instrument_kind=kind, source_id=bar.source,
                        quote_ts=_epoch(d), quote_expiry=q_expiry,
                        quote_time=_dt(d),
                    )
                except ValueError as e:
                    logger.debug("skip %s %s: %s", d, leg, e)
                    continue
                forwards[leg] = fwd.to_dict()
                fwd_objs[_LEG_TO_ENGINE[leg]] = fwd
                adj[leg] = round(fwd.carry_adjustment_pips, 3)

            if len(forwards) < 2:
                continue

            legs = [lg for lg in _LEG_ORDER if lg in forwards]
            pairs = {}
            for i, a in enumerate(legs):
                for b in legs[i + 1:]:
                    pairs[f"{a}_{b}"] = round(
                        (forwards[a]["mid"] - forwards[b]["mid"]) / _PIP, 3
                    )
            self.rows.append(EODBasisRow(
                trade_date=d, target_expiry=t_star,
                carry_rate_annual=round(day_carry, 5), carry_source=carry_source,
                forwards=forwards, basis_pips=pairs, carry_adjustment_pips=adj,
            ))

            # dislocation events for this trading day
            for ev in engine.detect(fwd_objs, now_ts=_epoch(d),
                                    target_expiry=t_star, cadence="eod"):
                ev.persistence_class = tracker.observe(ev)
                # EOD has no order book -> assumed-depth verdict (both legs assumed)
                self._exec_filter.stamp(ev, {})
                ev.composite_score = score_basis_event(ev)
                self.events.append(ev)
        logger.info("[eod_basis] %d dated rows", len(self.rows))
        return self.rows

    # -- expiry helpers --------------------------------------------------
    @staticmethod
    def _target_expiry(onshore_bar: Optional[EODBar], d: date) -> date:
        if onshore_bar and onshore_bar.expiry:
            return onshore_bar.expiry
        exp = month_end_expiry_estimate(d.year, d.month)
        if (exp - d).days < 3:  # rolled
            y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
            exp = month_end_expiry_estimate(y, m)
        return exp

    # -- output --------------------------------------------------------
    def write(self, run_id: str = "eod") -> Path:
        outdir = _RESULTS_EOD / run_id
        outdir.mkdir(parents=True, exist_ok=True)
        with (outdir / "basis_eod.jsonl").open("w", encoding="utf-8") as fh:
            for r in self.rows:
                fh.write(json.dumps(r.to_dict()) + "\n")
        with (outdir / "events.jsonl").open("w", encoding="utf-8") as fh:
            for ev in self.events:
                fh.write(json.dumps(ev.to_dict()) + "\n")
        (outdir / "summary.json").write_text(json.dumps(self.summary(), indent=2))
        return outdir

    def summary(self) -> dict:
        if not self.rows:
            return {"rows": 0}
        series = {p: [] for p in ("onshore_offshore", "onshore_spot", "offshore_spot")}
        for r in self.rows:
            for p, v in r.basis_pips.items():
                series.setdefault(p, []).append(v)
        stats = {}
        for p, xs in series.items():
            if not xs:
                continue
            xs_sorted = sorted(xs)
            stats[p] = {
                "n": len(xs),
                "mean_pips": round(sum(xs) / len(xs), 2),
                "abs_mean_pips": round(sum(abs(x) for x in xs) / len(xs), 2),
                "min_pips": round(xs_sorted[0], 2),
                "max_pips": round(xs_sorted[-1], 2),
                "p90_abs_pips": round(sorted(abs(x) for x in xs)[int(0.9 * len(xs))], 2),
            }
        ev_by_pair: dict = {}
        persistence = {"ephemeral": 0, "flickering": 0, "persistent": 0}
        verdicts = {"viable": 0, "risky": 0, "unlikely": 0, "unknown": 0}
        for ev in self.events:
            ev_by_pair[ev.leg_pair] = ev_by_pair.get(ev.leg_pair, 0) + 1
            persistence[ev.persistence_class] = persistence.get(ev.persistence_class, 0) + 1
            verdicts[ev.execution_verdict] = verdicts.get(ev.execution_verdict, 0) + 1

        src = {"calibrated": 0, "calibrated_pooled": 0, "assumed": 0}
        for r in self.rows:
            src[r.carry_source] = src.get(r.carry_source, 0) + 1
        per_day = [r.carry_rate_annual for r in self.rows if r.carry_source == "calibrated"]

        return {
            "rows": len(self.rows),
            "date_range": [self.rows[0].trade_date.isoformat(),
                           self.rows[-1].trade_date.isoformat()],
            "carry": {
                "mode": "calibrated" if self.calibrate_carry else "assumed_flat",
                "assumed_fallback_annual": self.carry_rate_annual,
                "seed_days": getattr(self, "_carry_seeds", 0),
                "by_source": src,
                "per_day_mean_annual": (round(sum(per_day) / len(per_day), 4)
                                        if per_day else None),
                "pooled_annual": (round(median([r.carry_rate_annual for r in self.rows
                                                if r.carry_source == "calibrated_pooled"]), 4)
                                  if src["calibrated_pooled"] else None),
            },
            "min_basis_threshold_pips": BASIS_DETECTION_CONFIG.min_basis_threshold_pips,
            "basis_stats_pips": stats,
            "dislocation_events": {
                "total": len(self.events),
                "days_with_an_event": len({ev.ts for ev in self.events}),
                "by_leg_pair": ev_by_pair,
                "execution_verdict": verdicts,
                "persistence": persistence,
                "persistence_note": (
                    "count-mode on daily bars: 'persistent' = basis present 4+ "
                    "consecutive trading days. NOT comparable to the intraday "
                    "duration-mode classes; treat execution_verdict as the headline."),
            },
        }


def _epoch(d: date) -> float:
    from datetime import datetime, time, timezone
    return datetime.combine(d, time(12, 0), tzinfo=timezone.utc).timestamp()


def _dt(d: date):
    from datetime import datetime, time, timezone
    return datetime.combine(d, time(12, 0), tzinfo=timezone.utc)
