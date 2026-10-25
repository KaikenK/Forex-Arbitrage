"""H6b (prospective, exploratory): do the semantic channels improve opportunity ranking?

LOCKED in RESEARCH_TRACKER §6 ("H6 PRE-REGISTERED", 2026-10-25). Reported descriptively; not
a decision test. The recorder data must not be examined before this script is logged in §6,
and it refuses to run until the open clauses in `semantic/currency_bias.H6B_UNRESOLVED` are set.

Inputs (written by the live backend, never modified here):
  <recordings>/<YYYY-MM-DD>/opportunities.jsonl   one detected crossed book per line, with the
                                                  `semantic_snapshot` of both channels
  <recordings>/<YYYY-MM-DD>/<hub>.jsonl           every quote observation (tick_recorder.py)
  --predictions live_predictions.jsonl            per-pair semantic predictions (optional)

Locked outcome: whether the cross is still present at the next poll of both legs
(sell-leg bid > buy-leg ask), plus the realised edge at that poll in pips.
Locked comparison: AUC for "still crossed" of detector-only vs + risk channel vs + direction
channel, 95% CI by bootstrapping days. The detector-only score is the opportunity's
`confidence_score`, the field the channels adjust in the live ranker; a channel enters the
score exactly as the ranker applies it (score - penalty, clamped to [0, 1]).

  python -m research.run_h6b --recordings %LOCALAPPDATA%\\arbex\\recordings
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from research.semantic.currency_bias import H6B_UNRESOLVED, ProtocolUnresolved, require_resolved

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
RESULTS = ARTIFACTS / "h6b_results.json"
OPPORTUNITY_LOG = "opportunities.jsonl"
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 20261025
# Locked sample window (§6, "H6b sample window fixed", 2026-10-25): from the first poll after
# the 2026-10-25 restart — operationally, opportunities that carry a `semantic_snapshot` —
# up to 2026-11-05 00:00 IST. Data after the end is not used, and the script does not run
# before it, so the recorder data is not examined early.
WINDOW_END_UTC = datetime(2026, 11, 4, 18, 30, tzinfo=UTC)


def default_recordings() -> Path:
    root = os.environ.get("ARBEX_RECORD_DIR")
    if root and root.lower() != "off":
        return Path(root)
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "arbex" / "recordings"


def leg_key(leg_id: str) -> tuple[str, str, str]:
    """`spot_kraken_eurusd` -> (kraken, KRAKEN, EURUSD); `upstox_nse_usdinr` -> (upstox, NSE, USDINR)."""
    name = leg_id[len("replay_"):] if leg_id.startswith("replay_") else leg_id
    parts = name.split("_")
    if parts[0] == "spot":
        return parts[1].lower(), parts[1].upper(), parts[-1].upper()
    return parts[0].lower(), parts[1].upper(), parts[-1].upper()


def quote_key(row: Mapping) -> tuple[str, str, str]:
    return (str(row.get("provider") or "").lower(), str(row.get("exchange") or "").upper(),
            str(row.get("symbol") or "").upper())


def pip_size(symbol: str) -> float:
    return 0.01 if symbol.upper().endswith("JPY") else 0.0001


def load_quotes(day_dirs: Sequence[Path]) -> dict[tuple[str, str, str], tuple[list[int], list[dict]]]:
    rows: dict[tuple[str, str, str], list[tuple[int, dict]]] = defaultdict(list)
    for day in day_dirs:
        for path in sorted(day.glob("*.jsonl")):
            if path.name == OPPORTUNITY_LOG:
                continue
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if row.get("recv_ts_ms") is not None:
                        rows[quote_key(row)].append((int(row["recv_ts_ms"]), row))
    out = {}
    for key, items in rows.items():
        items.sort(key=lambda item: item[0])
        out[key] = ([t for t, _ in items], [r for _, r in items])
    return out


def load_opportunities(day_dirs: Sequence[Path]) -> list[dict]:
    out = []
    for day in day_dirs:
        path = day / OPPORTUNITY_LOG
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def next_poll_outcome(opp: Mapping, quotes, *, one_sided: str) -> dict | None:
    """Still crossed at the next observation of both legs, and the realised edge there."""
    t = int(opp["timestamp_ms"])
    legs = {}
    for side in ("buy_source", "sell_source"):
        key = leg_key(str(opp[side]))
        times, rows = quotes.get(key, ([], []))
        i = bisect.bisect_right(times, t)
        if i >= len(rows):
            return None                     # no later observation recorded: not evaluable
        legs[side] = rows[i]
    ask, bid = legs["buy_source"].get("ask"), legs["sell_source"].get("bid")
    if ask is None or bid is None:
        if one_sided == "drop":
            return None
        if one_sided == "not_crossed":
            return {"still_crossed": 0, "edge_pips": None}
        raise ValueError(f"unknown one_sided_next_quote {one_sided!r}")
    symbol = leg_key(str(opp["buy_source"]))[2]
    edge = (float(bid) - float(ask)) / pip_size(symbol)
    return {"still_crossed": int(edge > 0), "edge_pips": edge}


def auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    """Mann-Whitney AUC (ties count half)."""
    positives, negatives = labels == 1, labels == 0
    if positives.sum() == 0 or negatives.sum() == 0:
        return None
    from scipy.stats import rankdata

    ranks = rankdata(scores)
    n_pos, n_neg = positives.sum(), negatives.sum()
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def model_scores(rows: Sequence[dict], nesting: str) -> dict[str, np.ndarray]:
    detector = np.array([r["confidence_score"] for r in rows], dtype=float)
    risk = np.array([r["risk_penalty"] for r in rows], dtype=float)
    direction = np.array([r["direction_penalty"] for r in rows], dtype=float)
    clamp = lambda x: np.clip(x, 0.0, 1.0)  # noqa: E731
    if nesting == "cumulative":
        return {"detector": detector, "plus_risk": clamp(detector - risk),
                "plus_risk_direction": clamp(detector - risk - direction)}
    if nesting == "separate":
        return {"detector": detector, "plus_risk": clamp(detector - risk),
                "plus_direction": clamp(detector - direction)}
    raise ValueError(f"unknown model_nesting {nesting!r}")


def day_bootstrap(rows: Sequence[dict], scores: Mapping[str, np.ndarray], *,
                  draws: int = BOOTSTRAP_DRAWS, seed: int = BOOTSTRAP_SEED) -> dict:
    labels = np.array([r["still_crossed"] for r in rows], dtype=int)
    days = np.array([r["day"] for r in rows])
    unique = np.unique(days)
    index_by_day = {d: np.flatnonzero(days == d) for d in unique}
    rng = np.random.default_rng(seed)
    point = {name: auc(s, labels) for name, s in scores.items()}
    samples: dict[str, list[float]] = defaultdict(list)
    for _ in range(draws):
        picked = np.concatenate([index_by_day[d] for d in rng.choice(unique, size=len(unique), replace=True)])
        base = auc(scores["detector"][picked], labels[picked])
        for name, s in scores.items():
            value = auc(s[picked], labels[picked])
            if value is None:
                continue
            samples[name].append(value)
            if name != "detector" and base is not None:
                samples[f"{name}_minus_detector"].append(value - base)
    ci = {name: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for name, v in samples.items() if v}
    return {"auc": point, "ci95": ci, "n": len(rows), "n_days": len(unique), "positives": int(labels.sum())}


def evaluation_rows(opportunities: Sequence[dict], quotes, *, one_sided: str,
                    window_end: datetime = WINDOW_END_UTC) -> tuple[list[dict], dict]:
    rows, skipped = [], defaultdict(int)
    end_ms = int(window_end.timestamp() * 1000)
    for opp in opportunities:
        if int(opp.get("timestamp_ms") or 0) >= end_ms:
            skipped["after_window_end"] += 1
            continue
        snapshot = opp.get("semantic_snapshot")
        if not isinstance(snapshot, dict):
            skipped["no_snapshot"] += 1
            continue
        risk = (snapshot.get("risk_channel") or {}).get("penalty")
        direction = (snapshot.get("direction_channel") or {}).get("penalty")
        if risk is None or direction is None:
            skipped["channels_unavailable"] += 1
            continue
        outcome = next_poll_outcome(opp, quotes, one_sided=one_sided)
        if outcome is None:
            skipped["not_evaluable"] += 1
            continue
        day = datetime.fromtimestamp(int(opp["timestamp_ms"]) / 1000, tz=UTC).date().isoformat()
        rows.append({"day": day, "confidence_score": float(opp["confidence_score"]),
                     "risk_penalty": float(risk), "direction_penalty": float(direction), **outcome})
    return rows, dict(skipped)


def main() -> None:
    parser = argparse.ArgumentParser(description="H6b: prospective ranking evaluation (exploratory).")
    parser.add_argument("--recordings", type=Path, default=default_recordings())
    parser.add_argument("--predictions", type=Path, default=None)
    args = parser.parse_args()
    try:
        require_resolved(H6B_UNRESOLVED)
    except ProtocolUnresolved as exc:
        raise SystemExit(str(exc)) from exc
    if datetime.now(UTC) < WINDOW_END_UTC:
        raise SystemExit(f"H6b window is still open until {WINDOW_END_UTC.isoformat()}; "
                         "the recorder data is not examined before then (RESEARCH_TRACKER §6).")
    day_dirs = sorted(d for d in args.recordings.iterdir() if d.is_dir())
    quotes = load_quotes(day_dirs)
    opportunities = load_opportunities(day_dirs)
    rows, skipped = evaluation_rows(opportunities, quotes, one_sided=str(H6B_UNRESOLVED["one_sided_next_quote"]))
    report = {"settings": dict(H6B_UNRESOLVED), "window_end_utc": WINDOW_END_UTC.isoformat(),
              "opportunities": len(opportunities), "skipped": skipped,
              "framing": "exploratory, descriptive; not a decision test",
              "generated_at": datetime.now(UTC).isoformat()}
    if rows:
        scores = model_scores(rows, str(H6B_UNRESOLVED["model_nesting"]))
        report["ranking"] = day_bootstrap(rows, scores)
        edges = [r["edge_pips"] for r in rows if r["edge_pips"] is not None]
        report["realised_edge_pips"] = {"mean": float(np.mean(edges)) if edges else None,
                                        "median": float(np.median(edges)) if edges else None, "n": len(edges)}
    if args.predictions and args.predictions.exists():
        with args.predictions.open(encoding="utf-8") as handle:
            report["prediction_log_rows"] = sum(1 for _ in handle)
    RESULTS.write_text(json.dumps(report, indent=2, default=float), encoding="utf-8")
    print(json.dumps(report, indent=2, default=float))


if __name__ == "__main__":
    main()
