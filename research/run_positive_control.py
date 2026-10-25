"""Positive control: do US macro surprises move the majors at 1–5 minutes?

Andersen, Bollerslev, Diebold & Vega (2003) establish that US announcement surprises move
USD exchange rates within minutes. If this pipeline cannot reproduce that on EUR/USD, every
later result is suspect. The gate rule was fixed in RESEARCH_TRACKER §6 *before* this ran:

  PASS requires all of
    (a) Non-Farm Employment Change: 5-min EUR/USD beta < 0, p < 0.05
    (b) pooled sign-oriented beta at 5 min > 0, p < 0.05
    (c) placebo (-5 min -> release) pooled beta insignificant, p > 0.05

Design:
  - High-impact US releases with an exact release time and a standardized surprise.
  - Each release is studied in all four majors (usd_policy="study"); the gate uses EUR/USD,
    the others are reported as a cross-pair coherence check.
  - HistData 1-minute bars stamped at bar close; entry = first bar at/after the release
    (the pre-release close), entry lag <= 2 min, exit lag <= 5 min (fail closed).
  - Surprises winsorized at +/-5 sigma; returns in basis points.
  - Standard errors clustered by release date (NFP, unemployment and earnings print together).
  - Pooled estimates orient both sides: surprise x news_sign (good news = positive) and the
    return as the *releasing currency's* return, so beta > 0 means "good news appreciates
    the currency", comparable across pairs and indicators.

Run:  python -m research.run_positive_control
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.cache_paths import histdata_cache_dir
from research.data.event_windows import plan_fetch_hours
from research.data.histdata import load_windowed_series
from research.data.tagging import USD_STUDY_PAIRS
from research.eventstudy.alignment import align_event
from research.eventstudy.schema import Event
from research.eventstudy.sign_conventions import CURRENCY_FOR_COUNTRY, currency_side, news_sign
from research.eventstudy.stats import benjamini_hochberg
from research.eventstudy.surprise import surprise_response
from research.plan_intraday import CALENDAR_STORE, load_causal_sample

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
OUTPUT = ARTIFACTS / "positive_control.json"

HORIZONS = [1, 5, 15, 30, 60, 120]
PLACEBO_MINUTES = 5
MAX_ENTRY_LAG_MINUTES = 2
MAX_EXIT_LAG_MINUTES = 5
WINSOR_SIGMA = 5.0
GATE_RECORD = ("FAIL on (a): pre-registered gate, runs 1 and 2 on 2026-10-24 (full-sample sigma); "
               "final under the two-attempt rule (RESEARCH_TRACKER §3, §6)")
GATE_HORIZON = 5
GATE_ALPHA = 0.05
HEADLINE_INDICATORS = [
    "Non-Farm Employment Change",
    "Unemployment Rate",
    "Average Hourly Earnings m/m",
    "CPI m/m",
    "Core CPI m/m",
    "Retail Sales m/m",
    "Unemployment Claims",
    "ISM Manufacturing PMI",
]


def _events(frame: pd.DataFrame) -> tuple[list[Event], dict[str, dict]]:
    events: list[Event] = []
    meta: dict[str, dict] = {}
    for row in frame.to_dict("records"):
        timestamp = pd.Timestamp(row["timestamp"]).to_pydatetime()
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        surprise = float(np.clip(row["standardized_surprise"], -WINSOR_SIGMA, WINSOR_SIGMA))
        for pair in USD_STUDY_PAIRS:
            event_id = f"{row['event_id']}:{pair.replace('/', '')}"
            events.append(Event(event_id=event_id, timestamp=timestamp, pair=pair,
                                surprise=surprise, source="calendar", headline=str(row["event_name"])))
            meta[event_id] = {
                "indicator": str(row["event_name"]),
                "country": str(row["country"]),
                "release_date": timestamp.date().isoformat(),
                "time_source": str(row.get("timestamp_source") or "source"),
                "in_window": bool(row.get("in_pandemic_window", False)),
            }
    return events, meta


def _fit(rows: list[dict], horizon_key: str) -> dict | None:
    usable = [r for r in rows if r.get(horizon_key) is not None]
    if len(usable) < 10:
        return None
    result = surprise_response(
        [r[horizon_key] for r in usable],
        [r["x"] for r in usable],
        clusters=[r["release_date"] for r in usable],
    )
    if result is None:
        return None
    return {"n": result.n, "clusters": result.n_clusters, "beta_bps": round(result.beta, 3),
            "se": round(result.beta_se, 3), "t": round(result.beta_t, 2), "p": result.beta_p,
            "r2": round(result.r_squared, 4)}


def main() -> int:
    frame = load_causal_sample(CALENDAR_STORE)
    frame = frame[frame["country"] == "United States"].copy()
    events, meta = _events(frame)
    print(f"US releases: {len(frame)} -> {len(events)} pair observations across {list(USD_STUDY_PAIRS)}")

    plan = plan_fetch_hours(events, pre_hours=1, post_hours=2)
    rows_by_pair: dict[str, list[dict]] = defaultdict(list)
    coverage: dict[str, dict] = {}
    for pair in USD_STUDY_PAIRS:
        series, missing = load_windowed_series(pair, plan.hours_by_pair.get(pair, []), histdata_cache_dir())
        pair_events = [e for e in events if e.pair == pair]
        aligned = 0
        for event in pair_events:
            obs = align_event(event, series, HORIZONS, max_entry_lag_minutes=MAX_ENTRY_LAG_MINUTES,
                              max_exit_lag_minutes=MAX_EXIT_LAG_MINUTES)
            placebo_event = Event(event_id=event.event_id, timestamp=event.timestamp - timedelta(minutes=PLACEBO_MINUTES),
                                  pair=pair, surprise=event.surprise)
            placebo = align_event(placebo_event, series, [PLACEBO_MINUTES],
                                  max_entry_lag_minutes=MAX_ENTRY_LAG_MINUTES, max_exit_lag_minutes=MAX_EXIT_LAG_MINUTES)
            if obs is None:
                continue
            aligned += 1
            info = meta[event.event_id]
            sign = news_sign(info["country"], info["indicator"])
            side = currency_side(pair, CURRENCY_FOR_COUNTRY[info["country"]])
            row = {"indicator": info["indicator"], "release_date": info["release_date"],
                   "time_source": info["time_source"], "in_window": info["in_window"], "raw_x": event.surprise, "sign": sign, "side": side}
            for horizon in HORIZONS:
                ret = obs.horizon_returns.get(horizon)
                row[f"r{horizon}"] = None if ret is None else ret * 1e4
            placebo_ret = None if placebo is None else placebo.horizon_returns.get(PLACEBO_MINUTES)
            row["placebo"] = None if placebo_ret is None else placebo_ret * 1e4
            rows_by_pair[pair].append(row)
        coverage[pair] = {"events": len(pair_events), "aligned": aligned, "bars": len(series.times),
                          "missing_years": missing}
        print(f"  {pair}: {aligned}/{len(pair_events)} aligned, {len(series.times):,} bars"
              + (f", MISSING YEARS {missing}" if missing else ""))

    results: dict = {"coverage": coverage, "per_indicator": {}, "pooled": {}, "placebo": {},
                     "pooled_observed_times_only": {}, "pooled_excluding_pandemic_window": {},
                     "nfp_excluding_pandemic_window": {}}
    for pair, rows in rows_by_pair.items():
        # Per-indicator: raw surprise, raw pair return. No sign assumption needed.
        results["per_indicator"][pair] = {}
        for indicator in HEADLINE_INDICATORS:
            subset = [dict(r, x=r["raw_x"]) for r in rows if r["indicator"] == indicator]
            results["per_indicator"][pair][indicator] = {
                f"{h}m": _fit(subset, f"r{h}") for h in HORIZONS
            }
        # Pooled: surprise oriented to "good news", return oriented to the releasing currency.
        oriented = []
        for r in rows:
            if r["sign"] is None or r["side"] is None:
                continue
            o = dict(r, x=r["raw_x"] * r["sign"])
            for horizon in HORIZONS:
                value = r[f"r{horizon}"]
                o[f"r{horizon}"] = None if value is None else value * r["side"]
            o["placebo"] = None if r["placebo"] is None else r["placebo"] * r["side"]
            oriented.append(o)
        pooled = {f"{h}m": _fit(oriented, f"r{h}") for h in HORIZONS}
        fits = [(k, v) for k, v in pooled.items() if v is not None]
        if fits:
            for (key, value), correction in zip(fits, benjamini_hochberg([v["p"] for _, v in fits])):
                value["q_bh"] = correction.q_value
        results["pooled"][pair] = pooled
        results["placebo"][pair] = _fit(oriented, "placebo")
        # Robustness, not part of the gate: drop every release whose time was filled from
        # the agency clock, so no estimate rests on imputation alone.
        observed = [o for o in oriented if o["time_source"] == "source"]
        results["pooled_observed_times_only"][pair] = {f"{h}m": _fit(observed, f"r{h}") for h in HORIZONS}
        # Option A robustness (RESEARCH_TRACKER §6), also non-gating: drop releases inside the
        # pre-registered pandemic window entirely.
        outside = [o for o in oriented if not o["in_window"]]
        results["pooled_excluding_pandemic_window"][pair] = {f"{h}m": _fit(outside, f"r{h}") for h in HORIZONS}
        nfp_outside = [dict(r, x=r["raw_x"]) for r in rows
                       if r["indicator"] == "Non-Farm Employment Change" and not r["in_window"]]
        results["nfp_excluding_pandemic_window"][pair] = {f"{h}m": _fit(nfp_outside, f"r{h}") for h in HORIZONS}

    eur = "EUR/USD"
    nfp = results["per_indicator"][eur]["Non-Farm Employment Change"][f"{GATE_HORIZON}m"]
    pooled5 = results["pooled"][eur][f"{GATE_HORIZON}m"]
    placebo = results["placebo"][eur]
    gate = {
        "a_nfp_negative_significant": bool(nfp and nfp["beta_bps"] < 0 and nfp["p"] < GATE_ALPHA),
        "b_pooled_positive_significant": bool(pooled5 and pooled5["beta_bps"] > 0 and pooled5["p"] < GATE_ALPHA),
        "c_placebo_insignificant": bool(placebo and placebo["p"] > GATE_ALPHA),
    }
    gate["PASS"] = all(gate.values())
    # The pre-registered gate was decided by runs 1 and 2 (2026-10-24): FAIL on (a), final
    # under the two-attempt rule. Runs on the Option A store are post-hoc consistency checks
    # (RESEARCH_TRACKER §6); their gate dict must never be cited as a pass.
    results["gate_record"] = GATE_RECORD
    results["gate_consistency_check_option_a"] = gate

    print("\nPooled sign-oriented beta (bps of the releasing currency per +1 sigma good-news surprise):")
    print(f"  {'pair':<9}" + "".join(f"{h:>4}m beta  p       " for h in HORIZONS))
    for pair in USD_STUDY_PAIRS:
        cells = []
        for h in HORIZONS:
            fit = results["pooled"][pair][f"{h}m"]
            cells.append(f"{fit['beta_bps']:>+8.2f} {fit['p']:<9.1e}" if fit else f"{'n/a':>8} {'':<9}")
        print(f"  {pair:<9}" + "  ".join(cells))
    print("\nRobustness - pooled oriented beta on observed release times only (no imputed times):")
    for pair in USD_STUDY_PAIRS:
        cells = []
        for h in HORIZONS:
            fit = results["pooled_observed_times_only"][pair][f"{h}m"]
            cells.append(f"{fit['beta_bps']:>+8.2f} {fit['p']:<9.1e}" if fit else f"{'n/a':>8} {'':<9}")
        five = results["pooled_observed_times_only"][pair]["5m"]
        print(f"  {pair:<9}" + "  ".join(cells) + (f"  (n={five['n']}, clusters={five['clusters']})" if five else ""))
    print("\nRobustness (Option A) - pooled oriented beta excluding the 2020-03..2021-06 window:")
    for pair in USD_STUDY_PAIRS:
        cells = []
        for h in HORIZONS:
            fit = results["pooled_excluding_pandemic_window"][pair][f"{h}m"]
            cells.append(f"{fit['beta_bps']:>+8.2f} {fit['p']:<9.1e}" if fit else f"{'n/a':>8} {'':<9}")
        five = results["pooled_excluding_pandemic_window"][pair]["5m"]
        print(f"  {pair:<9}" + "  ".join(cells) + (f"  (n={five['n']}, clusters={five['clusters']})" if five else ""))
    print("\nPayrolls 5-min beta excluding the window (raw surprise, raw pair return; non-gating):")
    for pair in USD_STUDY_PAIRS:
        fit = results["nfp_excluding_pandemic_window"][pair]["5m"]
        print(f"  {pair:<9} " + (f"beta {fit['beta_bps']:+.2f} bps/sigma  p {fit['p']:.3g}  (n={fit['n']})" if fit else "n/a"))
    print("\nPlacebo (-5 min -> release), pooled oriented:")
    for pair in USD_STUDY_PAIRS:
        fit = results["placebo"][pair]
        print(f"  {pair:<9} beta {fit['beta_bps']:+.3f} bps  p {fit['p']:.3f}  (n={fit['n']}, clusters={fit['clusters']})" if fit else f"  {pair}: n/a")
    print("\nPer-indicator 5-min beta (raw surprise, raw pair return):")
    for indicator in HEADLINE_INDICATORS:
        line = f"  {indicator:<30}"
        for pair in USD_STUDY_PAIRS:
            fit = results["per_indicator"][pair][indicator]["5m"]
            line += f"{pair}: {fit['beta_bps']:+6.2f} (p {fit['p']:.0e})  " if fit else f"{pair}: n/a  "
        print(line)
    print(f"\nGATE RECORD: {GATE_RECORD}")
    print(f"Consistency check under Option A (post-hoc, NOT a pass): {gate}")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    print(f"\nWritten: {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
