"""
Shared polling hub for broker quote APIs.

Cross-exchange comparison is only fair if both legs are observed at the same
moment. Polling NSE and BSE from two independent threads would bake our own
scheduling skew (up to one poll interval) into every "dislocation". A hub fetches
*all* registered instruments in one API request, so every quote from a poll
carries the identical observation timestamp and lands in the same alignment
window.

Read-only by construction: hubs only call market-quote endpoints.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Quote:
    bid: Optional[float]
    ask: Optional[float]
    bid_qty: float = 0.0
    ask_qty: float = 0.0
    last: Optional[float] = None
    exchange_ts_ms: Optional[int] = None   # venue's last-trade / quote time
    recv_ts_ms: int = 0                    # when we observed it (hub clock)
    depth: Dict[str, List[Dict[str, float]]] = field(default_factory=dict)

    @property
    def two_sided(self) -> bool:
        """A real, uncrossed book on both sides. LTP-only quotes never qualify."""
        return (self.bid is not None and self.ask is not None
                and 0 < self.bid < self.ask)


class QuoteAuthError(RuntimeError):
    """Credentials rejected; polling stops instead of hammering the API."""


Fetcher = Callable[[List[str]], Dict[str, Quote]]


class PolledQuoteHub:
    """Polls `fetch(keys)` for every registered instrument key on one thread."""

    def __init__(
        self,
        name: str,
        fetch: Fetcher,
        poll_interval_s: float = 1.0,
        clock: Callable[[], float] = time.time,
    ):
        self.name = name
        self._fetch = fetch
        self._poll_interval_s = poll_interval_s
        self._clock = clock
        self._keys: List[str] = []
        self._latest: Dict[str, Quote] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.polls = 0
        self.errors = 0
        self.last_error: Optional[str] = None
        self.last_latency_ms: Optional[float] = None
        self.auth_failed = False
        self._listeners: List[Callable[[str, Dict[str, Quote]], None]] = []

    def add_listener(self, fn: Callable[[str, Dict[str, Quote]], None]) -> None:
        """Called after every poll with (hub name, {key: Quote}) — used by the recorder."""
        self._listeners.append(fn)

    # -- registration ----------------------------------------------------
    def register(self, key: str) -> None:
        with self._lock:
            if key not in self._keys:
                self._keys.append(key)

    def release(self, key: str) -> None:
        with self._lock:
            if key in self._keys:
                self._keys.remove(key)
            empty = not self._keys
        if empty:
            self.stop()

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"{self.name}-hub", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except QuoteAuthError as e:
                self.auth_failed = True
                self.last_error = str(e)
                logger.error(f"[{self.name}] credentials rejected, polling stopped: {e}")
                return
            except Exception as e:  # network/parse errors: keep polling
                self.errors += 1
                self.last_error = str(e)
                logger.warning(f"[{self.name}] poll failed: {e}")
            self._stop.wait(self._poll_interval_s)

    def poll_once(self) -> None:
        with self._lock:
            keys = list(self._keys)
        if not keys:
            return
        started = self._clock()
        # Observation time: request midpoint, so both legs share one timestamp
        # that is unbiased with respect to the round trip.
        quotes = self._fetch(keys)
        finished = self._clock()
        self._publish(quotes, recv_ts_ms=int((started + finished) / 2 * 1000),
                      latency_ms=(finished - started) * 1000)

    def _publish(self, quotes: Dict[str, Quote], *, recv_ts_ms: int,
                 latency_ms: Optional[float] = None) -> None:
        stamped = {k: replace(q, recv_ts_ms=recv_ts_ms) for k, q in quotes.items()}
        with self._lock:
            self._latest.update(stamped)
            self.polls += 1
            self.last_latency_ms = latency_ms
        for fn in self._listeners:
            try:
                fn(self.name, stamped)
            except Exception as e:  # a broken listener must not stop the feed
                logger.warning(f"[{self.name}] listener failed: {e}")

    def latest(self, key: str) -> Optional[Quote]:
        with self._lock:
            return self._latest.get(key)

    def stats(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "running": self.running,
            "instruments": list(self._keys),
            "polls": self.polls,
            "errors": self.errors,
            "last_error": self.last_error,
            "last_latency_ms": round(self.last_latency_ms, 1) if self.last_latency_ms is not None else None,
            "auth_failed": self.auth_failed,
            "poll_interval_s": self._poll_interval_s,
        }
