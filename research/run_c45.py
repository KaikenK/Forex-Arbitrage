"""C45 (pre-registered 2026-11-21, RESEARCH_TRACKER §6): out-of-sample replication of window30 T2,
"news activity predicts the size of the next move", on GDELT headlines from 2025-05-01..2026-10-31.

Steps, in order (each refuses to run out of order):
  ingest   BigQuery extraction (`research/sql/c45_gkg_extract.sql`, project arbex-c45) -> monthly
           shards in `artifacts/c45/gkg_shards` (unchanged `gkg_ingest` code) + unfiltered daily
           counts `artifacts/c45/gkg_daily_counts.json`.
  freeze   hash the 18 monthly shards (same content hash as the main freeze) and list missing days by
           the DEVIATION 5 rules (missing month; unfiltered counts; tagged counts). Written once.
  score    `score_archive` stages on the frozen C45 events, into `artifacts/c45/scores/<stage>`
           (dedup is required; finbert only feeds the reported-only T1/T3).
  test     T2-clock (primary), T1/T3 on clock-clear rows (reported) -> `artifacts/c45_results.json`.
  bridge   T2-clock on the original 2019-10..2025-04 frozen sample (exploratory, already-seen data)
           -> `artifacts/c45_bridge_results.json`.

T2-clock: window30's T2 (OLS of log abnormal move on log(1 + activity) and pair dummies, SEs clustered
by UTC date) on clock-clear signal times only, with no release flag. Clock-clear = for both
currencies of the pair, no release clock (`artifacts/c45_release_clocks.json`, fixed before any C45
data existed) falls in (t - 45 min, t + h] on that weekday. Every other window30 definition is
reused from `run_window30` unchanged. Replicated if the activity beta at 60 min is > 0 with p < 0.05.

  python -m research.run_c45 ingest --project arbex-c45
  python -m research.run_c45 freeze
  python -m research.run_c45 score --stage dedup        (then optionally --stage finbert)
  python -m research.run_c45 test
  python -m research.run_c45 bridge
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from research import gkg_ingest
from research.eventstudy.stats import benjamini_hochberg
from research.run_window30 import (STEP_MINUTES, build_pair_news, caution, direction_test, drop_missing,
                                   release_flags, signal_times_30, abnormal_move, window_signal, caution_test,
                                   HORIZONS_MIN, LAG_MINUTES, WINDOW_MINUTES)
from research.semantic.currency_bias import MAJORS
from research.semantic.walkforward import clustered_ols

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
C45_DIR = ARTIFACTS / "c45"
SHARD_DIR = C45_DIR / "gkg_shards"
DAILY_COUNTS = C45_DIR / "gkg_daily_counts.json"
FREEZE_PATH = C45_DIR / "freeze.json"
SCORES_DIR = C45_DIR / "scores"
CLOCKS_PATH = ARTIFACTS / "c45_release_clocks.json"
RESULTS = ARTIFACTS / "c45_results.json"
BRIDGE_RESULTS = ARTIFACTS / "c45_bridge_results.json"

SAMPLE_START = date(2025, 5, 1)
SAMPLE_END = date(2026, 10, 31)
PRIMARY_HORIZON = 60
ALPHA = 0.05


class C45Error(RuntimeError):
    pass


# ---------------------------------------------------------------- ingest
def months(first: date = SAMPLE_START, last: date = SAMPLE_END) -> list[date]:
    return gkg_ingest.months(first.replace(day=1), last.replace(day=1))


def ingest(client, project: str, shard_dir: Path = SHARD_DIR, counts_out: Path = DAILY_COUNTS) -> dict:
    shard_dir.mkdir(parents=True, exist_ok=True)
    days = gkg_ingest.fetch_daily_counts(client, project, out=counts_out)
    report = gkg_ingest.ingest(client, project, shard_dir=shard_dir, wanted=months())
    return {"daily_count_days": days, **report}


# ---------------------------------------------------------------- freeze
def build_freeze(shard_dir: Path = SHARD_DIR, counts_path: Path = DAILY_COUNTS) -> dict:
    from research.freeze_gdelt_sample import content_hash, sentinel_completed_at

    expected = [gkg_ingest.shard_name(m) for m in months()]
    names, shards, tagged = [], [], Counter()
    for name in expected:
        sentinel = shard_dir / f"{name}.done"
        if not sentinel.exists():
            continue
        completed, source, _ = sentinel_completed_at(sentinel)
        shard = shard_dir / f"{name}.parquet"
        events = 0
        if shard.exists():
            stamps = pd.read_parquet(shard, columns=["timestamp"])["timestamp"]
            events = len(stamps)
            tagged.update(pd.to_datetime(stamps, utc=True).dt.strftime("%Y-%m-%d").value_counts().to_dict())
        names.append(name)
        shards.append({"window": name, "events": events, "completed_at": completed.isoformat(),
                       "completed_at_source": source})
    rules: dict[str, set[str]] = {"missing_month": set(), "unfiltered_counts": set(), "tagged_counts": set()}
    for name in sorted(set(expected) - set(names)):
        month = datetime.strptime(name[:8], "%Y%m%d").date()
        day = month
        while day.month == month.month and day <= SAMPLE_END:
            rules["missing_month"].add(day.isoformat())
            day += timedelta(days=1)
    if counts_path.exists():
        counts = json.loads(counts_path.read_text(encoding="utf-8"))["counts"]
        rules["unfiltered_counts"].update(gkg_ingest.gap_days(counts, SAMPLE_START, SAMPLE_END))
    if tagged:
        rules["tagged_counts"].update(gkg_ingest.gap_days(dict(tagged), SAMPLE_START, SAMPLE_END))
    return {"rule": "C45 (RESEARCH_TRACKER §6, 2026-11-21); DEVIATION 4/5 rules on the C45 period",
            "sample": [SAMPLE_START.isoformat(), SAMPLE_END.isoformat()], "source": "bigquery_gkg arbex-c45",
            "expected_windows": len(expected), "windows_done": len(names),
            "events": int(sum(s["events"] for s in shards)),
            "missing_windows": sorted(set().union(*rules.values())),
            "missing_days_by_rule": {k: len(v) for k, v in rules.items()},
            "missing_shards": sorted(set(expected) - set(names)),
            "sha256": content_hash(shard_dir, names), "shards": shards}


def write_freeze(shard_dir: Path = SHARD_DIR, out: Path = FREEZE_PATH, counts_path: Path = DAILY_COUNTS,
                 now: datetime | None = None) -> dict:
    if out.exists():
        raise C45Error(f"{out} exists; the C45 freeze is written once")
    freeze = {**build_freeze(shard_dir, counts_path), "frozen_at_utc": (now or datetime.now(UTC)).isoformat()}
    if freeze["windows_done"] == 0:
        raise C45Error("no ingested C45 shard to freeze")
    out.parent.mkdir(parents=True, exist_ok=True)
    temp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(freeze, indent=2), encoding="utf-8")
    os.replace(temp, out)
    return freeze


def load_frozen_events(freeze_path: Path = FREEZE_PATH, shard_dir: Path = SHARD_DIR) -> tuple[pd.DataFrame, dict]:
    from research.freeze_gdelt_sample import content_hash
    from research.gdelt_backfill import retag

    if not freeze_path.exists():
        raise C45Error("no C45 freeze; run `python -m research.run_c45 freeze` first")
    freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
    if content_hash(shard_dir, [s["window"] for s in freeze["shards"]]) != freeze["sha256"]:
        raise C45Error("C45 shards changed since the freeze (hash mismatch); refusing to analyse")
    frames = [pd.read_parquet(shard_dir / f"{s['window']}.parquet") for s in freeze["shards"] if s["events"] > 0]
    events = retag(pd.concat(frames, ignore_index=True).drop_duplicates("event_id")) if frames else pd.DataFrame()
    return events, freeze


# ---------------------------------------------------------------- score
def score(stage: str, *, chunk_size: int = 2000, rebuild: bool = False, scores_dir: Path = SCORES_DIR,
          freeze_path: Path = FREEZE_PATH, shard_dir: Path = SHARD_DIR) -> None:
    from research.score_archive import _archive, _archive_hash, _check_manifest, run_chunked, run_dedup

    events, freeze = load_frozen_events(freeze_path, shard_dir)
    frame = _archive(events)
    out_dir = scores_dir / stage
    _check_manifest(out_dir, _archive_hash(frame), chunk_size, rebuild, freeze["sha256"])
    print(f"C45 scoring {len(frame)} headlines, stage={stage} -> {out_dir}", flush=True)
    if stage == "dedup":
        run_dedup(frame, out_dir)
    else:
        run_chunked(stage, frame, out_dir, chunk_size)


def _scores(stage: str, freeze_sha: str, scores_dir: Path) -> pd.DataFrame | None:
    from research.score_archive import load_scores

    manifest = scores_dir / stage / "manifest.json"
    if not manifest.exists() or json.loads(manifest.read_text(encoding="utf-8")).get("freeze_sha256") != freeze_sha:
        return None
    frame = load_scores(stage, scores_dir)
    return frame.assign(event_id=frame["event_id"].astype(str)) if len(frame) else None


# ---------------------------------------------------------------- clock mask
def load_clocks(path: Path = CLOCKS_PATH) -> dict[str, dict]:
    return json.loads(path.read_text(encoding="utf-8"))["currencies"]


def clock_instants(clocks: Mapping[str, Mapping], first: date, last: date) -> dict[str, list[float]]:
    """Every release clock of every currency on every local weekday in [first - 1, last + 1], as UTC epochs."""
    out: dict[str, list[float]] = {}
    for currency, spec in clocks.items():
        zone = ZoneInfo(spec["zone"])
        stamps = []
        day = first - timedelta(days=1)
        while day <= last + timedelta(days=1):
            if day.weekday() < 5:
                for clock in spec["clocks"]:
                    hh, mm = (int(x) for x in clock.split(":"))
                    stamps.append(datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).timestamp())
            day += timedelta(days=1)
        out[currency] = sorted(stamps)
    return out


# ---------------------------------------------------------------- test
def build_clock_panel(times: np.ndarray, news, returns: Mapping[int, Mapping[str, np.ndarray]],
                      instants: Mapping[str, Sequence[float]]) -> pd.DataFrame:
    """One row per (pair, signal time, horizon), as `run_window30.build_panel`, with `clock_hit`
    (a release clock of base or quote in (t - 45 min, t + h]) in place of the calendar flag and
    `release` set False (no calendar for the period)."""
    frames = []
    for pair, pair_news in news.items():
        bias, activity = window_signal(pair_news, times)
        for h, by_pair in returns.items():
            r = by_pair[pair]
            frames.append(pd.DataFrame({
                "pair": pair, "time_s": times, "horizon": h, "bias": bias, "activity": activity,
                "caution": caution(bias, activity), "ret": r, "abnormal": abnormal_move(times, r),
                "clock_hit": release_flags(instants, pair, times, h), "release": False}))
    panel = pd.concat(frames, ignore_index=True)
    stamps = pd.to_datetime(panel["time_s"], unit="s", utc=True)
    panel["utc_date"] = stamps.dt.date.astype(str)
    panel["month"] = stamps.dt.strftime("%Y-%m")
    return panel


def activity_test_clock(panel: pd.DataFrame) -> dict:
    """T2-clock: OLS of log abnormal move on log(1 + activity) and pair dummies (no release flag)."""
    data = panel[np.isfinite(panel["abnormal"]) & (panel["abnormal"] > 0)].copy()
    data["log_abnormal"] = np.log(data["abnormal"])
    data["log_activity"] = np.log1p(data["activity"])
    dummies = []
    for pair in sorted(data["pair"].unique())[1:]:
        name = f"fe_{pair.replace('/', '')}"
        data[name] = (data["pair"] == pair).astype(float)
        dummies.append(name)
    fit = clustered_ols(data, target="log_abnormal", features=["log_activity", *dummies], cluster_col="utc_date")
    if fit is None:
        return {"n": int(len(data)), "beta": None, "p": None}
    coef = fit.coefficients["log_activity"]
    return {"n": fit.n, "n_dates": fit.n_clusters, "beta": coef.beta, "se": coef.se, "p": coef.p}


def run_clock_tests(panel: pd.DataFrame, *, with_bias: bool) -> dict:
    clear = panel[~panel["clock_hit"]]
    t2 = {h: activity_test_clock(clear[clear["horizon"] == h]) for h in sorted(clear["horizon"].unique())}
    keys = [h for h, r in t2.items() if r.get("p") is not None]
    for h, corr in zip(keys, benjamini_hochberg([t2[h]["p"] for h in keys])):
        t2[h]["q_bh"] = corr.q_value
    primary = t2.get(PRIMARY_HORIZON, {})
    replicated = bool(primary.get("beta") is not None and primary["beta"] > 0 and primary["p"] < ALPHA)
    reported = {}
    if with_bias:
        for h in sorted(clear["horizon"].unique()):
            sub = clear[clear["horizon"] == h]
            reported[f"T1_direction_{h}"] = direction_test(sub)
            reported[f"T3_caution_{h}"] = caution_test(sub)
    first = panel[panel["horizon"] == panel["horizon"].min()]
    return {"T2_clock": {f"h{h}": r for h, r in t2.items()},
            "decision": {"replicated": replicated,
                         "rule": f"activity beta > 0 with p < {ALPHA} at {PRIMARY_HORIZON} min (T2-clock)"},
            "reported_only": reported or {"T1_T3": "FinBERT scores not available for this sample"},
            "coverage": {"signal_rows": int(len(first)), "clock_clear_rows": int((~first["clock_hit"]).sum()),
                         "rows_with_activity": int((first["activity"] > 0).sum()),
                         "clock_clear_rows_with_activity": int(((~first["clock_hit"]) & (first["activity"] > 0)).sum())}}


def _news_frame(events: pd.DataFrame, canonical: set[str], finbert: pd.DataFrame | None) -> tuple[pd.DataFrame, bool]:
    meta = events.assign(event_id=events["event_id"].astype(str))[["event_id", "timestamp", "currencies"]]
    meta = meta[meta["event_id"].isin(canonical)]
    if finbert is not None:
        return meta.merge(finbert[["event_id", "polarity", "confidence"]], on="event_id"), True
    return meta.assign(polarity=0.0, confidence=1.0), False      # activity only; bias unused


def evaluate(events: pd.DataFrame, canonical: set[str], finbert: pd.DataFrame | None, missing_days: Sequence[str],
             start: date, end: date, returns_fn: Callable[[np.ndarray], Mapping], clocks: Mapping) -> dict:
    frame, with_bias = _news_frame(events, canonical, finbert)
    news = build_pair_news(frame, MAJORS)
    times = drop_missing(signal_times_30(start, end), missing_days)
    panel = build_clock_panel(times, news, returns_fn(times), clock_instants(clocks, start, end))
    if not with_bias:
        panel["bias"] = np.nan
    return run_clock_tests(panel, with_bias=with_bias)


def _settings(start: date, end: date) -> dict:
    return {"window_minutes": WINDOW_MINUTES, "lag_minutes": LAG_MINUTES, "step_minutes": STEP_MINUTES,
            "horizons_min": list(HORIZONS_MIN), "pairs": list(MAJORS), "sample": [start.isoformat(), end.isoformat()]}


def test(returns_fn=None) -> dict:
    from research.run_window30 import _returns

    events, freeze = load_frozen_events()
    dedup = _scores("dedup", freeze["sha256"], SCORES_DIR)
    if dedup is None:
        raise C45Error("dedup was not scored on the C45 freeze; run `run_c45 score --stage dedup`")
    canonical = set(dedup.loc[dedup["canonical"], "event_id"])
    finbert = _scores("finbert", freeze["sha256"], SCORES_DIR)
    report = {"framing": "C45, pre-registered 2026-11-21: out-of-sample replication of window30 T2 (T2-clock)",
              "freeze_sha256": freeze["sha256"], "events": freeze["events"], "canonical": len(canonical),
              "missing_days": len(freeze["missing_windows"]), "settings": _settings(SAMPLE_START, SAMPLE_END),
              "generated_at": datetime.now(UTC).isoformat(),
              **evaluate(events, canonical, finbert, freeze["missing_windows"], SAMPLE_START, SAMPLE_END,
                         returns_fn or _returns, load_clocks())}
    RESULTS.write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    return report


def bridge(returns_fn=None) -> dict:
    """T2-clock on the original frozen 2019-10..2025-04 sample (exploratory; already-seen data)."""
    from research import freeze_gdelt_sample as fz
    from research.run_window30 import _returns
    from research.score_archive import load_scores, require_frozen_scores

    freeze = fz.load_freeze()
    events = fz.load_frozen_events(freeze)
    for stage in ("dedup", "finbert"):
        require_frozen_scores(stage, freeze["sha256"])
    dedup = load_scores("dedup")
    canonical = set(dedup.loc[dedup["canonical"], "event_id"].astype(str))
    finbert = load_scores("finbert").assign(event_id=lambda f: f["event_id"].astype(str))
    report = {"framing": "C45 bridge: T2-clock on the original frozen sample (exploratory, already-seen data)",
              "freeze_sha256": freeze["sha256"], "settings": _settings(fz.SAMPLE_START, fz.SAMPLE_END),
              "generated_at": datetime.now(UTC).isoformat(),
              **evaluate(events, canonical, finbert, freeze["missing_windows"], fz.SAMPLE_START, fz.SAMPLE_END,
                         returns_fn or _returns, load_clocks())}
    BRIDGE_RESULTS.write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="C45: window30 T2 replication on 2025-05..2026-10 news.")
    sub = parser.add_subparsers(dest="step", required=True)
    p_ingest = sub.add_parser("ingest")
    p_ingest.add_argument("--project", default="arbex-c45")
    sub.add_parser("freeze")
    p_score = sub.add_parser("score")
    p_score.add_argument("--stage", choices=("dedup", "finbert"), required=True)
    p_score.add_argument("--rebuild", action="store_true")
    sub.add_parser("test")
    sub.add_parser("bridge")
    args = parser.parse_args(argv)
    np.seterr(all="ignore")
    if args.step == "ingest":
        from google.cloud import bigquery
        print(json.dumps(ingest(bigquery.Client(project=args.project), args.project), indent=2))
    elif args.step == "freeze":
        freeze = write_freeze()
        print(json.dumps({k: v for k, v in freeze.items() if k not in ("shards", "missing_windows")}, indent=2))
    elif args.step == "score":
        score(args.stage, rebuild=args.rebuild)
    elif args.step == "test":
        print(json.dumps(test(), indent=2, default=float))
    else:
        print(json.dumps(bridge(), indent=2, default=float))


if __name__ == "__main__":
    main()
