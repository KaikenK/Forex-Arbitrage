"""
OTC USD/INR spot leg — free, no API key.

The "OTC spot" leg of the basis is the global aggregated interbank USD/INR rate
(which trades ~24h and reflects offshore/global flow, distinct from the
ring-fenced onshore NSE price). Two genuinely free, key-less sources:

  1. Yahoo Finance chart API — ``query1.finance.yahoo.com/v8/finance/chart/USDINR=X``
     Intraday ``regularMarketPrice``. Unofficial but free; needs a browser UA.
  2. Frankfurter (``api.frankfurter.dev``) — ECB daily reference rate. Free,
     key-less; used as the fallback when Yahoo is unavailable.

Neither provides a bid/ask, so we synthesise one from the mid with a small
spread (``OTC_SPOT_SPREAD_PIPS``, default 2 pips ≈ 2 paise) and flag the tick
``extra["synthetic_spread"] = True``.

Pure ``urllib`` + a poller thread, same shape as ``UpstoxDataSource``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from backend.core.interfaces.data_source import (
    DataSourceConfig,
    DataSourceInterface,
    RawTick,
)

logger = logging.getLogger(__name__)

_UNDERLYING = "USDINR"
_PIP = 0.01
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

_YAHOO_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/"
              "USDINR=X?interval=1m&range=1d")
_FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest?base=USD&symbols=INR"


def _get_json(url: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read() or b"{}")


def fetch_yahoo() -> Optional[float]:
    try:
        d = _get_json(_YAHOO_URL)
        meta = d["chart"]["result"][0]["meta"]
        px = meta.get("regularMarketPrice")
        return float(px) if px else None
    except (urllib.error.URLError, KeyError, IndexError, TypeError, ValueError) as e:
        logger.debug("[otc_spot] yahoo failed: %s", e)
        return None


def fetch_frankfurter() -> Optional[float]:
    try:
        d = _get_json(_FRANKFURTER_URL)
        px = (d.get("rates") or {}).get("INR")
        return float(px) if px else None
    except (urllib.error.URLError, KeyError, TypeError, ValueError) as e:
        logger.debug("[otc_spot] frankfurter failed: %s", e)
        return None


class OtcSpotSource(DataSourceInterface):
    """Global USD/INR spot (Yahoo intraday, Frankfurter daily fallback)."""

    def __init__(self, config: DataSourceConfig, spread_pips: Optional[float] = None):
        super().__init__(config)
        try:
            self._spread_pips = float(
                spread_pips if spread_pips is not None
                else os.environ.get("OTC_SPOT_SPREAD_PIPS", "2.0"))
        except ValueError:
            self._spread_pips = 2.0
        try:
            self._poll_interval_s = float(os.environ.get("OTC_SPOT_POLL_INTERVAL", "2.0"))
        except ValueError:
            self._poll_interval_s = 2.0

        self._poller: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._mid: Optional[float] = None
        self._src = "?"
        self._last_ts_ms = 0

    def connect(self) -> bool:
        # prime once so a first tick is available immediately
        self._fetch_once()
        self._stop.clear()
        self._poller = threading.Thread(
            target=self._run_poller, name=f"{self.source_id}-poll", daemon=True)
        self._poller.start()
        self._is_connected = True
        logger.info(f"[{self.source_id}] connected — OTC USD/INR spot "
                    f"(source={self._src}, synth spread {self._spread_pips} pips, "
                    f"poll {self._poll_interval_s:.1f}s)")
        return True

    def disconnect(self) -> None:
        self._is_connected = False
        self._stop.set()
        logger.info(f"[{self.source_id}] disconnected")

    def _run_poller(self) -> None:
        while not self._stop.is_set():
            try:
                self._fetch_once()
            except Exception as e:
                logger.error(f"[{self.source_id}] poll error: {e}")
                self._record_error()
            self._stop.wait(self._poll_interval_s)

    def _fetch_once(self) -> None:
        mid = fetch_yahoo()
        src = "yahoo"
        if mid is None:
            mid = fetch_frankfurter()
            src = "frankfurter"
        if mid is None:
            return
        with self._lock:
            self._mid, self._src = mid, src
            self._last_ts_ms = int(time.time() * 1000)

    def get_tick(self, symbol: str) -> Optional[RawTick]:
        with self._lock:
            mid, src, ts_ms = self._mid, self._src, self._last_ts_ms
        if not mid:
            return None
        half = (self._spread_pips * _PIP) / 2.0
        self._record_tick(ts_ms)
        return RawTick(
            symbol=_UNDERLYING,
            bid=round(mid - half, 5),
            ask=round(mid + half, 5),
            timestamp_ms=ts_ms,
            source_id=self.source_id,
            last=round(mid, 5),
            extra={
                "leg": "otc",
                "instrument_kind": "spot",
                "synthetic_spread": True,
                "spread_pips": self._spread_pips,
                "source": src,
            },
        )

    def is_healthy(self) -> bool:
        # Yahoo intraday ~1 min; allow a generous staleness window
        return (self._is_connected and self._last_ts_ms > 0
                and int(time.time() * 1000) - self._last_ts_ms < 120_000)

    def get_supported_symbols(self) -> List[str]:
        return [_UNDERLYING]

    def get_stats(self) -> Dict[str, Any]:
        base = super().get_stats()
        base.update({"mid": self._mid, "source": self._src,
                     "synthetic_spread_pips": self._spread_pips})
        return base
