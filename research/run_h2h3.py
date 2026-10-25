"""H2 and H3 on US high-impact releases across the four majors (RESEARCH_TRACKER §2).

  H2 — per-category CAR with release-clustered SEs and Benjamini-Hochberg FDR.
       Supported if >= 2 categories survive FDR at q <= 0.05. One pass.
  H3 — reaction half-life per category, scheduled events only.
       Supported if half-life CIs are disjoint between >= 2 categories. One pass.

Operationalisation, fixed before this ran (2026-10-24):
  - Sample: the positive-control sample — US high-impact releases with an exact time and an
    Option A standardized surprise, each studied in EUR/USD, GBP/USD, USD/JPY, USD/CHF
    (HistData M1 stamped at bar close; entry lag <= 2 min, exit lag <= 5 min).
  - Categories: `data/tagging.tag_categories` of the indicator name (multi-label).
  - CAR: sign-oriented return (see eventstudy/category_reactions.py) in bps, at
    1/5/15/30/60/120 min, pooled across the four pairs, clustered by release date.
  - FDR grid: every category x horizon cell with >= MIN_CLUSTERS release dates. A category
    "survives" if any of its cells has q <= 0.05 with a positive oriented CAR.
  - H3: pre-registered decay half-life (= atlas._half_life) with a 1,000-draw release-
    clustered bootstrap 95% CI; time-to-half-peak reported as secondary, not pre-registered.
  - Robustness (non-gating): the same grid excluding the Option A pandemic window.

Run: python -m research.run_h2h3
"""
from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.cache_paths import histdata_cache_dir
from research.data.event_windows import plan_fetch_hours
from research.data.histdata import load_windowed_series
from research.data.tagging import USD_STUDY_PAIRS
from research.eventstudy.alignment import align_event
from research.eventstudy.category_reactions import (
    bootstrap_speed,
    build_panels,
    clustered_mean,
    decay_half_life,
    disjoint_pairs,
    orient,
    time_to_half_peak,
)
from research.eventstudy.schema import Event
from research.eventstudy.sign_conventions import CURRENCY_FOR_COUNTRY, currency_side, news_sign
from research.eventstudy.stats import benjamini_hochberg
from research.plan_intraday import CALENDAR_STORE, load_causal_sample

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
OUTPUT = ARTIFACTS / "h2h3.json"

HORIZONS = [1, 5, 15, 30, 60, 120]
MAX_ENTRY_LAG_MINUTES = 2
MAX_EXIT_LAG_MINUTES = 5
WINSOR_SIGMA = 5.0
MIN_CLUSTERS = 30
FDR_Q = 0.05
BOOTSTRAP_DRAWS = 1000


def load_observations() -> list[dict]:
    frame = load_causal_sample(CALENDAR_STORE)
    frame = frame[frame["country"] == "United States"]
    releases = []
    for row in frame.to_dict("records"):
        timestamp = pd.Timestamp(row["timestamp"]).to_pydatetime()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        sign = news_sign(str(row["country"]), str(row["event_name"]))
        categories = [c for c in str(row.get("categories") or "").split(";") if c]
        if sign is None or not categories:
            continue
        releases.append({
            "event_id": row["event_id"], "timestamp": timestamp, "indicator": str(row["event_name"]),
            "categories": categories, "sign": sign, "cluster": timestamp.date().isoformat(),
            "z": float(np.clip(row["standardized_surprise"], -WINSOR_SIGMA, WINSOR_SIGMA)),
            "in_window": bool(row.get("in_pandemic_window", False)),
        })

    observations: list[dict] = []
    for pair in USD_STUDY_PAIRS:
        events = [Event(event_id=f"{r['event_id']}:{pair}", timestamp=r["timestamp"], pair=pair, surprise=r["z"])
                  for r in releases]
        plan = plan_fetch_hours(events, pre_hours=1, post_hours=2)
        series, missing = load_windowed_series(pair, plan.hours_by_pair.get(pair, []), histdata_cache_dir())
        if missing:
            print(f"  WARNING {pair}: years not cached {missing}")
        side = currency_side(pair, CURRENCY_FOR_COUNTRY["United States"])
        aligned = 0
        for release, event in zip(releases, events):
            obs = align_event(event, series, HORIZONS, max_entry_lag_minutes=MAX_ENTRY_LAG_MINUTES,
                              max_exit_lag_minutes=MAX_EXIT_LAG_MINUTES)
            if obs is None:
                continue
            record = {"pair": pair, "categories": release["categories"], "cluster": release["cluster"],
                      "indicator": release["indicator"], "in_window": release["in_window"],
                      "timestamp": release["timestamp"]}
            usable = False
            for horizon in HORIZONS:
                ret = obs.horizon_returns.get(horizon)
                value = None if ret is None else orient(ret * 1e4, release["z"], release["sign"], side)
                record[f"o{horizon}"] = value
                usable = usable or value is not None
            if usable:
                observations.append(record)
                aligned += 1
        print(f"  {pair}: {aligned}/{len(releases)} releases aligned with a nonzero surprise")
    return observations


def car_grid(observations: list[dict]) -> dict:
    """H2: clustered mean oriented CAR per category x horizon, BH across the tested grid."""
    by_category: dict[str, list[dict]] = {}
    for obs in observations:
        for category in obs["categories"]:
            by_category.setdefault(category, []).append(obs)

    cells = []
    for category, rows in sorted(by_category.items()):
        for horizon in HORIZONS:
            pairs = [(r[f"o{horizon}"], r["cluster"]) for r in rows if r[f"o{horizon}"] is not None]
            fit = clustered_mean([v for v, _ in pairs], [c for _, c in pairs]) if pairs else None
            if fit is None or fit.clusters < MIN_CLUSTERS:
                continue
            cells.append({"category": category, "horizon": horizon, "n": fit.n, "clusters": fit.clusters,
                          "car_bps": fit.mean, "se_bps": fit.se, "t": fit.t, "p": fit.p})
    for cell, correction in zip(cells, benjamini_hochberg([c["p"] for c in cells], alpha=FDR_Q)):
        cell["q_bh"] = correction.q_value
        cell["survives"] = bool(correction.q_value <= FDR_Q and cell["car_bps"] > 0)
    surviving = sorted({c["category"] for c in cells if c["survives"]})
    return {"cells": cells, "tested_categories": sorted({c["category"] for c in cells}),
            "surviving_categories": surviving, "supported": len(surviving) >= 2}


def speed(observations: list[dict], tested: list[str]) -> dict:
    """H3: decay half-life (pre-registered) and time-to-half-peak (secondary), bootstrapped."""
    panels = build_panels(observations, HORIZONS)
    rng = np.random.default_rng(20261024)
    out: dict = {"half_life": {}, "time_to_half_peak": {}, "curves_bps": {}}
    for category in tested:
        panel = panels[category]
        out["curves_bps"][category] = dict(zip(map(str, HORIZONS), map(float, panel.curve())))
        for key, measure in (("half_life", decay_half_life), ("time_to_half_peak", time_to_half_peak)):
            estimate = bootstrap_speed(panel, measure, draws=BOOTSTRAP_DRAWS, rng=rng)
            out[key][category] = estimate.__dict__
    from research.eventstudy.category_reactions import SpeedEstimate
    half = {c: SpeedEstimate(**v) for c, v in out["half_life"].items()}
    ttp = {c: SpeedEstimate(**v) for c, v in out["time_to_half_peak"].items()}
    out["half_life_disjoint_pairs"] = disjoint_pairs(half)
    out["time_to_half_peak_disjoint_pairs"] = disjoint_pairs(ttp)
    out["supported"] = len(out["half_life_disjoint_pairs"]) >= 1
    return out


def _print_grid(title: str, grid: dict) -> None:
    print(f"\n{title}")
    print(f"  {'category':<16}" + "".join(f"{h:>5}m CAR   q     " for h in HORIZONS))
    for category in grid["tested_categories"]:
        line = f"  {category:<16}"
        by_h = {c["horizon"]: c for c in grid["cells"] if c["category"] == category}
        for h in HORIZONS:
            c = by_h.get(h)
            line += (f"{c['car_bps']:>+8.2f} {c['q_bh']:<6.3f}{'*' if c['survives'] else ' '}   " if c
                     else f"{'n/a':>8} {'':<6}    ")
        print(line)
    clusters = {c["category"]: c["clusters"] for c in grid["cells"] if c["horizon"] == 5}
    print("  release dates per category (5m):", clusters)
    print(f"  surviving: {grid['surviving_categories']}  -> H2 {'SUPPORTED' if grid['supported'] else 'NOT supported'}")


def main() -> int:
    print("H2/H3 - US high-impact releases x four majors (sign-oriented, release-clustered)")
    observations = load_observations()

    h2 = car_grid(observations)
    h2_robust = car_grid([o for o in observations if not o["in_window"]])
    h3 = speed(observations, h2["tested_categories"])

    _print_grid("H2 (pre-registered) - oriented CAR, bps; * = q <= 0.05 and CAR > 0", h2)
    _print_grid("H2 robustness (non-gating) - excluding the 2020-03..2021-06 window", h2_robust)

    print("\nH3 - decay half-life (pre-registered; minutes; undefined = no decay within 120m)")
    for category, e in h3["half_life"].items():
        print(f"  {category:<16} point {e['point']}  95% CI [{e['ci_low']}, {e['ci_high']}]  "
              f"undefined in {e['undefined_share']:.0%} of draws")
    print(f"  disjoint pairs: {h3['half_life_disjoint_pairs']}  -> H3 {'SUPPORTED' if h3['supported'] else 'NOT supported'}")
    print("\nSecondary (not pre-registered) - time to half of peak |CAR|, minutes:")
    for category, e in h3["time_to_half_peak"].items():
        print(f"  {category:<16} point {e['point']}  95% CI [{e['ci_low']}, {e['ci_high']}]")
    print(f"  disjoint pairs: {h3['time_to_half_peak_disjoint_pairs']}")
    print("\nOriented CAR curves (bps):")
    for category, curve in h3["curves_bps"].items():
        print(f"  {category:<16} " + "  ".join(f"{h}m {v:+.2f}" for h, v in curve.items()))

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({
        "design": __doc__, "n_observations": len(observations), "horizons": HORIZONS,
        "min_clusters": MIN_CLUSTERS, "h2": h2, "h2_excluding_pandemic_window": h2_robust, "h3": h3,
    }, indent=2, default=float), encoding="utf-8")
    print(f"\nWritten: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
