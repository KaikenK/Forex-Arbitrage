"""
Validate an Upstox setup and print a few onshore USD/INR quotes.

    python research/check_upstox.py
    python research/check_upstox.py --probe "NCD_FO|1769"

Upstox market data is free (no data subscription). This script:
  (1) resolves the near-month NSE USD/INR future from the public instrument master,
  (2) probes it via /v2/market-quote/quotes (that call IS the token healthcheck —
      market-data GETs need no static IP),
  (3) polls the quote endpoint and prints last price + 5-level depth.

Read-only — it never places an order. Off-hours the depth book is empty but the
quote endpoint still returns the previous close, which is enough to confirm the
token has market-data access.

    UPSTOX_ACCESS_TOKEN            required — an *analytics token* (1-year, no daily
                                  re-auth, generate from the Developer Apps > Analytics
                                  tab) OR a standard OAuth access token (expires daily
                                  ~03:30 IST). Try the analytics token first; if a quote
                                  call returns UDAPI100050 fall back to the OAuth token.
    UPSTOX_USDINR_INSTRUMENT_KEY   optional pin, e.g. "NCD_FO|1769"
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except Exception:
    pass

from backend.core.data_sources.upstox_data_source import UpstoxDataSource  # noqa: E402
from backend.core.data_sources.upstox_instruments import (  # noqa: E402
    resolve_near_month,
    usdinr_futures,
)
from backend.core.interfaces.data_source import DataSourceConfig  # noqa: E402

_QUOTE_URL = "https://api.upstox.com/v2/market-quote/quotes"
# Cloudflare on api.upstox.com 1010-blocks Python-urllib's default UA.
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


def _get(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/json",
        "User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read() or b"{}")


def _probe(token: str, key: str) -> None:
    url = f"{_QUOTE_URL}?instrument_key={urllib.parse.quote(key, safe='')}"
    try:
        body = _get(url, token)
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        print(f"probe {key}: HTTP {e.code}  {detail}")
        if "1010" in detail or "browser" in detail.lower():
            print("  -> Cloudflare bot-block on the User-Agent. Upgrade this file / the"
                  " adapter (they now send a browser UA). If it persists, install the"
                  " official SDK:  pip install upstox-python-sdk  and switch the client.")
        elif "UDAPI100050" in detail or e.code == 401:
            print("  -> token rejected. If this is an ANALYTICS token, some accounts hit a"
                  " known bug on market-quote — generate a standard OAuth access token"
                  " instead (authorization-code flow) and put that in UPSTOX_ACCESS_TOKEN.")
        return
    data = body.get("data") or {}
    node = next(iter(data.values()), None)
    if not node:
        print(f"probe {key}: empty data — {json.dumps(body)[:300]}")
        return
    depth = node.get("depth") or {}
    buy, sell = depth.get("buy") or [], depth.get("sell") or []
    top_bid = buy[0]["price"] if buy else None
    top_ask = sell[0]["price"] if sell else None
    print(f"probe {key}:")
    print(f"  symbol      : {node.get('symbol')}")
    print(f"  last_price   : {node.get('last_price')}   ohlc={node.get('ohlc')}")
    print(f"  depth        : {len(buy)}x{len(sell)}   top {top_bid} / {top_ask}")
    print(f"  volume / oi   : {node.get('volume')} / {node.get('oi')}")
    if node.get("last_price"):
        print("  -> market data works. Pin it: "
              f'UPSTOX_USDINR_INSTRUMENT_KEY="{key}" in .env')


def main() -> None:
    args = sys.argv[1:]
    probe = args[args.index("--probe") + 1] if "--probe" in args else None

    token = os.environ.get("UPSTOX_ACCESS_TOKEN")
    print(f"UPSTOX_ACCESS_TOKEN : {'set' if token else 'MISSING'}")
    pin = os.environ.get("UPSTOX_USDINR_INSTRUMENT_KEY")
    if pin:
        print(f"UPSTOX_USDINR_INSTRUMENT_KEY : {pin} (pinned)")
    if not token:
        print("\nSet UPSTOX_ACCESS_TOKEN in .env, then re-run.")
        return

    if probe:
        print()
        _probe(token, probe)
        return

    print("\n--- instrument master ---")
    try:
        futs = usdinr_futures(force=True)
        print(f"NSE USDINR FUT contracts: {len(futs)}  "
              f"(expiries {futs[0].expiry}..{futs[-1].expiry})" if futs else "none")
        nm = resolve_near_month(force=True)
        print(f"near-month: {nm.trading_symbol}  key={nm.instrument_key}  "
              f"expiry={nm.expiry}  weekly={nm.weekly}")
        print("\n--- quote probe (this is the token healthcheck) ---")
        _probe(token, nm.instrument_key)
    except Exception as e:
        print(f"resolve failed: {e}")
        print("  -> set UPSTOX_USDINR_INSTRUMENT_KEY in .env")
        return

    cfg = DataSourceConfig(
        source_id="upstox_usdinr_fut", source_type="basis_onshore",
        display_name="NSE USD/INR (Upstox)", symbols=["USDINR"],
        latency_estimate_ms=800.0, reliability_score=0.97,
    )
    src = UpstoxDataSource(cfg)
    print("\n--- polling quote for ~15s ---")
    if not src.connect():
        print("connect() failed — see the token healthcheck above.")
        return
    for _ in range(15):
        time.sleep(1)
        t = src.get_tick("USDINR")
        d = src.get_depth("USDINR")
        if t:
            print(f"  {t.bid:.4f} / {t.ask:.4f}   "
                  f"depth {len(d['bids']) if d else 0}x{len(d['asks']) if d else 0}   "
                  f"last={t.last}")
    src.disconnect()
    print("done. (off-hours the book is empty; last_price alone still confirms access)")


if __name__ == "__main__":
    main()
