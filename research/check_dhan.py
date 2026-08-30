"""
Validate a Dhan setup and print a few live ticks. Run this once the account is
active and DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN are in .env.

    python research/check_dhan.py

It (1) health-checks the token, (2) resolves the near-month NSE USD/INR future
from the scrip master, (3) connects the market feed, (4) prints ~10 ticks + the
depth book. Read-only - it never places an order.

    python research/check_dhan.py --probe 12345

``--probe SID`` skips resolution and asks the REST quote API what NSE-currency
instrument that security id is - use it to verify a candidate DHAN_USDINR_SECURITY_ID
you found on the Dhan web platform before pinning it in .env.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# load .env if python-dotenv is around
try:
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
except Exception:
    pass

from backend.config import BASIS_LEGS                       # noqa: E402
from backend.core.data_sources.dhan_data_source import DhanDataSource  # noqa: E402
from backend.core.data_sources.dhan_instruments import (   # noqa: E402
    resolve_near_month,
    usdinr_futures,
)
from backend.core.interfaces.data_source import DataSourceConfig  # noqa: E402


def _token_healthcheck(cid: str, tok: str) -> bool:
    """Authenticated REST ping so token validity is isolated from feed issues."""
    import json
    import urllib.request

    req = urllib.request.Request(
        "https://api.dhan.co/v2/fundlimit",
        headers={"access-token": tok, "client-id": cid, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read() or b"{}")
        avail = body.get("availabelBalance", body.get("availableBalance"))
        print(f"token healthcheck   : OK (HTTP 200)  available balance = {avail}")
        return True
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        print(f"token healthcheck   : FAIL (HTTP {e.code})  {detail}")
        print("  -> regenerate DHAN_ACCESS_TOKEN at web.dhan.co (tokens expire ~30 days)")
        return False
    except Exception as e:
        print(f"token healthcheck   : FAIL ({e})")
        return False


def _probe_sid(cid: str, tok: str, sid: str) -> None:
    """Ask the REST quote API what NSE_CURRENCY instrument `sid` is."""
    import json
    import urllib.request

    body = json.dumps({"NSE_CURRENCY": [int(sid)]}).encode()
    req = urllib.request.Request(
        "https://api.dhan.co/v2/marketfeed/quote", data=body, method="POST",
        headers={"access-token": tok, "client-id": cid,
                 "Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            payload = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        print(f"probe {sid}: HTTP {e.code}  {e.read().decode('utf-8','replace')[:300]}")
        return
    node = (payload.get("data", {}).get("NSE_CURRENCY", {}) or {}).get(str(sid))
    if not node:
        print(f"probe {sid}: no NSE_CURRENCY instrument with that id "
              f"(response: {json.dumps(payload)[:300]})")
        return
    print(f"probe {sid}: last_price={node.get('last_price')}  "
          f"ohlc={node.get('ohlc')}  volume={node.get('volume')}  oi={node.get('oi')}")
    print("  -> if last_price looks like USD/INR (~80-100) and volume/oi are non-zero,")
    print(f"     this is a live contract. Set DHAN_USDINR_SECURITY_ID={sid} in .env")


def main() -> None:
    args = sys.argv[1:]
    probe = None
    if "--probe" in args:
        probe = args[args.index("--probe") + 1]

    cid, tok = os.environ.get("DHAN_CLIENT_ID"), os.environ.get("DHAN_ACCESS_TOKEN")
    if probe:
        if not (cid and tok):
            print("set DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN in .env first")
            return
        _probe_sid(cid, tok, probe)
        return
    print(f"DHAN_CLIENT_ID     : {'set' if cid else 'MISSING'}")
    print(f"DHAN_ACCESS_TOKEN  : {'set' if tok else 'MISSING'}")
    pin = os.environ.get("DHAN_USDINR_SECURITY_ID")
    if pin:
        print(f"DHAN_USDINR_SECURITY_ID : {pin} (pinned)")

    if cid and tok:
        print()
        _token_healthcheck(cid, tok)

    print("\n--- scrip master ---")
    try:
        futs = usdinr_futures("NSE", force=True)
        print(f"NSE USDINR FUTCUR contracts: {len(futs)}  "
              f"(expiries {futs[0].expiry}..{futs[-1].expiry})" if futs else "none")
        nm = resolve_near_month("NSE", force=True, allow_stale=True)
        stale = "  (STALE master - pin DHAN_USDINR_SECURITY_ID for the real front month)" \
            if (nm.expiry.toordinal() < __import__("datetime").date.today().toordinal()) else ""
        print(f"near-month: {nm.trading_symbol}  sec_id={nm.security_id}  expiry={nm.expiry}{stale}")
    except Exception as e:
        print(f"resolve failed: {e}")
        print("  -> set DHAN_USDINR_SECURITY_ID in .env from the Dhan web F&O page")

    if not (cid and tok):
        print("\nSet the credentials in .env, then re-run.")
        return

    cfg = DataSourceConfig(
        source_id="dhan_usdinr_fut", source_type="basis_onshore",
        display_name="NSE USD/INR (Dhan)", symbols=["USDINR"],
        latency_estimate_ms=next(b.latency_estimate_ms for b in BASIS_LEGS if b.leg == "onshore"),
        reliability_score=0.97,
    )
    src = DhanDataSource(cfg)
    print("\n--- connecting feed ---")
    if not src.connect():
        print("connect() failed. The token healthcheck above tells you if the token is the "
              "problem; otherwise it is contract resolution - pin DHAN_USDINR_SECURITY_ID.")
        return

    print("connected. sampling ticks for ~15s ...")
    for _ in range(15):
        tick = src.get_tick("USDINR")
        depth = src.get_depth("USDINR")
        if tick:
            print(f"  {tick.bid:.4f} / {tick.ask:.4f}   "
                  f"depth {len(depth['bids']) if depth else 0}x{len(depth['asks']) if depth else 0}")
        time.sleep(1)
    src.disconnect()
    print("done.")


if __name__ == "__main__":
    main()
