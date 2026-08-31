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
    CARRY_RATE_ANNUAL,
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
from backend.core.normalization import InstrumentNormalizer, month_end_expiry_estimate
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
        return open(_RESULTS_RAW / f"basis_{day}.jsonl", "a", encoding="utf-8")

    async def run(self) -> None:
        self._running = True
        for s in self.sources:
            if not s.is_connected:
                s.connect()
        self._out = self._open_log()
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        self._events_out = open(_RESULTS_RAW / f"events_{day}.jsonl", "a", encoding="utf-8")
        logger.info("[basis_pipeline] recording to %s", self._out.name)
        if self.ws_manager:
            await self.ws_manager.broadcast_basis("basis_meta", {
                "target_expiry": self.normalizer.target_expiry.isoformat(),
                "carry_rate_annual": CARRY_RATE_ANNUAL, "mode": "live",
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
        forwards: Dict[str, Any] = {}
        books: Dict[str, Any] = {}
        for s in self.sources:
            raw = s.get_tick("USDINR")
            if raw is None:
                continue
            extra = raw.extra or {}
            leg = extra.get("leg", s.source_type.replace("basis_", ""))
            kind = extra.get("instrument_kind", "spot")
            q_expiry = None
            if extra.get("expiry"):
                try:
                    q_expiry = date.fromisoformat(extra["expiry"][:10])
                except ValueError:
                    q_expiry = None
            if kind == "future" and q_expiry is None:
                q_expiry = self.normalizer.target_expiry  # best effort
            try:
                fwd = self.normalizer.to_common_forward(
                    raw.bid, raw.ask,
                    leg=leg, instrument_kind=kind, source_id=s.source_id,
                    quote_ts=raw.timestamp_ms / 1000.0,
                    quote_expiry=q_expiry,
                    quote_time=datetime.fromtimestamp(raw.timestamp_ms / 1000.0, tz=timezone.utc),
                )
            except ValueError as e:
                logger.debug("[basis_pipeline] skip %s: %s", s.source_id, e)
                continue
            forwards[leg] = {"fwd": fwd, "staleness_ms": extra.get("staleness_ms", 0)}
            get_depth = getattr(s, "get_depth", None)
            if callable(get_depth):
                try:
                    book = get_depth("USDINR")
                except Exception:
                    book = None
                if book and book.get("bids") and book.get("asks"):
                    books[leg] = book

        if not forwards:
            return None, []

        pairs = {}
        legs = [lg for lg in ("onshore", "offshore", "otc") if lg in forwards]
        for i, a in enumerate(legs):
            for b in legs[i + 1:]:
                pairs[f"{a}_{b}"] = round(
                    InstrumentNormalizer.basis_pips(forwards[a]["fwd"], forwards[b]["fwd"]), 3
                )

        now = time.time()
        partial = len(forwards) < 2
        row = {
            "ts": now,
            "target_expiry": self.normalizer.target_expiry.isoformat(),
            "carry_rate_annual": CARRY_RATE_ANNUAL,
            "forwards": {lg: forwards[lg]["fwd"].to_dict() for lg in forwards},
            "basis_pips": pairs,
            "staleness_ms": {lg: forwards[lg]["staleness_ms"] for lg in forwards},
            "real_depth_legs": sorted(books.keys()),
            "partial": partial,          # < 2 legs live -> forwards render, no basis/events
        }
        self._out.write(json.dumps(row) + "\n")
        self._out.flush()
        self._snapshots += 1

        if partial:
            if self._snapshots % 60 == 0:
                logger.info("[basis_pipeline] %d partial snapshots; live legs=%s "
                            "(need offshore + otc for a basis)", self._snapshots, legs)
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
            logger.info("[basis_pipeline] %d snapshots, %d events; last basis=%s",
                        self._snapshots, self._events_detected, pairs)
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
    return BasisPipeline(sources=sources, normalizer=normalizer)


def _build_otc_source() -> RESTDataSource:
    cfg = _leg_cfg("otc")
    rest_cfg = RESTSourceConfig(
        base_url=os.environ.get("OTC_USDINR_BASE_URL", ""),
        endpoint_template=os.environ.get(
            "OTC_USDINR_ENDPOINT", "/latest?base=USD&symbols=INR"
        ),
        api_key=os.environ.get("OTC_USDINR_API_KEY"),
        response_parser=os.environ.get("OTC_USDINR_PARSER", "exchangerate"),
        poll_interval_ms=2000,
    )
    return RESTDataSource(cfg, rest_cfg)


def _env_offshore_fetcher():
    """
    Returns None unless CME_USDINR_URL is set. A real fetcher for the delayed CME
    quote is wired in T1.3; until then the offshore leg is inert and the pipeline
    records onshore + OTC.
    """
    url = os.environ.get("CME_USDINR_URL")
    if not url:
        return None

    import urllib.request

    def _fetch():
        req = urllib.request.Request(url, headers={"User-Agent": "arbex/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return {
            "bid": float(data["bid"]),
            "ask": float(data["ask"]),
            "last": data.get("last"),
            "expiry": data.get("expiry"),
            "quote_time": datetime.fromisoformat(data["quote_time"])
            if data.get("quote_time") else datetime.now(timezone.utc),
        }

    return _fetch
