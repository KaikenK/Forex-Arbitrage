"""
CME / SGX Delayed USD/INR Future — offshore leg of the Phase-3 basis study.

Offshore USD/INR futures (CME contract "6R" / SGX "Rupee") are the practical proxy
for the NDF market. Real-time offshore data is paid; the free tier is ~10 minutes
delayed, which is acceptable because the onshore-offshore basis moves on a
minutes-to-hours timescale (docs/SPEC.md section 10).

This source polls a delayed quote endpoint. The concrete HTTP call is injected as
a ``fetcher`` callable so the vendor can be swapped (CME delayed JSON, SGX, a
broker's offshore feed) without touching the pipeline. Every RawTick carries
``extra["staleness_ms"]`` = quote age at emit time.

Design Decisions:
- Polling, not streaming (delayed data has no push feed).
- Contract expiry is part of the fetcher's payload; the InstrumentNormalizer
  needs it to carry the quote to the common T*.
- If no fetcher is configured the source connects but yields no ticks, logging a
  single warning — the rest of the pipeline still runs on the onshore + OTC legs.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from backend.core.interfaces.data_source import (
    DataSourceConfig,
    DataSourceInterface,
    RawTick,
)

logger = logging.getLogger(__name__)

_UNDERLYING = "USDINR"

# A fetcher returns the latest delayed quote, or None if unavailable:
#   {"bid": float, "ask": float, "last": float | None,
#    "expiry": "YYYY-MM-DD", "quote_time": datetime (tz-aware)}
Fetcher = Callable[[], Optional[Dict[str, Any]]]


class CMEDelayedSource(DataSourceInterface):
    """Delayed offshore USD/INR future (CME/SGX), polled on an interval."""

    def __init__(
        self,
        config: DataSourceConfig,
        fetcher: Optional[Fetcher] = None,
        poll_interval_s: float = 30.0,
    ):
        super().__init__(config)
        self._fetcher = fetcher
        self._poll_interval_s = poll_interval_s

        self._lock = threading.Lock()
        self._poll_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        self._last_quote: Optional[Dict[str, Any]] = None
        self._last_poll_ms: int = 0
        self._warned_no_fetcher = False

    def connect(self) -> bool:
        self._stop.clear()
        self._is_connected = True
        if self._fetcher is None:
            logger.warning(f"[{self.source_id}] no fetcher configured — offshore "
                           f"leg will yield no ticks")
        else:
            self._poll_thread = threading.Thread(
                target=self._poll_loop, name=f"{self.source_id}-poll", daemon=True
            )
            self._poll_thread.start()
        logger.info(f"[{self.source_id}] connected (poll {self._poll_interval_s}s)")
        return True

    def disconnect(self) -> None:
        self._stop.set()
        self._is_connected = False
        logger.info(f"[{self.source_id}] disconnected")

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                q = self._fetcher()
                if q and q.get("bid") and q.get("ask"):
                    with self._lock:
                        self._last_quote = q
                        self._last_poll_ms = int(time.time() * 1000)
            except Exception as e:
                logger.warning(f"[{self.source_id}] fetch error: {e}")
                self._record_error()
            self._stop.wait(self._poll_interval_s)

    def get_tick(self, symbol: str) -> Optional[RawTick]:
        with self._lock:
            q = self._last_quote
        if not q:
            if self._fetcher is None and not self._warned_no_fetcher:
                self._warned_no_fetcher = True
            return None

        quote_time: datetime = q.get("quote_time") or datetime.now(timezone.utc)
        now_ms = int(time.time() * 1000)
        staleness_ms = max(0, now_ms - int(quote_time.timestamp() * 1000))

        self._record_tick(now_ms)
        return RawTick(
            symbol=_UNDERLYING,
            bid=float(q["bid"]),
            ask=float(q["ask"]),
            timestamp_ms=int(quote_time.timestamp() * 1000),
            source_id=self.source_id,
            last=q.get("last"),
            extra={
                "leg": "offshore",
                "instrument_kind": "future",
                "expiry": q.get("expiry"),
                "staleness_ms": staleness_ms,
            },
        )

    def is_healthy(self) -> bool:
        if not self._is_connected:
            return False
        if self._fetcher is None:
            return True  # degraded-but-expected
        return self._last_poll_ms > 0 and (
            int(time.time() * 1000) - self._last_poll_ms < self._poll_interval_s * 3000
        )

    def get_supported_symbols(self) -> List[str]:
        return [_UNDERLYING]

    def get_stats(self) -> Dict[str, Any]:
        base = super().get_stats()
        with self._lock:
            q = self._last_quote
        base.update({
            "has_fetcher": self._fetcher is not None,
            "last_poll_ms": self._last_poll_ms,
            "last_expiry": (q or {}).get("expiry"),
        })
        return base
