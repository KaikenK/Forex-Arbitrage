"""Every step that produces a paper result, in order, each behind its prerequisite gate.

  python -m research.reproduce --list              # dry run: steps, artifacts, gate status
  python -m research.reproduce --run h2h3          # one step (refuses if its gate is closed)
  python -m research.reproduce --run all           # in order, stopping at the first closed gate

Gates are the protocol's own preconditions (RESEARCH_TRACKER §6): the GDELT sample is frozen
only at/after its cutoff, scoring runs only on the frozen sample, H1/H5/H6 need scores stamped
with the freeze hash, and H6b runs only after its window closes. The underlying scripts
enforce the same rules themselves; the gates here make the order visible and fail early.

Laptop rule (§3): the heavy steps (FinBERT / sentence-transformer scoring, price loads) run one
at a time, never alongside the live services. `--run all` is sequential by construction.
See research/REPRODUCE.md for which step produces which paper table.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

RESEARCH = Path(__file__).resolve().parent
ARTIFACTS = RESEARCH / "artifacts"
H6B_WINDOW_END_UTC = datetime(2026, 11, 4, 18, 30, tzinfo=UTC)
GDELT_CUTOFF_UTC = datetime(2026, 11, 1, 18, 30, tzinfo=UTC)    # 2026-11-02 00:00 IST (DEVIATION 1)
HISTDATA_YEARS = range(2018, 2026)
MAJORS = ("EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF")


@dataclass
class Context:
    """Where the gates look. Defaults are the real locations; tests substitute temp dirs."""
    now: datetime = field(default_factory=lambda: datetime.now(UTC))
    artifacts: Path = ARTIFACTS
    histdata_dir: Path | None = None
    recordings_dir: Path | None = None

    @property
    def freeze_path(self) -> Path:
        return self.artifacts / "gdelt_sample_freeze.json"

    @property
    def scores_dir(self) -> Path:
        return self.artifacts / "archive_scores"


Gate = Callable[[Context], str | None]          # None = open, else the reason it is closed


def _exists(relative: str, why: str) -> Gate:
    def gate(ctx: Context) -> str | None:
        return None if (ctx.artifacts / relative).exists() else f"missing {relative} ({why})"
    return gate


def _histdata(ctx: Context) -> str | None:
    if ctx.histdata_dir is None:
        from research.data.cache_paths import histdata_cache_dir

        ctx.histdata_dir = histdata_cache_dir()
    from research.data.histdata import zip_path

    missing = [f"{p} {y}" for p in MAJORS for y in HISTDATA_YEARS if not zip_path(ctx.histdata_dir, p, y).exists()]
    return None if not missing else f"HistData M1 not cached: {', '.join(missing[:4])}{' ...' if len(missing) > 4 else ''} (python -m research.fetch_histdata)"


def _after_cutoff(ctx: Context) -> str | None:
    return None if ctx.now >= GDELT_CUTOFF_UTC else f"GDELT sample cutoff not reached ({GDELT_CUTOFF_UTC.isoformat()})"


def _frozen(ctx: Context) -> str | None:
    return None if ctx.freeze_path.exists() else "GDELT sample not frozen (step: freeze)"


def _freeze_sha(ctx: Context) -> str | None:
    import json

    try:
        return str(json.loads(ctx.freeze_path.read_text(encoding="utf-8"))["sha256"])
    except (OSError, ValueError, KeyError):
        return None


def _scored(*stages: str) -> Gate:
    def gate(ctx: Context) -> str | None:
        reason = _frozen(ctx)
        if reason:
            return reason
        sha = _freeze_sha(ctx)
        import json

        for stage in stages:
            manifest = ctx.scores_dir / stage / "manifest.json"
            try:
                meta = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return f"score stage '{stage}' not run on the frozen sample"
            if meta.get("freeze_sha256") != sha:
                return f"score stage '{stage}' was not produced on the current freeze"
        return None
    return gate


def _h6b_window_closed(ctx: Context) -> str | None:
    if ctx.now < H6B_WINDOW_END_UTC:
        return f"H6b window open until {H6B_WINDOW_END_UTC.isoformat()}"
    if ctx.recordings_dir is not None and not ctx.recordings_dir.exists():
        return f"no recordings at {ctx.recordings_dir}"
    return None


def _all(*gates: Gate) -> Gate:
    def gate(ctx: Context) -> str | None:
        for g in gates:
            reason = g(ctx)
            if reason:
                return reason
        return None
    return gate


@dataclass(frozen=True)
class Step:
    name: str
    produces: str                 # paper item
    args: tuple[str, ...]         # python -m <args>
    artifact: str                 # relative to research/artifacts
    gate: Gate
    heavy: bool = False


CALENDAR = _exists("calendar_events.parquet", "step: calendar")
CALENDAR_CSV = _exists("forex_factory_calendar.csv", "the acquired ForexFactory dump")
PRE_FIX_STORE = "calendar_events.parquet.bak-20261024-173354"

STEPS: tuple[Step, ...] = (
    Step("calendar", "calendar store (all scheduled-event results)",
         ("research.ingest_calendar", "--replace"), "calendar_events.parquet", CALENDAR_CSV),
    Step("positive_control", "Table: positive control (ABDV replication)",
         ("research.run_positive_control",), "positive_control.json", _all(CALENDAR, _histdata), heavy=True),
    Step("r1_usdinr", "Table: R1 re-run, USD/INR daily",
         ("research.run_inr_leadlag", "--json", "research/artifacts/r1_rerun.json"), "r1_rerun.json", CALENDAR),
    Step("r1_usdinr_excl_window", "Table: R1 re-run, USD/INR, pandemic window dropped",
         ("research.run_inr_leadlag", "--exclude-pandemic-window", "--json", "research/artifacts/r1_rerun.json"),
         "r1_rerun.json", CALENDAR),
    Step("r1_eurusd", "Table: R1 re-run, EUR/USD daily",
         ("research.run_inr_leadlag", "--pair", "EUR/USD", "--ticker", "EURUSD=X", "--json",
          "research/artifacts/r1_rerun.json"), "r1_rerun.json", CALENDAR),
    Step("r1_eurusd_excl_window", "Table: R1 re-run, EUR/USD, pandemic window dropped",
         ("research.run_inr_leadlag", "--pair", "EUR/USD", "--ticker", "EURUSD=X", "--exclude-pandemic-window",
          "--json", "research/artifacts/r1_rerun.json"), "r1_rerun.json", CALENDAR),
    Step("r1_legacy", "R1 as originally reported (reproduction check)",
         ("research.run_inr_leadlag", "--legacy", "--store", f"research/artifacts/{PRE_FIX_STORE}"),
         "(stdout)", _exists(PRE_FIX_STORE, "pre-fix calendar backup")),
    Step("h2h3", "Tables: H2 category CARs, H3 half-lives",
         ("research.run_h2h3",), "h2h3.json", _all(CALENDAR, _histdata), heavy=True),
    Step("features", "GDELT feature table (descriptive)",
         ("research.build_features",), "features.parquet", _exists("gdelt_shards", "GDELT backfill")),
    Step("freeze", "Frozen H1/H5/H6 news sample (coverage table)",
         ("research.freeze_gdelt_sample",), "gdelt_sample_freeze.json", _after_cutoff),
    Step("score_dedup", "scores: syndication dedup", ("research.score_archive", "--stage", "dedup"),
         "archive_scores/dedup", _frozen),
    Step("score_lexicon", "scores: lexicon polarity", ("research.score_archive", "--stage", "lexicon"),
         "archive_scores/lexicon", _scored("dedup")),
    Step("score_finbert", "scores: FinBERT polarity", ("research.score_archive", "--stage", "finbert"),
         "archive_scores/finbert", _scored("dedup"), heavy=True),
    Step("score_embed", "scores: sentence-transformer embeddings", ("research.score_archive", "--stage", "embed"),
         "archive_scores/embed", _scored("dedup"), heavy=True),
    Step("h1_primary", "Table: H1 (sentence-transformer, decides)",
         ("research.run_h1", "--embedder", "sentence_transformer"), "h1_results.json",
         _all(CALENDAR, _histdata, _scored("dedup", "finbert", "embed")), heavy=True),
    Step("h1_tfidf", "H1 robustness: TF-IDF (no decision weight)",
         ("research.run_h1", "--embedder", "tfidf_svd"), "h1_results.json",
         _all(CALENDAR, _histdata, _scored("dedup", "finbert")), heavy=True),
    Step("h1_hashing", "H1 robustness: hashing (no decision weight)",
         ("research.run_h1", "--embedder", "hashing"), "h1_results.json",
         _all(CALENDAR, _histdata, _scored("dedup", "finbert")), heavy=True),
    Step("h1_decide", "H1 verdict", ("research.run_h1", "--decide"), "h1_results.json",
         _exists("h1_results.json", "step: h1_primary")),
    Step("h5_lexicon", "Table: H5 (lexicon backend)", ("research.run_h5", "--backend", "lexicon"), "h5_results.json",
         _all(CALENDAR, _scored("dedup", "lexicon"))),
    Step("h5_finbert", "Table: H5 (FinBERT backend)", ("research.run_h5", "--backend", "finbert"), "h5_results.json",
         _all(CALENDAR, _scored("dedup", "finbert"))),
    Step("h5_decide", "H5 verdict", ("research.run_h5", "--decide"), "h5_results.json",
         _exists("h5_results.json", "steps: h5_lexicon, h5_finbert")),
    Step("h6", "Tables: H6 forecast test + horizon output", ("research.run_h6",), "h6_results.json",
         _all(CALENDAR, _histdata, _scored("dedup", "finbert", "embed")), heavy=True),
    Step("h6b", "Table: H6b prospective ranking (exploratory)", ("research.run_h6b",), "h6b_results.json",
         _h6b_window_closed),
)


def status(step: Step, ctx: Context) -> str | None:
    return step.gate(ctx)


def list_steps(ctx: Context) -> list[str]:
    lines = []
    for i, step in enumerate(STEPS, 1):
        reason = status(step, ctx)
        state = "READY" if reason is None else f"BLOCKED: {reason}"
        present = "present" if (ctx.artifacts / step.artifact).exists() else "absent"
        lines.append(f"{i:>2}. {step.name:<22} {'[heavy] ' if step.heavy else ''}{step.produces}\n"
                     f"      python -m {' '.join(step.args)}\n"
                     f"      artifact: research/artifacts/{step.artifact} ({present})\n"
                     f"      {state}")
    return lines


def run_step(step: Step, ctx: Context, *, runner=subprocess.run) -> int:
    reason = status(step, ctx)
    if reason is not None:
        print(f"[{step.name}] refused: {reason}")
        return 2
    print(f"[{step.name}] python -m {' '.join(step.args)}  ->  research/artifacts/{step.artifact}")
    completed = runner([sys.executable, "-m", *step.args], cwd=RESEARCH.parent)
    return int(getattr(completed, "returncode", 0) or 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reproduce every paper result, in order, gated.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="dry run: steps, artifacts and gate status")
    group.add_argument("--run", metavar="STEP", help="a step name, or 'all' (in order, stop at the first block)")
    args = parser.parse_args(argv)
    ctx = Context()
    if args.list:
        print("\n".join(list_steps(ctx)))
        return 0
    if args.run == "all":
        for step in STEPS:
            code = run_step(step, ctx)
            if code != 0:
                return code
        return 0
    by_name = {s.name: s for s in STEPS}
    if args.run not in by_name:
        parser.error(f"unknown step {args.run!r}; choose from: all, {', '.join(by_name)}")
    return run_step(by_name[args.run], ctx)


if __name__ == "__main__":
    raise SystemExit(main())
