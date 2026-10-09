"""
One exchange-listed USD/INR future as a `DataSourceInterface` leg.

Reads the latest quote for its contract from a shared `PolledQuoteHub` and
emits a `RawTick` only when the quote is a genuine two-sided book that is
fresher than ``max_staleness_ms``. No quote is ever synthesised: no spread is
invented around a last price, and a stale book is dropped rather than repeated.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.quote_hub import PolledQuoteHub
from backend.core.interfaces.data_source import DataSourceConfig, DataSourceInterface, RawTick

SYMBOL = "USDINR"


class ExchangeLegSource(DataSourceInterface):
    def __init__(
        self,
        config: DataSourceConfig,
        *,
        hub: PolledQuoteHub,
        contract: FutureContract,
        max_staleness_ms: int = 5_000,
        clock: Callable[[], float] = time.time,
    ):
        super().__init__(config)
        self._hub = hub
        self._contract = contract
        self._max_staleness_ms = max_staleness_ms
        self._clock = clock
        self._last_reason: Optional[str] = "not polled yet"

    @property
    def contract(self) -> FutureContract:
        return self._contract

    @property
    def hub(self) -> PolledQuoteHub:
        return self._hub

    # -- lifecycle -------------------------------------------------------
    def connect(self) -> bool:
        self._hub.register(self._contract.instrument_key)
        self._hub.start()
        self._is_connected = True
        return True

    def disconnect(self) -> None:
        self._hub.release(self._contract.instrument_key)
        self._is_connected = False

    # -- data ------------------------------------------------------------
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        if symbol.upper() != SYMBOL:
            return None
        q = self._hub.latest(self._contract.instrument_key)
        if q is None:
            self._last_reason = "no quote yet"
            return None
        if not q.two_sided:
            self._last_reason = "no two-sided book"
            return None
        age_ms = int(self._clock() * 1000) - q.recv_ts_ms
        if age_ms > self._max_staleness_ms:
            self._last_reason = f"stale ({age_ms} ms)"
            return None
        self._last_reason = None
        if q.recv_ts_ms != self._last_tick_time:   # count observations, not reads
            self._record_tick(q.recv_ts_ms)
        return RawTick(
            symbol=SYMBOL,
            bid=q.bid,
            ask=q.ask,
            timestamp_ms=q.recv_ts_ms,
            source_id=self.source_id,
            last=q.last,
            extra={
                "mode": "live_exchange",
                "exchange": self._contract.exchange,
                "provider": self._contract.provider,
                "instrument_key": self._contract.instrument_key,
                "trading_symbol": self._contract.trading_symbol,
                "expiry": self._contract.expiry.isoformat(),
                "exchange_ts_ms": q.exchange_ts_ms,
                "recv_ts_ms": q.recv_ts_ms,
                "bid_qty": q.bid_qty,
                "ask_qty": q.ask_qty,
            },
        )

    def get_depth(self) -> Optional[Dict[str, List[Dict[str, float]]]]:
        q = self._hub.latest(self._contract.instrument_key)
        return q.depth if q else None

    def is_healthy(self) -> bool:
        if not self._is_connected or self._hub.auth_failed:
            return False
        q = self._hub.latest(self._contract.instrument_key)
        return (q is not None and q.two_sided
                and int(self._clock() * 1000) - q.recv_ts_ms <= self._max_staleness_ms)

    def get_supported_symbols(self) -> List[str]:
        return [SYMBOL]

    def get_stats(self) -> Dict[str, Any]:
        stats = super().get_stats()
        stats.update({
            "contract": self._contract.to_dict(),
            "healthy": self.is_healthy(),
            "suppressed_reason": self._last_reason,
            "hub": self._hub.stats(),
        })
        return stats
