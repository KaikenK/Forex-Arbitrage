"""
Pre-flight check for live mode: run this on your machine before a demo.

    python research/check_live_feeds.py                 # Upstox (+ Kite if creds set)
    python research/check_live_feeds.py --providers upstox

For each provider and exchange it resolves the USD/INR futures, picks the
common expiry, fetches ONE quote for every leg in a single request, and prints
the books plus the executable cross-spread. If an exchange resolves no
contracts it prints the segments that mention USDINR in the master, which is
how to fix a wrong segment code (BSE currency futures are expected as BCD_FO).

Reads credentials from the environment / .env (UPSTOX_ACCESS_TOKEN,
KITE_API_KEY, KITE_ACCESS_TOKEN). Never places orders.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from backend.core.data_sources.live_feeds import (  # noqa: E402
    LiveFeedUnavailable,
    build_live_exchange_sources,
    cross_spreads,
)


def _diagnose_upstox_segments(exchange: str) -> None:
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
    segs = sorted({(r.get("segment"), r.get("instrument_type")) for r in rows
                   if "USDINR" in str(r.get("name")) + str(r.get("trading_symbol"))})
    print(f"    USDINR rows in Upstox {exchange} master by (segment, type): {segs or 'none'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="upstox,kite")
    ap.add_argument("--exchanges", default="NSE,BSE")
    args = ap.parse_args()

    try:
        setup = build_live_exchange_sources(
            providers=args.providers.split(","), exchanges=args.exchanges.split(","),
            as_of=date.today(), record_dir="off")
    except LiveFeedUnavailable as e:
        print(f"FAIL: {e}")
        if "upstox" in args.providers:
            for ex in args.exchanges.split(","):
                _diagnose_upstox_segments(ex)
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

    print("\nExecutable cross-spread (paise; > 0 means crossed = arbitrage before costs):")
    for row in cross_spreads(quotes) or [{"note": "fewer than two two-sided books right now"}]:
        print(f"  {row}")
    print("\nOK — outside NSE currency hours (Mon-Fri 09:00-17:00 IST) books are usually empty.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
