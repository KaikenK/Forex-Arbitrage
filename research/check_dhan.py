"""
Validate a Dhan setup and print a few live ticks. Run this once the account is
active and DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN are in .env.

    python research/check_dhan.py

It (1) resolves the near-month NSE USD/INR future from the scrip master,
(2) connects the market feed, (3) prints ~10 ticks + the depth book. Read-only —
it never places an order.
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


def main() -> None:
    cid, tok = os.environ.get("DHAN_CLIENT_ID"), os.environ.get("DHAN_ACCESS_TOKEN")
    print(f"DHAN_CLIENT_ID     : {'set' if cid else 'MISSING'}")
    print(f"DHAN_ACCESS_TOKEN  : {'set' if tok else 'MISSING'}")
    pin = os.environ.get("DHAN_USDINR_SECURITY_ID")
    if pin:
        print(f"DHAN_USDINR_SECURITY_ID : {pin} (pinned)")

    print("\n--- scrip master ---")
    try:
        futs = usdinr_futures("NSE", force=True)
        print(f"NSE USDINR FUTCUR contracts: {len(futs)}  "
              f"(expiries {futs[0].expiry}..{futs[-1].expiry})" if futs else "none")
        nm = resolve_near_month("NSE", force=True)
        print(f"near-month: {nm.trading_symbol}  sec_id={nm.security_id}  expiry={nm.expiry}")
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
        print("connect() failed — check token freshness (Dhan tokens expire) and try again.")
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
