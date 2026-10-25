from __future__ import annotations

from dataclasses import replace

from news.models import UnifiedNewsEvent
from typing import Any

from ..models import DatasetSplit
from ..models import MarketReactionTrainingRow
from ..models import PriceBar
from ..models import TimeSplitTrainingRow
from .event_alignment import EventAlignment
from .pair_mapper import PairMapper


class PriceWindowLabeler:
    def __init__(self, event_alignment: EventAlignment, pair_mapper: PairMapper) -> None:
        """Initializes the price-window label generation pipeline."""
        self._event_alignment = event_alignment
        self._pair_mapper = pair_mapper

    def build_training_rows(
        self,
        event: UnifiedNewsEvent,
        candidate_symbols: list[str],
        price_history: dict[str, list[PriceBar]],
        horizons_minutes: list[int],
        source_metadata: dict[str, str],
        row_overrides: dict[str, Any] | None = None,
    ) -> list[MarketReactionTrainingRow]:
        """Builds market-reaction training rows for one article."""
        article_text = self._article_text(event)
        pairs = self._pair_mapper.map_pairs(event.symbols, candidate_symbols)
        rows: list[MarketReactionTrainingRow] = []
        resolved_row_overrides = dict(row_overrides or {})

        for pair in pairs:
            pair_history = price_history.get(pair, [])
            for horizon_minutes in horizons_minutes:
                aligned_window = self._event_alignment.align(
                    event.published_at,
                    pair_history,
                    horizon_minutes,
                )
                if aligned_window is None:
                    continue

                # Bit-identical closes mean the price feed forward-filled a missing
                # bar, not that the market stood perfectly still -- two consecutive
                # closes matching to full float precision on a live FX pair does not
                # happen. Labelling these as "flat" injects fake zero-return rows:
                # 71 of 624 rows were affected, all at the trailing edge of the data
                # where the feed had nothing left to report. Fail closed instead.
                if aligned_window.start_bar.close == aligned_window.end_bar.close:
                    continue

                reaction_label = self._reaction_label(
                    aligned_window.end_bar.close,
                    aligned_window.start_bar.close,
                )
                # Record the prices the label was derived from, per horizon. Without
                # these a row states a return with nothing to check it against; with
                # them the dataset is auditable -- you can recompute the label, and
                # confirm window_start_at >= the event timestamp, which is the
                # leak-safety property that alignment is supposed to guarantee.
                row_metadata = dict(source_metadata)
                row_metadata.update(
                    {
                        "window_start_price": aligned_window.start_bar.close,
                        "window_end_price": aligned_window.end_bar.close,
                        "window_start_at": aligned_window.start_bar.timestamp.isoformat(),
                        "window_end_at": aligned_window.end_bar.timestamp.isoformat(),
                    },
                )
                rows.append(
                    MarketReactionTrainingRow(
                        article_text=article_text,
                        timestamp=event.published_at,
                        pair=pair,
                        horizon_minutes=horizon_minutes,
                        sentiment_label=event.raw_sentiment,
                        reaction_label=reaction_label,
                        magnitude_label=abs(reaction_label),
                        source_metadata=row_metadata,
                        **resolved_row_overrides,
                    ),
                )

        return rows

    def time_aware_split(
        self,
        rows: list[MarketReactionTrainingRow],
        train_ratio: float = 0.7,
        validation_ratio: float = 0.15,
    ) -> list[TimeSplitTrainingRow]:
        """Assigns time-aware splits to labeled rows without randomization."""
        if not 0.0 < train_ratio < 1.0:
            raise ValueError("train_ratio must be between 0 and 1.")
        if not 0.0 <= validation_ratio < 1.0:
            raise ValueError("validation_ratio must be between 0 and 1.")
        if train_ratio + validation_ratio >= 1.0:
            raise ValueError("train_ratio + validation_ratio must be less than 1.")

        ordered_rows = sorted(rows, key=lambda row: row.timestamp)
        total_rows = len(ordered_rows)
        train_cutoff = int(total_rows * train_ratio)
        validation_cutoff = int(total_rows * (train_ratio + validation_ratio))

        split_rows: list[TimeSplitTrainingRow] = []
        for index, row in enumerate(ordered_rows):
            if index < train_cutoff:
                split = DatasetSplit.TRAIN
            elif index < validation_cutoff:
                split = DatasetSplit.VALIDATION
            else:
                split = DatasetSplit.TEST
            split_rows.append(TimeSplitTrainingRow(row=row, split=split))

        return split_rows

    def _article_text(self, event: UnifiedNewsEvent) -> str:
        body = event.body or ""
        return " ".join(part for part in (event.headline, body) if part)

    def _reaction_label(self, end_price: float, start_price: float) -> float:
        if start_price <= 0:
            raise ValueError("start_price must be positive.")
        return (end_price - start_price) / start_price
