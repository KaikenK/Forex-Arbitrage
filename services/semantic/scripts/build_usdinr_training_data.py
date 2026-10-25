"""Build USD/INR training data from timestamped event seeds and aligned price bars."""
from __future__ import annotations

import argparse
import json
import site
import sys
from datetime import UTC
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
USER_SITE_PATH = Path(site.getusersitepackages())
for import_path in (str(USER_SITE_PATH), str(SRC_PATH)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from training.models import PriceBar
from training.usdinr_dataset_builder import DEFAULT_PAIR
from training.usdinr_dataset_builder import build_training_rows_from_events
from training.usdinr_dataset_builder import load_timestamped_event_seeds
from training.usdinr_dataset_builder import serialize_training_rows

DEFAULT_EVENTS_PATH = PROJECT_ROOT / "data" / "usdinr_timestamped_events.jsonl"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "usdinr_reaction_training_rows.jsonl"
DEFAULT_HORIZONS = [60, 240, 1440]
DEFAULT_TICKER = "INR=X"
DEFAULT_PRICE_PERIOD = "730d"
DEFAULT_PRICE_INTERVAL = "60m"


def main() -> None:
    args = _parse_args()
    raw_seed_count = _count_nonempty_lines(args.events_file.resolve())

    print(f"Step 1: Loading timestamped event seeds from {args.events_file}...")
    event_seeds = load_timestamped_event_seeds(args.events_file.resolve(), default_pair=args.pair)
    print(
        f"  -> {len(event_seeds)} timestamped event seeds "
        f"({max(0, raw_seed_count - len(event_seeds))} duplicate or empty rows skipped)"
    )

    print(
        f"Step 2: Downloading {args.pair} price data via yfinance "
        f"(interval={args.price_interval}, period={args.price_period})..."
    )
    price_history = _download_price_history(
        ticker=args.ticker,
        symbol=args.pair,
        period=args.price_period,
        interval=args.price_interval,
    )
    bar_count = len(price_history.get(args.pair, []))
    print(f"  -> {bar_count} price bars")
    if bar_count == 0:
        raise RuntimeError("No price data downloaded. Cannot build training rows.")

    print("Step 3: Building aligned training rows...")
    rows = build_training_rows_from_events(
        event_seeds,
        price_history,
        args.horizons,
        candidate_symbols=[args.pair],
    )
    print(f"  -> {len(rows)} aligned training rows")
    expected_rows = len(event_seeds) * len(args.horizons)
    print(f"  -> {max(0, expected_rows - len(rows))} rows rejected by alignment trust checks")
    if not rows:
        raise RuntimeError(
            "No rows were generated. Confirm the event timestamps overlap the downloaded price range "
            "and that the bar interval is fine enough for the requested horizons."
        )

    serialized_rows = serialize_training_rows(rows)
    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    with args.output_path.open("w", encoding="utf-8") as output_file:
        for row in serialized_rows:
            output_file.write(json.dumps(row) + "\n")

    print(f"\nWrote {len(serialized_rows)} rows to {args.output_path}")
    for horizon_minutes in sorted(args.horizons):
        horizon_count = sum(1 for row in serialized_rows if row["horizon_minutes"] == horizon_minutes)
        print(f"  horizon={horizon_minutes}m: {horizon_count} rows")


def _download_price_history(
    *,
    ticker: str,
    symbol: str,
    period: str,
    interval: str,
) -> dict[str, list[PriceBar]]:
    import yfinance as yf

    history_frame = yf.Ticker(ticker).history(period=period, interval=interval)
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
            ),
        )
    return {symbol: bars}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a USD/INR reaction-training table from timestamped event seeds.",
    )
    parser.add_argument("--events-file", type=Path, default=DEFAULT_EVENTS_PATH)
    parser.add_argument("--output-path", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--pair", default=DEFAULT_PAIR)
    parser.add_argument("--ticker", default=DEFAULT_TICKER)
    parser.add_argument("--price-period", default=DEFAULT_PRICE_PERIOD)
    parser.add_argument("--price-interval", default=DEFAULT_PRICE_INTERVAL)
    parser.add_argument(
        "--horizon",
        dest="horizons",
        action="append",
        type=int,
        help="Reaction horizon in minutes. Can be provided multiple times.",
    )
    args = parser.parse_args()
    args.horizons = args.horizons or list(DEFAULT_HORIZONS)
    return args


def _count_nonempty_lines(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


if __name__ == "__main__":
    main()
