"""
Upstox (API v2) data source — onshore NSE USD/INR near-month future.

Read-only: polls the market-quote endpoint for last price + 5-level depth.
**No order placement anywhere** — the Upstox trading endpoints are never called.

Auth (Upstox market data is free — no data subscription, unlike Dhan/Kite):
    UPSTOX_ACCESS_TOKEN           analytics token (preferred, no daily re-auth)
                                  or a standard access token (expires ~03:30 IST)
    UPSTOX_USDINR_INSTRUMENT_KEY  (optional) pin the contract, e.g. "NCD_FO|1769"
    UPSTOX_POLL_INTERVAL          (optional) seconds between quote polls, default 1.0

The near-month contract is resolved from Upstox's public instrument master
(`upstox_instruments.resolve_near_month`), which — unlike Dhan's — stays current.

Pure ``urllib`` REST; the ``upstox-python`` SDK is not required. A protobuf
websocket feed (v3) is a later upgrade; 1 s polling is well inside the free
rate limit (10 req/s) and matches the basis pipeline's cadence.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Dict, List, Optional

from backend.core.data_sources.upstox_instruments import (
    UpstoxContract,
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
_QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"

# api.upstox.com sits behind Cloudflare, which 1010-blocks bot-ish User-Agents
# (Python-urllib/*). Present a normal browser UA.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def _as_float(v: Any) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class UpstoxDataSource(DataSourceInterface):
    """Onshore NSE USD/INR near-month future via Upstox (quote + 5-level depth)."""

    def __init__(
        self,
        config: DataSourceConfig,
        access_token: Optional[str] = None,
    ):
        super().__init__(config)
        self._token = access_token or os.environ.get("UPSTOX_ACCESS_TOKEN")
        self._pinned_key = os.environ.get("UPSTOX_USDINR_INSTRUMENT_KEY")
        try:
            self._poll_interval_s = float(os.environ.get("UPSTOX_POLL_INTERVAL", "1.0"))
        except ValueError:
            self._poll_interval_s = 1.0

        self._contract: Optional[UpstoxContract] = None
        self._poller: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._last: Optional[Dict[str, Any]] = None
        self._depth: Optional[Dict[str, List[Dict[str, float]]]] = None
        self._last_ts_ms = 0
        self._stop = threading.Event()

    # -- lifecycle ----------------------------------------------------
    def connect(self) -> bool:
        if not self._token:
            logger.error(f"[{self.source_id}] UPSTOX_ACCESS_TOKEN not set")
            return False
        try:
            self._contract = self._resolve_contract()
        except Exception as e:
            logger.error(f"[{self.source_id}] could not resolve contract: {e}")
            return False

        self._stop.clear()
        self._poller = threading.Thread(
            target=self._run_poller, name=f"{self.source_id}-poll", daemon=True)
        self._poller.start()
        self._is_connected = True
        logger.info(f"[{self.source_id}] connected — {self._contract.trading_symbol} "
                    f"({self._contract.instrument_key}, expiry {self._contract.expiry}); "
                    f"polling every {self._poll_interval_s:.1f}s")
        return True

    def disconnect(self) -> None:
        self._is_connected = False
        self._stop.set()
        logger.info(f"[{self.source_id}] disconnected")

    def _resolve_contract(self) -> UpstoxContract:
        if self._pinned_key:
            by_key = {c.instrument_key: c for c in usdinr_futures()}
            if self._pinned_key in by_key:
                return by_key[self._pinned_key]
            logger.warning(f"[{self.source_id}] UPSTOX_USDINR_INSTRUMENT_KEY "
                           f"{self._pinned_key} not in master — using it anyway")
            return UpstoxContract(
                instrument_key=self._pinned_key, trading_symbol=f"{_UNDERLYING}-PINNED",
                expiry=date.today(), lot_size=1, qty_multiplier=1000.0, weekly=False,
            )
        return resolve_near_month(force=True)

    # -- polling ----------------------------------------------------
    def _run_poller(self) -> None:
        while not self._stop.is_set():
            try:
                self._fetch_once()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                if e.code == 401:
                    logger.error(f"[{self.source_id}] 401 — token expired/invalid. "
                                 f"Regenerate UPSTOX_ACCESS_TOKEN. {body}")
                    self._record_error()
                    break
                logger.error(f"[{self.source_id}] HTTP {e.code}: {body}")
                self._record_error()
            except Exception as e:
                logger.error(f"[{self.source_id}] poll error: {e}")
                self._record_error()
            self._stop.wait(self._poll_interval_s)

    def _fetch_once(self) -> None:
        key = self._contract.instrument_key
        url = f"{_QUOTE_URL}?instrument_key={urllib.parse.quote(key, safe='')}"
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": _UA,
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read() or b"{}")

        data = payload.get("data") or {}
        node = next(iter(data.values()), None) if isinstance(data, dict) else None
        if not node:
            return

        depth = node.get("depth") or {}
        buy = depth.get("buy") or []
        sell = depth.get("sell") or []
        bids, asks = [], []
        for lv in buy:
            p = _as_float(lv.get("price"))
            if p and p > 0:
                bids.append({"price": p, "qty": float(lv.get("quantity", 0) or 0)})
        for lv in sell:
            p = _as_float(lv.get("price"))
            if p and p > 0:
                asks.append({"price": p, "qty": float(lv.get("quantity", 0) or 0)})

        ltp = _as_float(node.get("last_price"))
        with self._lock:
            if bids and asks:
                self._depth = {"bids": bids, "asks": asks}
                self._last = {"bid": bids[0]["price"], "ask": asks[0]["price"], "last": ltp}
            elif ltp:
                # off-hours / no book — fall back to LTP as a degenerate quote
                self._last = {"bid": ltp, "ask": ltp, "last": ltp}
            self._last_ts_ms = int(time.time() * 1000)

    # -- DataSourceInterface --------------------------------------
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
                "instrument_key": self._contract.instrument_key if self._contract else None,
            },
        )

    def get_depth(self, symbol: str) -> Optional[Dict[str, List[Dict[str, float]]]]:
        with self._lock:
            return {k: list(v) for k, v in self._depth.items()} if self._depth else None

    @property
    def contract_expiry(self) -> Optional[date]:
        return self._contract.expiry if self._contract else None

    def is_healthy(self) -> bool:
        return (self._is_connected and self._last_ts_ms > 0
                and int(time.time() * 1000) - self._last_ts_ms < 15_000)

    def get_supported_symbols(self) -> List[str]:
        return [_UNDERLYING]

    def get_stats(self) -> Dict[str, Any]:
        base = super().get_stats()
        base.update({
            "contract": self._contract.to_dict() if self._contract else None,
            "has_depth": self._depth is not None,
            "poll_interval_s": self._poll_interval_s,
        })
        return base
