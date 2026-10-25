from __future__ import annotations

import json
import sys
from pathlib import Path
from datetime import UTC
from datetime import datetime
from datetime import timedelta

import httpx
import pytest
import respx
from pydantic import SecretStr

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

import news.enricher.budget as budget_module
import news.enricher.sentiment_enricher as sentiment_enricher_module
from news.enricher.alpha_vantage_enricher import AlphaVantageEnricher
from news.enricher.budget import AlphaVantageBudget
from news.enricher.sentiment_enricher import SentimentEnricher
from news.models import SentimentSource
from news.models import SourceType
from news.models import UnifiedNewsEvent

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"


class FakeAlphaVantageEnricher:
    def __init__(self, scores: list[float | None]) -> None:
        self._scores = scores
        self.calls: list[tuple[str, str]] = []

    async def enrich_score(self, symbol: str, headline: str) -> float | None:
        self.calls.append((symbol, headline))
        return self._scores.pop(0)


class FakeFinBERTEnricher:
    def __init__(self, score_value: float) -> None:
        self._score_value = score_value
        self.calls: list[str] = []

    async def score(self, text: str) -> float:
        self.calls.append(text)
        return self._score_value


def make_event(*, published_at: datetime, symbols: list[str]) -> UnifiedNewsEvent:
    return UnifiedNewsEvent(
        event_id="event-1",
        schema_version=1,
        source=SourceType.FINNHUB,
        headline="AAPL beats expectations",
        body="Revenue rose sharply.",
        published_at=published_at,
        symbols=symbols,
        raw_sentiment=0.0,
        sentiment_source=SentimentSource.NONE,
        ingested_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
@respx.mock
async def test_alpha_vantage_budget_returns_none_on_twenty_sixth_call(tmp_path: Path) -> None:
    budget = AlphaVantageBudget(state_file=tmp_path / "av_budget.json", daily_limit=25)
    enricher = AlphaVantageEnricher(api_key=SecretStr("demo"), budget=budget)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"feed": [{"title": "AAPL beats expectations", "overall_sentiment_score": "0.9"}]},
            request=request,
        )

    route = respx.get(ALPHA_VANTAGE_URL).mock(side_effect=handler)

    scores = [await enricher.enrich_score("AAPL", "AAPL beats expectations") for _ in range(25)]
    exhausted_score = await enricher.enrich_score("AAPL", "AAPL beats expectations")

    assert all(score == pytest.approx(0.8) for score in scores)
    assert exhausted_score is None
    assert route.call_count == 25


@pytest.mark.asyncio
async def test_alpha_vantage_budget_resets_on_new_utc_date(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    budget = AlphaVantageBudget(state_file=tmp_path / "av_budget.json", daily_limit=25)

    monkeypatch.setattr(budget_module, "utc_today", lambda: "2026-03-23")
    await budget.record_call()

    monkeypatch.setattr(budget_module, "utc_today", lambda: "2026-03-24")

    assert await budget.can_call() is True

    state = json.loads((tmp_path / "av_budget.json").read_text(encoding="utf-8"))
    assert state == {"date": "2026-03-24", "count": 0}


def test_alpha_vantage_budget_status_reports_usage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    budget = AlphaVantageBudget(state_file=tmp_path / "av_budget.json", daily_limit=25)
    monkeypatch.setattr(budget_module, "utc_today", lambda: "2026-03-23")
    (tmp_path / "av_budget.json").write_text('{"date": "2026-03-23", "count": 3}', encoding="utf-8")

    status = budget.get_budget_status()

    assert status["used"] == 3
    assert status["remaining"] == 22
    assert status["resets_at"] == "2026-03-24T00:00:00+00:00"


@pytest.mark.asyncio
async def test_sentiment_enricher_uses_alpha_vantage_then_finbert(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = datetime(2026, 3, 23, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(sentiment_enricher_module, "utc_now", lambda: current_time)

    fresh_event = make_event(published_at=current_time - timedelta(seconds=60), symbols=["AAPL"])

    av_primary = FakeAlphaVantageEnricher([0.6])
    finbert_primary = FakeFinBERTEnricher(0.2)
    primary_enricher = SentimentEnricher(av_primary, finbert_primary, priority_symbols=["AAPL"])

    av_event = await primary_enricher.enrich(fresh_event)

    assert av_event.raw_sentiment == pytest.approx(0.6)
    assert av_event.sentiment_source == SentimentSource.ALPHA_VANTAGE
    assert av_primary.calls == [("AAPL", "AAPL beats expectations")]
    assert finbert_primary.calls == []

    av_exhausted = FakeAlphaVantageEnricher([None])
    finbert_fallback = FakeFinBERTEnricher(0.25)
    fallback_enricher = SentimentEnricher(av_exhausted, finbert_fallback, priority_symbols=["AAPL"])

    finbert_event = await fallback_enricher.enrich(fresh_event)

    assert finbert_event.raw_sentiment == pytest.approx(0.25)
    assert finbert_event.sentiment_source == SentimentSource.FINBERT
    assert av_exhausted.calls == [("AAPL", "AAPL beats expectations")]
    assert finbert_fallback.calls == ["AAPL beats expectations Revenue rose sharply."]


@pytest.mark.asyncio
async def test_sentiment_enricher_skips_old_events(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = datetime(2026, 3, 23, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(sentiment_enricher_module, "utc_now", lambda: current_time)

    event = make_event(published_at=current_time - timedelta(seconds=601), symbols=["AAPL"])
    av_enricher = FakeAlphaVantageEnricher([0.9])
    finbert = FakeFinBERTEnricher(0.4)

    enricher = SentimentEnricher(av_enricher, finbert, priority_symbols=["AAPL"], max_age_seconds=300)
    enriched_event = await enricher.enrich(event)

    assert enriched_event == event
    assert av_enricher.calls == []
    assert finbert.calls == []
