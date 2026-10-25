"""M1 demo: build the first real USD/INR event-impact atlas from local events + yfinance.

Run:  python -m research.build_atlas_demo
Uses only free, no-key data (yfinance daily) + the existing news_stream event JSONL.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from research.data import load_events, load_price_series
from research.eventstudy import align_events, build_atlas

# Daily horizons (in minutes): 1, 2, 5 trading days.
HORIZONS = [1 * 1440, 2 * 1440, 5 * 1440]
EVENTS_PATH = (
    Path(__file__).resolve().parents[1].parent
    / "news_stream" / "data" / "usdinr_timestamped_events_targeted_expanded.jsonl"
)
PAIR_TICKER = {"USD/INR": "USDINR=X"}


def main() -> None:
    events = load_events(EVENTS_PATH)
    if not events:
        print(f"No events loaded from {EVENTS_PATH}")
        return
    start = min(e.timestamp for e in events)
    end = max(e.timestamp for e in events)
    print(f"Loaded {len(events)} events  |  {start.date()} .. {end.date()}")

    series_by_pair = {}
    for pair, ticker in PAIR_TICKER.items():
        try:
            series_by_pair[pair] = load_price_series(ticker, pair, start, end)
            print(f"Prices {pair} ({ticker}): {len(series_by_pair[pair].times)} daily bars")
        except Exception as exc:  # network / provider failure
            print(f"Price load failed for {pair}: {exc}")

    observations = align_events(
        events, series_by_pair, HORIZONS, max_entry_lag_minutes=4 * 1440
    )
    print(f"Aligned {len(observations)}/{len(events)} events (rest dropped: no/unmatured price)\n")

    atlas = build_atlas(observations, HORIZONS, mean_adjust=True)
    _print_atlas(atlas)


def _print_atlas(atlas) -> None:
    header = f"{'category':<16}{'horizon':>8}{'n':>4}{'meanAR_bps':>12}{'|AR|_bps':>10}{'t':>7}{'p':>8}{'hit':>6}"
    print(header)
    print("-" * len(header))
    for category in sorted(atlas):
        impact = atlas[category]
        for horizon in sorted(impact.horizons):
            h = impact.horizons[horizon]
            days = horizon // 1440
            star = " *" if h.p_value < 0.05 else ""
            print(
                f"{category:<16}{str(days) + 'd':>8}{h.n:>4}{h.mean_return_bps:>12.1f}"
                f"{h.mean_abs_return_bps:>10.1f}{h.t_stat:>7.2f}{h.p_value:>8.3f}{h.hit_rate:>6.2f}{star}"
            )
        if impact.half_life_minutes is not None:
            print(f"{'  half-life':<16}{str(impact.half_life_minutes / 1440) + 'd':>8}")
    print("\n(* = mean abnormal return significant at p<0.05; AR = vs sample-mean of all events)")


if __name__ == "__main__":
    main()
