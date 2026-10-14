"""
Records every live quote observation to JSONL so a session can be replayed.

Hooked onto a `PolledQuoteHub` as a listener, so it captures *all* observations,
including one-sided or empty books that the legs suppress — the share of time a
venue has no two-sided book is itself a result worth reporting.

Layout: ``<root>/<YYYY-MM-DD>/<hub>.jsonl`` (UTC day), one line per instrument per
poll. Recordings are research data and are git-ignored; publish them separately.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.quote_hub import Quote


class QuoteRecorder:
    def __init__(self, root: Path | str):
        self._root = Path(root)
        self._contracts: Dict[str, FutureContract] = {}
        self._lock = threading.Lock()
        self.rows_written = 0
        self.session_dir: Optional[Path] = None

    def register_contract(self, contract: FutureContract) -> None:
        self._contracts[contract.instrument_key] = contract

    def on_poll(self, hub_name: str, quotes: Dict[str, Quote]) -> None:
        lines = []
        day = None
        for key, q in quotes.items():
            c = self._contracts.get(key)
            day = datetime.fromtimestamp(q.recv_ts_ms / 1000, tz=timezone.utc).date().isoformat()
            lines.append(json.dumps({
                "symbol": c.symbol if c else None,
                "instrument_key": key,
                "provider": c.provider if c else hub_name,
                "exchange": c.exchange if c else None,
                "trading_symbol": c.trading_symbol if c else None,
                "expiry": c.expiry.isoformat() if c and c.expiry else None,
                "bid": q.bid,
                "ask": q.ask,
                "bid_qty": q.bid_qty,
                "ask_qty": q.ask_qty,
                "last": q.last,
                "exchange_ts_ms": q.exchange_ts_ms,
                "recv_ts_ms": q.recv_ts_ms,
                "depth": q.depth,
            }))
        if not lines:
            return
        with self._lock:
            self.session_dir = self._root / day
            self.session_dir.mkdir(parents=True, exist_ok=True)
            with (self.session_dir / f"{hub_name}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            self.rows_written += len(lines)
