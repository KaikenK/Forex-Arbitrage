"""
Free, keyless spot FX venues for the non-INR pairs.

NSE/BSE list only a few non-INR futures and their books are mostly empty, so
for EURUSD, GBPUSD, USDJPY, ... the live legs come from public spot quotes:

    venue      what it is                                  pairs
    kraken     crypto exchange with fiat FX order books    EURUSD GBPUSD USDJPY USDCAD AUDUSD
               (executable, public REST, no key)           USDCHF EURGBP EURJPY EURCHF
    bitstamp   crypto exchange with fiat FX order books    EURUSD GBPUSD
               (executable, public REST, no key)
    truefx     Integral ECN interbank top of book          EURUSD USDJPY GBPUSD EURGBP USDCHF
               (indicative, unauthenticated snapshot)      EURJPY EURCHF USDCAD AUDUSD

Every venue is fetched *concurrently inside one poll* of a single hub named
"spot", so all spot legs share one observation timestamp: the same rule the
NSE/BSE legs follow (one request per provider). A venue that fails is left
out of that poll; the others still publish.

Leg ids are ``spot_<venue>_<pair>`` (e.g. ``spot_kraken_eurusd``), so the
venue is the second field like the exchange is for ``upstox_nse_usdinr``.

Caveats worth stating in results: TrueFX is indicative (not a book a retail
account can hit); Kraken/Bitstamp are executable but charge taker fees
(~0.1-0.4%) that dwarf a sub-pip cross. Opportunities are "before costs".

Environment:
    ARBEX_SPOT_VENUES        default "kraken,truefx"; add bitstamp for a third
                             EURUSD/GBPUSD venue (it resets connections from
                             some networks, so it is opt-in)
    ARBEX_SPOT_POLL_INTERVAL default 1.0 seconds
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from backend.core.data_sources.exchange_contracts import FutureContract
from backend.core.data_sources.quote_hub import PolledQuoteHub, Quote

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
MAX_QUOTE_AGE_S = 30   # a venue timestamp older than this is a stale book, not a price

# pair -> venue's own instrument code
VENUE_PAIRS: Dict[str, Dict[str, str]] = {
    "kraken": {"EURUSD": "ZEURZUSD", "GBPUSD": "ZGBPZUSD", "USDJPY": "ZUSDZJPY", "USDCAD": "ZUSDZCAD",
               "AUDUSD": "AUDUSD", "USDCHF": "USDCHF", "EURGBP": "EURGBP", "EURJPY": "EURJPY",
               "EURCHF": "EURCHF"},
    "bitstamp": {"EURUSD": "eurusd", "GBPUSD": "gbpusd"},
    "truefx": {"EURUSD": "EUR/USD", "USDJPY": "USD/JPY", "GBPUSD": "GBP/USD", "EURGBP": "EUR/GBP",
               "USDCHF": "USD/CHF", "EURJPY": "EUR/JPY", "EURCHF": "EUR/CHF", "USDCAD": "USD/CAD",
               "AUDUSD": "AUD/USD", "GBPJPY": "GBP/JPY"},
}
VENUE_NAMES = {"kraken": "Kraken", "bitstamp": "Bitstamp", "truefx": "TrueFX"}
DEFAULT_VENUES = "kraken,truefx"

KRAKEN_URL = "https://api.kraken.com/0/public/Ticker?pair={pairs}"
BITSTAMP_URL = "https://www.bitstamp.net/api/v2/ticker/{pair}/"
TRUEFX_URL = "https://webrates.truefx.com/rates/connect.html?f=csv"


def _get(url: str, timeout_s: float) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout_s) as r:
        return r.read()


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x > 0 else None
    except (TypeError, ValueError):
        return None


def _fresh(ts_ms: Optional[int]) -> bool:
    return ts_ms is None or time.time() * 1000 - ts_ms <= MAX_QUOTE_AGE_S * 1000


# -- parsers (pure, unit-tested) ---------------------------------------------

def parse_kraken(payload: dict) -> Dict[str, Quote]:
    """Kraken /0/public/Ticker -> {kraken:<code>: Quote}. b/a = [price, whole-lot vol, lot vol]."""
    if payload.get("error"):
        raise RuntimeError(f"Kraken: {payload['error']}")
    out = {}
    for code, t in (payload.get("result") or {}).items():
        bid, ask = _f(t.get("b", [None])[0]), _f(t.get("a", [None])[0])
        out[f"kraken:{code}"] = Quote(bid=bid, ask=ask, bid_qty=float(t["b"][2]) if bid else 0.0,
                                      ask_qty=float(t["a"][2]) if ask else 0.0, last=_f(t.get("c", [None])[0]),
                                      depth={"bids": [{"price": bid, "qty": float(t["b"][2])}] if bid else [],
                                             "asks": [{"price": ask, "qty": float(t["a"][2])}] if ask else []})
    return out


def parse_bitstamp(code: str, payload: dict) -> Dict[str, Quote]:
    ts = int(payload["timestamp"]) * 1000 if payload.get("timestamp") else None
    if not _fresh(ts):
        return {}
    bid, ask = _f(payload.get("bid")), _f(payload.get("ask"))
    return {f"bitstamp:{code}": Quote(bid=bid, ask=ask, last=_f(payload.get("last")), exchange_ts_ms=ts,
                                      depth={"bids": [{"price": bid, "qty": 0.0}] if bid else [],
                                             "asks": [{"price": ask, "qty": 0.0}] if ask else []})}


def parse_truefx(csv_text: str) -> Dict[str, Quote]:
    """TrueFX CSV: pair,ts_ms,bid_big,bid_points,ask_big,ask_points,high,low,open.
    The price is big figure + points as text: '1.12' + '029' = 1.12029."""
    out = {}
    for line in csv_text.strip().splitlines():
        f = line.strip().split(",")
        if len(f) < 6 or "/" not in f[0]:
            continue
        try:
            ts = int(f[1])
            bid, ask = float(f[2] + f[3]), float(f[4] + f[5])
        except ValueError:
            continue
        if not _fresh(ts):
            continue
        out[f"truefx:{f[0]}"] = Quote(bid=bid, ask=ask, exchange_ts_ms=ts,
                                      depth={"bids": [{"price": bid, "qty": 0.0}], "asks": [{"price": ask, "qty": 0.0}]})
    return out


# -- fetchers ----------------------------------------------------------------

def _fetch_kraken(codes: List[str], get, timeout_s) -> Dict[str, Quote]:
    return parse_kraken(json.loads(get(KRAKEN_URL.format(pairs=",".join(codes)), timeout_s)))


def _fetch_bitstamp(codes: List[str], get, timeout_s) -> Dict[str, Quote]:
    # one pair per request, sent concurrently; a dropped connection loses only that pair
    def one(code):
        try:
            return parse_bitstamp(code, json.loads(get(BITSTAMP_URL.format(pair=code), timeout_s)))
        except Exception as e:
            logger.debug(f"bitstamp {code}: {e}")
            return {}
    out: Dict[str, Quote] = {}
    with ThreadPoolExecutor(max_workers=max(1, len(codes))) as pool:
        for part in pool.map(one, codes):
            out.update(part)
    return out


def _fetch_truefx(codes: List[str], get, timeout_s) -> Dict[str, Quote]:
    wanted = {f"truefx:{c}" for c in codes}
    return {k: q for k, q in parse_truefx(get(TRUEFX_URL, timeout_s).decode()).items() if k in wanted}


FETCHERS = {"kraken": _fetch_kraken, "bitstamp": _fetch_bitstamp, "truefx": _fetch_truefx}


class SpotFetcher:
    """One poll = every venue fetched in parallel; failures are per venue."""

    def __init__(self, get: Callable[[str, float], bytes] = _get, timeout_s: float = 4.0):
        self._get = get
        self._timeout = timeout_s
        self._pool = ThreadPoolExecutor(max_workers=len(FETCHERS), thread_name_prefix="spot")
        self.venue_errors: Dict[str, Optional[str]] = {}

    def __call__(self, keys: List[str]) -> Dict[str, Quote]:
        by_venue: Dict[str, List[str]] = {}
        for k in keys:
            venue, code = k.split(":", 1)
            by_venue.setdefault(venue, []).append(code)
        futures = {v: self._pool.submit(FETCHERS[v], codes, self._get, self._timeout)
                   for v, codes in by_venue.items() if v in FETCHERS}
        out: Dict[str, Quote] = {}
        for venue, fut in futures.items():
            try:
                out.update(fut.result())
                self.venue_errors[venue] = None
            except Exception as e:
                if self.venue_errors.get(venue) is None:
                    logger.warning(f"spot venue {venue} failed: {e}")
                self.venue_errors[venue] = str(e)
        if futures and not out and all(self.venue_errors.get(v) for v in futures):
            raise RuntimeError(f"every spot venue failed: {self.venue_errors}")
        return out


def make_spot_hub(get: Callable[[str, float], bytes] = _get) -> PolledQuoteHub:
    interval = float(os.environ.get("ARBEX_SPOT_POLL_INTERVAL", "1.0"))
    return PolledQuoteHub("spot", SpotFetcher(get), poll_interval_s=interval)


def spot_contracts(symbols: List[str], venues: Optional[List[str]] = None) -> Dict[str, List[FutureContract]]:
    """{symbol: [one spot 'contract' per venue quoting it]}; expiry is None for spot."""
    venues = venues or [v.strip() for v in os.environ.get("ARBEX_SPOT_VENUES", DEFAULT_VENUES).split(",")
                        if v.strip()]
    out: Dict[str, List[FutureContract]] = {}
    for sym in symbols:
        for venue in venues:
            code = VENUE_PAIRS.get(venue, {}).get(sym)
            if code:
                out.setdefault(sym, []).append(FutureContract(
                    exchange=venue.upper(), instrument_key=f"{venue}:{code}", trading_symbol=f"{sym} spot ({code})",
                    expiry=None, lot_size=1, contract_size=1.0, provider=venue, symbol=sym))
    return out
