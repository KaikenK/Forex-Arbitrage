"""
Upstox instrument-master resolver.

Downloads Upstox's public NSE instrument list (no API key needed) and resolves
the near-month NSE USD/INR currency future for `UpstoxDataSource`.

Unlike Dhan's public master, Upstox's list is kept current — it carries the live
NSE currency board (weekly + monthly USDINR futures out ~12 months). Currency
derivatives are segment ``NCD_FO``; the near-month *monthly* contract is the one
with ``weekly == False``.

Master format (gzipped JSON array), fields we use:
    segment, name, instrument_type, instrument_key, trading_symbol,
    expiry (epoch ms), lot_size, qty_multiplier, weekly
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
_CACHE = Path(__file__).resolve().parents[3] / "data" / "cache" / "upstox_nse_instruments.json.gz"
_CACHE_TTL_S = 12 * 3600

CURRENCY_SEGMENT = "NCD_FO"          # NSE currency derivatives
_UNDERLYING = "USDINR"


@dataclass(frozen=True)
class UpstoxOption:
    instrument_key: str
    trading_symbol: str
    expiry: date
    strike: float
    option_type: str                 # "CE" | "PE"


@dataclass(frozen=True)
class UpstoxContract:
    instrument_key: str              # e.g. "NCD_FO|1769" — what the API takes
    trading_symbol: str              # "USDINR FUT 28 SEP 26"
    expiry: date
    lot_size: int
    qty_multiplier: float            # contract size in USD (1000)
    weekly: bool

    def to_dict(self) -> dict:
        return {
            "instrument_key": self.instrument_key,
            "trading_symbol": self.trading_symbol,
            "expiry": self.expiry.isoformat(),
            "lot_size": self.lot_size,
            "qty_multiplier": self.qty_multiplier,
            "weekly": self.weekly,
        }


class StaleMasterError(RuntimeError):
    """Raised when the instrument master has no unexpired USD/INR future."""


def _download_master(force: bool = False) -> bytes:
    _CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not force and _CACHE.exists() and (time.time() - _CACHE.stat().st_mtime) < _CACHE_TTL_S:
        return _CACHE.read_bytes()
    req = urllib.request.Request(MASTER_URL, headers={
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
    _CACHE.write_bytes(raw)
    logger.info("[upstox_instruments] refreshed instrument master (%d bytes)", len(raw))
    return raw


def load_master(force: bool = False, master_json: Optional[list] = None) -> list:
    if master_json is not None:
        return master_json
    raw = _download_master(force=force)
    try:
        return json.loads(gzip.decompress(raw))
    except (OSError, gzip.BadGzipFile):
        # already-decompressed cache or a plain JSON body
        return json.loads(raw)


def _expiry_of(row: dict) -> Optional[date]:
    ms = row.get("expiry")
    if not ms:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).date()
    except (ValueError, OSError, TypeError):
        return None


def usdinr_futures(
    *,
    force: bool = False,
    master_json: Optional[list] = None,
) -> List[UpstoxContract]:
    """All NSE USD/INR FUT contracts, sorted by expiry (weekly + monthly)."""
    out: List[UpstoxContract] = []
    for r in load_master(force=force, master_json=master_json):
        if r.get("segment") != CURRENCY_SEGMENT:
            continue
        if r.get("instrument_type") != "FUT":
            continue
        if r.get("name") != _UNDERLYING and r.get("underlying_symbol") != _UNDERLYING:
            continue
        exp = _expiry_of(r)
        if exp is None:
            continue
        out.append(UpstoxContract(
            instrument_key=str(r.get("instrument_key", "")),
            trading_symbol=str(r.get("trading_symbol", "")),
            expiry=exp,
            lot_size=int(r.get("lot_size") or 1),
            qty_multiplier=float(r.get("qty_multiplier") or 1000.0),
            weekly=bool(r.get("weekly", False)),
        ))
    return sorted(out, key=lambda c: c.expiry)


def resolve_near_month(
    *,
    as_of: Optional[date] = None,
    min_days_to_expiry: int = 2,
    prefer_monthly: bool = True,
    nth: int = 0,
    force: bool = False,
    master_json: Optional[list] = None,
) -> UpstoxContract:
    """
    The ``nth`` unexpired USD/INR future (0 = nearest). With ``prefer_monthly``
    the monthly contracts (``weekly == False``) are used when available — the
    standard basis reference, matching the EOD NSE-settlement series. ``nth=1``
    gives the far-month contract for calendar comparison.
    """
    as_of = as_of or date.today()
    futs = usdinr_futures(force=force, master_json=master_json)
    live = [c for c in futs if (c.expiry - as_of).days >= min_days_to_expiry]
    if not live:
        raise StaleMasterError(
            f"no unexpired NSE USDINR FUT in the Upstox master as of {as_of} "
            f"({len(futs)} total, latest {futs[-1].expiry if futs else 'none'}). "
            f"Set UPSTOX_USDINR_INSTRUMENT_KEY to pin it."
        )
    pool = [c for c in live if not c.weekly] if prefer_monthly else list(live)
    pool = pool or live
    return pool[min(nth, len(pool) - 1)]


def usdinr_options(
    expiry: date,
    *,
    force: bool = False,
    master_json: Optional[list] = None,
) -> List[UpstoxOption]:
    """All NSE USD/INR CE/PE contracts for one expiry, sorted by strike then type."""
    out: List[UpstoxOption] = []
    for r in load_master(force=force, master_json=master_json):
        if r.get("segment") != CURRENCY_SEGMENT:
            continue
        if r.get("instrument_type") not in ("CE", "PE"):
            continue
        if r.get("name") != _UNDERLYING and r.get("underlying_symbol") != _UNDERLYING:
            continue
        if _expiry_of(r) != expiry:
            continue
        try:
            k = float(r.get("strike_price"))
        except (TypeError, ValueError):
            continue
        out.append(UpstoxOption(
            instrument_key=str(r.get("instrument_key", "")),
            trading_symbol=str(r.get("trading_symbol", "")),
            expiry=expiry,
            strike=k,
            option_type=r["instrument_type"],
        ))
    return sorted(out, key=lambda o: (o.strike, o.option_type))
