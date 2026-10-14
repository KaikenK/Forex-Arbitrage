"""
Replays a recorded live session (see `tick_recorder.QuoteRecorder`).

All recorded providers and exchanges are replayed from one shared clock, so
legs observed together live are observed together in replay, and the replayed
quotes flow through the same `ExchangeLegSource` checks (two-sided, staleness)
as live ones. Observation timestamps are shifted to the replay wall clock so
the aligner and staleness gates behave exactly as they did live.

This is what makes the live results reproducible for reviewers without broker
accounts: record during market hours, replay anywhere.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path
from typing import Callable, Dict, List, Tuple

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.quote_hub import PolledQuoteHub, Quote
from backend.core.instruments import REGISTRY, futures_lot_units, normalize_symbol


OPPORTUNITY_LOG = "opportunities.jsonl"   # written alongside the quote files in live mode


def _read_rows(session_dir: Path) -> List[dict]:
    """Recorded quote rows (one file per provider); the opportunity log is skipped."""
    rows = []
    for path in sorted(Path(session_dir).glob("*.jsonl")):
        if path.name == OPPORTUNITY_LOG:
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if "instrument_key" in row and "recv_ts_ms" in row:
                rows.append(row)
    return rows


def _recorded_symbol(row: dict) -> str:
    """Pair of a recorded row; early recordings were USDINR only."""
    sym = normalize_symbol(row.get("symbol") or "")
    if sym in REGISTRY:
        return sym
    head = normalize_symbol(row.get("trading_symbol") or "")[:6]
    return head if head in REGISTRY else "USDINR"



def load_recorded_contracts(session_dir: Path | str) -> List[FutureContract]:
    """One contract per recorded instrument, rebuilt from the recording metadata."""
    seen: Dict[str, FutureContract] = {}
    for r in _read_rows(Path(session_dir)):
        key = r["instrument_key"]
        if key in seen or not r.get("exchange"):
            continue
        seen[key] = FutureContract(
            exchange=r["exchange"], instrument_key=key,
            trading_symbol=r.get("trading_symbol") or key,
            expiry=date.fromisoformat(r["expiry"]) if r.get("expiry") else None,   # None: spot venue
            lot_size=1,
            contract_size=futures_lot_units(_recorded_symbol(r)) if r.get("expiry") else 1.0,
            provider=r.get("provider") or "",
            symbol=_recorded_symbol(r),
        )
    return list(seen.values())


class ReplayQuoteHub(PolledQuoteHub):
    def __init__(self, session_dir: Path | str, *, speed: float = 1.0, loop: bool = False,
                 clock: Callable[[], float] = time.time, poll_interval_s: float = 0.05):
        super().__init__("replay", fetch=lambda keys: {}, poll_interval_s=poll_interval_s, clock=clock)
        self._speed = max(speed, 1e-6)
        self._loop = loop
        by_ts: Dict[int, Dict[str, Quote]] = {}
        for r in _read_rows(Path(session_dir)):
            by_ts.setdefault(int(r["recv_ts_ms"]), {})[r["instrument_key"]] = Quote(
                bid=r.get("bid"), ask=r.get("ask"),
                bid_qty=r.get("bid_qty") or 0.0, ask_qty=r.get("ask_qty") or 0.0,
                last=r.get("last"), exchange_ts_ms=r.get("exchange_ts_ms"),
                depth=r.get("depth") or {},
            )
        self._observations: List[Tuple[int, Dict[str, Quote]]] = sorted(by_ts.items())
        self._next = 0
        self._wall_start_ms: int | None = None
        self.loops = 0

    @property
    def finished(self) -> bool:
        return self._next >= len(self._observations) and not self._loop

    def poll_once(self) -> None:
        if not self._observations:
            return
        now_ms = int(self._clock() * 1000)
        if self._wall_start_ms is None:
            self._wall_start_ms = now_ms
        data_start = self._observations[0][0]
        while True:
            if self._next >= len(self._observations):
                if not self._loop:
                    return
                self._next, self._wall_start_ms = 0, now_ms
                self.loops += 1
            ts, quotes = self._observations[self._next]
            due_ms = self._wall_start_ms + int((ts - data_start) / self._speed)
            if due_ms > now_ms:
                return
            wanted = {k: q for k, q in quotes.items() if k in self._keys}
            self._publish(wanted, recv_ts_ms=due_ms)
            self._next += 1
