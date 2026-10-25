"""Pair -> ticker / asset-path table, and the archive scripts that now follow --pair."""
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

import pytest

from market.pair_assets import PAIR_TICKERS
from market.pair_assets import STUDY_PAIRS
from market.pair_assets import pair_slug
from market.pair_assets import repo_root
from market.pair_assets import runtime_archive_dir
from market.pair_assets import sentiment_assets_dir
from market.pair_assets import ticker_for

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def test_every_study_pair_has_a_ticker() -> None:
    assert set(STUDY_PAIRS) == set(PAIR_TICKERS)
    assert ticker_for("USD/INR") == "INR=X"
    assert ticker_for("eurusd") == "EURUSD=X"
    assert ticker_for("GBP_USD") == "GBPUSD=X"
    assert ticker_for("USD/JPY") == "JPY=X"
    assert ticker_for("USD/CHF") == "CHF=X"


def test_unknown_pair_fails_loudly_instead_of_defaulting_to_inr() -> None:
    with pytest.raises(KeyError, match="--ticker"):
        ticker_for("NZD/CAD")


def test_paths_follow_the_repo_layout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARBEX_ROOT", raising=False)
    root = repo_root()
    assert (root / "backend").is_dir() and (root / "services" / "semantic").is_dir()
    assert pair_slug("EUR/USD") == "eurusd"
    assert sentiment_assets_dir("EUR/USD") == root / "sentiment_assets" / "eurusd"
    assert runtime_archive_dir("USD/JPY") == root / "sentiment_assets" / "runtime" / "usdjpy"


def test_arbex_root_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARBEX_ROOT", str(tmp_path))
    assert sentiment_assets_dir("USD/INR") == tmp_path / "sentiment_assets" / "usdinr"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_labeller_defaults_follow_the_pair(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARBEX_ROOT", str(tmp_path))
    module = _script("append_live_archive_reaction_rows")
    args = module.resolve_pair_defaults(argparse.Namespace(
        pair="EUR/USD", ticker=None, seeds_file=None, output_path=None, summary_path=None))
    assert args.ticker == "EURUSD=X", "a EUR/USD row must never be labelled against INR=X"
    assets = tmp_path / "sentiment_assets" / "eurusd"
    assert args.seeds_file == assets / "eurusd_live_archive_event_seeds.jsonl"
    assert args.output_path == assets / "eurusd_reaction_training_rows_targeted_expanded.jsonl"

    explicit = module.resolve_pair_defaults(argparse.Namespace(
        pair="USD/INR", ticker="USDINR=X", seeds_file=None, output_path=None, summary_path=None))
    assert explicit.ticker == "USDINR=X", "an explicit --ticker still wins"


def test_seed_exporter_reads_the_pairs_own_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ARBEX_ROOT", str(tmp_path))
    module = _script("export_live_archive_event_seeds")
    args = module.resolve_pair_defaults(argparse.Namespace(
        pair="USD/JPY", analysis_archive=None, feed_archive=None, output_path=None))
    assert args.analysis_archive == tmp_path / "sentiment_assets" / "runtime" / "usdjpy" / "live_analysis.jsonl"
    assert args.output_path == tmp_path / "sentiment_assets" / "usdjpy" / "usdjpy_live_archive_event_seeds.jsonl"
