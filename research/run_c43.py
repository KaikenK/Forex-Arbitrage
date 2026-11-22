"""C43 (pre-specified 2026-11-05): analysis of the shadow engine's rows. Exploratory, descriptive,
no decision weight.

LOCKED in RESEARCH_TRACKER §6 ("C43 — shadow engine"); written late, on 2026-11-21, before any
shadow outcome was examined (§6 2026-11-21 entry, which also fixes how the two shadow folders are
merged). Refuses to run before 2026-11-18 00:00 IST, the same gate as the RQ5 extension and C40.

Sample: shadow rows from the shadow start (2026-11-05 19:49:52 IST) to 2026-11-18 00:00 IST, one per
opportunity id (earliest `written_at`) over every shadow folder given, joined to the recorder's
`opportunities.jsonl` by id. Outcome: H6b's locked next-poll outcome (`run_h6b.next_poll_outcome`,
one-sided next quotes dropped as C19).

  (a) still-crossed AUC of `adjusted_confidence_score` vs the detector's `confidence_score` on the
      same opportunities, with the 95 % CI of the difference from a day-cluster bootstrap (2,000 draws).
  (b) AUC for NOT still crossed of the FX-filtered `activity_factor` (shadow row) vs the unfiltered one
      (C40-3's measure, min(1, window_bias.item_count / 5) from the recorded snapshot), same rows.
  (c) share of opportunities where the V1 and currency-index window directions agree (inputs only).
  (d) share of window items the FX filter removes (inputs only).

  python -m research.run_c43 [--recordings <dir>] [--shadow <dir> ...] [--out <json>]
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from research.run_c40 import _ci, _day_index, activity_factor
from research.run_h6b import (BOOTSTRAP_DRAWS, BOOTSTRAP_SEED, RQ5_END_UTC, auc, day_bootstrap,
                              default_recordings, load_opportunities, load_quotes, next_poll_outcome)

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
RESULTS = ARTIFACTS / "c43_results.json"
END_UTC = RQ5_END_UTC                                   # 2026-11-18 00:00 IST
SHADOW_START_UTC = datetime(2026, 11, 5, 14, 19, 52, tzinfo=UTC)    # 2026-11-05 19:49:52 IST
SHADOW_FILE = "semantic_shadow.jsonl"
DEFAULT_SHADOW_DIRS = (Path(r"C:\Users\xyreo\arbex\data\shadow_app"),
                       Path(r"C:\Users\xyreo\arbex\data\shadow_real"))


def load_shadow(dirs: Iterable[Path]) -> tuple[dict[str, dict], dict]:
    """Union of every folder's rows, one per opportunity id (earliest `written_at`)."""
    rows: dict[str, dict] = {}
    counts = {"files": 0, "rows_read": 0, "duplicate_ids": 0, "per_folder": {}}
    for root in dirs:
        n = 0
        for path in sorted(Path(root).glob(f"*/{SHADOW_FILE}")):
            counts["files"] += 1
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    n += 1
                    key = str(row.get("id"))
                    old = rows.get(key)
                    if old is not None:
                        counts["duplicate_ids"] += 1
                        if float(row.get("written_at") or 0) >= float(old.get("written_at") or 0):
                            continue
                    rows[key] = row
        counts["per_folder"][str(root)] = n
        counts["rows_read"] += n
    return rows, counts


def joined_rows(shadow: Mapping[str, dict], opportunities: Sequence[dict], quotes, *,
                start: datetime = SHADOW_START_UTC, end: datetime = END_UTC) -> tuple[list[dict], dict]:
    by_id = {str(o.get("id")): o for o in opportunities}
    start_ms, end_ms = int(start.timestamp() * 1000), int(end.timestamp() * 1000)
    out, skipped = [], Counter()
    for key, s in shadow.items():
        opp = by_id.get(key)
        if opp is None:
            skipped["no_matching_opportunity"] += 1
            continue
        ts = int(opp.get("timestamp_ms") or 0)
        if not start_ms <= ts < end_ms:
            skipped["outside_window"] += 1
            continue
        outcome = next_poll_outcome(opp, quotes, one_sided="drop")
        if outcome is None:
            skipped["not_evaluable"] += 1
            continue
        filtered = s.get("window_filtered") or {}
        out.append({
            "day": datetime.fromtimestamp(ts / 1000, tz=UTC).date().isoformat(),
            "pair": s.get("pair"),
            "confidence_score": float(opp["confidence_score"]),
            "adjusted_confidence_score": (None if s.get("adjusted_confidence_score") is None
                                          else float(s["adjusted_confidence_score"])),
            "activity_unfiltered": activity_factor(opp.get("semantic_snapshot")),
            "activity_filtered": (None if filtered.get("activity_factor") is None
                                  else float(filtered["activity_factor"])),
            **outcome})
    return out, dict(skipped)


def test_a(rows: Sequence[dict], *, draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED) -> dict:
    usable = [r for r in rows if r["adjusted_confidence_score"] is not None]
    out: dict = {"what": "still-crossed AUC, adjusted vs detector confidence, same opportunities",
                 "n": len(usable)}
    labels = [r["still_crossed"] for r in usable]
    if not usable or len(set(labels)) < 2:
        out["not_computable"] = "both outcomes are needed for an AUC"
        return out
    scores = {"detector": np.array([r["confidence_score"] for r in usable], dtype=float),
              "adjusted": np.array([r["adjusted_confidence_score"] for r in usable], dtype=float)}
    out.update(day_bootstrap(usable, scores, draws=draws, seed=seed))
    out["not_still_crossed"] = int(len(usable) - sum(labels))
    return out


def test_b(rows: Sequence[dict], *, draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED) -> dict:
    usable = [r for r in rows if r["activity_filtered"] is not None and r["activity_unfiltered"] is not None]
    out: dict = {"what": "AUC for NOT still crossed, FX-filtered vs unfiltered activity_factor, same rows",
                 "n": len(usable)}
    labels = np.array([1 - r["still_crossed"] for r in usable], dtype=int)
    if not usable or len(set(labels.tolist())) < 2:
        out["not_computable"] = "both outcomes are needed for an AUC"
        return out
    filt = np.array([r["activity_filtered"] for r in usable], dtype=float)
    unfilt = np.array([r["activity_unfiltered"] for r in usable], dtype=float)
    unique, by_day = _day_index(np.array([r["day"] for r in usable]))
    rng = np.random.default_rng(seed)
    samples = {"filtered": [], "unfiltered": [], "filtered_minus_unfiltered": []}
    for _ in range(draws):
        picked = np.concatenate([by_day[d] for d in rng.choice(unique, size=len(unique), replace=True)])
        a, b = auc(filt[picked], labels[picked]), auc(unfilt[picked], labels[picked])
        if a is not None:
            samples["filtered"].append(a)
        if b is not None:
            samples["unfiltered"].append(b)
        if a is not None and b is not None:
            samples["filtered_minus_unfiltered"].append(a - b)
    out.update({"auc_not_still_crossed": {"filtered": auc(filt, labels), "unfiltered": auc(unfilt, labels)},
                "ci95": {k: _ci(v) for k, v in samples.items()}, "n_days": int(len(unique)),
                "vanished": int(labels.sum()), "bootstrap_draws": draws})
    return out


def test_c(shadow: Mapping[str, dict]) -> dict:
    pairs = [(s.get("window_direction_v1"), s.get("window_direction_currency_index")) for s in shadow.values()]
    both = [(a, b) for a, b in pairs if a is not None and b is not None]
    directional = [(a, b) for a, b in both if a != "neutral" and b != "neutral"]
    agree = sum(a == b for a, b in both)
    return {"what": "V1 window direction vs currency-index window direction (inputs only)",
            "n_with_both": len(both), "agree": agree, "agree_share": agree / len(both) if both else None,
            "n_both_directional": len(directional),
            "agree_share_both_directional": (sum(a == b for a, b in directional) / len(directional)
                                             if directional else None),
            "crosstab": {f"{a}|{b}": n for (a, b), n in sorted(Counter(both).items())}}


def test_d(shadow: Mapping[str, dict]) -> dict:
    kept, dropped, shares = 0, 0, []
    for s in shadow.values():
        f = s.get("window_filtered") or {}
        k, d = f.get("item_count"), f.get("fx_filter_dropped")
        if k is None or d is None:
            continue
        kept, dropped = kept + int(k), dropped + int(d)
        if k + d > 0:
            shares.append(d / (k + d))
    return {"what": "share of window items the FX filter removes (inputs only)", "rows": len(shares),
            "pooled_share_removed": dropped / (kept + dropped) if kept + dropped else None,
            "median_row_share_removed": float(np.median(shares)) if shares else None,
            "items_kept": kept, "items_removed": dropped}


def run(recordings: Path, shadow_dirs: Sequence[Path], *, now: datetime | None = None,
        draws: int = BOOTSTRAP_DRAWS) -> dict:
    """`now` is for tests only; the command line always uses the wall clock."""
    if (now or datetime.now(UTC)) < END_UTC:
        raise SystemExit(f"C43 sample is still open until {END_UTC.isoformat()}; shadow outcomes are not "
                         "examined before then (RESEARCH_TRACKER §6).")
    shadow, counts = load_shadow(shadow_dirs)
    day_dirs = sorted(d for d in recordings.iterdir() if d.is_dir())
    quotes = load_quotes(day_dirs)
    rows, skipped = joined_rows(shadow, load_opportunities(day_dirs), quotes)
    stamps = [int(o["timestamp_ms"]) for o in shadow.values() if o.get("timestamp_ms")]
    return {"framing": "C43, pre-specified 2026-11-05; exploratory, descriptive, no decision weight",
            "shadow": counts, "shadow_ids": len(shadow),
            "shadow_span_utc": ([datetime.fromtimestamp(min(stamps) / 1000, tz=UTC).isoformat(),
                                 datetime.fromtimestamp(max(stamps) / 1000, tz=UTC).isoformat()]
                                if stamps else None),
            "evaluable_rows": len(rows), "skipped": skipped,
            "a_adjusted_vs_detector": test_a(rows, draws=draws),
            "b_filtered_vs_unfiltered_activity": test_b(rows, draws=draws),
            "c_direction_agreement": test_c(shadow), "d_filter_removal": test_d(shadow),
            "venue_caveat": "TrueFX is an indicative feed: crosses are price discrepancies, not executable arbitrage",
            "generated_at": datetime.now(UTC).isoformat()}


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="C43: shadow-engine analysis (exploratory).")
    parser.add_argument("--recordings", type=Path, default=default_recordings())
    parser.add_argument("--shadow", type=Path, action="append", default=None)
    parser.add_argument("--out", type=Path, default=RESULTS)
    args = parser.parse_args(argv)
    report = run(args.recordings, args.shadow or list(DEFAULT_SHADOW_DIRS))
    args.out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
