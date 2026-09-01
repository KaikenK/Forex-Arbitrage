"""
Upstox options-implied forward — the synthetic-future leg of retail-arb mode.

Batch-quotes the NSE USD/INR near-month **future** plus a band of near-the-money
**CE/PE strikes** for the same expiry, collapses the options via put-call parity
(`normalization.options_forward.implied_forward_from_chain`) into one forward
price, and emits it as a ``RawTick`` with ``instrument_kind = "options_forward"``.

Compared against the real future at the same expiry inside
``BasisArbitrageEngine`` (leg pair ``future_options``), any persistent gap is the
conversion / reversal (box) arbitrage a retail trader executes entirely on NSE.

Free — Upstox market data, one batch ``/v2/market-quote/quotes`` call per poll.
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
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from backend.config import OPTIONS_DISCOUNT_RATE_ANNUAL
from backend.core.data_sources.upstox_data_source import _UA, _as_float
from backend.core.data_sources.upstox_instruments import (
    resolve_near_month,
    usdinr_options,
)
from backend.core.interfaces.data_source import (
    DataSourceConfig,
    DataSourceInterface,
    RawTick,
)
from backend.core.normalization import implied_forward_from_chain

logger = logging.getLogger(__name__)

_UNDERLYING = "USDINR"
_QUOTES_URL = "https://api.upstox.com/v2/market-quote/quotes"


class UpstoxOptionsSource(DataSourceInterface):
    """Put-call-parity synthetic USD/INR forward from the NSE options chain."""

    def __init__(
        self,
        config: DataSourceConfig,
        access_token: Optional[str] = None,
        *,
        strikes_each_side: int = 5,
        n_strikes_avg: int = 3,
        rate_annual: float = OPTIONS_DISCOUNT_RATE_ANNUAL,
    ):
        super().__init__(config)
        self._token = access_token or os.environ.get("UPSTOX_ACCESS_TOKEN")
        self._each_side = strikes_each_side
        self._n_avg = n_strikes_avg
        self._rate = rate_annual
        try:
            self._poll_interval_s = float(os.environ.get("UPSTOX_OPT_POLL_INTERVAL", "4.0"))
        except ValueError:
            self._poll_interval_s = 4.0

        self._expiry: Optional[date] = None
        self._fut_key: str = ""
        self._opt_by_key: Dict[str, Any] = {}          # instrument_key -> UpstoxOption
        self._poller: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._last: Optional[Dict[str, Any]] = None     # {bid, ask, last, strikes, ref}
        self._last_ts_ms = 0

    # -- lifecycle --------------------------------------------------
    def connect(self) -> bool:
        if not self._token:
            logger.error(f"[{self.source_id}] UPSTOX_ACCESS_TOKEN not set")
            return False
        try:
            fut = resolve_near_month(force=True)
            self._expiry = fut.expiry
            self._fut_key = fut.instrument_key
            opts = usdinr_options(self._expiry)
            self._opt_by_key = {o.instrument_key: o for o in opts}
            if not self._opt_by_key:
                logger.error(f"[{self.source_id}] no USDINR options for {self._expiry}")
                return False
        except Exception as e:
            logger.error(f"[{self.source_id}] could not resolve options chain: {e}")
            return False

        self._stop.clear()
        self._poller = threading.Thread(
            target=self._run_poller, name=f"{self.source_id}-poll", daemon=True)
        self._poller.start()
        self._is_connected = True
        logger.info(f"[{self.source_id}] connected — {len(self._opt_by_key)} USDINR "
                    f"CE/PE for {self._expiry}; poll {self._poll_interval_s:.0f}s")
        return True

    def disconnect(self) -> None:
        self._is_connected = False
        self._stop.set()
        logger.info(f"[{self.source_id}] disconnected")

    def _run_poller(self) -> None:
        while not self._stop.is_set():
            try:
                self._fetch_once()
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:200]
                if e.code == 401:
                    logger.error(f"[{self.source_id}] 401 — regenerate UPSTOX_ACCESS_TOKEN. {body}")
                    self._record_error()
                    break
                logger.warning(f"[{self.source_id}] HTTP {e.code}: {body}")
                self._record_error()
            except Exception as e:
                logger.warning(f"[{self.source_id}] poll error: {e}")
                self._record_error()
            self._stop.wait(self._poll_interval_s)

    def _quote(self, keys: List[str]) -> Dict[str, Any]:
        q = ",".join(urllib.parse.quote(k, safe="") for k in keys)
        req = urllib.request.Request(
            f"{_QUOTES_URL}?instrument_key={q}",
            headers={"Authorization": f"Bearer {self._token}",
                     "Accept": "application/json", "User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read() or b"{}").get("data") or {}

    def _fetch_once(self) -> None:
        # 1. get the future's mid to centre the strike band
        fut = next(iter(self._quote([self._fut_key]).values()), None)
        ref = _mid_of(fut)
        if not ref:
            return
        # 2. select strikes nearest ref, quote future + those CE/PE
        strikes = sorted({o.strike for o in self._opt_by_key.values()})
        near = sorted(strikes, key=lambda k: abs(k - ref))[: self._each_side * 2]
        want = {o.instrument_key for o in self._opt_by_key.values() if o.strike in near}
        nodes = self._quote([self._fut_key] + sorted(want))

        chain: Dict[float, Dict[str, Dict[str, float]]] = {}
        for node in nodes.values():
            tok = str(node.get("instrument_token") or "")
            o = self._opt_by_key.get(tok)
            if not o:
                continue
            d = node.get("depth") or {}
            bids, asks = d.get("buy") or [], d.get("sell") or []
            bid = _as_float(bids[0]["price"]) if bids else _as_float(node.get("last_price"))
            ask = _as_float(asks[0]["price"]) if asks else _as_float(node.get("last_price"))
            if not bid or not ask:
                continue
            side = "call" if o.option_type == "CE" else "put"
            chain.setdefault(o.strike, {})[side] = {"bid": bid, "ask": ask}

        days = max(0.0, (self._expiry - datetime.now(timezone.utc).date()).days)
        q = implied_forward_from_chain(chain, ref, days_to_expiry=days,
                                       rate_annual=self._rate, n_strikes=self._n_avg)
        if q is None:
            return
        with self._lock:
            self._last = {"bid": q.bid, "ask": q.ask, "last": q.mid,
                          "strikes": list(q.strikes_used), "ref": ref}
            self._last_ts_ms = int(time.time() * 1000)

    # -- DataSourceInterface --------------------------------------
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        with self._lock:
            snap, ts_ms = self._last, self._last_ts_ms
        if not snap:
            return None
        self._record_tick(ts_ms)
        return RawTick(
            symbol=_UNDERLYING, bid=float(snap["bid"]), ask=float(snap["ask"]),
            timestamp_ms=ts_ms, source_id=self.source_id, last=snap.get("last"),
            extra={
                "leg": "options",
                "instrument_kind": "options_forward",
                "expiry": self._expiry.isoformat() if self._expiry else None,
                "strikes_used": snap.get("strikes"),
                "ref_future": snap.get("ref"),
                "synthetic": True,
            },
        )

    def is_healthy(self) -> bool:
        return (self._is_connected and self._last_ts_ms > 0
                and int(time.time() * 1000) - self._last_ts_ms < self._poll_interval_s * 3000)

    def get_supported_symbols(self) -> List[str]:
        return [_UNDERLYING]

    def get_stats(self) -> Dict[str, Any]:
        base = super().get_stats()
        base.update({"expiry": self._expiry.isoformat() if self._expiry else None,
                     "n_strikes": len(self._opt_by_key),
                     "last_strikes_used": (self._last or {}).get("strikes")})
        return base


def _mid_of(node: Optional[dict]) -> Optional[float]:
    if not node:
        return None
    d = node.get("depth") or {}
    b, s = d.get("buy") or [], d.get("sell") or []
    if b and s:
        bp, sp = _as_float(b[0]["price"]), _as_float(s[0]["price"])
        if bp and sp:
            return (bp + sp) / 2.0
    return _as_float(node.get("last_price"))
