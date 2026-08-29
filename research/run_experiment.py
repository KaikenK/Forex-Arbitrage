"""
Arbex — Reproducible research harness (synthetic USD/INR mode)

Runs the full documented detection pipeline offline against the six
session-aware synthetic USD/INR feeds and writes a reproducible results
bundle to ``research/results/<run_id>/``.

Pipeline exercised (real production classes):

    SessionAwareSyntheticSource x6
        -> TickNormalizer
        -> time-windowing (AlignedTickWindow)
        -> ArbitrageEngine.detect
        -> OpportunityTracker  (persistence class)
        -> SimulatedExecutionFilter  (feasibility verdict)
        -> OpportunityRanker  (composite score / leaderboard)
        -> MetricsCollector + raw opportunity log

Determinism: a fake monotonic clock replaces ``time.time`` and the live
USD/INR rate fetch is disabled, so a given (seed, step, window, duration)
produces a statistically identical run on any machine.

Usage:
    python -m research.run_experiment --minutes 30 --seed 42
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time as _time_mod

# The synthetic sources derive per-source seeds via ``hash(source_id)``, which
# Python randomises per process unless PYTHONHASHSEED is fixed. Re-exec once with
# it pinned so runs are bit-for-bit reproducible across machines.
if os.environ.get("PYTHONHASHSEED") != "0":
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, "-m", "research.run_experiment", *sys.argv[1:]])
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median


# --------------------------------------------------------------------------
# 1. Determinism shims  (must run before importing backend modules)
# --------------------------------------------------------------------------

class _FakeClock:
    """Controllable stand-in for wall-clock time."""

    __slots__ = ("t",)

    def __init__(self, start: float) -> None:
        self.t = start

    def time(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


_EPOCH_START = 1_700_000_000.0  # fixed reference epoch
_CLOCK = _FakeClock(_EPOCH_START)
_REAL_TIME = _time_mod.time
_time_mod.time = _CLOCK.time  # type: ignore[assignment]

# Disable the live-rate HTTP call in SharedReferencePrice so the base price
# is a fixed, documented constant (USDINR_BASE_PRICE).
import urllib.request as _urllib_request  # noqa: E402


def _blocked_urlopen(*_args, **_kwargs):  # noqa: ANN001
    raise OSError("network disabled for reproducible run")


_urllib_request.urlopen = _blocked_urlopen  # type: ignore[assignment]


# --------------------------------------------------------------------------
# 2. Backend imports
# --------------------------------------------------------------------------

from backend.config import (  # noqa: E402
    ARBITRAGE_RESEARCH_CONFIG,
    RESEARCH_SYMBOL,
    SYNTHETIC_GENERATION_CONFIG,
    USDINR_BASE_PRICE,
)
from backend.core.analytics.metrics_collector import MetricsCollector  # noqa: E402
from backend.core.arbitrage.arbitrage_engine import (  # noqa: E402
    ArbitrageConfig,
    ArbitrageEngine,
)
from backend.core.arbitrage.opportunity_ranker import (  # noqa: E402
    OpportunityRanker,
    RankingConfig,
)
from backend.core.arbitrage.opportunity_tracker import (  # noqa: E402
    OpportunityTracker,
    TrackerConfig,
)
from backend.core.arbitrage.tick_aligner import AlignedTickWindow  # noqa: E402
from backend.core.data_sources.session_synthetic_source import (  # noqa: E402
    create_synthetic_sources,
    reset_synthetic_state,
)
from backend.core.execution.execution_filter import (  # noqa: E402
    ExecutionFilterConfig,
    SimulatedExecutionFilter,
)
from backend.core.interfaces.normalized_tick import TickNormalizer  # noqa: E402

PIP = 0.01  # USD/INR pip value
ROOT = Path(__file__).resolve().parent
RESULTS_DIR = ROOT / "results"


# --------------------------------------------------------------------------
# 3. Harness
# --------------------------------------------------------------------------

@dataclass
class RunConfig:
    minutes: float = 30.0
    seed: int = 42
    step_ms: int = 20
    window_ms: int = ARBITRAGE_RESEARCH_CONFIG.alignment_window_ms
    tracker_tick_ms: int = 100
    grace_ms: int = 120  # how long past window_end before a window is scored


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def run(cfg: RunConfig) -> dict:
    import random as _random

    _CLOCK.t = _EPOCH_START
    # ArbitrageEngine uses the global `random` module for a rare bonus path;
    # seed it so the run is fully reproducible.
    _random.seed(cfg.seed)
    reset_synthetic_state(seed=cfg.seed)

    sources = create_synthetic_sources()
    for s in sources:
        s.connect()
    session_of = {s.source_id: s.session.value for s in sources}

    normalizer = TickNormalizer(
        {s.source_id: s.config.latency_estimate_ms for s in sources}
    )

    engine = ArbitrageEngine(
        ArbitrageConfig(
            min_profit_pips=ARBITRAGE_RESEARCH_CONFIG.min_profit_pips,
            min_confidence=ARBITRAGE_RESEARCH_CONFIG.min_confidence,
            enable_cross_source=True,
            enable_triangular=False,
            enable_session_analysis=True,
        )
    )
    tracker = OpportunityTracker(TrackerConfig())
    episode_final_class: list[str] = []

    def _on_tracker_event(tracked, event):  # noqa: ANN001
        if event in ("ended", "archived"):
            episode_final_class.append(tracked.persistence_class.value)

    tracker.on_state_change(_on_tracker_event)
    exec_filter = SimulatedExecutionFilter(ExecutionFilterConfig())
    for s in sources:
        exec_filter.set_source_latency(s.source_id, s.config.latency_estimate_ms)
    ranker = OpportunityRanker(RankingConfig(min_composite_score=20.0, max_results=100))
    for s in sources:
        ranker.set_source_reliability(s.source_id, s.config.reliability_score)

    metrics = MetricsCollector()

    windows: dict[int, dict[str, list]] = {}
    opp_rows: list[dict] = []
    best_by_key: dict[str, dict] = {}  # composite leaderboard snapshot
    tick_count = 0

    total_steps = int(cfg.minutes * 60 * 1000 // cfg.step_ms)
    last_tracker_tick = 0

    def score_window(win_key: int) -> None:
        by_source = windows.pop(win_key)
        norm_by_source = {sid: ticks for sid, ticks in by_source.items() if ticks}
        if len(norm_by_source) < 2:
            return
        start = win_key * cfg.window_ms
        window = AlignedTickWindow(
            window_start_ms=start,
            window_end_ms=start + cfg.window_ms,
            symbol=RESEARCH_SYMBOL,
            ticks_by_source=norm_by_source,
        )
        opportunities = engine.detect(window)
        if not opportunities:
            return

        persistence_data: dict[str, dict] = {}
        execution_assessments: dict[str, dict] = {}
        tracked_by_opp: dict[str, object] = {}
        assess_by_opp: dict[str, object] = {}
        for opp in opportunities:
            tracked = tracker.update(opp)
            assess = exec_filter.assess(tracked)
            tracked_by_opp[opp.id] = tracked
            assess_by_opp[opp.id] = assess
            persistence_data[tracked.key] = {
                "persistence_class": tracked.persistence_class.value,
                "stability_score": tracked.stability_score,
                "detection_count": tracked.detection_count,
                "first_seen_ts": tracked.first_seen_ts,
            }
            execution_assessments[tracked.key] = {
                "verdict": assess.execution_verdict.value,
                "feasibility_score": assess.execution_feasibility_score,
                "verdict_reasons": assess.verdict_reasons,
            }

        ranked = {
            r.opportunity.id: r
            for r in ranker.rank_with_context(
                opportunities, persistence_data, execution_assessments
            )
        }
        for opp in opportunities:
            tracked = tracked_by_opp[opp.id]
            assess = assess_by_opp[opp.id]
            sess = session_of.get(opp.sell_source) or session_of.get(opp.buy_source) or opp.session
            row = {
                "sim_ts_ms": int(_CLOCK.t * 1000),
                "type": opp.type.value,
                "buy_source": opp.buy_source,
                "sell_source": opp.sell_source,
                "buy_session": session_of.get(opp.buy_source, "?"),
                "sell_session": session_of.get(opp.sell_source, "?"),
                "session": sess,
                "profit_pips": round(opp.estimated_profit_pips, 3),
                "profit_pct": round(opp.estimated_profit_pct, 6),
                "confidence": round(opp.confidence_score, 3),
                "latency_risk_ms": round(opp.latency_risk_ms, 1),
                "persistence_class": tracked.persistence_class.value,
                "stability": round(tracked.stability_score, 3),
                "detection_count": tracked.detection_count,
                "cumulative_duration_ms": tracked.cumulative_duration_ms,
                "verdict": assess.execution_verdict.value,
                "feasibility_score": round(assess.execution_feasibility_score, 1),
                "expected_slippage_pips": round(assess.expected_slippage_pips, 3),
                "net_profit_pips": round(assess.net_expected_profit_pips, 3),
                "composite_score": round(ranked[opp.id].composite_score, 2) if opp.id in ranked else None,
            }
            opp_rows.append(row)
            metrics.record_opportunity(
                session=sess,
                duration_ms=max(tracked.cumulative_duration_ms, cfg.window_ms),
                profit_pips=opp.estimated_profit_pips,
                persistence_class=tracked.persistence_class.value,
            )
            key = tracked.key
            prev = best_by_key.get(key)
            if prev is None or (row["composite_score"] or 0) > (prev["composite_score"] or 0):
                best_by_key[key] = row

    for step in range(total_steps):
        _CLOCK.advance(cfg.step_ms / 1000.0)
        now_ms = int(_CLOCK.t * 1000)

        for s in sources:
            raw = s.get_tick(RESEARCH_SYMBOL)
            if raw is None:
                continue
            norm = normalizer.normalize(raw)
            if norm is None:
                continue
            tick_count += 1
            metrics.record_tick(
                session=session_of[s.source_id],
                spread_pips=(raw.ask - raw.bid) / PIP,
            )
            wkey = norm.timestamp_ms // cfg.window_ms
            windows.setdefault(wkey, {}).setdefault(s.source_id, []).append(norm)

        # score windows that are safely in the past
        cutoff = (now_ms - cfg.grace_ms) // cfg.window_ms
        for wkey in sorted(k for k in windows if k < cutoff):
            score_window(wkey)

        if now_ms - last_tracker_tick >= cfg.tracker_tick_ms:
            tracker.tick()
            last_tracker_tick = now_ms

    # drain
    for wkey in sorted(windows):
        score_window(wkey)
    for _ in range(20):
        _CLOCK.advance(cfg.tracker_tick_ms / 1000.0)
        tracker.tick()
    for tracked in tracker.get_active():
        episode_final_class.append(tracked.persistence_class.value)

    return _summarise(
        cfg, sources, tick_count, opp_rows, best_by_key,
        episode_final_class, tracker, exec_filter, metrics,
    )


def _summarise(cfg, sources, tick_count, opp_rows, best_by_key,
               episode_final_class, tracker, exec_filter, metrics) -> dict:
    tstats = tracker.get_stats()
    fstats = exec_filter.get_stats()
    msum = metrics.get_summary()

    profits = [r["profit_pips"] for r in opp_rows]
    nets = [r["net_profit_pips"] for r in opp_rows]
    verdicts = {"viable": 0, "risky": 0, "unlikely": 0}
    persistence = {"ephemeral": 0, "flickering": 0, "persistent": 0}
    by_session: dict[str, dict] = {}
    for r in opp_rows:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1
        persistence[r["persistence_class"]] = persistence.get(r["persistence_class"], 0) + 1
        b = by_session.setdefault(r["session"], {"detections": 0, "profit_sum": 0.0})
        b["detections"] += 1
        b["profit_sum"] += r["profit_pips"]

    # per distinct opportunity episode (final persistence class at end of life)
    opp_persistence = {"ephemeral": 0, "flickering": 0, "persistent": 0}
    for c in episode_final_class:
        opp_persistence[c] = opp_persistence.get(c, 0) + 1

    sim_seconds = cfg.minutes * 60
    summary = {
        "run": {
            "seed": cfg.seed,
            "sim_minutes": cfg.minutes,
            "step_ms": cfg.step_ms,
            "window_ms": cfg.window_ms,
            "sources": len(sources),
            "base_price": USDINR_BASE_PRICE,
            "arbitrage_injection_rate": SYNTHETIC_GENERATION_CONFIG.arbitrage_injection_rate,
            "min_profit_pips": ARBITRAGE_RESEARCH_CONFIG.min_profit_pips,
            "min_confidence": ARBITRAGE_RESEARCH_CONFIG.min_confidence,
        },
        "throughput": {
            "ticks_processed": tick_count,
            "ticks_per_sim_second": round(tick_count / sim_seconds, 1),
            "ticks_per_sim_minute": round(tick_count / cfg.minutes, 0),
            "windows_scored": msum.get("total_opportunities_detected", 0),
        },
        "detection": {
            "raw_detections": len(opp_rows),
            "opportunity_episodes": sum(opp_persistence.values()),
            "detections_per_sim_minute": round(len(opp_rows) / cfg.minutes, 2),
            "episodes_per_sim_minute": round(sum(opp_persistence.values()) / cfg.minutes, 2),
        },
        "persistence_distribution_per_detection": persistence,
        "persistence_distribution_per_episode": opp_persistence,
        "execution_verdicts_per_detection": verdicts,
        "actionability_funnel": {
            "opportunity_episodes": sum(opp_persistence.values()),
            "flickering_or_persistent_episodes": opp_persistence["flickering"] + opp_persistence["persistent"],
            "persistent_episodes": opp_persistence["persistent"],
            "detections_with_viable_verdict": verdicts.get("viable", 0),
        },
        "cost_filter_effect": {
            "profitable_on_paper": sum(1 for p in profits if p > 0),
            "still_viable_after_L2_and_latency": verdicts.get("viable", 0),
            "pct_filtered_out": round(
                100 * (1 - verdicts.get("viable", 0) / max(1, sum(1 for p in profits if p > 0))), 1
            ),
        },
        "profit_pips": {
            "mean_gross": round(mean(profits), 3) if profits else 0.0,
            "median_gross": round(median(profits), 3) if profits else 0.0,
            "max_gross": round(max(profits), 3) if profits else 0.0,
            "mean_net_after_costs": round(mean(nets), 3) if nets else 0.0,
        },
        "by_session": {
            k: {
                "detections": v["detections"],
                "avg_profit_pips": round(v["profit_sum"] / v["detections"], 3),
            }
            for k, v in sorted(by_session.items())
        },
        "session_spread_pips": {
            name: m["avg_spread_pips"]
            for name, m in msum.get("session_metrics", {}).items()
        },
        "tracker_stats": tstats,
        "execution_filter_stats": fstats,
        "leaderboard_top10": sorted(
            best_by_key.values(),
            key=lambda r: (r["composite_score"] or 0),
            reverse=True,
        )[:10],
    }
    return {"summary": summary, "opportunities": opp_rows}


def main() -> None:
    ap = argparse.ArgumentParser(description="Arbex reproducible research harness")
    ap.add_argument("--minutes", type=float, default=30.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--step-ms", type=int, default=20)
    ap.add_argument("--window-ms", type=int, default=None,
                    help="alignment window (default: config value)")
    ap.add_argument("--run-id", type=str, default=None)
    args = ap.parse_args()

    cfg = RunConfig(minutes=args.minutes, seed=args.seed, step_ms=args.step_ms)
    if args.window_ms is not None:
        cfg.window_ms = args.window_ms
    wall_start = _REAL_TIME()
    out = run(cfg)
    wall_elapsed = _REAL_TIME() - wall_start

    run_id = args.run_id or f"seed{cfg.seed}_{int(cfg.minutes)}min"
    outdir = RESULTS_DIR / run_id
    outdir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "wall_seconds": round(wall_elapsed, 2),
        "config": vars(cfg),
        "python": _time_mod.__name__ and __import__("sys").version.split()[0],
        "determinism": "fake monotonic clock; live-rate fetch disabled; seeded RNG",
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (outdir / "summary.json").write_text(json.dumps(out["summary"], indent=2))

    # opportunities as CSV (pandas-free writer)
    rows = out["opportunities"]
    if rows:
        cols = list(rows[0].keys())
        lines = [",".join(cols)]
        for r in rows:
            lines.append(",".join("" if r[c] is None else str(r[c]) for c in cols))
        (outdir / "opportunities.csv").write_text("\n".join(lines))

    s = out["summary"]
    print(f"\n=== Arbex run '{run_id}'  (git {manifest['git_sha']}, {wall_elapsed:.1f}s wall) ===")
    print(f"  sim duration        : {cfg.minutes:.0f} min   seed {cfg.seed}   window {cfg.window_ms} ms")
    print(f"  ticks processed     : {s['throughput']['ticks_processed']:,}  "
          f"({s['throughput']['ticks_per_sim_minute']:,.0f}/sim-min)")
    print(f"  raw detections      : {s['detection']['raw_detections']:,}  "
          f"({s['detection']['detections_per_sim_minute']}/sim-min)")
    print(f"  opportunity episodes: {s['detection']['opportunity_episodes']:,}  "
          f"({s['detection']['episodes_per_sim_minute']}/sim-min)")
    print(f"  persistence (det.)  : {s['persistence_distribution_per_detection']}")
    print(f"  persistence (episode): {s['persistence_distribution_per_episode']}")
    print(f"  verdicts (det.)     : {s['execution_verdicts_per_detection']}")
    print(f"  actionability funnel: {s['actionability_funnel']}")
    print(f"  cost-filter effect  : {s['cost_filter_effect']}")
    print(f"  gross profit (pips) : mean {s['profit_pips']['mean_gross']}  "
          f"median {s['profit_pips']['median_gross']}  max {s['profit_pips']['max_gross']}")
    print(f"  by session          : {s['by_session']}")
    print(f"  wrote               : {outdir}")


if __name__ == "__main__":
    main()
