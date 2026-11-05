"""C40 (pre-registered 2026-11-05): news as risk on the live recorder.

LOCKED in RESEARCH_TRACKER §6 ("C40 PRE-REGISTERED"); operational choices fixed before any
recorder outcome is examined in §6 ("C40 implementation note"). Refuses to run before
2026-11-18 00:00 IST, the same gate as the RQ5 extension.

Outcome (H6b's locked outcome): `still_crossed` at the next poll of both legs
(`run_h6b.next_poll_outcome`), plus the realised edge in pips there.

  C40-1 (primary)  share still crossed is LOWER in release windows (-5..+15 min) than in matched
                   controls (same pair, weekday and clock time, +-1..3 weeks, no high-impact release
                   for either currency within +-60 min); difference in proportions.
  C40-2            AUC for NOT still crossed of the older leg's quote age at detection (> 0.5).
  C40-3            AUC for NOT still crossed of the live window's activity_factor (> 0.5).

Benjamini-Hochberg over the three at q < 0.05; one-sided day-cluster bootstrap p-values and 95 %
CIs (2,000 resamples). Supported live if C40-1 or C40-3 passes in the stated direction; C40-2 is
a feed-quality check. Not computable below 30 release windows with an opportunity (C40-1) or
200 opportunities with an outcome (C40-2, C40-3).

  python -m research.run_c40 [--recordings <dir>] [--calendar-archive <dir>] [--predictions <jsonl>]
"""
from __future__ import annotations

import argparse
import bisect
import heapq
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from research.measure_arrival_latency import default_calendar_archive
from research.run_h6b import (ARTIFACTS, BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, CLEAN_WINDOW_START_UTC,
                              DEFAULT_PREDICTIONS, RQ5_END_UTC, WINDOW_END_UTC, auc, default_recordings,
                              leg_key, load_opportunities, load_quotes, load_resend_periods,
                              next_poll_outcome, resend_exclusions)
from research.semantic.currency_bias import H6B_UNRESOLVED

RESULTS = ARTIFACTS / "c40_results.json"
END_UTC = RQ5_END_UTC                                    # 2026-11-18 00:00 IST
STUDY_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "CHF", "INR")
PRIMARY_WINDOW = (timedelta(minutes=-5), timedelta(minutes=15))
WIDE_WINDOW = (timedelta(minutes=-15), timedelta(minutes=30))      # reported only
CONTROL_WEEKS = (-3, -2, -1, 1, 2, 3)
CONTROL_CLEAR = timedelta(minutes=60)
MIN_RELEASE_WINDOWS = 30
MIN_OUTCOMES = 200
BH_Q = 0.05
# Implementation note (§6): the §8 outage and restart gaps are found in the quote files, as
# silences longer than GAP_SECONDS in the union of all hubs' receipt times. An opportunity is
# dropped when a gap starts within GAP_GUARD_SECONDS after it (its next poll falls after the
# gap); a release or control window is dropped when it overlaps a gap.
GAP_SECONDS = 5.0
GAP_GUARD_SECONDS = 10.0
# activity_factor as the live window computes it (backend/core/sentiment_bridge.py, semantic_engine.py).
ACTIVITY_SATURATION_COUNT = 5.0


# ---------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------

def load_releases(archive: Path | None) -> tuple[list[dict], list[tuple[datetime, datetime]]]:
    """High-impact study-currency rows from the weekly ForexFactory archive, and the time spans the
    archive covers (a time outside every span has an unknown calendar: neither release nor control)."""
    releases: dict[tuple[str, str, str], dict] = {}
    spans: list[tuple[datetime, datetime]] = []
    if archive is None or not archive.exists():
        return [], []
    for file in sorted(archive.glob("ff_calendar_*.json")):
        try:
            rows = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        stamps = []
        for row in rows:
            try:
                stamp = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00")).astimezone(UTC)
            except (KeyError, ValueError):
                continue
            stamps.append(stamp)
            currency = str(row.get("country", "")).upper()
            if str(row.get("impact", "")).lower() != "high" or currency not in STUDY_CURRENCIES:
                continue
            if (stamp.hour, stamp.minute) == (0, 0):          # all-day items carry no release time
                continue
            key = (currency, str(row.get("title", "")), stamp.isoformat())
            releases[key] = {"currency": currency, "title": key[1], "time": stamp}
        if stamps:
            day0 = min(stamps).replace(hour=0, minute=0, second=0, microsecond=0)
            spans.append((day0, max(stamps).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)))
    return sorted(releases.values(), key=lambda r: r["time"]), spans


def pair_currencies(pair: str) -> tuple[str, str]:
    return pair[:3].upper(), pair[3:6].upper()


def find_gaps(quotes, *, gap_seconds: float = GAP_SECONDS) -> tuple[list[tuple[int, int]], tuple[int, int] | None]:
    """Recorder silences (ms ranges) in the union of every hub's receipt times, and the recorded span."""
    limit = gap_seconds * 1000
    gaps, first, prev = [], None, None
    for t in heapq.merge(*(ts for ts, _ in quotes.values())):      # streamed: no copy of every timestamp
        if prev is None:
            first = t
        elif t - prev > limit:
            gaps.append((prev, t))
        prev = t
    return gaps, (None if first is None else (first, prev))


def quote_age_ms(opp: Mapping, quotes) -> dict | None:
    """Age of each leg's latest recorded quote at detection (receipt clock, as /live's `age_ms`), and
    on the exchange clock where the feed stamps it (reported only)."""
    t = int(opp["timestamp_ms"])
    recv, exch = [], []
    for side in ("buy_source", "sell_source"):
        times, rows = quotes.get(leg_key(str(opp[side])), ([], []))
        i = bisect.bisect_right(times, t) - 1
        if i < 0:
            return None
        recv.append(t - times[i])
        stamp = rows[i].get("exchange_ts_ms")
        exch.append(None if stamp is None else t - int(stamp))
    return {"older_leg_age_ms": max(recv),
            "older_leg_exchange_age_ms": None if None in exch else max(exch)}


def activity_factor(snapshot: Mapping | None) -> float | None:
    """The live 30-minute window's activity_factor, rebuilt from its recorded `item_count`."""
    if not isinstance(snapshot, Mapping):
        return None
    window = snapshot.get("window_bias")
    if not isinstance(window, Mapping) or window.get("item_count") is None:
        return None
    return min(1.0, float(window["item_count"]) / ACTIVITY_SATURATION_COUNT)


def _overlaps(a: int, b: int, ranges: Sequence[tuple[int, int]]) -> bool:
    return any(lo < b and a < hi for lo, hi in ranges)


def _ms(stamp: datetime) -> int:
    return int(stamp.timestamp() * 1000)


def build_rows(opportunities: Sequence[dict], quotes, *, end: datetime, gaps: Sequence[tuple[int, int]],
               exclude: Sequence[tuple[datetime, datetime]] = (), one_sided: str) -> tuple[list[dict], dict]:
    end_ms = _ms(end)
    guard = GAP_GUARD_SECONDS * 1000
    gap_starts = sorted(a for a, _ in gaps)
    excluded = [(_ms(a), _ms(b)) for a, b in exclude]
    rows, skipped = [], defaultdict(int)
    for opp in opportunities:
        ts = int(opp.get("timestamp_ms") or 0)
        if ts >= end_ms:
            skipped["after_end"] += 1
            continue
        i = bisect.bisect_left(gap_starts, ts)
        if i < len(gap_starts) and gap_starts[i] - ts <= guard:
            skipped["next_poll_after_gap"] += 1
            continue
        if any(a <= ts < b for a, b in excluded):
            skipped["resend_period"] += 1
            continue
        outcome = next_poll_outcome(opp, quotes, one_sided=one_sided)
        if outcome is None:
            skipped["not_evaluable"] += 1
            continue
        age = quote_age_ms(opp, quotes) or {"older_leg_age_ms": None, "older_leg_exchange_age_ms": None}
        rows.append({"ts": ts, "day": datetime.fromtimestamp(ts / 1000, tz=UTC).date().isoformat(),
                     "pair": leg_key(str(opp["buy_source"]))[2],
                     "activity_factor": activity_factor(opp.get("semantic_snapshot")),
                     "item_count": ((opp.get("semantic_snapshot") or {}).get("window_bias") or {}).get("item_count"),
                     **age, **outcome})
    return rows, dict(skipped)


# ---------------------------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------------------------

def release_windows(releases: Sequence[dict], pairs: Sequence[str], spans, gaps, recorded: tuple[int, int] | None,
                    *, window: tuple[timedelta, timedelta], end: datetime) -> tuple[list[dict], list[dict], dict]:
    """Release windows (one per pair x release time) and their matched controls."""
    counts = defaultdict(int)
    if recorded is None:
        return [], [], {"no_recorded_span": 1}
    lo_rec, hi_rec = recorded
    by_currency: dict[str, list[datetime]] = defaultdict(list)
    for r in releases:
        by_currency[r["currency"]].append(r["time"])

    def covered(t0: datetime, t1: datetime) -> bool:
        return any(a <= t0 and t1 <= b for a, b in spans)

    def usable(t0: datetime, t1: datetime) -> bool:
        a, b = _ms(t0), _ms(t1)
        return lo_rec <= a and b <= min(hi_rec, _ms(end)) and not _overlaps(a, b, gaps) and covered(t0, t1)

    def clear(pair: str, t: datetime) -> bool:
        return all(abs(x - t) > CONTROL_CLEAR for c in pair_currencies(pair) for x in by_currency.get(c, ()))

    rel, ctl, seen_ctl = [], [], set()
    for pair in pairs:
        times = sorted({x for c in pair_currencies(pair) for x in by_currency.get(c, ())})
        for t in times:
            t0, t1 = t + window[0], t + window[1]
            if not usable(t0, t1):
                counts["release_window_unusable"] += 1
                continue
            rel.append({"pair": pair, "anchor": t, "start": _ms(t0), "end": _ms(t1)})
            for k in CONTROL_WEEKS:
                c = t + timedelta(weeks=k)
                c0, c1 = c + window[0], c + window[1]
                if (pair, c) in seen_ctl:
                    continue
                if not clear(pair, c):
                    counts["control_near_release"] += 1
                    continue
                if not usable(c0, c1):
                    counts["control_unusable"] += 1
                    continue
                seen_ctl.add((pair, c))
                ctl.append({"pair": pair, "anchor": c, "start": _ms(c0), "end": _ms(c1)})
    return rel, ctl, dict(counts)


def _assign(rows: Sequence[dict], windows: Sequence[dict]) -> tuple[list[int], list[int]]:
    """Indices of rows inside any window of their pair, and per-window opportunity counts."""
    by_pair: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for r_idx, r in enumerate(rows):
        by_pair[r["pair"]].append((r["ts"], r_idx))
    for v in by_pair.values():
        v.sort()
    inside, per_window = set(), []
    for w in windows:
        items = by_pair.get(w["pair"], [])
        lo = bisect.bisect_left(items, (w["start"], -1))
        hi = bisect.bisect_left(items, (w["end"], -1))
        per_window.append(hi - lo)
        inside.update(i for _, i in items[lo:hi])
    return sorted(inside), per_window


# ---------------------------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------------------------

def _one_sided_p(samples: Sequence[float], null: float, *, lower_is_effect: bool) -> float | None:
    if not samples:
        return None
    s = np.asarray(samples, dtype=float)
    wrong = np.sum(s >= null) if lower_is_effect else np.sum(s <= null)
    return float((wrong + 1) / (len(s) + 1))


def _ci(samples: Sequence[float]) -> list[float] | None:
    return [float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))] if samples else None


def _day_index(days: np.ndarray) -> tuple[np.ndarray, dict]:
    unique = np.unique(days)
    return unique, {d: np.flatnonzero(days == d) for d in unique}


def release_test(rows: Sequence[dict], rel: Sequence[dict], ctl: Sequence[dict], *,
                 draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED) -> dict:
    """C40-1: difference in still-crossed share (release minus control), rate ratio and edge."""
    rel_idx, rel_counts = _assign(rows, rel)
    ctl_idx, ctl_counts = _assign(rows, ctl)
    windows_with_opp = sum(1 for n in rel_counts if n > 0)
    out: dict = {"release_windows": len(rel), "control_windows": len(ctl),
                 "release_windows_with_opportunity": windows_with_opp,
                 "release_opportunities": len(rel_idx), "control_opportunities": len(ctl_idx)}
    if windows_with_opp < MIN_RELEASE_WINDOWS or not ctl_idx:
        out["not_computable"] = (f"{windows_with_opp} release windows contain an opportunity "
                                 f"(needs {MIN_RELEASE_WINDOWS}) or no control opportunity")
        return out
    labels = np.array([r["still_crossed"] for r in rows], dtype=float)
    days = np.array([r["day"] for r in rows])
    group = np.full(len(rows), -1)
    group[rel_idx], group[ctl_idx] = 1, 0
    edges = np.array([np.nan if r["edge_pips"] is None else r["edge_pips"] for r in rows], dtype=float)
    win_days_rel = np.array([datetime.fromtimestamp(w["start"] / 1000, tz=UTC).date().isoformat() for w in rel])
    win_days_ctl = np.array([datetime.fromtimestamp(w["start"] / 1000, tz=UTC).date().isoformat() for w in ctl])
    rel_counts_a, ctl_counts_a = np.array(rel_counts, dtype=float), np.array(ctl_counts, dtype=float)

    def stats(idx: np.ndarray, rel_w: np.ndarray, ctl_w: np.ndarray) -> dict | None:
        g = group[idx]
        a, b = idx[g == 1], idx[g == 0]
        if not len(a) or not len(b) or not len(rel_w) or not len(ctl_w):
            return None
        ctl_rate = ctl_counts_a[ctl_w].mean()
        return {"diff": float(labels[a].mean() - labels[b].mean()),
                "rate_ratio": None if ctl_rate == 0 else float(rel_counts_a[rel_w].mean() / ctl_rate),
                "edge_diff": float(np.nanmean(edges[a]) - np.nanmean(edges[b]))
                if np.isfinite(edges[a]).any() and np.isfinite(edges[b]).any() else None}

    point = stats(np.arange(len(rows)), np.arange(len(rel)), np.arange(len(ctl)))
    a, b = np.array(rel_idx), np.array(ctl_idx)
    out.update({"still_crossed_share_release": float(labels[a].mean()),
                "still_crossed_share_control": float(labels[b].mean()),
                "difference": point["diff"], "rate_ratio": point["rate_ratio"],
                "mean_edge_pips_release": float(np.nanmean(edges[a])) if np.isfinite(edges[a]).any() else None,
                "mean_edge_pips_control": float(np.nanmean(edges[b])) if np.isfinite(edges[b]).any() else None})
    # Cluster unit: every day with an opportunity or a window, so empty windows are resampled too.
    unique = np.unique(np.concatenate([days, win_days_rel, win_days_ctl]))
    by_day = {d: np.flatnonzero(days == d) for d in unique}
    rel_by_day = {d: np.flatnonzero(win_days_rel == d) for d in unique}
    ctl_by_day = {d: np.flatnonzero(win_days_ctl == d) for d in unique}
    rng = np.random.default_rng(seed)
    samples: dict[str, list[float]] = defaultdict(list)
    for _ in range(draws):
        picked = rng.choice(unique, size=len(unique), replace=True)
        s = stats(np.concatenate([by_day[d] for d in picked]),
                  np.concatenate([rel_by_day[d] for d in picked]).astype(int),
                  np.concatenate([ctl_by_day[d] for d in picked]).astype(int))
        if s is None:
            continue
        for key, value in s.items():
            if value is not None:
                samples[key].append(value)
    out.update({"difference_ci95": _ci(samples["diff"]), "rate_ratio_ci95": _ci(samples["rate_ratio"]),
                "edge_difference_ci95": _ci(samples["edge_diff"]),
                "p_one_sided": _one_sided_p(samples["diff"], 0.0, lower_is_effect=True),
                "expected_sign": "difference < 0", "n_days": int(len(unique)), "bootstrap_draws": draws})
    return out


def auc_test(rows: Sequence[dict], field: str, *, draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED) -> dict:
    """AUC of `field` for NOT still crossed (> 0.5 expected), day-cluster bootstrap."""
    usable = [r for r in rows if r.get(field) is not None]
    out: dict = {"field": field, "n": len(usable), "expected_sign": "AUC > 0.5"}
    if len(usable) < MIN_OUTCOMES:
        out["not_computable"] = f"{len(usable)} opportunities with an outcome and {field} (needs {MIN_OUTCOMES})"
        return out
    scores = np.array([r[field] for r in usable], dtype=float)
    labels = np.array([1 - r["still_crossed"] for r in usable], dtype=int)      # 1 = vanished
    point = auc(scores, labels)
    unique, by_day = _day_index(np.array([r["day"] for r in usable]))
    rng = np.random.default_rng(seed)
    samples = []
    for _ in range(draws):
        picked = np.concatenate([by_day[d] for d in rng.choice(unique, size=len(unique), replace=True)])
        value = auc(scores[picked], labels[picked])
        if value is not None:
            samples.append(value)
    out.update({"auc_not_still_crossed": point, "auc_ci95": _ci(samples), "n_days": int(len(unique)),
                "vanished": int(labels.sum()), "p_one_sided": _one_sided_p(samples, 0.5, lower_is_effect=False),
                "bootstrap_draws": draws})
    return out


def benjamini_hochberg(pvalues: Mapping[str, float | None]) -> dict[str, float | None]:
    items = sorted((p, k) for k, p in pvalues.items() if p is not None)
    m, q, running = len(items), {}, 1.0
    for rank in range(m, 0, -1):
        p, key = items[rank - 1]
        running = min(running, p * m / rank)
        q[key] = float(running)
    return {k: q.get(k) for k in pvalues}


def decide(tests: Mapping[str, dict]) -> dict:
    q = benjamini_hochberg({k: t.get("p_one_sided") for k, t in tests.items()})
    passed = {}
    for key, test in tests.items():
        if "not_computable" in test or q[key] is None:
            passed[key] = None
            continue
        right_sign = (test["difference"] < 0) if key == "C40-1" else (test["auc_not_still_crossed"] > 0.5)
        passed[key] = bool(right_sign and q[key] < BH_Q)
        test["q_bh"] = q[key]
        test["passes"] = passed[key]
    news = [passed["C40-1"], passed["C40-3"]]
    verdict = ("NOT COMPUTABLE" if all(p is None for p in news) else
               "SUPPORTED" if any(p for p in news) else "NOT SUPPORTED")
    return {"news_as_risk_live": verdict, "feed_quality_C40-2": passed["C40-2"],
            "rule": "supported if C40-1 or C40-3 passes in the stated direction after BH (q < 0.05)"}


# ---------------------------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------------------------

def section(opportunities, quotes, releases, spans, gaps, recorded, *, end: datetime,
            exclude=(), draws: int = BOOTSTRAP_DRAWS) -> dict:
    one_sided = str(H6B_UNRESOLVED["one_sided_next_quote"])
    rows, skipped = build_rows(opportunities, quotes, end=end, gaps=gaps, exclude=exclude, one_sided=one_sided)
    pairs = sorted({r["pair"] for r in rows})
    excl_ms = [(_ms(a), _ms(b)) for a, b in exclude]
    window_gaps = list(gaps) + excl_ms
    rel, ctl, wcounts = release_windows(releases, pairs, spans, window_gaps, recorded, window=PRIMARY_WINDOW, end=end)
    clean_start = _ms(CLEAN_WINDOW_START_UTC)
    clean = [r for r in rows if r["ts"] >= clean_start]
    tests = {"C40-1": release_test(rows, rel, ctl, draws=draws),
             "C40-2": auc_test(rows, "older_leg_age_ms", draws=draws),
             "C40-3": auc_test(clean, "activity_factor", draws=draws)}
    out = {"rows": len(rows), "days": len({r["day"] for r in rows}), "pairs": pairs, "skipped": skipped,
           "window_counts": wcounts, "tests": tests, "decision": decide(tests)}
    rel_w, ctl_w, _ = release_windows(releases, pairs, spans, window_gaps, recorded, window=WIDE_WINDOW, end=end)
    split = _ms(WINDOW_END_UTC)
    out["reported_only"] = {
        "C40-1_wide_window_-15_+30": release_test(rows, rel_w, ctl_w, draws=draws),
        "C40-2_exchange_clock_age": auc_test(rows, "older_leg_exchange_age_ms", draws=draws),
        "C40-3_full_span_incl_pre_C28": auc_test(rows, "activity_factor", draws=draws),
        "C40-3_item_count_clean": auc_test(clean, "item_count", draws=draws),
        "C40-3_h6b_window_part": auc_test([r for r in clean if r["ts"] < split], "activity_factor", draws=draws),
        "C40-3_rq5_extension_part": auc_test([r for r in clean if r["ts"] >= split], "activity_factor", draws=draws),
    }
    return out


def run(recordings: Path, archive: Path | None, predictions: Path | None, *, now: datetime | None = None,
        end: datetime = END_UTC, draws: int = BOOTSTRAP_DRAWS) -> dict:
    """`now` is for tests only; the command line always uses the wall clock."""
    if (now or datetime.now(UTC)) < END_UTC:
        raise SystemExit(f"C40 sample is still open until {END_UTC.isoformat()}; the recorder data is not "
                         "examined before then (RESEARCH_TRACKER §6).")
    day_dirs = sorted(d for d in recordings.iterdir() if d.is_dir())
    quotes = load_quotes(day_dirs)
    opportunities = load_opportunities(day_dirs)
    releases, spans = load_releases(archive)
    gaps, recorded = find_gaps(quotes)
    report = {"framing": "C40, pre-registered 2026-11-05; BH over C40-1..3, day-cluster bootstrap",
              "end_utc": end.isoformat(), "opportunities": len(opportunities),
              "releases_high_impact": len(releases), "calendar_weeks": len(spans),
              "recorder_gaps": len(gaps), "gap_rule_seconds": GAP_SECONDS,
              "venue_caveat": "TrueFX is an indicative feed: crosses are price discrepancies, not executable arbitrage",
              "generated_at": datetime.now(UTC).isoformat()}
    report["main"] = section(opportunities, quotes, releases, spans, gaps, recorded, end=end, draws=draws)
    if predictions is not None and predictions.exists():
        periods = [p for p in load_resend_periods(predictions) if p["start"] < end]
        report["sensitivity_minus_resends_C39"] = section(opportunities, quotes, releases, spans, gaps, recorded,
                                                          end=end, exclude=resend_exclusions(periods), draws=draws)
        report["sensitivity_minus_resends_C39"]["resend_periods"] = len(periods)
    else:
        report["sensitivity_minus_resends_C39"] = {"unavailable": f"prediction log not found: {predictions}"}
    return report


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="C40: news as risk on the live recorder (pre-registered).")
    parser.add_argument("--recordings", type=Path, default=default_recordings())
    parser.add_argument("--calendar-archive", type=Path, default=default_calendar_archive())
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    report = run(args.recordings, args.calendar_archive, args.predictions)
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
