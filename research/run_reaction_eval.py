"""RQ1–RQ4: release → per-pair affected / direction / move / confidence, walk-forward.

Pre-registered in RESEARCH_TRACKER §2 (RQ1–RQ5, 2026-11-02) with clarifications C20–C25 (§6).
Unit: high-impact exact-time releases (USD, EUR, GBP, JPY, CHF) with an Option A z ≠ 0,
2018-01..2025-04, × the four majors; HistData M1; release-moment clustering.

Writes:
  artifacts/reaction_panel.parquet        release × pair returns and controls (cache)
  artifacts/reaction_predictions.parquet  out-of-sample predictions, every target
  artifacts/reaction_eval.json            RQ1–RQ4 tables
  artifacts/reaction_model.json           final model fitted on the full sample (live export)

Run: python -m research.run_reaction_eval [--rebuild-panel]
     python -m research.run_reaction_eval --arrival-offset 3   # arrival-latency re-test (§6): arrival
       targets only, written to *_arrival3.* files; the primary outputs and live export are untouched
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.cache_paths import histdata_cache_dir
from research.data.histdata import load_windowed_series
from research.eventstudy.sign_conventions import AMBIGUOUS, CURRENCY_FOR_COUNTRY, NEGATIVE_NEWS, news_sign
from research.eventstudy.stats import benjamini_hochberg
from research.plan_intraday import CALENDAR_STORE, load_causal_sample
from research.reaction import metrics
from research.reaction.model import LEVELS, _conf_features, fit_fold, fit_logistic, predict
from research.reaction.panel import (
    ARRIVAL_HORIZONS, ARRIVAL_OFFSET_MIN, PRIMARY_ARRIVAL_H, PRIMARY_RELEASE_H, RELEASE_HORIZONS,
    STUDY_PAIRS, hours_needed, panel_for_pair, releases_from_calendar,
)

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
PANEL = ARTIFACTS / "reaction_panel.parquet"
PREDICTIONS = ARTIFACTS / "reaction_predictions.parquet"
OUTPUT = ARTIFACTS / "reaction_eval.json"
MODEL_EXPORT = ARTIFACTS / "reaction_model.json"
MIN_TRAIN_MONTHS = 12
AFFECTED_MULTIPLE = 2.0

# (target column, control column, label). The first of each family is primary.
TARGETS = ([(f"r{h}", "v_release", f"release_{h}m") for h in RELEASE_HORIZONS]
           + [(f"a{h}", "v_arrival", f"arrival_{ARRIVAL_OFFSET_MIN}+{h}m") for h in ARRIVAL_HORIZONS])
PRIMARY = {f"r{PRIMARY_RELEASE_H}", f"a{PRIMARY_ARRIVAL_H}"}


# ---------------------------------------------------------------- panel

def build_panel(arrival_offset_min: int = ARRIVAL_OFFSET_MIN) -> pd.DataFrame:
    releases = releases_from_calendar(load_causal_sample(CALENDAR_STORE), news_sign)
    times = np.array([int(r.timestamp.timestamp()) for r in releases], dtype=np.int64)
    hours = hours_needed(times, arrival_offset_min)
    frames = []
    for pair in STUDY_PAIRS:
        series, missing = load_windowed_series(pair, hours, histdata_cache_dir())
        if missing:
            print(f"  WARNING {pair}: HistData years not cached {missing}")
        bar_times = np.array([int(t.timestamp()) for t in series.times], dtype=np.int64)
        prices = np.asarray(series.prices, dtype=float)
        frame = panel_for_pair(releases, pair, bar_times, prices, arrival_offset_min)
        print(f"  {pair}: {int(np.isfinite(frame[f'r{PRIMARY_RELEASE_H}']).sum())}/{len(frame)} releases with a "
              f"{PRIMARY_RELEASE_H}-min return")
        frames.append(frame)
    panel = pd.concat(frames, ignore_index=True)
    panel["side"] = panel["side"].astype("object").where(panel["side"].notna(), None)
    return panel


# ---------------------------------------------------------------- walk-forward

def walk_forward(panel: pd.DataFrame, target: str, control: str) -> pd.DataFrame:
    months = sorted(panel["month"].unique())
    outputs = []
    for month in months[MIN_TRAIN_MONTHS:]:
        train, test = panel[panel["month"] < month], panel[panel["month"] == month]
        if test.empty:
            continue
        model = fit_fold(train, target=target, control=control)
        pred = predict(model, test)
        # Baselines, fitted on the same training rows.
        own_train = train[train["side"].notna() & np.isfinite(train[target]) & (train[target] != 0)]
        majority = own_train.groupby("pair")[target].apply(lambda s: 1.0 if (s > 0).mean() >= 0.5 else -1.0)
        pred["majority_sign"] = test["pair"].map(majority).astype(float)
        pred["majority_sign"] = pred["majority_sign"].where(test["side"].notna(), np.nan)
        usable = train[np.isfinite(train[target])]
        pair_mean = usable.groupby("pair")[target].apply(lambda s: float(np.abs(s).mean()))
        cell_mean = usable.groupby(["currency", "category", "pair"])[target].apply(lambda s: float(np.abs(s).mean()))
        pred["base_pair_mean"] = test["pair"].map(pair_mean).astype(float)
        keys = list(zip(test["currency"], test["category"], test["pair"]))
        pred["base_cell_mean"] = [cell_mean.get(k, pair_mean.get(k[2], np.nan)) for k in keys]
        vfit = {}
        for pair, group in usable[np.isfinite(usable[control])].groupby("pair"):
            design = np.column_stack([np.ones(len(group)), group[control]])
            vfit[pair], *_ = np.linalg.lstsq(design, np.abs(group[target].to_numpy()), rcond=None)
        pred["base_v_fit"] = [max(0.0, float(vfit[p][0] + vfit[p][1] * v)) if p in vfit and np.isfinite(v) else np.nan
                              for p, v in zip(test["pair"], test[control])]
        pred["target"] = target
        pred["fold_month"] = month
        outputs.append(pd.concat([test, pred], axis=1))
    return pd.concat(outputs, ignore_index=True)


# ---------------------------------------------------------------- tables

def _ci(clusters, fn):
    ci = metrics.cluster_bootstrap_ci(clusters, fn)
    return None if ci is None else [round(ci[0], 4), round(ci[1], 4)]


def direction_table(rows: pd.DataFrame, target: str) -> dict:
    own = rows[rows["side"].notna() & np.isfinite(rows[target]) & (rows[target] != 0)].reset_index(drop=True)
    real = own[target].to_numpy()
    theory = np.sign(own["z"].to_numpy()) * own["news_sign"].astype(float).to_numpy() * own["side"].astype(float).to_numpy()
    preds = {"model": own["s_hat"].to_numpy(dtype=float), "theory_rule": theory,
             "majority": own["majority_sign"].to_numpy(dtype=float)}
    clusters = own["moment"].to_numpy()
    out = {"rows": int(len(own)), "moments": int(own["moment"].nunique()),
           "model_abstained": int(np.isnan(preds["model"]).sum())}
    for name, sign in preds.items():
        acc = metrics.accuracy(sign, real)
        out[name] = {"accuracy": acc, "n": int(np.sum(np.isfinite(sign) & (sign != 0))),
                     "ci95": _ci(clusters, lambda idx, s=sign: metrics.accuracy(s[idx], real[idx]))}
    hit = np.where(np.isfinite(preds["model"]), (np.sign(preds["model"]) == np.sign(real)).astype(float), np.nan)
    test = metrics.clustered_mean_diff(hit - 0.5, clusters)
    out["model"]["p_vs_coin"] = None if test is None else test["p"]
    score = preds["model"] * own["p_dir"].to_numpy(dtype=float)
    out["model"]["auc"] = metrics.auc(score, real)
    agree = np.isfinite(preds["model"]) & np.isfinite(theory)
    out["model_agrees_with_theory"] = float(np.mean(preds["model"][agree] == theory[agree])) if agree.any() else None
    absz = np.abs(own["z"].to_numpy())
    edges = np.quantile(absz, [1 / 3, 2 / 3])
    out["by_abs_z_tercile"] = []
    for k, (lo, hi) in enumerate([(0, edges[0]), (edges[0], edges[1]), (edges[1], np.inf)]):
        sel = (absz >= lo) & (absz < hi) if k < 2 else absz >= lo
        out["by_abs_z_tercile"].append({"abs_z_range": [round(float(lo), 3), None if not np.isfinite(hi) else round(float(hi), 3)],
                                        "n": int(sel.sum()), "accuracy": metrics.accuracy(preds["model"][sel], real[sel])})
    out["by_currency"] = {c: metrics.accuracy(preds["model"][(own["currency"] == c).to_numpy()],
                                              real[(own["currency"] == c).to_numpy()])
                          for c in sorted(own["currency"].unique())}
    return out


def magnitude_table(rows: pd.DataFrame, target: str, control: str) -> dict:
    own = rows[rows["side"].notna() & np.isfinite(rows[target]) & np.isfinite(rows[control])].reset_index(drop=True)
    real = np.abs(own[target].to_numpy())
    clusters = own["moment"].to_numpy()
    out = {"rows": int(len(own)), "moments": int(own["moment"].nunique()), "mean_abs_move_bps": float(real.mean())}
    model_err = np.abs(own["expected_move_bps"].to_numpy(dtype=float) - real)
    for name, col in [("model", "expected_move_bps"), ("pair_mean", "base_pair_mean"),
                      ("cell_mean", "base_cell_mean"), ("v_fit", "base_v_fit")]:
        pred = own[col].to_numpy(dtype=float)
        entry = {"mae_bps": metrics.mae(pred, real), "spearman": metrics.spearman(pred, real)}
        if name != "model":
            diff = metrics.clustered_mean_diff(model_err - np.abs(pred - real), clusters)
            entry["model_minus_baseline_abs_error"] = diff
        out[name] = entry
    return out


def confidence_table(rows: pd.DataFrame, target: str) -> dict:
    own = rows[rows["side"].notna() & np.isfinite(rows[target]) & (rows[target] != 0)
               & np.isfinite(rows["p_dir"].astype(float))].reset_index(drop=True)
    p = own["p_dir"].to_numpy(dtype=float)
    correct = (np.sign(own["s_hat"].to_numpy(dtype=float)) == np.sign(own[target].to_numpy())).astype(float)
    out = {"rows": int(len(own)), "ece": metrics.ece(p, correct), "brier": metrics.brier(p, correct),
           "brier_constant_rate": metrics.brier(np.full(len(correct), correct.mean()), correct) if len(correct) else None,
           "mean_p": float(p.mean()) if len(p) else None, "hit_rate": float(correct.mean()) if len(correct) else None,
           "reliability": metrics.reliability(p, correct)}
    real_abs = np.abs(rows[target].to_numpy(dtype=float))
    own_mask = rows["side"].notna().to_numpy()
    for level in LEVELS:
        tag = int(round(level * 100))
        if f"lo{tag}" in rows:
            out[f"coverage_{tag}"] = metrics.interval_coverage(rows[f"lo{tag}"].to_numpy(dtype=float)[own_mask],
                                                               rows[f"hi{tag}"].to_numpy(dtype=float)[own_mask],
                                                               real_abs[own_mask])
    return out


def affected_table(rows: pd.DataFrame) -> dict:
    data = rows[np.isfinite(rows["r15"]) & np.isfinite(rows["v_release"]) & (rows["v_release"] > 0)].reset_index(drop=True)
    label = np.abs(data["r15"].to_numpy()) > AFFECTED_MULTIPLE * data["v_release"].to_numpy()
    rule = data["side"].notna().to_numpy()
    tp, fp, fn = int(np.sum(rule & label)), int(np.sum(rule & ~label)), int(np.sum(~rule & label))
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    score = data["expected_move_bps"].to_numpy(dtype=float)
    out = {"rows": int(len(data)), "affected_rate": float(label.mean()),
           "rule": {"precision": precision, "recall": recall, "f1": f1},
           "auc_expected_move": metrics.auc(score, np.where(label, 1.0, -1.0)),
           "affected_rate_own": float(label[rule].mean()) if rule.any() else None,
           "affected_rate_cross": float(label[~rule].mean()) if (~rule).any() else None,
           "by_release_currency": {}}
    for currency in sorted(data["currency"].unique()):
        sel = (data["currency"] == currency).to_numpy()
        out["by_release_currency"][currency] = {
            pair: float(label[sel & (data["pair"] == pair).to_numpy()].mean())
            for pair in STUDY_PAIRS if np.any(sel & (data["pair"] == pair).to_numpy())}
    return out


def horizon_profile(rows_by_target: dict[str, pd.DataFrame]) -> dict:
    """Mean realised move in the predicted direction (bps), release- and arrival-time."""
    profile = {}
    for target, rows in rows_by_target.items():
        own = rows[rows["side"].notna() & np.isfinite(rows[target]) & np.isfinite(rows["s_hat"].astype(float))]
        directed = own[target].to_numpy() * own["s_hat"].to_numpy(dtype=float)
        fit = metrics.clustered_mean_diff(directed, own["moment"].to_numpy())
        profile[target] = None if fit is None else {k: fit[k] for k in ("mean", "se", "p", "n", "clusters")}
    return profile


# ---------------------------------------------------------------- live export

def _jsonable(d: dict) -> list:
    return [{"key": list(k) if isinstance(k, tuple) else k,
             "value": v.tolist() if isinstance(v, np.ndarray) else v} for k, v in d.items()]


def export_model(panel: pd.DataFrame, profile: dict) -> dict:
    """Fit on the full sample (the live model) and record σ per indicator for live surprises."""
    store = pd.read_parquet(CALENDAR_STORE)
    store = store[store["standardized_surprise"].notna() & (store["standardized_surprise"] != 0)]
    store = store.assign(currency=store["currencies"].astype(str).str.split(";").str[0])
    sigma = (store["surprise"] / store["standardized_surprise"]).abs()
    sigma_table = (store.assign(sigma=sigma).groupby(["currency", "event_name"])["sigma"].median()
                   .reset_index().to_dict("records"))
    targets = {}
    for target, control in [(f"r{PRIMARY_RELEASE_H}", "v_release"), (f"a{PRIMARY_ARRIVAL_H}", "v_arrival")]:
        model = fit_fold(panel, target=target, control=control)
        targets[target] = {
            "control": control,
            "beta_cell": _jsonable(model.beta_cell), "beta_pooled": _jsonable(model.beta_pooled),
            "move_cell": _jsonable(model.move_cell), "move_pooled": _jsonable(model.move_pooled),
            "move_pair_mean": model.move_pair_mean,
            "conf_category": _jsonable(model.conf_category),
            "conf_pooled": None if model.conf_pooled is None else model.conf_pooled.tolist(),
            "conformal_q": {str(k): v for k, v in model.conformal_q.items()},
            "split_move_cell": _jsonable(model.split_move_cell), "split_move_pooled": _jsonable(model.split_move_pooled),
            "split_pair_mean": model.split_pair_mean,
        }
    control_medians = {pair: {"v_release": float(g["v_release"].median()), "v_arrival": float(g["v_arrival"].median())}
                       for pair, g in panel.groupby("pair")}
    # Product calibration (engineering, not a paper result — RQ results row 2026-11-02): the live
    # direction is the theory rule, so its confidence maps |z| to the theory rule's hit rate.
    theory_conf = {}
    for target in (f"r{PRIMARY_RELEASE_H}", "r1"):
        own = panel[panel["side"].notna() & np.isfinite(panel[target]) & (panel[target] != 0)
                    & panel["news_sign"].notna()]
        sign = np.sign(own["z"]) * own["news_sign"].astype(float) * own["side"].astype(float)
        correct = (sign.to_numpy() == np.sign(own[target].to_numpy())).astype(float)
        w = fit_logistic(_conf_features(own["z"].to_numpy()), correct)
        theory_conf[target] = {"weights": None if w is None else w.tolist(), "rows": int(len(own)),
                               "hit_rate": float(correct.mean())}
    news_sign_map = {"negative": sorted([[CURRENCY_FOR_COUNTRY[c], e] for c, e in NEGATIVE_NEWS]),
                     "ambiguous": sorted([[CURRENCY_FOR_COUNTRY[c], e] for c, e in AMBIGUOUS])}
    return {"schema": "release_reaction_v1", "fitted_at": datetime.now(UTC).isoformat(),
            "sample": {"start": str(panel["month"].min()), "end": str(panel["month"].max()),
                       "releases": int(panel["release_id"].nunique()), "rows": int(len(panel))},
            "arrival_offset_min": ARRIVAL_OFFSET_MIN, "targets": targets, "horizon_profile": profile,
            "control_median_bps": control_medians, "sigma_by_indicator": sigma_table,
            "theory_rule_confidence": theory_conf, "news_sign": news_sign_map,
            "post_arrival_mean_abs_bps": targets[f"a{PRIMARY_ARRIVAL_H}"]["move_pair_mean"],
            "category_by_indicator": (panel[["currency", "indicator", "category"]].drop_duplicates()
                                      .rename(columns={"indicator": "event_name"}).to_dict("records"))}


def run_arrival_offset(offset: int, *, rebuild: bool = False) -> int:
    """Arrival targets only, at `offset` minutes after the release; separate output files."""
    panel_path = ARTIFACTS / f"reaction_panel_arrival{offset}.parquet"
    if panel_path.exists() and not rebuild:
        panel = pd.read_parquet(panel_path)
        panel["side"] = panel["side"].astype("object").where(panel["side"].notna(), None)
    else:
        print(f"Building panel with arrival offset {offset} min ...")
        panel = build_panel(offset)
        panel.to_parquet(panel_path, index=False)
    results: dict = {"pre_registration": "RESEARCH_TRACKER §6 ARRIVAL-LATENCY STUDY (2026-11-02)",
                     "arrival_offset_min": offset, "computed_at": datetime.now(UTC).isoformat(), "targets": {}}
    rows_by_target, preds = {}, []
    for h in ARRIVAL_HORIZONS:
        target, control, label = f"a{h}", "v_arrival", f"arrival_{offset}+{h}m"
        rows = walk_forward(panel, target, control)
        rows_by_target[target] = rows
        preds.append(rows.assign(target_label=label))
        results["targets"][label] = {
            "primary": h == PRIMARY_ARRIVAL_H, "target": target, "control": control,
            "test_months": int(rows["fold_month"].nunique()),
            "direction": direction_table(rows, target),
            "magnitude": magnitude_table(rows, target, control),
            "confidence": confidence_table(rows, target),
        }
        d = results["targets"][label]["direction"]["model"]
        print(f"  {label:>16}: direction acc {d['accuracy']}, AUC {d['auc']}")
    results["horizon_profile"] = horizon_profile(rows_by_target)
    pd.concat(preds, ignore_index=True).astype({"side": "float"}).to_parquet(
        ARTIFACTS / f"reaction_predictions_arrival{offset}.parquet", index=False)
    out = ARTIFACTS / f"reaction_eval_arrival{offset}.json"
    out.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    print(f"Wrote {out.name}")
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rebuild-panel", action="store_true")
    parser.add_argument("--export-only", action="store_true",
                        help="refit and write reaction_model.json from the cached panel and eval")
    parser.add_argument("--arrival-offset-from", type=Path, default=None,
                        help="read the offset from arrival_latency.json (observed_offset_min)")
    parser.add_argument("--arrival-offset", type=int, default=None,
                        help="arrival-latency re-test: assumed minutes from release to article (default: the "
                             f"pre-registered {ARRIVAL_OFFSET_MIN}, full run)")
    args = parser.parse_args()
    if args.arrival_offset_from is not None:
        offset = json.loads(args.arrival_offset_from.read_text(encoding="utf-8")).get("observed_offset_min")
        if offset is None:
            print("arrival latency study estimated no offset (< 10 releases); the 15-min assumption stands")
            return 0
        args.arrival_offset = int(offset)
    if args.arrival_offset is not None:
        return run_arrival_offset(args.arrival_offset, rebuild=args.rebuild_panel)
    if PANEL.exists() and not args.rebuild_panel:
        panel = pd.read_parquet(PANEL)
        panel["side"] = panel["side"].astype("object").where(panel["side"].notna(), None)
    else:
        print("Building release × pair panel from HistData ...")
        panel = build_panel()
        panel.to_parquet(PANEL, index=False)
    print(f"Panel: {panel['release_id'].nunique()} releases × {panel['pair'].nunique()} pairs = {len(panel)} rows")
    if args.export_only:
        profile = json.loads(OUTPUT.read_text(encoding="utf-8"))["horizon_profile"]
        MODEL_EXPORT.write_text(json.dumps(export_model(panel, profile), indent=2, default=float), encoding="utf-8")
        print(f"Wrote {MODEL_EXPORT.name}")
        return 0

    results: dict = {"pre_registration": "RESEARCH_TRACKER §2 RQ1–RQ5 (2026-11-02), C20–C25",
                     "computed_at": datetime.now(UTC).isoformat(), "targets": {}}
    rows_by_target, all_preds = {}, []
    for target, control, label in TARGETS:
        rows = walk_forward(panel, target, control)
        rows_by_target[target] = rows
        all_preds.append(rows.assign(target_label=label))
        results["targets"][label] = {
            "primary": target in PRIMARY, "target": target, "control": control,
            "test_months": int(rows["fold_month"].nunique()),
            "direction": direction_table(rows, target),
            "magnitude": magnitude_table(rows, target, control),
            "confidence": confidence_table(rows, target),
        }
        d = results["targets"][label]["direction"]["model"]
        print(f"  {label:>16}: direction acc {d['accuracy']}, AUC {d['auc']}")
    secondary = [(label, r["direction"]["model"]["p_vs_coin"]) for label, r in results["targets"].items()
                 if not r["primary"] and r["direction"]["model"]["p_vs_coin"] is not None]
    for (label, _), corr in zip(secondary, benjamini_hochberg([p for _, p in secondary], alpha=0.05)):
        results["targets"][label]["direction"]["model"]["q_bh_vs_coin"] = corr.q_value
    results["rq1_affected"] = affected_table(rows_by_target[f"r{PRIMARY_RELEASE_H}"])
    results["horizon_profile"] = horizon_profile(rows_by_target)

    pd.concat(all_preds, ignore_index=True).astype({"side": "float"}).to_parquet(PREDICTIONS, index=False)
    OUTPUT.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    MODEL_EXPORT.write_text(json.dumps(export_model(panel, results["horizon_profile"]), indent=2, default=float),
                            encoding="utf-8")
    print(f"Wrote {OUTPUT.name}, {PREDICTIONS.name}, {MODEL_EXPORT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
