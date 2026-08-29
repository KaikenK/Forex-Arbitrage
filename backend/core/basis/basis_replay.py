"""
Basis dashboard replay — streams a completed EOD run over the /ws/basis channel
so the dashboard demos on real NSE-futures data before the live Dhan feed exists.

Reads ``research/results/eod/<run>/{basis_eod.jsonl, events.jsonl}`` and, one
trading date at a time, broadcasts a ``basis_snapshot`` plus any ``basis_event``s
for that date, then sleeps ``interval_s`` (accelerated wall clock).

Started automatically by the LIVE_USDINR_BASIS lifespan branch when
``BASIS_REPLAY=1`` (or when the Dhan credentials are absent).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

_RESULTS_EOD = Path(__file__).resolve().parents[3] / "research" / "results" / "eod"


class BasisReplayer:
    def __init__(self, ws_manager: Any, run_id: Optional[str] = None,
                 interval_s: float = 0.4, loop: bool = True):
        self.ws = ws_manager
        self.run_id = run_id or os.environ.get("BASIS_REPLAY_RUN", "real")
        self.interval_s = float(os.environ.get("BASIS_REPLAY_INTERVAL", interval_s))
        self.loop = loop
        self._running = False
        self._rows: List[dict] = []
        self._events_by_ts: dict = {}

    def _load(self) -> bool:
        d = _RESULTS_EOD / self.run_id
        bp = d / "basis_eod.jsonl"
        if not bp.exists():
            # fall back to any available run
            runs = sorted(x for x in _RESULTS_EOD.glob("*") if (x / "basis_eod.jsonl").exists())
            if not runs:
                logger.warning("[basis_replay] no EOD run to replay under %s", _RESULTS_EOD)
                return False
            d = runs[-1]
            bp = d / "basis_eod.jsonl"
            self.run_id = d.name
        self._rows = [json.loads(l) for l in bp.read_text().splitlines() if l.strip()]
        ep = d / "events.jsonl"
        if ep.exists():
            for l in ep.read_text().splitlines():
                if l.strip():
                    e = json.loads(l)
                    key = datetime.fromtimestamp(e["ts"], tz=timezone.utc).date().isoformat()
                    self._events_by_ts.setdefault(key, []).append(e)
        logger.info("[basis_replay] run '%s': %d rows, %d event-days",
                    self.run_id, len(self._rows), len(self._events_by_ts))
        return bool(self._rows)

    async def run(self) -> None:
        if not self._load():
            return
        self._running = True
        await self.ws.broadcast_basis("basis_meta", {
            "mode": "replay", "run_id": self.run_id,
            "target_expiry": self._rows[0].get("target_expiry"),
            "carry_rate_annual": self._rows[0].get("carry_rate_annual"),
            "date_range": [self._rows[0]["trade_date"], self._rows[-1]["trade_date"]],
        })
        try:
            while self._running:
                for row in self._rows:
                    if not self._running:
                        break
                    await self.ws.broadcast_basis("basis_snapshot", {
                        "ts": row.get("ts") or 0,
                        "trade_date": row["trade_date"],
                        "target_expiry": row.get("target_expiry"),
                        "forwards": row.get("forwards", {}),
                        "basis_pips": row.get("basis_pips", {}),
                        "carry_adjustment_pips": row.get("carry_adjustment_pips", {}),
                        "mode": "replay",
                    })
                    for e in self._events_by_ts.get(row["trade_date"], []):
                        await self.ws.broadcast_basis("basis_event", e)
                    await asyncio.sleep(self.interval_s)
                if not self.loop:
                    break
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            pass

    def stop(self) -> None:
        self._running = False
