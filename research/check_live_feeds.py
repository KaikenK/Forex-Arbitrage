"""
Pre-flight check for live mode: run this on your machine before a demo.

    python research/check_live_feeds.py                 # Upstox (+ Kite if creds set)
    python research/check_live_feeds.py --providers upstox
    python research/check_live_feeds.py --symbols all   # every NSE/BSE-listed pair

For each provider, exchange and pair it resolves the futures, picks the common
expiry per pair, fetches ONE quote for every leg in a single request per
provider, and prints the books plus the executable cross-spread per pair. Pairs
that cannot be set up are listed with the reason (e.g. not listed on BSE). If
an exchange resolves no contracts it prints the segments that mention each pair
in the master, which is how to fix a wrong segment code (BSE currency futures
are expected as BCD_FO). --symbols defaults to ARBEX_SYMBOLS, else USDINR.

Reads credentials from the environment / .env (UPSTOX_ACCESS_TOKEN,
KITE_API_KEY, KITE_ACCESS_TOKEN). Never places orders.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from backend.config import configured_symbols  # noqa: E402
from backend.core.data_sources.live_feeds import (  # noqa: E402
    LiveFeedUnavailable,
    build_live_exchange_sources,
    cross_spreads,
)
from backend.core.instruments import parse_symbol_list  # noqa: E402


def _diagnose_upstox_segments(exchange: str, symbols) -> None:
    import gzip
    import json
    from backend.core.data_sources.upstox_feed import _CACHE_DIR
    cache = _CACHE_DIR / f"upstox_{exchange.lower()}_instruments.json.gz"
    if not cache.exists():
        return
    raw = cache.read_bytes()
    try:
        rows = json.loads(gzip.decompress(raw))
    except OSError:
        rows = json.loads(raw)
    for sym in symbols:
        segs = sorted({(r.get("segment"), r.get("instrument_type")) for r in rows
                       if sym in str(r.get("name")) + str(r.get("trading_symbol"))})
        print(f"    {sym} rows in Upstox {exchange} master by (segment, type): {segs or 'none'}")


IST = timezone(timedelta(hours=5, minutes=30))


def _market_open(now: datetime) -> bool:
    """NSE/BSE currency derivatives: Mon-Fri 09:00-17:00 IST (exchange holidays not checked)."""
    return now.weekday() < 5 and 9 <= now.hour < 17


def _verdict(quotes, now: datetime) -> str:
    empty = sorted(k for k, q in quotes.items() if q is None or not q.two_sided)
    if not _market_open(now):
        return (f"Outside currency market hours ({now:%a %H:%M} IST; Mon-Fri 09:00-17:00) - "
                f"empty books are expected.")
    if empty:
        return (f"Market is open ({now:%a %H:%M} IST) and the connection works, but "
                f"{', '.join(empty)} has no live orders right now: that venue is illiquid, "
                f"not a connection problem.")
    return f"OK - market open ({now:%a %H:%M} IST) and every leg has a two-sided book."


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="upstox,kite")
    ap.add_argument("--exchanges", default="NSE,BSE")
    ap.add_argument("--symbols", default=None, help='e.g. "USDINR,EURINR" or "all" (default: ARBEX_SYMBOLS)')
    args = ap.parse_args()
    symbols = parse_symbol_list(args.symbols) if args.symbols else configured_symbols()
    print(f"Pairs: {', '.join(symbols)}")

    try:
        setup = build_live_exchange_sources(
            symbols=symbols, providers=args.providers.split(","), exchanges=args.exchanges.split(","),
            as_of=date.today(), record_dir="off")
    except LiveFeedUnavailable as e:
        print(f"FAIL: {e}")
        if "upstox" in args.providers:
            for ex in args.exchanges.split(","):
                _diagnose_upstox_segments(ex, symbols)
        return 1

    print("Legs:")
    for line in setup.describe():
        print("  " + line)

    quotes = {}
    for name, hub in setup.hubs.items():
        legs = [s for s in setup.sources if s.hub is hub]
        for s in legs:
            hub.register(s.contract.instrument_key)
        try:
            hub.poll_once()          # one request for all of this provider's legs
        except Exception as e:
            print(f"FAIL: {name} quote request: {e}")
            return 1
        print(f"\n{name}: one request, {hub.stats()['last_latency_ms']} ms")
        for s in legs:
            q = hub.latest(s.contract.instrument_key)
            quotes[s.source_id] = q
            book = (f"bid {q.bid} x {q.bid_qty}  ask {q.ask} x {q.ask_qty}" if q and q.two_sided
                    else f"NO two-sided book (last {q.last if q else None})")
            print(f"  {s.source_id:22s} {book}")

    print("\nExecutable cross-spread per pair (INR pairs in paise, others in pips;"
          " > 0 means crossed = arbitrage before costs):")
    rows = cross_spreads(quotes, {s.source_id: s.symbol for s in setup.sources})
    for sym in symbols:
        mine = [r for r in rows if r["symbol"] == sym]
        if sym in setup.skipped and not mine:
            print(f"  {sym}: skipped - {setup.skipped[sym]}")
        for row in mine or ([] if sym in setup.skipped else [{"note": "fewer than two two-sided books right now"}]):
            print(f"  {sym}: {row}")
    print("\n" + _verdict(quotes, datetime.now(IST)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
