"""
Basis pipeline assembly (Phase 3, DataMode.LIVE_USDINR_BASIS).

Wires the three real USD/INR legs (onshore Dhan future, offshore delayed future,
OTC spot) through the InstrumentNormalizer and records the normalised forwards +
pairwise basis to ``research/results/raw/basis_<date>.jsonl``.

This is the P1 deliverable — it proves the feeds + Option-A normalisation work
end-to-end and gives immediate visibility into the real basis. The full
BasisArbitrageEngine / tracker / execution-filter chain is P2 and plugs in where
``_on_snapshot`` currently just records.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import (
    BASIS_DETECTION_CONFIG,
    BASIS_LEGS,
)
from backend.core.basis.basis_engine import (
    BasisArbitrageEngine,
    BasisPersistenceTracker,
    score_basis_event,
)
from backend.core.basis.basis_execution import BasisExecutionFilter
from backend.core.basis.basis_event import BasisEvent, RAW_STREAM
from backend.core.data_sources.cme_delayed_source import CMEDelayedSource
from backend.core.data_sources.dhan_data_source import DhanDataSource
from backend.core.data_sources.rest_data_source import RESTDataSource, RESTSourceConfig
from backend.core.interfaces.data_source import DataSourceConfig, DataSourceInterface
from backend.core.normalization import (
    CarryCalibrator,
    InstrumentNormalizer,
    month_end_expiry_estimate,
)
from backend.core.redis_client import redis_client

logger = logging.getLogger(__name__)

_RESULTS_RAW = Path(__file__).resolve().parents[3] / "research" / "results" / "raw"
_PIP = 0.01


def _leg_cfg(leg: str) -> DataSourceConfig:
    b = next(x for x in BASIS_LEGS if x.leg == leg)
    return DataSourceConfig(
        source_id=b.source_id,
        source_type=f"basis_{leg}",
        display_name=b.display_name,
        latency_estimate_ms=b.latency_estimate_ms,
        reliability_score=b.reliability_score,
        symbols=["USDINR"],
        extra_config={"leg": leg, "instrument_kind": b.instrument_kind},
    )


def _default_target_expiry() -> date:
    """NSE near-month expiry estimate (data source overrides with the real one)."""
    now = datetime.now(timezone.utc).date()
    exp = month_end_expiry_estimate(now.year, now.month)
    if (exp - now).days < 5:  # rolled or near-expiry (illiquid) — use next month
        y, m = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
        exp = month_end_expiry_estimate(y, m)
    return exp


@dataclass
class BasisPipeline:
    sources: List[DataSourceInterface]
    normalizer: InstrumentNormalizer
    poll_interval_s: float = 1.0
    ws_manager: Optional[Any] = None
    legs: tuple = ("onshore", "offshore", "otc")   # which legs to compare, in order
    log_prefix: str = "basis"                      # research/results/raw/<prefix>_<day>.jsonl
    carry_calibrator: Optional[CarryCalibrator] = None  # infer carry from near/far curve
    _running: bool = field(default=False, repr=False)
    _out: Optional[Any] = field(default=None, repr=False)
    _snapshots: int = 0
    _events_out: Optional[Any] = field(default=None, repr=False)
    engine: BasisArbitrageEngine = field(
        default_factory=lambda: BasisArbitrageEngine(BASIS_DETECTION_CONFIG))
    tracker: BasisPersistenceTracker = field(
        default_factory=lambda: BasisPersistenceTracker(BASIS_DETECTION_CONFIG, mode="duration"))
    exec_filter: BasisExecutionFilter = field(default_factory=BasisExecutionFilter)
    _events_detected: int = 0

    def _open_log(self):
        _RESULTS_RAW.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        return open(_RESULTS_RAW / f"{self.log_prefix}_{day}.jsonl", "a", encoding="utf-8")

    async def run(self) -> None:
        self._running = True
        for s in self.sources:
            if not s.is_connected:
                s.connect()
        self._out = self._open_log()
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        self._events_out = open(
            _RESULTS_RAW / f"{self.log_prefix}_events_{day}.jsonl", "a", encoding="utf-8")
        logger.info("[basis_pipeline] recording to %s", self._out.name)
        if self.ws_manager:
            await self.ws_manager.broadcast_basis("basis_meta", {
                "target_expiry": self.normalizer.target_expiry.isoformat(),
                "carry_rate_annual": self.normalizer.carry_rate_annual,
                "mode": "live", "legs": list(self.legs), "track": self.log_prefix,
            })
        try:
            while self._running:
                snapshot, events = await asyncio.to_thread(self._tick_once)
                if snapshot and self.ws_manager:
                    await self.ws_manager.broadcast_basis("basis_snapshot", snapshot)
                for ev in events or ():
                    self._events_out.write(json.dumps(ev.to_dict()) + "\n")
                    await redis_client.xadd(RAW_STREAM, ev.to_stream_fields())
                    if self.ws_manager:
                        await self.ws_manager.broadcast_basis("basis_event", ev.to_dict())
                if events:
                    self._events_out.flush()
                self.tracker.tick(time.time())
                await asyncio.sleep(self.poll_interval_s)
        except asyncio.CancelledError:
            pass
        finally:
            if self._out:
                self._out.close()
            if self._events_out:
                self._events_out.close()
            for s in self.sources:
                try:
                    s.disconnect()
                except Exception:
                    pass

    def _tick_once(self):
        """Returns (snapshot_dict_or_None, list_of_events)."""
        now = time.time()
        now_ms = now * 1000.0
        cfg = self.engine.config
        raw_legs: Dict[str, Dict[str, Any]] = {}
        books: Dict[str, Any] = {}

        # -- pass 1: collect raw quotes ----------------------------------
        for s in self.sources:
            raw = s.get_tick("USDINR")
            if raw is None:
                continue
            extra = raw.extra or {}
            leg = extra.get(
                "leg",
                s.source_type.replace("basis_", "").replace("retail_", ""))
            kind = extra.get("instrument_kind", "spot")
            q_expiry = None
            if extra.get("expiry"):
                try:
                    q_expiry = date.fromisoformat(extra["expiry"][:10])
                except ValueError:
                    q_expiry = None
            if kind in ("future", "options_forward") and q_expiry is None:
                q_expiry = self.normalizer.target_expiry  # best effort
            staleness = int(extra.get("staleness_ms")
                            or max(0.0, now_ms - raw.timestamp_ms))
            raw_legs[leg] = {
                "bid": raw.bid, "ask": raw.ask, "kind": kind, "expiry": q_expiry,
                "source_id": s.source_id, "quote_ts": raw.timestamp_ms / 1000.0,
                "staleness_ms": staleness,
            }
            get_depth = getattr(s, "get_depth", None)
            if callable(get_depth):
                try:
                    book = get_depth("USDINR")
                except Exception:
                    book = None
                if book and book.get("bids") and book.get("asks"):
                    books[leg] = book

        # -- carry calibration from the near/far futures curve ----------
        # The two NSE contracts define the fair carry; inferring it here makes
        # the near/far calendar basis collapse to ~0 unless a contract is
        # genuinely mispriced, instead of reporting the (assumed - true) carry
        # gap as a permanent phantom dislocation.
        if self.carry_calibrator is not None:
            near = raw_legs.get("future") or raw_legs.get("onshore")
            far = raw_legs.get("far")
            spot = raw_legs.get("otc")
            if near and far and near["expiry"] and far["expiry"]:
                # retail-arb: near vs far NSE future defines the fair carry
                self.normalizer.carry_rate_annual = self.carry_calibrator.update(
                    (near["bid"] + near["ask"]) / 2.0, near["expiry"],
                    (far["bid"] + far["ask"]) / 2.0, far["expiry"],
                )
            elif near and near["expiry"] and spot:
                # research: onshore near future vs same-day spot -> (F/S - 1)*365/days.
                # Without this, the spot leg is carried a full month at an assumed
                # rate and the (assumed - true) gap sits in the reported basis.
                self.normalizer.carry_rate_annual = self.carry_calibrator.update(
                    (spot["bid"] + spot["ask"]) / 2.0,
                    datetime.now(timezone.utc).date(),
                    (near["bid"] + near["ask"]) / 2.0, near["expiry"],
                )
            carry_meta = self.carry_calibrator.to_dict()
        else:
            carry_meta = {
                "carry_rate_annual": round(self.normalizer.carry_rate_annual, 5),
                "carry_source": "assumed", "carry_samples": 0,
            }

        # -- pass 2: validity gates + normalise ------------------------
        forwards: Dict[str, Any] = {}
        suppressed: Dict[str, str] = {}
        for leg, q in raw_legs.items():
            spread_pips = (q["ask"] - q["bid"]) / _PIP
            if spread_pips <= 0.0:
                suppressed[leg] = "degenerate quote (LTP fallback — not trading)"
                continue
            if spread_pips > cfg.max_leg_spread_pips:
                suppressed[leg] = (f"spread {spread_pips:.1f}p > "
                                   f"{cfg.max_leg_spread_pips:.0f}p gate")
                continue
            if q["staleness_ms"] > cfg.max_leg_staleness_ms:
                suppressed[leg] = f"feed stale {q['staleness_ms'] // 1000}s"
                continue
            try:
                fwd = self.normalizer.to_common_forward(
                    q["bid"], q["ask"], leg=leg, instrument_kind=q["kind"],
                    source_id=q["source_id"], quote_ts=q["quote_ts"],
                    quote_expiry=q["expiry"],
                    quote_time=datetime.fromtimestamp(q["quote_ts"], tz=timezone.utc),
                )
            except ValueError as e:
                suppressed[leg] = f"normalise failed: {e}"
                continue
            forwards[leg] = {"fwd": fwd, "staleness_ms": q["staleness_ms"]}

        if not forwards:
            return None, []

        pairs = {}
        legs = [lg for lg in self.legs if lg in forwards]
        for i, a in enumerate(legs):
            for b in legs[i + 1:]:
                pairs[f"{a}_{b}"] = round(
                    InstrumentNormalizer.basis_pips(forwards[a]["fwd"], forwards[b]["fwd"]), 3
                )

        partial = len(forwards) < 2
        row = {
            "ts": now,
            "target_expiry": self.normalizer.target_expiry.isoformat(),
            "carry_rate_annual": carry_meta["carry_rate_annual"],
            "carry_source": carry_meta["carry_source"],
            "forwards": {lg: forwards[lg]["fwd"].to_dict() for lg in forwards},
            "basis_pips": pairs,
            "staleness_ms": {lg: forwards[lg]["staleness_ms"] for lg in forwards},
            "real_depth_legs": sorted(books.keys()),
            "suppressed_legs": suppressed,
            "partial": partial,          # < 2 legs live -> forwards render, no basis/events
        }
        self._out.write(json.dumps(row) + "\n")
        self._out.flush()
        self._snapshots += 1

        if partial:
            if self._snapshots % 60 == 0:
                logger.info("[basis_pipeline] %d partial snapshots; live legs=%s "
                            "suppressed=%s", self._snapshots, legs, suppressed)
            return row, []

        # dislocation detection + persistence stamping
        fwd_objs = {lg: forwards[lg]["fwd"] for lg in forwards}
        staleness = {lg: forwards[lg]["staleness_ms"] for lg in forwards}
        events = self.engine.detect(
            fwd_objs, now_ts=now, target_expiry=self.normalizer.target_expiry,
            cadence="tick", staleness_ms=staleness,
        )
        for ev in events:
            ev.persistence_class = self.tracker.observe(ev)
            self.exec_filter.stamp(ev, books, staleness_ms=staleness)
            ev.composite_score = score_basis_event(ev)   # now sees a real verdict
            self._events_detected += 1

        if self._snapshots % 60 == 0:
            logger.info("[basis_pipeline] %d snapshots, %d events; basis=%s "
                        "carry=%s(%s) suppressed=%s",
                        self._snapshots, self._events_detected, pairs,
                        carry_meta["carry_rate_annual"], carry_meta["carry_source"],
                        suppressed or "-")
        return row, events

    def stop(self) -> None:
        self._running = False


def _build_onshore_source() -> DataSourceInterface:
    """
    Onshore NSE USD/INR future. Broker is selected by ARBEX_ONSHORE_BROKER
    (default 'upstox' — free market data, current instrument master). 'dhan'
    keeps the DhanDataSource path (needs a paid Dhan Data API subscription).
    """
    broker = os.environ.get("ARBEX_ONSHORE_BROKER", "upstox").lower()
    cfg = _leg_cfg("onshore")
    if broker == "dhan":
        return DhanDataSource(cfg)
    from backend.core.data_sources.upstox_data_source import UpstoxDataSource
    return UpstoxDataSource(cfg)


def build_basis_pipeline() -> BasisPipeline:
    sources: List[DataSourceInterface] = [
        _build_onshore_source(),
        CMEDelayedSource(_leg_cfg("offshore"), fetcher=_env_offshore_fetcher()),
        _build_otc_source(),
    ]
    normalizer = InstrumentNormalizer(
        target_expiry=_default_target_expiry(),
        carry_rate_annual=BASIS_DETECTION_CONFIG.carry_rate_annual,
    )
    calibrator = (CarryCalibrator(fallback_annual=BASIS_DETECTION_CONFIG.carry_rate_annual)
                  if BASIS_DETECTION_CONFIG.calibrate_carry_from_curve else None)
    return BasisPipeline(sources=sources, normalizer=normalizer,
                         carry_calibrator=calibrator)


def build_retail_arb_pipeline() -> BasisPipeline:
    """
    Retail-arbitrage mode — all legs on NSE, all retail-tradable:
      future  : near-month USD/INR future (Upstox, real 5-level book)
      options : put-call-parity synthetic forward from the near-month chain
      far     : far-month future (calendar comparison)
    Compared as `future_options` and `future_far` by `BasisArbitrageEngine`.
    """
    from backend.config import RETAIL_ARB_CONFIG
    from backend.core.data_sources.upstox_data_source import UpstoxDataSource
    from backend.core.data_sources.upstox_options_source import UpstoxOptionsSource

    def _cfg(leg: str, sid: str) -> DataSourceConfig:
        return DataSourceConfig(source_id=sid, source_type=f"retail_{leg}",
                                display_name=f"NSE USD/INR {leg}", symbols=["USDINR"],
                                extra_config={"leg": leg})

    sources: List[DataSourceInterface] = [
        UpstoxDataSource(_cfg("future", "nse_usdinr_fut"), leg="future", month_offset=0),
        UpstoxOptionsSource(_cfg("options", "nse_usdinr_opt")),
        UpstoxDataSource(_cfg("far", "nse_usdinr_fut_far"), leg="far", month_offset=1),
    ]
    normalizer = InstrumentNormalizer(
        target_expiry=_default_target_expiry(),
        carry_rate_annual=RETAIL_ARB_CONFIG.carry_rate_annual,
    )
    calibrator = (CarryCalibrator(fallback_annual=RETAIL_ARB_CONFIG.carry_rate_annual)
                  if RETAIL_ARB_CONFIG.calibrate_carry_from_curve else None)
    return BasisPipeline(
        sources=sources, normalizer=normalizer,
        legs=("future", "options", "far"), log_prefix="retail",
        carry_calibrator=calibrator,
        engine=BasisArbitrageEngine(RETAIL_ARB_CONFIG),
        tracker=BasisPersistenceTracker(RETAIL_ARB_CONFIG, mode="duration"),
    )


def _build_otc_source() -> DataSourceInterface:
    """
    OTC USD/INR spot. Default: `OtcSpotSource` (free, key-less — Yahoo intraday
    with a Frankfurter daily fallback). `OTC_USDINR_BASE_URL` set -> fall back to
    the generic `RESTDataSource` against that endpoint.
    """
    cfg = _leg_cfg("otc")
    if os.environ.get("OTC_USDINR_BASE_URL"):
        rest_cfg = RESTSourceConfig(
            base_url=os.environ["OTC_USDINR_BASE_URL"],
            endpoint_template=os.environ.get(
                "OTC_USDINR_ENDPOINT", "/latest?base=USD&symbols=INR"),
            api_key=os.environ.get("OTC_USDINR_API_KEY"),
            response_parser=os.environ.get("OTC_USDINR_PARSER", "exchangerate"),
            poll_interval_ms=2000,
        )
        return RESTDataSource(cfg, rest_cfg)
    from backend.core.data_sources.otc_spot_source import OtcSpotSource
    return OtcSpotSource(cfg)


_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
_YF_SIR = "https://query1.finance.yahoo.com/v8/finance/chart/SIR=F?interval=1m&range=1d"


def _yahoo_offshore_fetcher(spread_pips: float = 3.0):
    """
    Free offshore leg: CME Indian Rupee/USD future via Yahoo (`SIR=F`), quoted
    `1/USDINR x 10000` (~104.9) and ~10 min delayed. No key. Converted to USD/INR
    on the fly; a synthetic bid/ask (`spread_pips`) is applied since Yahoo gives
    no book. `quote_time` from the feed -> `CMEDelayedSource` computes staleness.
    """
    import urllib.request

    def _fetch():
        try:
            req = urllib.request.Request(_YF_SIR, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                d = json.loads(resp.read() or b"{}")
            m = d["chart"]["result"][0]["meta"]
            raw = m.get("regularMarketPrice")
        except Exception as e:
            logger.debug("[offshore] yahoo SIR=F fetch failed: %s", e)
            return None
        if not raw:
            return None
        mid = 10000.0 / float(raw)                     # -> USD/INR
        half = (spread_pips * _PIP) / 2.0
        qt = datetime.fromtimestamp(m.get("regularMarketTime", 0) or time.time(),
                                    tz=timezone.utc)
        return {
            "bid": round(mid - half, 5), "ask": round(mid + half, 5),
            "last": round(mid, 5),
            "expiry": _default_target_expiry().isoformat(),  # CME near-month ~ NSE T*
            "quote_time": qt,
        }

    return _fetch


def _env_offshore_fetcher():
    """
    `CME_USDINR_URL` set -> a generic delayed-JSON fetcher against it.
    Otherwise -> the free Yahoo `SIR=F` fetcher (unless ARBEX_OFFSHORE=off).
    """
    url = os.environ.get("CME_USDINR_URL")
    if url:
        import urllib.request

        def _fetch():
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            return {
                "bid": float(data["bid"]), "ask": float(data["ask"]),
                "last": data.get("last"), "expiry": data.get("expiry"),
                "quote_time": datetime.fromisoformat(data["quote_time"])
                if data.get("quote_time") else datetime.now(timezone.utc),
            }
        return _fetch

    if os.environ.get("ARBEX_OFFSHORE", "yahoo").lower() in ("off", "none", ""):
        return None
    return _yahoo_offshore_fetcher()
