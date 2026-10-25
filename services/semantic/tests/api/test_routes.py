from __future__ import annotations

import json
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from api.dashboard_service import DashboardDataService
from api.main import build_app
from api.schemas import CorrelationResponse
from market.analyzer import UnifiedArticleAnalyzer
from market.live_sentiment import LiveSentimentSnapshot
from market.models import KeyDriver
from market.models import MarketReactionSignal
from market.schemas import AnalysisContext
from market.schemas import ArticleInput
from market.schemas import SentimentInference
from market.service import UnifiedArticleAnalysisService
from news.models import SentimentSource
from retrieval.interfaces import EvidenceRetriever
from retrieval.models import EvidenceType
from retrieval.models import RetrievedEvidence


class FakeSentimentScorer:
    async def score(self, context: AnalysisContext) -> SentimentInference:
        """Returns a fixed sentiment score for API tests."""
        return SentimentInference(
            score=0.4,
            confidence=0.75,
            source=SentimentSource.FINBERT,
            key_drivers=[
                KeyDriver(
                    label="growth surprise",
                    rationale="The article implies stronger growth momentum.",
                    evidence_ids=["hist-1"],
                ),
            ],
        )


class FakeReactionScorer:
    async def score(self, context: AnalysisContext) -> list[MarketReactionSignal]:
        """Returns a fixed reaction signal for API tests."""
        symbol = context.event.symbols[0] if context.event.symbols else "EUR/USD"
        return [
            MarketReactionSignal(
                event_id=context.event.event_id,
                symbol=symbol,
                direction=1.0,
                magnitude=0.5,
                confidence=0.65,
                horizon_minutes=60,
                key_drivers=["relative rate repricing"],
                produced_at=context.event.ingested_at,
            ),
        ]


class FakeEvidenceRetriever(EvidenceRetriever):
    async def retrieve(self, context: object, signals: object) -> list[RetrievedEvidence]:
        """Returns fixed evidence for API tests."""
        del context, signals
        return [
            RetrievedEvidence(
                evidence_id="hist-1",
                source="archive",
                evidence_type=EvidenceType.ARTICLE,
                title="Prior euro rally on growth surprise",
                snippet="A similar macro surprise supported the euro in a prior session.",
                url=None,
                published_at=datetime(2026, 3, 20, 8, 0, tzinfo=UTC),
                relevance_score=0.77,
            ),
        ]


def _build_service() -> UnifiedArticleAnalysisService:
    analyzer = UnifiedArticleAnalyzer(
        sentiment_scorer=FakeSentimentScorer(),
        reaction_scorer=FakeReactionScorer(),
        evidence_retriever=FakeEvidenceRetriever(),
        clock=lambda: datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
    )
    return UnifiedArticleAnalysisService(
        analyzer=analyzer,
        clock=lambda: datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
    )


class FakeDashboardDataService(DashboardDataService):
    def __init__(self) -> None:
        super().__init__(
            evaluation_payload={"direction_accuracy": 0.5, "direction_macro_f1": 0.4},
            training_table_path=None,
            live_aggregator=None,
        )

    def get_correlation_page(self, *, offset: int, limit: int) -> CorrelationResponse:
        return CorrelationResponse(
            total=3,
            offset=offset,
            limit=limit,
            items=[
                {
                    "pair": "USD/INR",
                    "reaction_label": -0.001,
                    "timestamp": "2026-03-25T12:00:00+00:00",
                },
            ],
        )

    async def get_live_sentiment(
        self,
        *,
        pair: str,
        window_minutes: int,
    ) -> LiveSentimentSnapshot | None:
        return LiveSentimentSnapshot(
            pair=pair,
            overall_sentiment=0.12,
            confidence=0.67,
            bullish_count=2,
            bearish_count=1,
            neutral_count=0,
            item_count=3,
            top_drivers=[
                {
                    "headline": "RBI update",
                    "sentiment": "+0.45",
                    "source": "RSS",
                    "category": "central_bank",
                },
            ],
            updated_at=datetime(2026, 3, 25, 12, 0, tzinfo=UTC),
            window_minutes=window_minutes,
        )

    def get_sentiment_bias(
        self,
        *,
        pair: str,
        from_timestamp: datetime,
        to_timestamp: datetime,
        aggregate_interval_minutes: int,
        horizon_minutes: int,
    ) -> dict[str, object]:
        return {
            "pair": pair,
            "from_timestamp": from_timestamp,
            "to_timestamp": to_timestamp,
            "aggregate_interval_minutes": aggregate_interval_minutes,
            "horizon_minutes": horizon_minutes,
            "bins": [
                {
                    "timestamp": datetime(2026, 3, 25, 10, 0, tzinfo=UTC),
                    "bias_score": 0.21,
                    "confidence": 0.61,
                    "average_reaction": 0.0002,
                    "average_surprise": 0.3,
                    "bullish_count": 2,
                    "bearish_count": 0,
                    "neutral_count": 0,
                    "item_count": 2,
                },
            ],
            "summary": {
                "average_bias_score": 0.21,
                "average_confidence": 0.61,
                "average_reaction": 0.0002,
                "average_surprise": 0.3,
                "total_items": 2,
                "bullish_ratio": 1.0,
                "bearish_ratio": 0.0,
                "neutral_ratio": 0.0,
            },
        }


async def _request(
    app: object,
    method: str,
    url: str,
    json_body: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    split_result = urlsplit(url)
    body = b""
    headers = [(b"content-type", b"application/json")]
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")

    scope = {
        "type": "http",
        "method": method,
        "path": split_result.path,
        "query_string": split_result.query.encode("utf-8"),
        "headers": headers,
    }

    sent_messages: list[dict[str, object]] = []
    receive_messages = [{"type": "http.request", "body": body, "more_body": False}]

    async def receive() -> dict[str, object]:
        if receive_messages:
            return receive_messages.pop(0)
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent_messages.append(message)

    await app(scope, receive, send)

    start_message = sent_messages[0]
    body_message = sent_messages[1]
    status_code = int(start_message["status"])
    payload = json.loads(body_message["body"].decode("utf-8"))
    return status_code, payload


@pytest.mark.asyncio
async def test_analyze_route_returns_json_analysis() -> None:
    app = build_app(_build_service())

    status_code, payload = await _request(
        app,
        "POST",
        "/analyze",
        {
            "article_text": "Euro rises versus the dollar after growth data surprises to the upside.",
            "currency_pair": "EUR/USD",
            "source": "RSS",
        },
    )

    assert status_code == 200
    result = payload["result"]
    assert result["affected_symbols"] == ["EUR/USD"]
    assert result["sentiment_score"] == pytest.approx(0.4)
    assert result["market_score"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_analyze_route_validates_request_body() -> None:
    app = build_app(_build_service())

    status_code, payload = await _request(app, "POST", "/analyze", {"headline": "Missing text"})

    assert status_code == 400
    assert payload["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_history_route_returns_prior_analyses() -> None:
    service = _build_service()
    await service.analyze_article(
        ArticleInput(
            article_text="Euro rises against the dollar on positive growth data.",
            currency_pair="EUR/USD",
        ),
    )
    app = build_app(service)

    status_code, payload = await _request(app, "GET", "/history?limit=1")

    assert status_code == 200
    assert len(payload["items"]) == 1


@pytest.mark.asyncio
async def test_health_and_metrics_routes_return_json() -> None:
    service = _build_service()
    app = build_app(service)

    health_status, health_payload = await _request(app, "GET", "/health")
    metrics_status, metrics_payload = await _request(app, "GET", "/metrics")

    assert health_status == 200
    assert health_payload["health"]["status"] == "HEALTHY"
    assert metrics_status == 200
    assert metrics_payload["metrics"]["analysis_requests"] == 0


@pytest.mark.asyncio
async def test_dashboard_routes_return_json() -> None:
    app = build_app(_build_service(), FakeDashboardDataService())

    evaluation_status, evaluation_payload = await _request(app, "GET", "/evaluation")
    live_status, live_payload = await _request(app, "GET", "/sentiment/live?pair=USD/INR&window_minutes=15")
    bias_status, bias_payload = await _request(
        app,
        "GET",
        "/sentiment/bias?pair=USD/INR&from_timestamp=2026-03-25T09:00:00Z&to_timestamp=2026-03-25T12:00:00Z&aggregate_interval_minutes=60&horizon_minutes=60",
    )
    correlation_status, correlation_payload = await _request(app, "GET", "/correlation?offset=1&limit=2")

    assert evaluation_status == 200
    assert evaluation_payload["direction_accuracy"] == pytest.approx(0.5)
    assert live_status == 200
    assert live_payload["pair"] == "USD/INR"
    assert live_payload["window_minutes"] == 15
    assert bias_status == 200
    assert bias_payload["pair"] == "USD/INR"
    assert bias_payload["summary"]["average_bias_score"] == pytest.approx(0.21)
    assert correlation_status == 200
    assert correlation_payload["offset"] == 1
    assert correlation_payload["limit"] == 2
    assert correlation_payload["total"] == 3


@pytest.mark.asyncio
async def test_dashboard_routes_validate_query_params() -> None:
    app = build_app(_build_service(), FakeDashboardDataService())

    correlation_status, correlation_payload = await _request(app, "GET", "/correlation?offset=abc&limit=2")
    live_status, live_payload = await _request(app, "GET", "/sentiment/live?window_minutes=0")
    bias_status, bias_payload = await _request(
        app,
        "GET",
        "/sentiment/bias?pair=USD/INR&from_timestamp=bad&to_timestamp=2026-03-25T12:00:00Z",
    )

    assert correlation_status == 400
    assert correlation_payload["code"] == "VALIDATION_ERROR"
    assert correlation_payload["message"] == "offset must be an integer."
    assert live_status == 400
    assert live_payload["code"] == "VALIDATION_ERROR"
    assert live_payload["message"] == "window_minutes must be greater than zero."
    assert bias_status == 400
    assert bias_payload["code"] == "VALIDATION_ERROR"
    assert bias_payload["message"] == "from_timestamp must be a valid ISO timestamp."
