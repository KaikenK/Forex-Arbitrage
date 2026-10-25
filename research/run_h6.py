"""Run the pre-registered H6 test (RESEARCH_TRACKER §6, "H6 PRE-REGISTERED", 2026-10-25).

Refuses to run until (1) every open clause in `semantic/currency_bias.UNRESOLVED` is set by a
§6 entry, (2) the GDELT sample is frozen (`freeze_gdelt_sample.py`, hash-verified) and
(3) the dedup, finbert and embed scores were produced on that freeze. Events come only from
the frozen shards.

  python -m research.run_h6

Writes `artifacts/h6_results.json` and `artifacts/h6_horizon.json` (the horizon-output rule;
the live service reads it to state its signal horizon).
"""
from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.cache_paths import histdata_cache_dir
from research.data.histdata import load_windowed_series
from research.features import compute_event_semantics
from research.freeze_gdelt_sample import SAMPLE_END, SAMPLE_START, load_freeze, load_frozen_events
from research.score_archive import load_scores, require_frozen_scores
from research.semantic.currency_bias import (
    H6_CURRENCIES,
    H6_PAIRS,
    MAJORS,
    PRIMARY_HORIZON_MIN,
    SECONDARY_HORIZONS_MIN,
    UNRESOLVED,
    USDINR,
    ProtocolUnresolved,
    apply_missing_window_rule,
    assemble_panel,
    baseline_frame,
    bh_secondaries,
    build_currency_streams,
    build_pair_streams,
    build_release_streams,
    forward_log_returns,
    h6_test,
    horizon_output,
    raw_signal_frame,
    require_resolved,
    signal_times,
    usdinr_daily_returns,
)

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CALENDAR = ARTIFACTS / "calendar_events.parquet"
RESULTS = ARTIFACTS / "h6_results.json"
HORIZON = ARTIFACTS / "h6_horizon.json"


def _hours(times: np.ndarray, horizon_minutes: float) -> list[datetime]:
    """UTC hours covering entry (t) and exit (t + h) bars, plus the next hour for lag."""
    hours: set[datetime] = set()
    for t in times:
        for anchor in (t, t + horizon_minutes * 60):
            stamp = datetime.fromtimestamp(anchor, tz=UTC).replace(minute=0, second=0, microsecond=0)
            hours.add(stamp)
            hours.add(datetime.fromtimestamp(stamp.timestamp() + 3600, tz=UTC))
    return sorted(hours)


def _major_targets(times: np.ndarray, horizon: float, settings) -> dict[str, np.ndarray]:
    entry_lag, exit_lag = settings["intraday_entry_exit_lag_minutes"]
    out = {}
    for pair in MAJORS:
        series, missing = load_windowed_series(pair, _hours(times, horizon), histdata_cache_dir())
        if missing:
            print(f"  {pair}: HistData years missing {missing} -> those rows fail closed")
        bars = np.array([t.timestamp() for t in series.times], dtype=float)
        out[pair] = forward_log_returns(bars, np.asarray(series.prices, dtype=float), times,
                                        horizon_minutes=horizon, entry_lag_minutes=entry_lag,
                                        exit_lag_minutes=exit_lag)
        del series, bars
    return out


def _usdinr_target(times: np.ndarray, settings) -> np.ndarray:
    from research.data.prices_yf import load_price_series

    start = datetime.combine(SAMPLE_START, datetime.min.time(), tzinfo=UTC)
    end = datetime.combine(SAMPLE_END, datetime.min.time(), tzinfo=UTC)
    series = load_price_series("USDINR=X", USDINR, start, end)
    closes = pd.Series(series.prices, index=[t.date() for t in series.times]).sort_index()
    closes = closes[~closes.index.duplicated(keep="last")]
    return usdinr_daily_returns(closes, times, str(settings["usdinr_target_bar"]))


def _load_inputs(settings):
    from research.run_h1 import _embedder_factory, _finbert

    freeze = load_freeze()
    events = load_frozen_events(freeze)
    for stage in ("dedup", "finbert", "embed"):
        require_frozen_scores(stage, freeze["sha256"])
    dedup = load_scores("dedup")
    canonical = dict(zip(dedup.loc[dedup["canonical"], "event_id"].astype(str),
                         dedup.loc[dedup["canonical"], "coverage_count"].astype(int)))
    semantics = compute_event_semantics(events, embedder_factory=_embedder_factory("sentence_transformer", events),
                                        scorer=_finbert(events), currencies=list(H6_CURRENCIES), canonical=canonical)
    meta = events.assign(event_id=events["event_id"].astype(str))[["event_id", "timestamp", "headline", "currencies"]]
    rows = semantics.merge(meta, on="event_id", how="left")
    streams = build_currency_streams(rows)
    finbert = load_scores("finbert")[["event_id", "polarity", "confidence"]].assign(
        event_id=lambda f: f["event_id"].astype(str))
    pair_events = meta[meta["event_id"].isin(canonical)].merge(finbert, on="event_id")
    pair_streams = build_pair_streams(pair_events, H6_PAIRS, str(settings["baseline_usd_policy"]))
    releases = build_release_streams(pd.read_parquet(CALENDAR))
    return freeze, streams, pair_streams, releases


def _run_horizon(times, horizon, pairs, streams, pair_streams, releases, settings) -> dict:
    raw = raw_signal_frame(streams, releases, times, settings)
    base = baseline_frame({p: pair_streams[p] for p in pairs}, times)
    targets = _major_targets(times, horizon, settings) if set(pairs) <= set(MAJORS) else {
        **{p: v for p, v in _major_targets(times, horizon, settings).items() if p in pairs},
        USDINR: _usdinr_target(times, settings)}
    panel = assemble_panel(times, {p: targets[p] for p in pairs}, base, settings)
    out = {"n_signal_times": int(len(times)), "rows_with_target": int(panel["target"].notna().sum()),
           "pair_bias": h6_test(panel, raw, settings, pairs=pairs)}
    for ablation in ("news_only", "event_only"):
        out[ablation] = h6_test(panel, raw, settings, pairs=pairs, feature=ablation)
    return out | {"_panel": panel, "_raw": raw}


def main() -> None:
    try:
        require_resolved(UNRESOLVED)
    except ProtocolUnresolved as exc:
        raise SystemExit(str(exc)) from exc
    settings = dict(UNRESOLVED)
    freeze, streams, pair_streams, releases = _load_inputs(settings)

    daily = apply_missing_window_rule(signal_times(SAMPLE_START, SAMPLE_END, hourly=False),
                                      freeze["missing_windows"], str(settings["missing_window_rule"]))
    hourly = apply_missing_window_rule(signal_times(SAMPLE_START, SAMPLE_END, hourly=True),
                                       freeze["missing_windows"], str(settings["missing_window_rule"]))

    primary = _run_horizon(daily, PRIMARY_HORIZON_MIN, H6_PAIRS, streams, pair_streams, releases, settings)
    inr_panel = primary["_panel"][primary["_panel"]["pair"] == USDINR].drop(
        columns=[c for c in primary["_panel"].columns if c.startswith("fe_")])
    usdinr_alone = h6_test(inr_panel, primary["_raw"], settings, pairs=[USDINR])

    secondaries = {h: _run_horizon(hourly, h, MAJORS, streams, pair_streams, releases, settings)
                   for h in SECONDARY_HORIZONS_MIN}
    q = bh_secondaries({h: r["pair_bias"] for h, r in secondaries.items()})

    ic_by_horizon = {PRIMARY_HORIZON_MIN: _ic(primary["pair_bias"])}
    ic_by_horizon.update({h: _ic(r["pair_bias"]) for h, r in secondaries.items()})
    horizon = horizon_output(ic_by_horizon)

    strip = lambda r: {k: v for k, v in r.items() if not k.startswith("_")}  # noqa: E731
    report = {
        "freeze_sha256": freeze["sha256"], "coverage": freeze.get("coverage"),
        "settings": settings, "generated_at": datetime.now(UTC).isoformat(),
        "h6a_primary_daily": strip(primary),
        "verdict": "SUPPORTED" if primary["pair_bias"]["passes"] else "NOT SUPPORTED",
        "reported_only": {
            "usd_inr_alone": usdinr_alone,
            "intraday_secondaries": {str(h): strip(r) | {"criterion_i_q_bh": q[h]} for h, r in secondaries.items()},
            "horizon_output": horizon,
        },
    }
    RESULTS.write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    HORIZON.write_text(json.dumps({**horizon, "freeze_sha256": freeze["sha256"],
                                   "generated_at": report["generated_at"]}, indent=2, default=float), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("verdict", "coverage")} | {"horizon": horizon["label"]}, indent=2))


def _ic(test: dict) -> tuple[float | None, float | None]:
    crit = test.get("criterion_ii") or {}
    return crit.get("ic"), crit.get("ic_p")


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
