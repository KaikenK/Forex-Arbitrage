"""
Upstox (API v2) feed — NSE and BSE currency futures (USDINR, EURINR, GBPINR,
JPYINR and the EURUSD/GBPUSD/USDJPY cross-currency futures). Free.

Ported from the `arbex-pivot` branch (`upstox_instruments.py`,
`upstox_data_source.py`, verified live there on 2026-08-31) and generalised:

* exchange-aware instrument master: NSE currency derivatives are segment
  ``NCD_FO`` in ``NSE.json.gz``; BSE currency derivatives are ``BCD_FO`` in
  ``BSE.json.gz``;
* expiry dates are read in IST (the master stores an epoch instant; a UTC
  conversion can land on the wrong calendar day);
* one quote request for *all* legs (the endpoint takes a comma-separated
  ``instrument_key`` list), so NSE and BSE are observed at the same instant;
* only genuine two-sided books are emitted — the pivot's last-price fallback
  produced ``bid == ask`` quotes off-hours, which can fake a crossed market.

Auth: ``UPSTOX_ACCESS_TOKEN`` — an Analytics Token from the Upstox developer
console (about a year's validity) or a daily OAuth access token. Market data is
free on Upstox. Read-only: no trading endpoint is ever called.

Upstox gotchas (all handled here, found on `arbex-pivot`):
  1. api.upstox.com is behind Cloudflare and blocks Python's default
     User-Agent (error 1010) — send a browser UA.
  2. The full-quote endpoint is ``/v2/market-quote/quotes`` (plural).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.quote_hub import PolledQuoteHub, Quote, QuoteAuthError
from backend.core.instruments import futures_lot_units

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
UNDERLYING = "USDINR"   # default when no symbols are requested
QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"
MASTER_URL = "https://assets.upstox.com/market-quote/instruments/exchange/{exchange}.json.gz"
SEGMENTS = {"NSE": "NCD_FO", "BSE": "BCD_FO"}
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "cache"
_CACHE_TTL_S = 12 * 3600


def _as_float(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _as_int(v) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# -- instrument master -------------------------------------------------------

def parse_upstox_master(rows: List[dict], *, exchange: str,
                        symbols: Optional[List[str]] = None) -> List[FutureContract]:
    """Currency futures on `symbols` (default USDINR) for one exchange, from an
    Upstox instrument-master array."""
    segment = SEGMENTS[exchange]
    wanted = set(symbols or [UNDERLYING])
    out: List[FutureContract] = []
    for r in rows:
        if r.get("segment") != segment or r.get("instrument_type") != "FUT":
            continue
        underlying = next((n for n in (r.get("underlying_symbol"), r.get("name")) if n in wanted), None)
        if underlying is None:
            continue
        ms = _as_int(r.get("expiry"))
        if ms is None:
            continue
        out.append(FutureContract(
            exchange=exchange,
            instrument_key=str(r.get("instrument_key", "")),
            trading_symbol=str(r.get("trading_symbol", "")),
            expiry=datetime.fromtimestamp(ms / 1000, tz=IST).date(),
            lot_size=int(r.get("lot_size") or 1),
            contract_size=float(r.get("qty_multiplier") or futures_lot_units(underlying)),
            weekly=bool(r.get("weekly", False)),
            provider="upstox",
            symbol=underlying,
        ))
    return sorted(out, key=lambda c: (c.symbol, c.expiry))



def load_upstox_contracts(exchange: str, symbols: Optional[List[str]] = None, *,
                          force: bool = False) -> List[FutureContract]:
    """Download (12 h cache) the public instrument master and parse it. No auth needed."""
    cache = _CACHE_DIR / f"upstox_{exchange.lower()}_instruments.json.gz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if force or not cache.exists() or time.time() - cache.stat().st_mtime > _CACHE_TTL_S:
        req = urllib.request.Request(MASTER_URL.format(exchange=exchange),
                                     headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            cache.write_bytes(resp.read())
    raw = cache.read_bytes()
    try:
        rows = json.loads(gzip.decompress(raw))
    except OSError:
        rows = json.loads(raw)
    return parse_upstox_master(rows, exchange=exchange, symbols=symbols)


# -- quotes ------------------------------------------------------------------

def _levels(side) -> List[Dict[str, float]]:
    out = []
    for lv in side or []:
        p = _as_float(lv.get("price"))
        if p and p > 0:
            out.append({"price": p, "qty": float(lv.get("quantity") or 0)})
    return out


def parse_upstox_quotes(payload: dict, *, recv_ts_ms: int) -> Dict[str, Quote]:
    """Map an Upstox full-quote response to {instrument_key: Quote}."""
    data = payload.get("data") or {}
    out: Dict[str, Quote] = {}
    for response_key, node in data.items():
        if not isinstance(node, dict):
            continue
        # The response is keyed "SEGMENT:SYMBOL"; the request key ("SEGMENT|token")
        # is echoed as instrument_token.
        key = str(node.get("instrument_token") or response_key)
        depth = node.get("depth") or {}
        bids, asks = _levels(depth.get("buy")), _levels(depth.get("sell"))
        out[key] = Quote(
            bid=bids[0]["price"] if bids else None,
            ask=asks[0]["price"] if asks else None,
            bid_qty=bids[0]["qty"] if bids else 0.0,
            ask_qty=asks[0]["qty"] if asks else 0.0,
            last=_as_float(node.get("last_price")),
            exchange_ts_ms=_as_int(node.get("last_trade_time")),
            recv_ts_ms=recv_ts_ms,
            depth={"bids": bids, "asks": asks},
        )
    return out


def make_upstox_fetcher(token: str, *, timeout_s: float = 5.0):
    def fetch(keys: List[str]) -> Dict[str, Quote]:
        qs = urllib.parse.urlencode({"instrument_key": ",".join(keys)})
        req = urllib.request.Request(f"{QUOTE_URL}?{qs}", headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "User-Agent": BROWSER_UA,
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code == 401:
                raise QuoteAuthError(f"Upstox 401 — regenerate UPSTOX_ACCESS_TOKEN: {body}") from e
            raise RuntimeError(f"Upstox HTTP {e.code}: {body}") from e
        return parse_upstox_quotes(payload, recv_ts_ms=int(time.time() * 1000))
    return fetch


def make_upstox_hub(token: Optional[str] = None) -> PolledQuoteHub:
    token = token or os.environ.get("UPSTOX_ACCESS_TOKEN")
    if not token:
        raise QuoteAuthError("UPSTOX_ACCESS_TOKEN is not set")
    interval = float(os.environ.get("UPSTOX_POLL_INTERVAL", "1.0"))
    return PolledQuoteHub("upstox", make_upstox_fetcher(token), poll_interval_s=interval)
