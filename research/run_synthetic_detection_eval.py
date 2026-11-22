"""Ground-truth detection evaluation of the cross-source detector on the built-in simulator.

Run:  python -m research.run_synthetic_detection_eval [--seeds 5] [--duration-s 600] [--W 0 50 100 250]

Offline, seeded, no accounts, no network. It drives the EXISTING synthetic source
(backend/core/data_sources/session_synthetic_source.py), the existing TickNormalizer, TickAligner and
ArbitrageEngine, unchanged, and scores the engine against the generator's own injection flag
(`extra["arbitrage_injected"]`).

Ground truth (stated precisely)
    An *injection* is a tick the generator flagged `arbitrage_injected=True`: one source's mid price
    is offset by +/-(5 to 15) pips for that single tick (the offset is applied after the provider
    smoothing, so it does not persist into later ticks). Each injection has a symbol, a source id and
    the tick timestamp T (the generator's tick time, already delayed by provider latency + jitter).

    A *detector hit* is one opportunity the engine emits (after its own min-profit / min-confidence /
    latency filters, using the synthetic research configuration of the live app) together with the
    aligned window it came from [window_start, window_end].

    A hit and an injection *match* if: same symbol, the injected tick's source is one of the hit's
    sources, and the hit's window overlaps [T, T + W].  W (ms) is the matching tolerance.
      recall    = injections with >= 1 matching hit / all injections
      precision = hits with >= 1 matching injection / all hits
      latency   = for each detected injection, max(0, window_end of its earliest matching hit - T),
                  in simulated (data) milliseconds. This is the alignment delay of the detector (the
                  window must close before it is evaluated); real compute time is not measured.
    Two hit views are reported: "all" (every opportunity type the engine emits) and "cross_source"
    (only crossed-book hits, the type that is an arbitrage in the strict sense).

Choice of W
    Default 100 ms (= two generator tick intervals of 50 ms, and the aligner's max_window_age_ms): it
    tolerates one subsequent tick and the window-boundary skew caused by the 50-80 ms provider
    latencies. W = 0 is the strict same-window match; W = 250 is shown to expose how a loose window
    credits hits caused by later, unrelated ticks of the same source (it can only inflate both
    metrics). The sweep is {0, 50, 100, 250}.

Determinism
    The generator seeds each source with `seed + hash(source_id)`, and str hash() is randomised per
    process, and it reads wall-clock time, and the shared reference price calls a live-rate API. This
    script does not edit the generator; it (a) replaces the generator module's `time` with a simulated
    clock, (b) makes the live-rate lookup fail so the fixed fallback price is used, and (c) re-seeds
    each source's RNG from crc32(source_id). The engine's latency-arbitrage branch uses the global
    `random` module, so that is seeded too. Same seed -> same numbers.

Limits (read before quoting any number)
    * Ground truth is an injected single-source price offset, so the results describe this detector
      on this simulator. They say nothing about real markets, real latencies or real opportunity
      frequencies, and the injected size (5-15 pips) and rate (0.25% per tick) are generator settings.
    * Sources also drift independently of each other (up to +/-10 pips), which produces genuine
      crossed books that are not injections; those count against precision by construction, and
      they are a property of the simulator, not of any market.
    * The engine compares the latest tick per source in a window, so an injection followed by another
      tick from the same source inside the same window is invisible to it; recall depends on the
      alignment window (default = the app's 200 ms).
    * Bootstrap intervals resample injections (or hits) as independent events across the pooled
      seeds; events within a run are serially dependent, so the intervals are somewhat optimistic.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import subprocess
import sys
import types
import zlib
from pathlib import Path
from unittest import mock

import numpy as np

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from backend.config import ARBITRAGE_RESEARCH_CONFIG, SYNTHETIC_GENERATION_CONFIG  # noqa: E402
from backend.core.arbitrage.arbitrage_engine import ArbitrageConfig, ArbitrageEngine  # noqa: E402
from backend.core.arbitrage.tick_aligner import TickAligner  # noqa: E402
from backend.core.data_sources import session_synthetic_source as sss  # noqa: E402
from backend.core.interfaces.normalized_tick import TickNormalizer  # noqa: E402

DEFAULT_OUT = REPO / "research" / "artifacts" / "synthetic_detection_eval.json"
START_MS = 1_767_621_600_000  # 2026-01-05 14:00:00 UTC (fixed; London/New York overlap)
DEFAULT_W = (0, 50, 100, 250)
PRIMARY_W = 100


class _SimClock:
    """Stands in for the `time` module inside the generator."""

    def __init__(self, start_ms: int):
        self.now_ms = start_ms

    def time(self) -> float:
        return self.now_ms / 1000.0


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True,
                              check=True).stdout.strip()
    except Exception:
        return "unknown"


def _no_live_rate(symbol):  # forces the generator's fixed fallback price (offline)
    raise OSError("offline: live rate disabled for the evaluation")


def simulate(seed: int, duration_s: float, alignment_ms: int) -> dict:
    """One seeded run: simulated ticks -> normalizer -> aligner -> engine. Returns injections and hits."""
    logging.disable(logging.CRITICAL)
    random.seed(seed)
    clock = _SimClock(START_MS)
    cfg = SYNTHETIC_GENERATION_CONFIG
    with mock.patch.object(sss, "time", types.SimpleNamespace(time=clock.time)), \
            mock.patch.object(sss, "_fetch_live_rate", _no_live_rate):
        sss.SharedReferencePrice._instances.clear()
        sources = sss.create_synthetic_sources()
        for s in sources:
            s._rng = random.Random(seed * 1000 + zlib.crc32(s.source_id.encode()) % 1000)
            s.connect()
        for ref in sss.SharedReferencePrice.all():
            ref.reset(seed)

        normalizer = TickNormalizer()
        engine = ArbitrageEngine(ArbitrageConfig(
            min_profit_pips=ARBITRAGE_RESEARCH_CONFIG.min_profit_pips,
            min_confidence=ARBITRAGE_RESEARCH_CONFIG.min_confidence,
            enable_cross_source=True, enable_triangular=False, enable_session_analysis=True))
        for s in sources:  # same wiring as MultiSourceStreamer.add_source
            normalizer.set_source_latency(s.source_id, s.config.latency_estimate_ms)
            engine.config.source_latencies[s.source_id] = s.config.latency_estimate_ms
            engine.config.source_reliabilities[s.source_id] = s.config.reliability_score

        normalized, injections = [], []
        for step in range(int(duration_s * 1000 / cfg.tick_interval_ms)):
            clock.now_ms = START_MS + step * cfg.tick_interval_ms
            for s in sources:
                raw = s.get_tick(s.symbol)
                if raw is None:
                    continue
                tick = normalizer.normalize(raw)
                if tick is None:
                    continue
                normalized.append(tick)
                if raw.extra.get("arbitrage_injected"):
                    injections.append({"symbol": tick.symbol, "source": tick.source_id,
                                       "t_ms": tick.timestamp_ms})

    windows = []

    async def _align():
        aligner = TickAligner(window_size_ms=alignment_ms)
        aligner.on_window_complete(windows.append)
        for t in normalized:
            await aligner.add_tick(t)
        await aligner.flush()

    asyncio.run(_align())
    windows.sort(key=lambda w: w.window_start_ms)

    hits = []
    for w in windows:
        for opp in engine.detect(w):
            hits.append({"symbol": opp.symbols[0], "sources": list(opp.sources), "type": opp.type.value,
                         "w0": w.window_start_ms, "w1": w.window_end_ms})
    logging.disable(logging.NOTSET)
    return {"seed": seed, "n_ticks": len(normalized), "n_windows": len(windows),
            "injections": injections, "hits": hits}


def _matches(inj: dict, hit: dict, W: int) -> bool:
    return (inj["symbol"] == hit["symbol"] and inj["source"] in hit["sources"]
            and hit["w1"] >= inj["t_ms"] and hit["w0"] <= inj["t_ms"] + W)


def score(run: dict, W: int, view: str) -> dict:
    """Per-injection / per-hit outcomes for one run, one tolerance W and one hit view."""
    hits = [h for h in run["hits"] if view == "all" or h["type"] == view]
    hits_by_sym = {}
    for h in hits:
        hits_by_sym.setdefault(h["symbol"], []).append(h)
    inj_detected, latencies = [], []
    for inj in run["injections"]:
        lat = [max(0, h["w1"] - inj["t_ms"]) for h in hits_by_sym.get(inj["symbol"], ())
               if _matches(inj, h, W)]
        inj_detected.append(bool(lat))
        if lat:
            latencies.append(min(lat))
    hit_true = [any(_matches(inj, h, W) for inj in run["injections"] if inj["symbol"] == h["symbol"])
                for h in hits]
    return {"inj_detected": inj_detected, "hit_true": hit_true, "latencies": latencies}


def _rates(s: dict) -> dict:
    n_inj, n_hit = len(s["inj_detected"]), len(s["hit_true"])
    det, tp = sum(s["inj_detected"]), sum(s["hit_true"])
    lat = s["latencies"]
    return {"n_injections": n_inj, "n_detected": det, "n_hits": n_hit, "n_hits_matched": tp,
            "recall": det / n_inj if n_inj else None, "precision": tp / n_hit if n_hit else None,
            "latency_ms_median": float(np.median(lat)) if lat else None,
            "latency_ms_mean": float(np.mean(lat)) if lat else None}


def _bootstrap(values, stat, n_boot: int, rng) -> list | None:
    v = np.asarray(values, dtype=float)
    if v.size == 0:
        return None
    idx = rng.integers(0, v.size, size=(n_boot, v.size))
    d = stat(v[idx], axis=1)
    return [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]


def pooled(per_seed: list[dict], n_boot: int, rng) -> dict:
    s = {k: [x for p in per_seed for x in p[k]] for k in ("inj_detected", "hit_true", "latencies")}
    out = _rates(s)
    out["recall_ci95"] = _bootstrap(s["inj_detected"], np.mean, n_boot, rng)
    out["precision_ci95"] = _bootstrap(s["hit_true"], np.mean, n_boot, rng)
    out["latency_ms_median_ci95"] = _bootstrap(s["latencies"], np.median, n_boot, rng)
    return out


def evaluate(seeds, duration_s=600.0, windows_ms=DEFAULT_W, alignment_ms=None, n_boot=2000) -> dict:
    alignment_ms = alignment_ms or ARBITRAGE_RESEARCH_CONFIG.alignment_window_ms
    runs = [simulate(s, duration_s, alignment_ms) for s in seeds]
    rng = np.random.default_rng(0)
    results = {}
    for view in ("all", "cross_source"):
        for W in windows_ms:
            scored = [score(r, W, view) for r in runs]
            results[f"{view}|W={W}"] = {
                "view": view, "W_ms": W,
                "per_seed": [{"seed": r["seed"], **_rates(sc)} for r, sc in zip(runs, scored)],
                "pooled": pooled(scored, n_boot, rng),
            }
    hit_types = {}
    for r in runs:
        for h in r["hits"]:
            hit_types[h["type"]] = hit_types.get(h["type"], 0) + 1
    return {
        "script": "research/run_synthetic_detection_eval.py",
        "code_commit": _git("rev-parse", "HEAD"),
        "working_tree_dirty": bool(_git("status", "--porcelain")),
        "parameters": {
            "seeds": list(seeds), "duration_s_per_seed": duration_s, "windows_W_ms": list(windows_ms),
            "primary_W_ms": PRIMARY_W, "alignment_window_ms": alignment_ms,
            "tick_interval_ms": SYNTHETIC_GENERATION_CONFIG.tick_interval_ms,
            "arbitrage_injection_rate": SYNTHETIC_GENERATION_CONFIG.arbitrage_injection_rate,
            "injected_offset_pips": [5.0, 15.0],
            "engine": {"min_profit_pips": ARBITRAGE_RESEARCH_CONFIG.min_profit_pips,
                       "min_confidence": ARBITRAGE_RESEARCH_CONFIG.min_confidence,
                       "enable_session_analysis": True, "enable_latency_arbitrage": True},
            "bootstrap": {"resamples": n_boot, "unit": "injection / hit / detected injection, pooled across seeds",
                          "rng_seed": 0},
            "start_utc_ms": START_MS,
        },
        "run_totals": [{"seed": r["seed"], "n_ticks": r["n_ticks"], "n_windows": r["n_windows"],
                        "n_injections": len(r["injections"]), "n_hits": len(r["hits"])} for r in runs],
        "hit_counts_by_type": hit_types,
        "results": results,
        "limits": ("Ground truth is an injected single-source price offset; results describe the detector "
                   "on this simulator, not real markets. Latency is simulated alignment delay, not compute "
                   "time. Bootstrap treats events as independent."),
    }


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seeds", type=int, default=5, help="number of seeds (1..N)")
    ap.add_argument("--duration-s", type=float, default=600.0, help="simulated seconds per seed")
    ap.add_argument("--W", type=int, nargs="+", default=list(DEFAULT_W), help="match tolerances in ms")
    ap.add_argument("--alignment-ms", type=int, default=None, help="aligner window (default: app's 200)")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    res = evaluate(range(1, a.seeds + 1), a.duration_s, tuple(a.W), a.alignment_ms, a.bootstrap)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(res, indent=2), encoding="utf-8")
    for key, r in res["results"].items():
        p = r["pooled"]
        f = lambda v: "n/a" if v is None else f"{v:.3f}"  # noqa: E731
        print(f"{key:20s} recall {f(p['recall'])} {p['recall_ci95']}  precision {f(p['precision'])} "
              f"{p['precision_ci95']}  latency_med {f(p['latency_ms_median'])} ms  "
              f"(inj {p['n_injections']}, hits {p['n_hits']})")
    print(f"wrote {a.out}")
    return res


if __name__ == "__main__":
    main()
