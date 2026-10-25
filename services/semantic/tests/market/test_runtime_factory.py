from __future__ import annotations

import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from market.runtime_factory import build_runtime_service
from market.schemas import ArticleInput


def _write_artifact(path: Path) -> None:
    artifact_payload = {
        "hash_dimension": 32,
        "k_neighbors": 1,
        "direction_neutral_threshold": 0.0005,
        "metadata_fields": ["source", "event_category", "macro_event_type", "scheduled"],
        "training_rows": [
            {
                "event_id": "row-rate",
                "pair": "EUR/USD",
                "horizon_minutes": 60,
                "timestamp": "2026-03-20T12:00:00+00:00",
                "direction_label": 1,
                "magnitude_label": 0.42,
                "feature_vector": [
                    0.0,
                    0.2,
                    0.1,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.4,
                    0.5,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                ],
                "event_category": "central-bank",
                "macro_event_type": "rate-decision",
                "scheduled": True,
                "actual": 4.5,
                "previous": 4.5,
                "calendar_source": "federalreserve.gov",
                "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20250129a.htm",
                "source_metadata": {
                    "source": "RSS",
                    "event_category": "central-bank",
                    "macro_event_type": "rate-decision",
                    "scheduled": "true",
                },
            },
            {
                "event_id": "row-cpi",
                "pair": "EUR/USD",
                "horizon_minutes": 60,
                "timestamp": "2026-03-18T12:00:00+00:00",
                "direction_label": -1,
                "magnitude_label": 0.18,
                "feature_vector": [
                    0.0,
                    0.1,
                    0.3,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.2,
                    0.1,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                ],
                "event_category": "macro",
                "macro_event_type": "cpi",
                "scheduled": False,
                "actual": 2.9,
                "previous": 2.7,
                "calendar_source": "bls.gov",
                "source_url": "https://www.bls.gov/news.release/cpi.nr0.htm",
                "source_metadata": {
                    "source": "RSS",
                    "event_category": "macro",
                    "macro_event_type": "cpi",
                    "scheduled": "false",
                },
            },
        ],
    }
    path.write_text(json.dumps(artifact_payload), encoding="utf-8")


def _write_training_table(path: Path) -> None:
    training_rows = [
        {
            "article_text": "ECB signals higher rates and EUR/USD rallies after the decision.",
            "timestamp": "2026-03-20T12:00:00+00:00",
            "pair": "EUR/USD",
            "horizon_minutes": 60,
            "sentiment_label": 0.8,
            "reaction_label": 0.5,
            "magnitude_label": 0.42,
            "event_category": "central-bank",
            "macro_event_type": "rate-decision",
            "scheduled": True,
            "actual": 4.5,
            "previous": 4.5,
            "calendar_source": "federalreserve.gov",
            "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20250129a.htm",
            "source_metadata": {
                "event_id": "row-rate",
                "source": "RSS",
                "event_category": "central-bank",
                "macro_event_type": "rate-decision",
                "scheduled": "true",
            },
        },
        {
            "article_text": "Inflation surprises higher and EUR/USD sells off after CPI.",
            "timestamp": "2026-03-18T12:00:00+00:00",
            "pair": "EUR/USD",
            "horizon_minutes": 60,
            "sentiment_label": -0.5,
            "reaction_label": -0.2,
            "magnitude_label": 0.18,
            "event_category": "macro",
            "macro_event_type": "cpi",
            "scheduled": False,
            "actual": 2.9,
            "previous": 2.7,
            "calendar_source": "bls.gov",
            "source_url": "https://www.bls.gov/news.release/cpi.nr0.htm",
            "source_metadata": {
                "event_id": "row-cpi",
                "source": "RSS",
                "event_category": "macro",
                "macro_event_type": "cpi",
                "scheduled": "false",
            },
        },
    ]
    path.write_text("\n".join(json.dumps(training_row) for training_row in training_rows) + "\n", encoding="utf-8")


def _write_linear_artifact(path: Path) -> None:
    artifact_payload = {
        "hash_dimension": 32,
        "k_neighbors": 1,
        "direction_neutral_threshold": 0.0005,
        "metadata_fields": ["source", "event_category", "macro_event_type", "scheduled"],
        "model_family": "linear",
        "direction_classes": [-1, 0, 1],
        "direction_intercepts": [-1.0, 0.2, 1.4],
        "direction_coefficients": [[0.0] * 32, [0.0] * 32, [0.0] * 32],
        "magnitude_intercept": 0.24,
        "magnitude_coefficients": [0.0] * 32,
        "training_rows": [
            {
                "event_id": "row-rate",
                "pair": "EUR/USD",
                "horizon_minutes": 60,
                "timestamp": "2026-03-20T12:00:00+00:00",
                "direction_label": 1,
                "magnitude_label": 0.42,
                "feature_vector": [0.0] * 32,
                "event_category": "central-bank",
                "macro_event_type": "rate-decision",
                "scheduled": True,
                "actual": 4.5,
                "previous": 4.5,
                "calendar_source": "federalreserve.gov",
                "source_url": "https://www.federalreserve.gov/newsevents/pressreleases/monetary20250129a.htm",
                "source_metadata": {
                    "source": "RSS",
                    "event_category": "central-bank",
                    "macro_event_type": "rate-decision",
                    "scheduled": "true",
                },
            },
        ],
    }
    path.write_text(json.dumps(artifact_payload), encoding="utf-8")


@pytest.mark.asyncio
async def test_build_runtime_service_uses_artifact_and_training_table(tmp_path: Path) -> None:
    artifact_path = tmp_path / "reaction_model_artifact.json"
    training_table_path = tmp_path / "training_rows.jsonl"
    _write_artifact(artifact_path)
    _write_training_table(training_table_path)

    service = build_runtime_service(
        reaction_artifact_path=artifact_path,
        training_table_path=training_table_path,
    )

    analysis = await service.analyze_article(
        ArticleInput(
            article_text="ECB signals higher rates and EUR/USD rallies after the decision.",
            headline="ECB signals higher rates",
            currency_pair="EUR/USD",
            event_category="central-bank",
            macro_event_type="rate-decision",
            scheduled=True,
        ),
    )

    assert "EUR/USD" in analysis.affected_symbols
    assert analysis.market_score >= 0.0
    assert analysis.evidence
    assert analysis.evidence[0].evidence_id == "row-rate"
    assert analysis.evidence[0].macro_event_type == "rate-decision"


@pytest.mark.asyncio
async def test_build_runtime_service_supports_linear_artifact(tmp_path: Path) -> None:
    artifact_path = tmp_path / "reaction_model_artifact_linear.json"
    training_table_path = tmp_path / "training_rows.jsonl"
    _write_linear_artifact(artifact_path)
    _write_training_table(training_table_path)

    service = build_runtime_service(
        reaction_artifact_path=artifact_path,
        training_table_path=training_table_path,
    )

    analysis = await service.analyze_article(
        ArticleInput(
            article_text="ECB signals higher rates and EUR/USD rallies after the decision.",
            headline="ECB signals higher rates",
            currency_pair="EUR/USD",
            event_category="central-bank",
            macro_event_type="rate-decision",
            scheduled=True,
        ),
    )

    assert "EUR/USD" in analysis.affected_symbols
    assert analysis.market_score > 0.0
    assert analysis.evidence
    assert analysis.evidence[0].scheduled is True
    assert analysis.evidence[0].matching_reason == "pair+horizon+event_family+scheduled"
