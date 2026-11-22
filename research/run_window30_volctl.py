"""Window-30 volatility control: is the T2 activity effect just recent realised volatility?

window30's T2 regresses the log abnormal move of the next 30 or 60 minutes on log(1 + activity),
a release flag and pair dummies. Activity is net of the same-clock baseline (the abnormal move is
|r| over the median |r| at that clock on the previous 20 weekdays) but not of volatility that is
already running in the news window. This adds two controls and reports how far the activity
coefficient shrinks on the same rows:

  prior abnormal move   |r(t - 45, t - 15)| over the median |r| of the same window at the same
                        clock on the previous 20 weekdays (>= 10): the move inside the news window
  trailing 24 h level   mean |30-minute return| over the 24 hours ending at t (windows that end at
                        or before t, at least 24 of them), over its own median at the same clock on
                        the previous 20 weekdays

Models, each on the rows where both controls exist (so the comparison is like for like):
  base      log abnormal ~ log(1 + activity) + release flag + pair dummies   (window30 T2)
  +prior    base + log prior abnormal move
  +level    base + log trailing 24 h ratio
  +both     base + both
Shrinkage = 1 - beta(controlled) / beta(base). SEs clustered by UTC date, as T2.

Exploratory; no decision weight. The protocol text is in paper/ieee/VOLCTL_PREREG_DRAFT.md and
must be appended to RESEARCH_TRACKER section 6 before this is run. Needs the frozen GDELT sample,
its scores and HistData, exactly like `run_window30`.

  python -m research.run_window30_volctl
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research import run_window30 as w30
from research.semantic.walkforward import clustered_ols

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
RESULTS = ARTIFACTS / "window30_volctl_results.json"

LEVEL_HOURS = 24.0
LEVEL_MIN_WINDOWS = 24                   # of the 48 half-hour windows in 24 h
PRIOR_START_MIN = w30.WINDOW_MINUTES + w30.LAG_MINUTES      # 45: the news window is (t - 45, t - 15]
MODELS = {
    "base": [],
    "plus_prior": ["log_prior"],
    "plus_level": ["log_level"],
    "plus_both": ["log_prior", "log_level"],
}


def trailing_level(times: np.ndarray, returns_30: np.ndarray, *, hours: float = LEVEL_HOURS,
                   min_windows: int = LEVEL_MIN_WINDOWS, window_minutes: float = w30.WINDOW_MINUTES) -> np.ndarray:
    """Mean |r| of the 30-minute windows that start at s with t - hours <= s and s + 30 min <= t.
    `times` ascending (epoch seconds); returns_30[j] is the return from times[j] to times[j] + 30 min."""
    times = np.asarray(times, dtype=float)
    values = np.abs(np.asarray(returns_30, dtype=float))
    ok = np.isfinite(values)
    csum = np.concatenate([[0.0], np.cumsum(np.where(ok, values, 0.0))])
    ccount = np.concatenate([[0], np.cumsum(ok)])
    lo = np.searchsorted(times, times - hours * 3600, side="left")
    hi = np.searchsorted(times, times - window_minutes * 60, side="right")
    hi = np.maximum(hi, lo)
    count = ccount[hi] - ccount[lo]
    sums = csum[hi] - csum[lo]
    out = np.full(len(times), np.nan)
    enough = count >= min_windows
    out[enough] = sums[enough] / count[enough]
    return out


def control_columns(times: np.ndarray, prior_returns: np.ndarray, returns_30: np.ndarray) -> pd.DataFrame:
    """Per signal time: prior abnormal move and trailing-24h ratio (NaN where undefined)."""
    prior = w30.abnormal_move(times, prior_returns)
    level = w30.abnormal_move(times, trailing_level(times, returns_30))
    return pd.DataFrame({"time_s": times, "prior_abnormal": prior, "level_ratio": level})


def add_controls(panel: pd.DataFrame, controls_by_pair: dict[str, pd.DataFrame]) -> pd.DataFrame:
    frames = [controls.assign(pair=pair) for pair, controls in controls_by_pair.items()]
    merged = panel.merge(pd.concat(frames, ignore_index=True), on=["pair", "time_s"], how="left")
    merged["log_prior"] = np.log(merged["prior_abnormal"].where(merged["prior_abnormal"] > 0))
    merged["log_level"] = np.log(merged["level_ratio"].where(merged["level_ratio"] > 0))
    return merged


def activity_fit(data: pd.DataFrame, extra: list[str]) -> dict | None:
    data = data.copy()
    dummies = []
    for pair in sorted(data["pair"].unique())[1:]:
        name = f"fe_{pair.replace('/', '')}"
        data[name] = (data["pair"] == pair).astype(float)
        dummies.append(name)
    fit = clustered_ols(data, target="log_abnormal", features=["log_activity", "release_flag", *dummies, *extra],
                        cluster_col="utc_date")
    if fit is None:
        return None
    c = fit.coefficients["log_activity"]
    return {"n": fit.n, "n_dates": fit.n_clusters, "beta": c.beta, "se": c.se, "p": c.p,
            **{f"beta_{name}": fit.coefficients[name].beta for name in extra}}


def shrinkage(beta_base: float, beta_controlled: float) -> float:
    return 1.0 - beta_controlled / beta_base


def run_tests(panel: pd.DataFrame) -> dict:
    """Per horizon: the four models on the rows where both controls exist, with shrinkage."""
    out: dict[str, dict] = {}
    for h in sorted(panel["horizon"].unique()):
        sub = panel[(panel["horizon"] == h) & np.isfinite(panel["abnormal"]) & (panel["abnormal"] > 0)].copy()
        sub["log_abnormal"] = np.log(sub["abnormal"])
        sub["log_activity"] = np.log1p(sub["activity"])
        sub["release_flag"] = sub["release"].astype(float)
        rows = sub[np.isfinite(sub["log_prior"]) & np.isfinite(sub["log_level"])]
        fits = {name: activity_fit(rows, extra) for name, extra in MODELS.items()}
        base = fits["base"]
        for name, fit in fits.items():
            if fit is not None and base is not None and name != "base":
                fit["shrinkage_vs_base"] = shrinkage(base["beta"], fit["beta"])
        out[f"T2_activity_{h}"] = {"rows_with_controls": int(len(rows)),
                                   "rows_without_controls": int(len(sub) - len(rows)), "models": fits}
    return out


def _control_returns(times: np.ndarray) -> dict[str, dict[str, np.ndarray]]:
    from research.data.cache_paths import histdata_cache_dir
    from research.data.histdata import load_windowed_series
    from research.semantic.currency_bias import MAJORS, forward_log_returns

    entry_lag, exit_lag = w30.ENTRY_EXIT_LAG_MINUTES
    hours = w30._hours(np.concatenate([times - PRIOR_START_MIN * 60, times]), 60)
    out: dict[str, dict[str, np.ndarray]] = {"prior": {}, "r30": {}}
    for pair in MAJORS:
        series, missing = load_windowed_series(pair, hours, histdata_cache_dir())
        if missing:
            print(f"  {pair}: HistData years missing {missing} -> those rows fail closed")
        bars = np.array([t.timestamp() for t in series.times], dtype=float)
        prices = np.asarray(series.prices, dtype=float)
        kw = {"entry_lag_minutes": entry_lag, "exit_lag_minutes": exit_lag}
        out["prior"][pair] = forward_log_returns(bars, prices, times - PRIOR_START_MIN * 60,
                                                 horizon_minutes=w30.WINDOW_MINUTES, **kw)
        out["r30"][pair] = forward_log_returns(bars, prices, times, horizon_minutes=w30.WINDOW_MINUTES, **kw)
        del series, bars, prices
    return out


def main() -> None:
    from research.freeze_gdelt_sample import SAMPLE_END, SAMPLE_START, load_freeze, load_frozen_events
    from research.score_archive import load_scores, require_frozen_scores

    freeze = load_freeze()
    events = load_frozen_events(freeze)
    for stage in ("dedup", "finbert"):
        require_frozen_scores(stage, freeze["sha256"])
    dedup = load_scores("dedup")
    canonical = set(dedup.loc[dedup["canonical"], "event_id"].astype(str))
    finbert = load_scores("finbert")[["event_id", "polarity", "confidence"]].assign(
        event_id=lambda f: f["event_id"].astype(str))
    meta = events.assign(event_id=events["event_id"].astype(str))[["event_id", "timestamp", "currencies"]]
    scored = meta[meta["event_id"].isin(canonical)].merge(finbert, on="event_id")
    news = w30.build_pair_news(scored)
    times = w30.drop_missing(w30.signal_times_30(SAMPLE_START, SAMPLE_END), freeze["missing_windows"])
    panel = w30.build_panel(times, news, w30._returns(times),
                            w30.release_times_from_calendar(pd.read_parquet(w30.CALENDAR)))
    ctrl = _control_returns(times)
    controls = {pair: control_columns(times, ctrl["prior"][pair], ctrl["r30"][pair]) for pair in news}
    report = {"freeze_sha256": freeze["sha256"], "generated_at": datetime.now(UTC).isoformat(),
              "framing": "exploratory; volatility-control check of window30 T2; no decision weight",
              "tests": run_tests(add_controls(panel, controls))}
    RESULTS.write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    print(json.dumps(report, indent=2, default=float))


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
