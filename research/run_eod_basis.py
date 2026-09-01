"""
EOD onshore-offshore USD/INR basis — zero-KYC historical path.

    python research/run_eod_basis.py                       # sample CSVs
    python research/run_eod_basis.py --onshore-csv ... --offshore-csv ... --spot-csv ...
    python research/run_eod_basis.py --use-yfinance --start 2025-01-01 --end 2026-08-28
      (offshore = INR=F, spot = USDINR=X ; still needs a CSV for the onshore NSE leg)

Writes research/results/eod/<run-id>/{basis_eod.jsonl, summary.json}.
Same Option-A normalisation as the live pipeline — see docs/SPEC.md section 3.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from backend.config import CARRY_RATE_ANNUAL  # noqa: E402
from backend.core.basis import EODBasisRunner  # noqa: E402
from backend.core.data_sources.eod_loader import load_leg  # noqa: E402

DATA = REPO / "data" / "eod"


def main() -> None:
    ap = argparse.ArgumentParser(description="EOD USD/INR onshore-offshore basis")
    ap.add_argument("--onshore-csv", default=str(DATA / "SAMPLE_nse_usdinr_fut.csv"))
    ap.add_argument("--offshore-csv", default=None)
    ap.add_argument("--spot-csv", default=None)
    ap.add_argument("--use-yfinance", action="store_true",
                    help="fetch the spot leg (USDINR=X) from yfinance — free, no account. "
                         "The onshore NSE future and offshore CME future still need CSVs "
                         "(NSE blocks scrapers; Yahoo has no rupee future).")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--carry", type=float, default=CARRY_RATE_ANNUAL,
                    help="fallback annualised carry when a day cannot be calibrated")
    ap.add_argument("--no-calibrate-carry", action="store_true",
                    help="disable per-day carry calibration (F_onshore/S); use --carry flat")
    ap.add_argument("--offshore-convention", default="auto",
                    choices=["auto", "USDINR", "INRUSD", "USDINR_x100", "INRUSD_x10000"],
                    help="CME/SGX rupee futures quote USD-per-INR — 'auto' inverts them")
    ap.add_argument("--run-id", default="sample")
    args = ap.parse_args()

    onshore = load_leg("onshore", csv_path=args.onshore_csv)

    if args.use_yfinance:
        spot = load_leg("spot", yf_ticker="USDINR=X", start=args.start, end=args.end)
    else:
        spot = load_leg("spot", csv_path=args.spot_csv or str(DATA / "SAMPLE_usdinr_spot.csv"))

    if args.offshore_csv:
        offshore = load_leg("offshore", csv_path=args.offshore_csv,
                            convention=args.offshore_convention)
    elif not args.use_yfinance:
        offshore = load_leg("offshore", csv_path=str(DATA / "SAMPLE_cme_inr_fut.csv"))
    else:
        offshore = []          # no free automated offshore source — onshore vs spot only
        print("  (no --offshore-csv given - running onshore vs spot only)")

    for name, bars in (("onshore", onshore), ("offshore", offshore), ("spot", spot)):
        if name == "offshore" and args.use_yfinance and not args.offshore_csv:
            continue
        n = len(bars)
        rng = f"{bars[0].trade_date}..{bars[-1].trade_date}" if bars else "(none)"
        flag = "  <-- EMPTY: check the CSV has data rows, not just a header" if n == 0 else ""
        print(f"  loaded {name:<9}: {n:>4} bars  {rng}{flag}")

    overlap = (set(b.trade_date for b in onshore)
               & (set(b.trade_date for b in offshore) | set(b.trade_date for b in spot)))
    if not overlap:
        print("\n  No overlapping dates between the onshore leg and the others - "
              "0 rows. Check date ranges and that each file actually loaded.")

    runner = EODBasisRunner(carry_rate_annual=args.carry,
                            calibrate_carry=not args.no_calibrate_carry)
    runner.run(onshore, offshore, spot)
    outdir = runner.write(run_id=args.run_id)
    s = runner.summary()

    _c = s.get("carry", {})
    _bs = _c.get("by_source", {})
    print(f"\n=== EOD USD/INR basis  (run '{args.run_id}', carry {_c.get('mode')}: "
          f"{_bs.get('calibrated', 0)} per-day + {_bs.get('calibrated_pooled', 0)} pooled "
          f"@ {_c.get('pooled_annual')}, {_bs.get('assumed', 0)} assumed) ===")
    print(f"  dated rows      : {s['rows']}   {s.get('date_range')}")
    for pair, st in s.get("basis_stats_pips", {}).items():
        print(f"  {pair:<18}: mean {st['mean_pips']:+.2f}  |mean| {st['abs_mean_pips']:.2f}  "
              f"p90|.| {st['p90_abs_pips']:.2f}  range [{st['min_pips']:+.1f}, {st['max_pips']:+.1f}]  pips")
    print(f"  wrote           : {outdir}")

    if args.onshore_csv.startswith(str(DATA / "SAMPLE")):
        print("\n  NOTE: running on SAMPLE data. Replace with real NSE / CME / RBI "
              "files - see data/eod/README.md.")


if __name__ == "__main__":
    main()
