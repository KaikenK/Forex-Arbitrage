from __future__ import annotations

import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent
from training.labels.event_alignment import EventAlignment
from training.labels.pair_mapper import PairMapper
from training.labels.price_window_labeler import PriceWindowLabeler
from training.models import DatasetSplit
from training.models import MarketReactionTrainingRow
from training.models import PriceBar


def make_event(*, timestamp: datetime, symbols: list[str]) -> UnifiedNewsEvent:
    return UnifiedNewsEvent(
        event_id="event-1",
        schema_version=1,
        source=SourceType.RSS,
        headline="EUR/USD climbs after CPI release",
        body="GBP/USD also reacts to the macro surprise.",
        published_at=timestamp,
        symbols=symbols,
        raw_sentiment=0.4,
        sentiment_source=SentimentSource.NONE,
        ingested_at=timestamp,
    )


def test_price_window_labeler_generates_rows_for_multiple_pairs_and_horizons() -> None:
    labeler = PriceWindowLabeler(EventAlignment(), PairMapper())
    event_timestamp = datetime(2026, 3, 25, 12, 0, 30, tzinfo=UTC)
    event = make_event(timestamp=event_timestamp, symbols=["EUR/USD"])
    price_history = {
        "EUR/USD": [
            PriceBar(symbol="EUR/USD", timestamp=datetime(2026, 3, 25, 12, 1, 0, tzinfo=UTC), close=1.1000),
            PriceBar(symbol="EUR/USD", timestamp=datetime(2026, 3, 25, 12, 6, 0, tzinfo=UTC), close=1.1022),
            PriceBar(symbol="EUR/USD", timestamp=datetime(2026, 3, 25, 12, 16, 0, tzinfo=UTC), close=1.1030),
        ],
        "GBP/USD": [
            PriceBar(symbol="GBP/USD", timestamp=datetime(2026, 3, 25, 12, 1, 0, tzinfo=UTC), close=1.2700),
            PriceBar(symbol="GBP/USD", timestamp=datetime(2026, 3, 25, 12, 6, 0, tzinfo=UTC), close=1.2690),
            PriceBar(symbol="GBP/USD", timestamp=datetime(2026, 3, 25, 12, 16, 0, tzinfo=UTC), close=1.2710),
        ],
    }

    rows = labeler.build_training_rows(
        event=event,
        candidate_symbols=["GBP/USD"],
        price_history=price_history,
        horizons_minutes=[5, 15],
        source_metadata={"source": "rss", "event_id": event.event_id},
    )

    assert len(rows) == 4
    assert {(row.pair, row.horizon_minutes) for row in rows} == {
        ("EUR/USD", 5),
        ("EUR/USD", 15),
        ("GBP/USD", 5),
        ("GBP/USD", 15),
    }
    assert all(row.article_text.startswith("EUR/USD climbs after CPI release") for row in rows)
    assert all(row.sentiment_label == 0.4 for row in rows)

    eur_5m_row = next(row for row in rows if row.pair == "EUR/USD" and row.horizon_minutes == 5)
    gbp_5m_row = next(row for row in rows if row.pair == "GBP/USD" and row.horizon_minutes == 5)

    assert eur_5m_row.reaction_label > 0
    assert eur_5m_row.magnitude_label == abs(eur_5m_row.reaction_label)
    assert gbp_5m_row.reaction_label < 0


def test_time_aware_split_orders_rows_by_timestamp_without_randomization() -> None:
    labeler = PriceWindowLabeler(EventAlignment(), PairMapper())
    rows = [
        MarketReactionTrainingRow(
            article_text=f"row-{index}",
            timestamp=datetime(2026, 3, 25, 12, index, tzinfo=UTC),
            pair="EUR/USD",
            horizon_minutes=5,
            sentiment_label=0.0,
            reaction_label=0.01 * index,
            magnitude_label=0.01 * index,
            source_metadata={"source": "rss"},
        )
        for index in range(6)
    ]

    split_rows = labeler.time_aware_split(rows, train_ratio=0.5, validation_ratio=0.25)

    assert [split_row.row.article_text for split_row in split_rows] == [
        "row-0",
        "row-1",
        "row-2",
        "row-3",
        "row-4",
        "row-5",
    ]
    assert [split_row.split for split_row in split_rows] == [
        DatasetSplit.TRAIN,
        DatasetSplit.TRAIN,
        DatasetSplit.TRAIN,
        DatasetSplit.VALIDATION,
        DatasetSplit.TEST,
        DatasetSplit.TEST,
    ]


def test_price_window_labeler_skips_rows_with_untrustworthy_alignment_gaps() -> None:
    labeler = PriceWindowLabeler(EventAlignment(), PairMapper())
    event_timestamp = datetime(2026, 3, 25, 12, 0, 0, tzinfo=UTC)
    event = make_event(timestamp=event_timestamp, symbols=["USD/INR"])
    price_history = {
        "USD/INR": [
            PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 25, 12, 10, 0, tzinfo=UTC), close=83.10),
            PriceBar(symbol="USD/INR", timestamp=datetime(2026, 3, 25, 12, 15, 0, tzinfo=UTC), close=83.18),
        ],
    }

    rows = labeler.build_training_rows(
        event=event,
        candidate_symbols=["USD/INR"],
        price_history=price_history,
        horizons_minutes=[5],
        source_metadata={"source": "rss", "event_id": event.event_id},
    )

    assert rows == []
