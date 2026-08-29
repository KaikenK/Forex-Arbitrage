"""
Zerodha Kite Connect Data Source — onshore NSE USD/INR near-month future.

Implements DataSourceInterface for the onshore leg of the Phase-3 basis study.
Provides real tick data AND 5-level market depth for the NSE currency-derivatives
(CDS) USD/INR future.

Auth: Kite Connect needs an API key + secret and a daily access token obtained
through a login/TOTP redirect flow. Set in the environment / .env:

    KITE_API_KEY, KITE_API_SECRET, KITE_ACCESS_TOKEN

The access token is generated once per day (see `docs/SPEC.md` section 5 and the
Kite Connect docs) and pasted in; this class does not automate the browser login.

Design Decisions:
- The `kiteconnect` package is an optional dependency — this module imports it
  lazily so the rest of the system runs without it.
- The near-month contract symbol (e.g. `USDINR26SEPFUT`) and its expiry date are
  resolved once at connect() from the instruments dump, then cached.
- `get_tick()` returns the last quote seen by the WebSocket ticker (non-blocking);
  `get_depth()` returns the last 5x5 book.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from backend.core.interfaces.data_source import (
    DataSourceConfig,
    DataSourceInterface,
    RawTick,
)

logger = logging.getLogger(__name__)

_NSE_CDS_EXCHANGE = "CDS"          # NSE currency-derivatives segment
_UNDERLYING = "USDINR"


class KiteDataSource(DataSourceInterface):
    """Onshore NSE USD/INR near-month future via Kite Connect (ticks + 5-level depth)."""

    def __init__(
        self,
        config: DataSourceConfig,
        api_key: Optional[str] = None,
        access_token: Optional[str] = None,
    ):
        super().__init__(config)
        self._api_key = api_key or os.environ.get("KITE_API_KEY")
        self._access_token = access_token or os.environ.get("KITE_ACCESS_TOKEN")

        self._kite = None          # KiteConnect REST handle
        self._ticker = None        # KiteTicker WebSocket handle
        self._lock = threading.Lock()

        self._instrument_token: Optional[int] = None
        self._tradingsymbol: Optional[str] = None
        self._expiry: Optional[date] = None

        self._last_tick: Optional[Dict[str, Any]] = None
        self._last_depth: Optional[Dict[str, List[Dict[str, float]]]] = None
        self._last_ts_ms: int = 0

    # -- lifecycle --------------------------------------------------------
    def connect(self) -> bool:
        if not self._api_key or not self._access_token:
            logger.error(f"[{self.source_id}] KITE_API_KEY / KITE_ACCESS_TOKEN not set")
            return False
        try:
            from kiteconnect import KiteConnect, KiteTicker  # type: ignore
        except ImportError:
            logger.error(f"[{self.source_id}] pip install kiteconnect")
            return False

        try:
            self._kite = KiteConnect(api_key=self._api_key)
            self._kite.set_access_token(self._access_token)

            self._resolve_near_month_contract()
            if self._instrument_token is None:
                return False

            self._ticker = KiteTicker(self._api_key, self._access_token)
            self._ticker.on_ticks = self._on_ticks
            self._ticker.on_connect = self._on_connect
            self._ticker.on_error = lambda ws, code, reason: logger.warning(
                f"[{self.source_id}] ticker error {code}: {reason}"
            )
            self._ticker.connect(threaded=True)

            self._is_connected = True
            logger.info(f"[{self.source_id}] connected — {self._tradingsymbol} "
                        f"(token {self._instrument_token}, expiry {self._expiry})")
            return True
        except Exception as e:
            logger.error(f"[{self.source_id}] connect failed: {e}")
            self._record_error()
            return False

    def disconnect(self) -> None:
        try:
            if self._ticker is not None:
                self._ticker.close()
        except Exception:
            pass
        self._is_connected = False
        logger.info(f"[{self.source_id}] disconnected")

    def _resolve_near_month_contract(self) -> None:
        """Find the nearest non-expired USD/INR future on NSE CDS."""
        instruments = self._kite.instruments(_NSE_CDS_EXCHANGE)
        futs = [
            i for i in instruments
            if i.get("name") == _UNDERLYING and i.get("instrument_type") == "FUT"
        ]
        today = datetime.now(timezone.utc).date()
        upcoming = sorted(
            (i for i in futs if _as_date(i["expiry"]) >= today),
            key=lambda i: _as_date(i["expiry"]),
        )
        if not upcoming:
            logger.error(f"[{self.source_id}] no upcoming {_UNDERLYING} FUT on CDS")
            return
        near = upcoming[0]
        self._instrument_token = int(near["instrument_token"])
        self._tradingsymbol = near["tradingsymbol"]
        self._expiry = _as_date(near["expiry"])

    # -- ticker callbacks ----------------------------------------------
    def _on_connect(self, ws, response) -> None:  # noqa: ANN001
        ws.subscribe([self._instrument_token])
        ws.set_mode(ws.MODE_FULL, [self._instrument_token])  # full = depth included

    def _on_ticks(self, ws, ticks) -> None:  # noqa: ANN001
        for t in ticks:
            if t.get("instrument_token") != self._instrument_token:
                continue
            depth = t.get("depth") or {}
            with self._lock:
                self._last_tick = {
                    "bid": _touch(depth.get("buy"), t.get("last_price")),
                    "ask": _touch(depth.get("sell"), t.get("last_price")),
                    "last": t.get("last_price"),
                    "volume": t.get("volume_traded"),
                }
                if depth:
                    self._last_depth = {
                        "bids": [{"price": lv["price"], "qty": lv["quantity"]}
                                 for lv in depth.get("buy", [])],
                        "asks": [{"price": lv["price"], "qty": lv["quantity"]}
                                 for lv in depth.get("sell", [])],
                    }
                self._last_ts_ms = int(time.time() * 1000)

    # -- DataSourceInterface -----------------------------------------
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        with self._lock:
            snap = self._last_tick
            ts_ms = self._last_ts_ms
        if not snap or snap["bid"] is None or snap["ask"] is None:
            return None
        self._record_tick(ts_ms)
        return RawTick(
            symbol=_UNDERLYING,
            bid=float(snap["bid"]),
            ask=float(snap["ask"]),
            timestamp_ms=ts_ms,
            source_id=self.source_id,
            volume=snap.get("volume"),
            last=snap.get("last"),
            extra={
                "leg": "onshore",
                "instrument_kind": "future",
                "expiry": self._expiry.isoformat() if self._expiry else None,
                "tradingsymbol": self._tradingsymbol,
            },
        )

    def get_depth(self, symbol: str) -> Optional[Dict[str, List[Dict[str, float]]]]:
        """5x5 market depth for the near-month future (onshore leg only)."""
        with self._lock:
            return dict(self._last_depth) if self._last_depth else None

    @property
    def contract_expiry(self) -> Optional[date]:
        return self._expiry

    def is_healthy(self) -> bool:
        if not self._is_connected:
            return False
        age = int(time.time() * 1000) - self._last_ts_ms
        return self._last_ts_ms > 0 and age < 10_000

    def get_supported_symbols(self) -> List[str]:
        return [_UNDERLYING]

    def get_stats(self) -> Dict[str, Any]:
        base = super().get_stats()
        base.update({
            "tradingsymbol": self._tradingsymbol,
            "expiry": self._expiry.isoformat() if self._expiry else None,
            "has_depth": self._last_depth is not None,
        })
        return base


def _as_date(v: Any) -> date:
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def _touch(levels: Optional[list], fallback: Optional[float]) -> Optional[float]:
    if levels:
        return levels[0].get("price")
    return fallback
