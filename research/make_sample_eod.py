"""
Generate SAMPLE EOD CSVs for the USD/INR basis path so `run_eod_basis.py` runs
out of the box. Replace these with real NSE-CDS bhavcopy / CME settlement /
RBI-reference files (see data/eod/README.md) for actual results.

Deterministic (seeded). Injects a realistic ~1.9% forward premium and a small,
mostly-negative, occasionally-spiking onshore-offshore basis (the NDF-premium
phenomenon).
"""

from __future__ import annotations

import csv
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.core.normalization import month_end_expiry_estimate  # noqa: E402

import argparse

_ap = argparse.ArgumentParser(description="Generate synthetic EOD USD/INR sample CSVs")
_ap.add_argument("--outdir", default=str(Path(__file__).resolve().parent.parent / "data" / "eod"))
_args, _ = _ap.parse_known_args()
OUT = Path(_args.outdir)
OUT.mkdir(parents=True, exist_ok=True)

rng = random.Random(20260828)
PIP = 0.01
PREM_ANNUAL = 0.019            # ~1.9% USD/INR forward premium
N_DAYS = 45
END = date(2026, 8, 28)


def _business_days(end: date, n: int) -> list[date]:
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _near_month_expiry(d: date) -> date:
    exp = month_end_expiry_estimate(d.year, d.month)
    if (exp - d).days < 3:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        exp = month_end_expiry_estimate(y, m)
    return exp


def main() -> None:
    dates = _business_days(END, N_DAYS)
    spot = 86.55
    basis_pips = -2.0                      # offshore slightly rich vs onshore

    onshore_rows, offshore_rows, spot_rows = [], [], []
    for i, d in enumerate(dates):
        spot += rng.gauss(0, 0.06)
        spot = max(85.5, min(87.8, spot))

        expiry = _near_month_expiry(d)
        tau = (expiry - d).days / 365.0
        fwd_factor = 1 + PREM_ANNUAL * tau

        onshore_fut = spot * fwd_factor + rng.gauss(0, 0.004)

        # basis: mean-reverting around -2 pips, with rare stress spikes
        basis_pips += 0.25 * (-2.0 - basis_pips) + rng.gauss(0, 1.1)
        if rng.random() < 0.07:
            basis_pips += rng.choice([-1, 1]) * rng.uniform(8, 20)
        offshore_fut = onshore_fut - basis_pips * PIP + rng.gauss(0, 0.003)

        onshore_rows.append({
            "Date": d.isoformat(),
            "Symbol": "USDINR",
            "Expiry": expiry.isoformat(),
            "Open": round(onshore_fut - abs(rng.gauss(0, 0.02)), 4),
            "High": round(onshore_fut + abs(rng.gauss(0, 0.03)), 4),
            "Low": round(onshore_fut - abs(rng.gauss(0, 0.03)), 4),
            "Close": round(onshore_fut, 4),
            "Settle": round(onshore_fut, 4),
            "OpenInterest": rng.randint(120_000, 260_000),
        })
        offshore_rows.append({
            "Date": d.isoformat(),
            "Symbol": "INR=F",
            "Open": round(offshore_fut - abs(rng.gauss(0, 0.02)), 4),
            "High": round(offshore_fut + abs(rng.gauss(0, 0.03)), 4),
            "Low": round(offshore_fut - abs(rng.gauss(0, 0.03)), 4),
            "Close": round(offshore_fut, 4),
            "Volume": rng.randint(3_000, 12_000),
        })
        spot_rows.append({
            "Date": d.isoformat(),
            "Symbol": "USDINR=X",
            "Close": round(spot, 4),
        })

    _write(OUT / "SAMPLE_nse_usdinr_fut.csv", onshore_rows)
    _write(OUT / "SAMPLE_cme_inr_fut.csv", offshore_rows)
    _write(OUT / "SAMPLE_usdinr_spot.csv", spot_rows)
    print(f"wrote 3 sample CSVs ({N_DAYS} business days) to {OUT}")


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main()
