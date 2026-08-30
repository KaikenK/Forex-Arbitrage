"""
Dhan (DhanHQ v2) data source — onshore NSE USD/INR near-month future.

Read-only: subscribes to the market feed for ticks + market depth. **No order
placement anywhere** — the DhanHQ trading endpoints are never called.

Auth (free Dhan account, keep it unfunded):
    DHAN_CLIENT_ID          your Dhan client id
    DHAN_ACCESS_TOKEN       generate at web.dhan.co -> DhanHQ APIs
    DHAN_USDINR_SECURITY_ID (optional) pin the contract if auto-resolution fails

The near-month contract is resolved from Dhan's public scrip master
(`dhan_instruments.resolve_near_month`); if that snapshot is stale, set
DHAN_USDINR_SECURITY_ID from the Dhan web platform's option chain / F&O page.

`dhanhq` is an optional dependency — imported lazily.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from backend.core.data_sources.dhan_instruments import (
    DhanContract,
    resolve_near_month,
    usdinr_futures,
)
from backend.core.interfaces.data_source import (
    DataSourceConfig,
    DataSourceInterface,
    RawTick,
)

logger = logging.getLogger(__name__)

_UNDERLYING = "USDINR"


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class DhanDataSource(DataSourceInterface):
    """Onshore NSE USD/INR near-month future via DhanHQ (ticks + market depth)."""

    def __init__(
        self,
        config: DataSourceConfig,
        client_id: Optional[str] = None,
        access_token: Optional[str] = None,
        exchange: str = "NSE",
    ):
        super().__init__(config)
        self._client_id = client_id or os.environ.get("DHAN_CLIENT_ID")
        self._access_token = access_token or os.environ.get("DHAN_ACCESS_TOKEN")
        self._exchange = exchange
        self._pinned_sec_id = os.environ.get("DHAN_USDINR_SECURITY_ID")

        self._feed = None
        self._feed_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        self._contract: Optional[DhanContract] = None
        self._last: Optional[Dict[str, Any]] = None
        self._depth: Optional[Dict[str, List[Dict[str, float]]]] = None
        self._last_ts_ms = 0

    # -- lifecycle ------------------------------------------------------
    def connect(self) -> bool:
        if not self._client_id or not self._access_token:
            logger.error(f"[{self.source_id}] DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN not set")
            return False
        try:
            self._contract = self._resolve_contract()
        except Exception as e:
            logger.error(f"[{self.source_id}] could not resolve contract: {e}")
            return False

        try:
            from dhanhq import DhanContext, MarketFeed  # type: ignore
        except ImportError:
            logger.error(f"[{self.source_id}] pip install dhanhq")
            return False

        try:
            seg = MarketFeed.BSE_CURR if self._exchange == "BSE" else MarketFeed.NSE_CURR
            instruments = [(seg, str(self._contract.security_id), MarketFeed.Full)]
            ctx = DhanContext(self._client_id, self._access_token)
            self._feed = MarketFeed(ctx, instruments, version="v2")
            self._feed_thread = threading.Thread(
                target=self._run_feed, name=f"{self.source_id}-feed", daemon=True)
            self._feed_thread.start()
            self._is_connected = True
            logger.info(f"[{self.source_id}] connected — {self._contract.trading_symbol} "
                        f"(sec {self._contract.security_id}, expiry {self._contract.expiry})")
            return True
        except Exception as e:
            logger.error(f"[{self.source_id}] feed connect failed: {e}")
            self._record_error()
            return False

    def disconnect(self) -> None:
        self._is_connected = False
        try:
            if self._feed is not None:
                closer = getattr(self._feed, "close_connection", None) or \
                         getattr(self._feed, "disconnect", None)
                if closer:
                    closer()
        except Exception:
            pass
        logger.info(f"[{self.source_id}] disconnected")

    def _resolve_contract(self) -> DhanContract:
        if self._pinned_sec_id:
            futs = {c.security_id: c for c in usdinr_futures(self._exchange)}
            if self._pinned_sec_id in futs:
                return futs[self._pinned_sec_id]
            logger.warning(f"[{self.source_id}] DHAN_USDINR_SECURITY_ID "
                           f"{self._pinned_sec_id} not in master — using it anyway")
            return DhanContract(
                security_id=self._pinned_sec_id, trading_symbol=f"{_UNDERLYING}-PINNED",
                expiry=date.today(), lot_size=1.0, exchange=self._exchange,
                exchange_segment="NSE_CURRENCY" if self._exchange == "NSE" else "BSE_CURRENCY",
            )
        return resolve_near_month(self._exchange, force=True, allow_stale=True)

    def _run_feed(self) -> None:
        try:
            self._feed.run_forever()
            while self._is_connected:
                data = self._feed.get_data()
                if data:
                    self._ingest(data)
        except Exception as e:
            hint = ""
            if "Data API" in str(e) or "806" in str(e):
                hint = " (enable 'Data APIs' in the Dhan profile — free activation)"
            elif "close frame" in str(e):
                hint = " (idle disconnect — normal outside NSE currency hours 09:00-17:00 IST)"
            logger.error(f"[{self.source_id}] feed loop error: {e}{hint}")
            self._record_error()

    def _ingest(self, data: Dict[str, Any]) -> None:
        """
        Normalise a dhanhq (>=2.x) MarketFeed packet into our state.

        Depth subscription -> {"type": "Market Depth", "LTP", "depth": [
            {"bid_price","ask_price","bid_quantity","ask_quantity", ...} x5 ]}
        Ticker / Quote / Full packets carry "LTP" and are used as a fallback.
        """
        if not isinstance(data, dict):
            return
        ptype = data.get("type", "")
        with self._lock:
            levels = data.get("depth")
            if ptype in ("Market Depth", "Full Data") and isinstance(levels, list) and levels:
                bids, asks = [], []
                for lv in levels:
                    try:
                        bp, ap = float(lv["bid_price"]), float(lv["ask_price"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    if bp > 0:
                        bids.append({"price": bp, "qty": float(lv.get("bid_quantity", 0))})
                    if ap > 0:
                        asks.append({"price": ap, "qty": float(lv.get("ask_quantity", 0))})
                if bids and asks:
                    self._depth = {"bids": bids, "asks": asks}
                    self._last = {"bid": bids[0]["price"], "ask": asks[0]["price"],
                                  "last": _as_float(data.get("LTP"))}
                    self._last_ts_ms = int(time.time() * 1000)
                return
            ltp = _as_float(data.get("LTP")) or _as_float(data.get("last_price"))
            if ltp:
                self._last = {"bid": ltp, "ask": ltp, "last": ltp}
                self._last_ts_ms = int(time.time() * 1000)

    # -- DataSourceInterface -----------------------------------------
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        with self._lock:
            snap, ts_ms = self._last, self._last_ts_ms
        if not snap or not snap.get("bid") or not snap.get("ask"):
            return None
        self._record_tick(ts_ms)
        return RawTick(
            symbol=_UNDERLYING,
            bid=float(snap["bid"]),
            ask=float(snap["ask"]),
            timestamp_ms=ts_ms,
            source_id=self.source_id,
            last=snap.get("last"),
            extra={
                "leg": "onshore",
                "instrument_kind": "future",
                "expiry": self._contract.expiry.isoformat() if self._contract else None,
                "tradingsymbol": self._contract.trading_symbol if self._contract else None,
            },
        )

    def get_depth(self, symbol: str) -> Optional[Dict[str, List[Dict[str, float]]]]:
        with self._lock:
            return dict(self._depth) if self._depth else None

    @property
    def contract_expiry(self) -> Optional[date]:
        return self._contract.expiry if self._contract else None

    def is_healthy(self) -> bool:
        return (self._is_connected and self._last_ts_ms > 0
                and int(time.time() * 1000) - self._last_ts_ms < 10_000)

    def get_supported_symbols(self) -> List[str]:
        return [_UNDERLYING]

    def get_stats(self) -> Dict[str, Any]:
        base = super().get_stats()
        base.update({
            "contract": self._contract.to_dict() if self._contract else None,
            "has_depth": self._depth is not None,
        })
        return base
