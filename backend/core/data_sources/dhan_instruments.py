"""
Dhan instrument-master resolver.

Downloads Dhan's public scrip master (no API key needed) and resolves the
near-month NSE USD/INR currency future — its security id, expiry and lot size —
for `DhanDataSource`.

Master format (``api-scrip-master.csv``):
    SEM_EXM_EXCH_ID, SEM_SEGMENT, SEM_SMST_SECURITY_ID, SEM_INSTRUMENT_NAME,
    SEM_EXPIRY_CODE, SEM_TRADING_SYMBOL, SEM_LOT_UNITS, SEM_CUSTOM_SYMBOL,
    SEM_EXPIRY_DATE, SEM_STRIKE_PRICE, SEM_OPTION_TYPE, SEM_TICK_SIZE
"""

from __future__ import annotations

import csv
import io
import logging
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
_CACHE = Path(__file__).resolve().parents[3] / "data" / "cache" / "dhan_scrip_master.csv"
_CACHE_TTL_S = 12 * 3600

# Dhan exchange-segment constants used by the market feed
EXCHANGE_SEGMENT = {"NSE": "NSE_CURRENCY", "BSE": "BSE_CURRENCY"}


@dataclass(frozen=True)
class DhanContract:
    security_id: str
    trading_symbol: str
    expiry: date
    lot_size: float
    exchange: str                    # "NSE" | "BSE"
    exchange_segment: str            # "NSE_CURRENCY" | ...

    def to_dict(self) -> dict:
        return {
            "security_id": self.security_id,
            "trading_symbol": self.trading_symbol,
            "expiry": self.expiry.isoformat(),
            "lot_size": self.lot_size,
            "exchange": self.exchange,
            "exchange_segment": self.exchange_segment,
        }


def _download_master(force: bool = False) -> str:
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not force and _CACHE.exists() and (time.time() - _CACHE.stat().st_mtime) < _CACHE_TTL_S:
        return _CACHE.read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(MASTER_URL, headers={"User-Agent": "arbex/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", "replace")
    _CACHE.write_text(text, encoding="utf-8")
    logger.info("[dhan_instruments] refreshed scrip master (%d bytes)", len(text))
    return text


def load_master(force: bool = False, text: Optional[str] = None) -> List[dict]:
    text = text if text is not None else _download_master(force=force)
    return list(csv.DictReader(io.StringIO(text)))


def _expiry_of(row: dict) -> Optional[date]:
    raw = (row.get("SEM_EXPIRY_DATE") or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(raw[:19] if " " in raw else raw[:10], fmt).date()
        except ValueError:
            continue
    return None


def usdinr_futures(
    exchange: str = "NSE",
    *,
    force: bool = False,
    master_text: Optional[str] = None,
) -> List[DhanContract]:
    """All USD/INR FUTCUR contracts for an exchange, sorted by expiry."""
    out: List[DhanContract] = []
    for r in load_master(force=force, text=master_text):
        if r.get("SEM_INSTRUMENT_NAME") != "FUTCUR":
            continue
        if r.get("SEM_EXM_EXCH_ID") != exchange:
            continue
        sym = r.get("SEM_TRADING_SYMBOL", "")
        if "USDINR" not in sym:
            continue
        exp = _expiry_of(r)
        if exp is None:
            continue
        try:
            lot = float(r.get("SEM_LOT_UNITS") or 1)
        except ValueError:
            lot = 1.0
        out.append(DhanContract(
            security_id=str(r.get("SEM_SMST_SECURITY_ID", "")).strip(),
            trading_symbol=sym,
            expiry=exp,
            lot_size=lot,
            exchange=exchange,
            exchange_segment=EXCHANGE_SEGMENT.get(exchange, "NSE_CURRENCY"),
        ))
    return sorted(out, key=lambda c: c.expiry)


def resolve_near_month(
    exchange: str = "NSE",
    *,
    as_of: Optional[date] = None,
    min_days_to_expiry: int = 2,
    force: bool = False,
    master_text: Optional[str] = None,
) -> DhanContract:
    """
    The nearest USD/INR future that still has ``>= min_days_to_expiry`` days left.
    Raises if the master has nothing suitable (e.g. a stale snapshot).
    """
    as_of = as_of or date.today()
    futs = usdinr_futures(exchange, force=force, master_text=master_text)
    live = [c for c in futs if (c.expiry - as_of).days >= min_days_to_expiry]
    if not live:
        raise RuntimeError(
            f"no upcoming {exchange} USDINR FUTCUR in the scrip master as of {as_of} "
            f"(found {len(futs)} total, latest expiry "
            f"{futs[-1].expiry if futs else 'none'}). Try force=True to refresh."
        )
    return live[0]
