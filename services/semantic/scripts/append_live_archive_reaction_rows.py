"""Append reaction-training rows for one study pair from an exported live-archive seed ledger.

Defaults follow --pair: ticker from market.pair_assets.PAIR_TICKERS, paths under
sentiment_assets/<slug>/. Run once per pair, e.g. --pair EUR/USD.
"""
from __future__ import annotations

import argparse
import json
import site
import sys
from collections import Counter
from datetime import UTC
from datetime import datetime
from datetime import timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
USER_SITE_PATH = Path(site.getusersitepackages())
for import_path in (str(USER_SITE_PATH), str(SRC_PATH)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from market.pair_assets import sentiment_assets_dir
from market.pair_assets import pair_slug
from market.pair_assets import ticker_for
from training.models import PriceBar
from training.usdinr_dataset_builder import DEFAULT_PAIR
from training.usdinr_dataset_builder import build_training_rows_from_events
from training.usdinr_dataset_builder import load_timestamped_event_seeds
from training.usdinr_dataset_builder import serialize_training_rows

DEFAULT_HORIZONS = [60, 240, 1440]
DEFAULT_PRICE_INTERVAL = "60m"


def main() -> None:
    args = _parse_args()
    output_path = args.output_path.resolve()
    seeds = load_timestamped_event_seeds(args.seeds_file.resolve(), default_pair=args.pair)
    if args.limit is not None:
        seeds = seeds[: args.limit]
    if not seeds:
        raise RuntimeError("No seed rows available to label.")

    price_history = _download_price_history_for_seeds(
        seeds=seeds,
        ticker=args.ticker,
        symbol=args.pair,
        interval=args.price_interval,
        max_horizon_minutes=max(args.horizons),
        buffer_hours=args.buffer_hours,
    )
    bar_count = len(price_history.get(args.pair, []))
    if bar_count == 0:
        raise RuntimeError("No intraday price bars were downloaded for the requested window.")

    candidate_rows = build_training_rows_from_events(
        seeds,
        price_history,
        args.horizons,
        candidate_symbols=[args.pair],
    )
    serialized_rows = serialize_training_rows(candidate_rows)

    existing_identities = _existing_row_identities(output_path)
    new_rows: list[dict[str, Any]] = []
    emitted_identities: set[tuple[str, str, int]] = set()
    duplicate_count = 0
    for row in serialized_rows:
        identity = _row_identity(row)
        if identity in existing_identities or identity in emitted_identities:
            duplicate_count += 1
            continue
        emitted_identities.add(identity)
        new_rows.append(row)

    if new_rows:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("a", encoding="utf-8") as handle:
            for row in new_rows:
                handle.write(json.dumps(row, ensure_ascii=True))
                handle.write("\n")

    summary = _build_summary(
        seeds=seeds,
        bars_downloaded=bar_count,
        candidate_rows=serialized_rows,
        appended_rows=new_rows,
        duplicate_count=duplicate_count,
        output_path=output_path,
        pair=args.pair,
        ticker=args.ticker,
        price_interval=args.price_interval,
        horizons=args.horizons,
    )
    args.summary_path.parent.mkdir(parents=True, exist_ok=True)
    args.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Seeds file: {args.seeds_file.resolve()}")
    print(f"Output table: {output_path}")
    print(f"Summary file: {args.summary_path.resolve()}")
    print(f"Seeds loaded: {summary['seed_count']}")
    print(f"Price bars downloaded: {bar_count}")
    print(f"Candidate rows built: {summary['candidate_row_count']}")
    print(f"Rows appended: {summary['appended_row_count']}")
    print(f"Rows skipped as duplicates: {duplicate_count}")


def _download_price_history_for_seeds(
    *,
    seeds: list[Any],
    ticker: str,
    symbol: str,
    interval: str,
    max_horizon_minutes: int,
    buffer_hours: int,
) -> dict[str, list[PriceBar]]:
    import yfinance as yf

    earliest_timestamp = min(seed.published_at for seed in seeds)
    latest_timestamp = max(seed.published_at for seed in seeds)
    window_start = (earliest_timestamp - timedelta(hours=buffer_hours)).astimezone(UTC)
    requested_window_end = (latest_timestamp + timedelta(minutes=max_horizon_minutes) + timedelta(hours=buffer_hours)).astimezone(UTC)
    current_utc = datetime.now(UTC)
    window_end = min(requested_window_end, current_utc)

    history_frame = yf.Ticker(ticker).history(
        start=window_start.strftime("%Y-%m-%d"),
        end=(window_end + timedelta(days=1)).strftime("%Y-%m-%d"),
        interval=interval,
    )

    bars: list[PriceBar] = []
    for timestamp, row in history_frame.iterrows():
        bar_timestamp = timestamp.to_pydatetime()
        if bar_timestamp.tzinfo is None:
            bar_timestamp = bar_timestamp.replace(tzinfo=UTC)
        else:
            bar_timestamp = bar_timestamp.astimezone(UTC)
        bars.append(
            PriceBar(
                symbol=symbol,
                timestamp=bar_timestamp,
                close=float(row["Close"]),
            )
        )
    return {symbol: bars}


def _existing_row_identities(path: Path) -> set[tuple[str, str, int]]:
    identities: set[tuple[str, str, int]] = set()
    if not path.exists():
        return identities
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        try:
            identities.add(_row_identity(payload))
        except (KeyError, TypeError, ValueError):
            continue
    return identities


def _row_identity(row: dict[str, Any]) -> tuple[str, str, int]:
    source_metadata = row.get("source_metadata", {})
    event_id = None
    if isinstance(source_metadata, dict):
        event_id = source_metadata.get("event_id")
    if event_id is None:
        event_id = f"{row['pair']}::{row['timestamp']}::{row['article_text']}"
    return (str(event_id), str(row["pair"]), int(row["horizon_minutes"]))


def _build_summary(
    *,
    seeds: list[Any],
    bars_downloaded: int,
    candidate_rows: list[dict[str, Any]],
    appended_rows: list[dict[str, Any]],
    duplicate_count: int,
    output_path: Path,
    pair: str,
    ticker: str,
    price_interval: str,
    horizons: list[int],
) -> dict[str, Any]:
    seed_sources = Counter(seed.source.value for seed in seeds)
    seed_categories = Counter((seed.event_category or "unknown") for seed in seeds)
    appended_horizons = Counter(str(row["horizon_minutes"]) for row in appended_rows)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return {
        "generated_at": now,
        "dataset_generation": {
            "seed_source": "exported live archive event ledger",
            "labeling_method": "timestamped seed -> 60m intraday bars -> leak-safe future price windows",
            "pair": pair,
            "ticker": ticker,
            "price_interval": price_interval,
            "horizons": horizons,
        },
        "seed_count": len(seeds),
        "seed_sources": dict(sorted(seed_sources.items())),
        "seed_categories": dict(sorted(seed_categories.items())),
        "bars_downloaded": bars_downloaded,
        "candidate_row_count": len(candidate_rows),
        "appended_row_count": len(appended_rows),
        "duplicate_row_count": duplicate_count,
        "appended_horizons": dict(sorted(appended_horizons.items(), key=lambda item: int(item[0]))),
        "output_path": str(output_path),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Append reaction-training rows from an exported live-archive event seed ledger.",
    )
    parser.add_argument("--seeds-file", type=Path, help="Default: sentiment_assets/<slug>/<slug>_live_archive_event_seeds.jsonl")
    parser.add_argument("--output-path", type=Path, help="Default: sentiment_assets/<slug>/<slug>_reaction_training_rows_targeted_expanded.jsonl")
    parser.add_argument("--summary-path", type=Path, help="Default: sentiment_assets/<slug>/live_archive_append_summary.json")
    parser.add_argument("--pair", default=DEFAULT_PAIR)
    parser.add_argument("--ticker", help="Price ticker; default from the pair (USD/INR -> INR=X, EUR/USD -> EURUSD=X, ...).")
    parser.add_argument("--price-interval", default=DEFAULT_PRICE_INTERVAL)
    parser.add_argument("--buffer-hours", type=int, default=24)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--horizon",
        dest="horizons",
        action="append",
        type=int,
        help="Reaction horizon in minutes. Can be provided multiple times.",
    )
    args = parser.parse_args()
    args.horizons = args.horizons or list(DEFAULT_HORIZONS)
    return resolve_pair_defaults(args)


def resolve_pair_defaults(args: argparse.Namespace) -> argparse.Namespace:
    """Fill ticker and paths from --pair, so non-INR pairs never label against INR=X."""
    slug = pair_slug(args.pair)
    assets = sentiment_assets_dir(args.pair)
    args.ticker = args.ticker or ticker_for(args.pair)
    args.seeds_file = args.seeds_file or assets / f"{slug}_live_archive_event_seeds.jsonl"
    args.output_path = args.output_path or assets / f"{slug}_reaction_training_rows_targeted_expanded.jsonl"
    args.summary_path = args.summary_path or assets / "live_archive_append_summary.json"
    return args


if __name__ == "__main__":
    main()