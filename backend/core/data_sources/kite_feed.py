"""
Zerodha Kite Connect feed — NSE (CDS) and BSE (BCD) currency futures (USDINR,
EURINR, GBPINR, JPYINR, EURUSD, GBPUSD, USDJPY).

The low-cost showcase provider: Kite Connect market data costs ₹500/month,
versus institutional terminals at thousands of dollars. It is a second,
independent provider for the *same* exchange contracts Upstox serves, so the
system can compare providers on one venue (Upstox-NSE vs Kite-NSE) as well as
venues through one provider (NSE vs BSE).

Plain REST, no ``kiteconnect`` SDK needed:
  instruments  GET https://api.kite.trade/instruments/{CDS|BCD}   (CSV)
  quotes       GET https://api.kite.trade/quote?i=CDS:SYM&i=BCD:SYM
  headers      X-Kite-Version: 3,  Authorization: token <api_key>:<access_token>

Auth: ``KITE_API_KEY`` + ``KITE_ACCESS_TOKEN``. The access token expires daily
(~06:00 IST) and is generated through Kite's login redirect; paste the day's
token into ``.env`` before a session. Quote rate limit is 1 request/second, so
the poll interval defaults to 1.0 s. Read-only: no order endpoint is called.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.quote_hub import PolledQuoteHub, Quote, QuoteAuthError
from backend.core.instruments import futures_lot_units

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
API_ROOT = "https://api.kite.trade"
EXCHANGE_CODES = {"NSE": "CDS", "BSE": "BCD"}
UNDERLYING = "USDINR"   # default when no symbols are requested
_MONTHLY_SYMBOL = re.compile(r"^[A-Z]{6}\d{2}[A-Z]{3}FUT$")   # weekly: USDINR26O16FUT


def _headers(api_key: str, access_token: str) -> Dict[str, str]:
    return {"X-Kite-Version": "3", "Authorization": f"token {api_key}:{access_token}"}


def _as_float(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _ist_to_ms(text) -> Optional[int]:
    if not text:
        return None
    try:
        dt = datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    except ValueError:
        return None
    return int(dt.timestamp() * 1000)


# -- instruments -------------------------------------------------------------

def parse_kite_instruments(csv_text: str, *, exchange: str,
                           symbols: Optional[List[str]] = None) -> List[FutureContract]:
    code = EXCHANGE_CODES[exchange]
    wanted = set(symbols or [UNDERLYING])
    out: List[FutureContract] = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        if row.get("name") not in wanted or row.get("instrument_type") != "FUT":
            continue
        if row.get("exchange") != code or not row.get("expiry"):
            continue
        symbol = row["tradingsymbol"]
        out.append(FutureContract(
            exchange=exchange,
            instrument_key=f"{code}:{symbol}",
            trading_symbol=symbol,
            expiry=date.fromisoformat(row["expiry"]),
            lot_size=int(float(row.get("lot_size") or 1)),
            contract_size=futures_lot_units(row["name"]),   # e.g. USD 1,000 per USDINR lot
            weekly=not _MONTHLY_SYMBOL.match(symbol),
            provider="kite",
            symbol=row["name"],
        ))
    return sorted(out, key=lambda c: (c.symbol, c.expiry))



def load_kite_contracts(exchange: str, symbols: Optional[List[str]] = None, *,
                        api_key: Optional[str] = None,
                        access_token: Optional[str] = None) -> List[FutureContract]:
    api_key = api_key or os.environ.get("KITE_API_KEY", "")
    access_token = access_token or os.environ.get("KITE_ACCESS_TOKEN", "")
    req = urllib.request.Request(f"{API_ROOT}/instruments/{EXCHANGE_CODES[exchange]}",
                                 headers=_headers(api_key, access_token))
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8")
    return parse_kite_instruments(text, exchange=exchange, symbols=symbols)


# -- quotes ------------------------------------------------------------------

def _levels(side) -> List[Dict[str, float]]:
    out = []
    for lv in side or []:
        p = _as_float(lv.get("price"))
        if p and p > 0:
            out.append({"price": p, "qty": float(lv.get("quantity") or 0)})
    return out


def parse_kite_quotes(payload: dict, *, recv_ts_ms: int) -> Dict[str, Quote]:
    out: Dict[str, Quote] = {}
    for key, node in (payload.get("data") or {}).items():
        if not isinstance(node, dict):
            continue
        depth = node.get("depth") or {}
        bids, asks = _levels(depth.get("buy")), _levels(depth.get("sell"))
        out[key] = Quote(
            bid=bids[0]["price"] if bids else None,
            ask=asks[0]["price"] if asks else None,
            bid_qty=bids[0]["qty"] if bids else 0.0,
            ask_qty=asks[0]["qty"] if asks else 0.0,
            last=_as_float(node.get("last_price")),
            exchange_ts_ms=_ist_to_ms(node.get("last_trade_time")),
            recv_ts_ms=recv_ts_ms,
            depth={"bids": bids, "asks": asks},
        )
    return out


def make_kite_fetcher(api_key: str, access_token: str, *, timeout_s: float = 5.0):
    def fetch(keys: List[str]) -> Dict[str, Quote]:
        qs = urllib.parse.urlencode([("i", k) for k in keys])
        req = urllib.request.Request(f"{API_ROOT}/quote?{qs}", headers=_headers(api_key, access_token))
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")[:300]
            if e.code in (401, 403):
                raise QuoteAuthError(f"Kite {e.code} — refresh KITE_ACCESS_TOKEN (expires daily): {body}") from e
            raise RuntimeError(f"Kite HTTP {e.code}: {body}") from e
        return parse_kite_quotes(payload, recv_ts_ms=int(time.time() * 1000))
    return fetch


def make_kite_hub(api_key: Optional[str] = None, access_token: Optional[str] = None) -> PolledQuoteHub:
    api_key = api_key or os.environ.get("KITE_API_KEY")
    access_token = access_token or os.environ.get("KITE_ACCESS_TOKEN")
    if not api_key or not access_token:
        raise QuoteAuthError("KITE_API_KEY / KITE_ACCESS_TOKEN are not set")
    interval = max(1.0, float(os.environ.get("KITE_POLL_INTERVAL", "1.0")))  # 1 req/s limit
    return PolledQuoteHub("kite", make_kite_fetcher(api_key, access_token), poll_interval_s=interval)
