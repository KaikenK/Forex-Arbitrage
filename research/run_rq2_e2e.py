"""RQ2-E2E (secondary): real release headlines → release extractor → per-pair direction.

Pre-registered in RESEARCH_TRACKER §2 (RQ2-E2E, 2026-11-02) with clarification C30 (§6):
  candidates  frozen GDELT slice headlines (hash-verified freeze) tagged to a currency and a
              category (`data/tagging`) that match a high-impact exact-time calendar release of the
              same currency and category whose time is within RELEASE_WINDOW before the headline
  extraction  the live extractor (`services/semantic/src/market/release_extractor.py`) run on the
              headline against the calendar rows of that hour (forecast strings rebuilt from the
              store with the indicator's unit). Success = it returns a match; correct = the matched
              indicator is one of the candidate releases and the extracted actual equals the
              calendar actual within tolerance (C30)
  direction   from the article time t, r(t, t + 60 min) on each own pair (HistData M1) for
              (i) the structured pipeline (theory sign of the extracted z, as live),
              (ii) FinBERT polarity of the headline oriented to the currency (frozen scores),
              (iii) the V1 text model (`ArtifactReactionScorer`, pooled_multipair artifact);
              plus release-time r(release, +15 min) for the structured pipeline
  clustering  release moment; bootstrap CIs

Before the freeze it refuses to compute any accuracy; `--dry-run` prints candidate counts only.

Run: python -m research.run_rq2_e2e [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from research.data.cache_paths import histdata_cache_dir
from research.data.histdata import load_windowed_series
from research.data.tagging import tag_categories, tag_currencies
from research.eventstudy.sign_conventions import news_sign
from research.plan_intraday import CALENDAR_STORE, load_causal_sample
from research.reaction import metrics
from research.reaction.panel import STUDY_PAIRS, pair_side, signed_log_returns

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
OUTPUT = ARTIFACTS / "rq2_e2e.json"
# The service code lives in the same repo on the semantic-microservice branch; the original
# checkout (no services/) uses the sibling worktree.
_REPO = Path(__file__).resolve().parents[1]
SERVICE_SRC = next((p for p in (_REPO / "services" / "semantic" / "src",
                                _REPO.parent / "Forex-Arbitrage-semantic" / "services" / "semantic" / "src")
                    if p.exists()), _REPO / "services" / "semantic" / "src")
V1_ARTIFACT = SERVICE_SRC.parent / "artifacts" / "pooled_multipair" / "reaction_model_artifact.json"
RELEASE_WINDOW = timedelta(minutes=60)
ARTICLE_HORIZON_MIN = 60
RELEASE_HORIZON_MIN = 15
TOLERANCE = {"%": 0.05, "count": 500.0, "": 0.05}            # C30: half of the usual published step
STUDY_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CHF"}

_PERCENT_NAMES = ("m/m", "y/y", "q/q", "3m/y", "rate", "cpi", "ppi", "pce")
_COUNT_NAMES = ("change", "claims", "openings", "permits", "starts", "payrolls")


def _service_modules():
    if str(SERVICE_SRC) not in sys.path:
        sys.path.insert(0, str(SERVICE_SRC))
    from market import release_extractor
    return release_extractor


# ---------------------------------------------------------------- calendar rows as the live feed shows them

def unit_for(event_name: str) -> str:
    name = event_name.casefold()
    if any(key in name for key in _COUNT_NAMES):
        return "count"
    if any(key in name for key in _PERCENT_NAMES):
        return "%"
    return ""


def forecast_string(event_name: str, value: float) -> str:
    """ForexFactory-style forecast string for a stored numeric forecast."""
    unit = unit_for(event_name)
    if unit == "count":
        return f"{value / 1e3:g}K"
    if unit == "%":
        return f"{value:g}%"
    return f"{value:g}"


def calendar_releases(frame: pd.DataFrame) -> pd.DataFrame:
    """High-impact exact-time releases with forecast and actual, study currencies only."""
    out = frame.assign(currency=frame["currencies"].astype(str).str.split(";").str[0],
                       category=frame["categories"].astype(str).str.split(";").str[0])
    out = out[out["currency"].isin(STUDY_CURRENCIES) & out["forecast"].notna() & out["actual"].notna()]
    out = out.assign(timestamp=pd.to_datetime(out["timestamp"], utc=True))
    return out[["event_id", "timestamp", "currency", "category", "country", "event_name", "forecast", "actual",
                "standardized_surprise"]].reset_index(drop=True)


# ---------------------------------------------------------------- candidates

def candidates(headlines: pd.DataFrame, releases: pd.DataFrame) -> pd.DataFrame:
    """One row per (headline, currency) with the releases it can report (same currency and
    category, released within RELEASE_WINDOW before the headline)."""
    rows = []
    by_currency = {c: g.sort_values("timestamp") for c, g in releases.groupby("currency")}
    for h in headlines.itertuples(index=False):
        ts = pd.Timestamp(h.timestamp).tz_convert(UTC)
        currencies = set(tag_currencies(h.headline)) & STUDY_CURRENCIES
        categories = set(tag_categories(h.headline))
        for currency in sorted(currencies):
            group = by_currency.get(currency)
            if group is None:
                continue
            near = group[(group["timestamp"] <= ts) & (group["timestamp"] >= ts - RELEASE_WINDOW)
                         & group["category"].isin(categories)]
            if near.empty:
                continue
            rows.append({"event_id": h.event_id, "headline": h.headline, "timestamp": ts, "currency": currency,
                         "release_ids": list(near["event_id"]), "release_events": list(near["event_name"]),
                         "moment": near["timestamp"].min().isoformat()})
    return pd.DataFrame(rows)


def calendar_rows_near(releases: pd.DataFrame, ts: pd.Timestamp, extractor) -> list:
    window = releases[(releases["timestamp"] <= ts + timedelta(minutes=5))
                      & (releases["timestamp"] >= ts - extractor.RELEASE_MATCH_WINDOW)]
    return [extractor.CalendarRow(currency=r.currency, event=r.event_name, utc=r.timestamp.to_pydatetime(),
                                  forecast=forecast_string(r.event_name, r.forecast), impact="high")
            for r in window.itertuples(index=False)]


def extract(cands: pd.DataFrame, releases: pd.DataFrame, sigma: dict, extractor) -> pd.DataFrame:
    """Extractor outcome per candidate: matched event, extracted actual/z, correctness."""
    out = []
    actual_by_key = {(r.event_name, r.timestamp): r.actual for r in releases.itertuples(index=False)}
    for c in cands.itertuples(index=False):
        rows = calendar_rows_near(releases, c.timestamp, extractor)
        match = extractor.match_release(c.headline, c.timestamp.to_pydatetime(), rows, sigma) if rows else None
        rec = {"matched": match is not None, "matched_event": None, "extracted_actual": None, "z": None,
               "release_utc": None, "correct_release": False, "correct_actual": False}
        if match is not None:
            rec.update(matched_event=match.event, extracted_actual=match.actual, z=match.z,
                       release_utc=pd.Timestamp(match.release_utc))
            rec["correct_release"] = match.event in c.release_events
            truth = actual_by_key.get((match.event, pd.Timestamp(match.release_utc)))
            rec["correct_actual"] = bool(truth is not None and abs(match.actual - truth) <= TOLERANCE[match.unit])
        out.append(rec)
    return pd.concat([cands.reset_index(drop=True), pd.DataFrame(out)], axis=1)


# ---------------------------------------------------------------- directions

def theory_direction(currency: str, event: str | None, z: float | None, pair: str) -> float:
    side = pair_side(pair, currency)
    if side is None or event is None or z is None or z == 0:
        return np.nan
    country = {"USD": "United States", "EUR": "Euro Area", "GBP": "United Kingdom", "JPY": "Japan",
               "CHF": "Switzerland"}[currency]
    sign = news_sign(country, event)
    return np.nan if sign is None else float(np.sign(z) * sign * side)


def v1_text_model_directions(rows: pd.DataFrame, artifact: Path = V1_ARTIFACT) -> dict[tuple[str, str], float]:
    """(event_id, pair) → direction of the V1 text model (the deployed ArtifactReactionScorer)."""
    _service_modules()
    from market.artifact_reaction_scorer import ArtifactReactionScorer
    from market.schemas import AnalysisContext, ArticleInput
    from news.models import SentimentSource, SourceType, UnifiedNewsEvent

    scorer = ArtifactReactionScorer.from_path(artifact_path=artifact)
    out: dict[tuple[str, str], float] = {}

    async def run() -> None:
        for (event_id, headline, ts), group in rows.groupby(["event_id", "headline", "timestamp"]):
            pairs = sorted(set(group["pair"]))
            event = UnifiedNewsEvent(event_id=str(event_id), schema_version=1, source=SourceType.RSS, headline=headline,
                                     body=None, published_at=ts.to_pydatetime(), symbols=pairs, raw_sentiment=0.0,
                                     sentiment_source=SentimentSource.NONE, ingested_at=ts.to_pydatetime())
            context = AnalysisContext(article=ArticleInput(article_text=headline, headline=headline,
                                                           published_at=ts.to_pydatetime()), event=event)
            for signal in await scorer.score(context):
                out[(str(event_id), signal.symbol)] = float(np.sign(signal.direction)) if signal.direction else np.nan

    asyncio.run(run())
    return out


def pair_rows(extracted: pd.DataFrame) -> pd.DataFrame:
    """Expand each candidate to its own pairs (pairs containing the currency)."""
    rows = []
    for r in extracted.itertuples(index=False):
        for pair in STUDY_PAIRS:
            if pair_side(pair, r.currency) is not None:
                rows.append({**r._asdict(), "pair": pair})
    return pd.DataFrame(rows)


def attach_returns(rows: pd.DataFrame, bars: Callable[[str, list[datetime]], tuple[np.ndarray, np.ndarray]]) -> pd.DataFrame:
    rows = rows.copy()
    rows["r_article"] = np.nan
    rows["r_release"] = np.nan
    for pair, idx in rows.groupby("pair").groups.items():
        sub = rows.loc[idx]
        t = np.array([int(x.timestamp()) for x in sub["timestamp"]], dtype=np.int64)
        rel = np.array([int(x.timestamp()) if isinstance(x, pd.Timestamp) else -1 for x in sub["release_utc"]],
                       dtype=np.int64)
        hours = sorted({datetime.fromtimestamp(h * 3600, UTC) for s in np.concatenate([t, rel[rel > 0]])
                        for h in range(int(s // 3600) - 1, int((s + 2 * 3600) // 3600) + 1)})
        bar_times, prices = bars(pair, hours)
        rows.loc[idx, "r_article"] = signed_log_returns(bar_times, prices, t, t + ARTICLE_HORIZON_MIN * 60)
        r_rel = signed_log_returns(bar_times, prices, rel, rel + RELEASE_HORIZON_MIN * 60)
        rows.loc[idx, "r_release"] = np.where(rel > 0, r_rel, np.nan)
    return rows


def _histdata_bars(pair: str, hours: list[datetime]) -> tuple[np.ndarray, np.ndarray]:
    series, _ = load_windowed_series(pair, hours, histdata_cache_dir())
    return (np.array([int(t.timestamp()) for t in series.times], dtype=np.int64),
            np.asarray(series.prices, dtype=float))


def direction_summary(rows: pd.DataFrame, column: str, target: str) -> dict:
    data = rows[np.isfinite(rows[column].astype(float)) & np.isfinite(rows[target].astype(float))
                & (rows[target] != 0)].reset_index(drop=True)
    pred, real = data[column].to_numpy(dtype=float), data[target].to_numpy(dtype=float)
    clusters = data["moment"].to_numpy()
    return {"n": int(len(data)), "moments": int(data["moment"].nunique()) if len(data) else 0,
            "accuracy": metrics.accuracy(pred, real),
            "ci95": metrics.cluster_bootstrap_ci(clusters, lambda i: metrics.accuracy(pred[i], real[i])) if len(data) > 1 else None}


def evaluate(rows: pd.DataFrame) -> dict:
    out = {"article_time_r60": {}, "release_time_r15": {}}
    for name in ("structured", "finbert", "v1_text_model"):
        out["article_time_r60"][name] = direction_summary(rows, f"dir_{name}", "r_article")
    common = rows[[f"dir_{n}" for n in ("structured", "finbert", "v1_text_model")]].notna().all(axis=1)
    out["article_time_r60_common_rows"] = {name: direction_summary(rows[common], f"dir_{name}", "r_article")
                                           for name in ("structured", "finbert", "v1_text_model")}
    out["release_time_r15"]["structured"] = direction_summary(rows, "dir_structured", "r_release")
    return out


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="candidate counts only; no accuracy")
    args = parser.parse_args()
    extractor = _service_modules()
    releases = calendar_releases(load_causal_sample(CALENDAR_STORE))
    sigma = {(r.currency, r.event_name): abs(r.surprise / r.standardized_surprise)
             for r in pd.read_parquet(CALENDAR_STORE).assign(currency=lambda f: f["currencies"].astype(str).str.split(";").str[0])
             .dropna(subset=["surprise", "standardized_surprise"]).query("standardized_surprise != 0").itertuples(index=False)}

    from research.freeze_gdelt_sample import FREEZE_PATH, SHARD_DIR, load_freeze, load_frozen_events
    if args.dry_run:
        files = sorted(SHARD_DIR.glob("*.parquet"))
        headlines = pd.concat([pd.read_parquet(f, columns=["event_id", "timestamp", "headline"]) for f in files]) if files else pd.DataFrame()
        cands = candidates(headlines.drop_duplicates("event_id"), releases) if len(headlines) else pd.DataFrame()
        print(json.dumps({"slice_shards": len(files), "headlines": int(len(headlines)), "candidates": int(len(cands))}))
        return 0
    freeze = load_freeze(FREEZE_PATH)                                    # refuses before the freeze
    events = load_frozen_events(freeze, SHARD_DIR)
    from research.score_archive import load_scores, require_frozen_scores
    require_frozen_scores("finbert", freeze["sha256"])
    finbert = load_scores("finbert")[["event_id", "polarity"]]

    cands = candidates(events[["event_id", "timestamp", "headline"]].drop_duplicates("event_id"), releases)
    extracted = extract(cands, releases, sigma, extractor)
    rows = attach_returns(pair_rows(extracted), _histdata_bars)
    rows["dir_structured"] = [theory_direction(r.currency, r.matched_event, r.z, r.pair) for r in rows.itertuples(index=False)]
    rows = rows.merge(finbert, on="event_id", how="left")
    rows["dir_finbert"] = [float(np.sign(p) * pair_side(pair, c)) if pd.notna(p) and p != 0 else np.nan
                           for p, pair, c in zip(rows["polarity"], rows["pair"], rows["currency"])]
    v1 = v1_text_model_directions(rows)
    rows["dir_v1_text_model"] = [v1.get((str(e), p), np.nan) for e, p in zip(rows["event_id"], rows["pair"])]

    result = {
        "pre_registration": "RESEARCH_TRACKER §2 RQ2-E2E (2026-11-02) + C30",
        "freeze_sha256": freeze["sha256"], "computed_at": datetime.now(UTC).isoformat(),
        "candidates": int(len(cands)), "candidate_moments": int(cands["moment"].nunique()) if len(cands) else 0,
        "extraction": {"matched": float(extracted["matched"].mean()) if len(extracted) else None,
                       "correct_release": float(extracted["correct_release"].mean()) if len(extracted) else None,
                       "correct_actual": float(extracted["correct_actual"].mean()) if len(extracted) else None,
                       "correct_actual_given_matched": float(extracted.loc[extracted["matched"], "correct_actual"].mean())
                       if extracted["matched"].any() else None},
        "direction": evaluate(rows),
    }
    OUTPUT.write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    rows.drop(columns=["release_ids", "release_events"]).to_parquet(ARTIFACTS / "rq2_e2e_rows.parquet", index=False)
    print(json.dumps({k: result[k] for k in ("candidates", "extraction")}, default=float))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
