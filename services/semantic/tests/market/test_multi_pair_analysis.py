"""One /analyze call scores every requested pair, with one sentiment pass.

The expensive step is FinBERT. Scoring extra pairs must not repeat it, and article-level
fields must describe only the primary pair — averaging or conflict-checking across pairs
is wrong when USD sits on opposite sides (EUR/USD down and USD/JPY up are the same move).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_SRC = Path(__file__).resolve().parents[2] / "src"
if str(PROJECT_SRC) not in sys.path:
    sys.path.append(str(PROJECT_SRC))

from api.schemas import ApiRouteError
from api.schemas import parse_analyze_article_request
from market.analyzer import UnifiedArticleAnalyzer
from market.empty_evidence_retriever import EmptyEvidenceRetriever
from market.history_store import article_analysis_from_payload
from market.history_store import JsonlAnalysisHistoryStore
from market.models import MarketReactionSignal
from market.models import RiskFlag
from market.schemas import AnalysisContext
from market.schemas import ArticleInput
from market.schemas import SentimentInference
from market.service import UnifiedArticleAnalysisService
from news.models import SentimentSource

FIXED_NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)

# A strong US jobs beat: the dollar strengthens against everything. In pair terms that
# is EUR/USD DOWN, USD/JPY UP, USD/INR UP — opposite signs, one consistent story.
DOLLAR_STRENGTH = {"USD/INR": 1.0, "EUR/USD": -1.0, "USD/JPY": 1.0, "GBP/USD": -1.0, "USD/CHF": 1.0}


class CountingSentimentScorer:
    def __init__(self) -> None:
        self.calls = 0

    async def score(self, context: AnalysisContext) -> SentimentInference:
        del context
        self.calls += 1
        return SentimentInference(score=0.6, confidence=0.8, source=SentimentSource.FINBERT, key_drivers=[])


class PerPairReactionScorer:
    """One signal per symbol, like ArtifactReactionScorer, with a fixed direction per pair."""

    async def score(self, context: AnalysisContext) -> list[MarketReactionSignal]:
        return [
            MarketReactionSignal(
                event_id=context.event.event_id,
                symbol=symbol,
                direction=DOLLAR_STRENGTH.get(symbol, 0.0),
                magnitude=0.002,
                confidence=0.7,
                horizon_minutes=60,
                key_drivers=[symbol],
                produced_at=FIXED_NOW,
            )
            for symbol in context.event.symbols
        ]


def _service(sentiment: CountingSentimentScorer) -> UnifiedArticleAnalysisService:
    analyzer = UnifiedArticleAnalyzer(
        sentiment_scorer=sentiment,
        reaction_scorer=PerPairReactionScorer(),
        evidence_retriever=EmptyEvidenceRetriever(),
        clock=lambda: FIXED_NOW,
    )
    return UnifiedArticleAnalysisService(analyzer=analyzer)


def _analyze(article: ArticleInput):
    sentiment = CountingSentimentScorer()
    analysis = asyncio.run(_service(sentiment).analyze_article(article))
    return analysis, sentiment


HEADLINE = "US payrolls beat forecasts as wages accelerate"


def test_one_call_scores_every_requested_pair_with_one_sentiment_pass() -> None:
    analysis, sentiment = _analyze(
        ArticleInput(
            article_text=HEADLINE,
            headline=HEADLINE,
            currency_pair="USD/INR",
            currency_pairs=["EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF"],
            published_at=FIXED_NOW,
        )
    )

    assert sentiment.calls == 1, "FinBERT must run once, not once per pair"
    assert set(analysis.pair_scores) == {"USD/INR", "EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF"}
    for pair, expected_sign in DOLLAR_STRENGTH.items():
        assert analysis.pair_scores[pair]["market_score"] == pytest.approx(expected_sign * 0.002)


def test_article_level_fields_describe_the_primary_pair_only() -> None:
    analysis, _ = _analyze(
        ArticleInput(
            article_text=HEADLINE,
            headline=HEADLINE,
            currency_pair="USD/INR",
            currency_pairs=["EUR/USD", "USD/JPY"],
            published_at=FIXED_NOW,
        )
    )

    # Averaging +1, -1, +1 would give +0.00067; the primary pair's own score is +0.002.
    assert analysis.market_score == pytest.approx(analysis.pair_scores["USD/INR"]["market_score"])
    assert analysis.confidence == pytest.approx(analysis.pair_scores["USD/INR"]["confidence"])


def test_opposite_usd_sides_are_not_flagged_as_conflict_or_ambiguity() -> None:
    analysis, _ = _analyze(
        ArticleInput(
            article_text=HEADLINE,
            headline=HEADLINE,
            currency_pair="USD/INR",
            currency_pairs=["EUR/USD", "USD/JPY", "GBP/USD", "USD/CHF"],
            published_at=FIXED_NOW,
        )
    )

    assert RiskFlag.CONFLICTING_EVIDENCE not in analysis.risk_flags
    assert RiskFlag.AMBIGUOUS_PAIR_MAPPING not in analysis.risk_flags


def test_single_pair_request_is_unchanged() -> None:
    analysis, sentiment = _analyze(
        ArticleInput(article_text=HEADLINE, headline=HEADLINE, currency_pair="USD/INR", published_at=FIXED_NOW)
    )

    assert sentiment.calls == 1
    assert list(analysis.pair_scores) == ["USD/INR"]
    assert analysis.market_score == pytest.approx(0.002)


def test_pair_scores_survive_the_history_round_trip(tmp_path: Path) -> None:
    analysis, _ = _analyze(
        ArticleInput(
            article_text=HEADLINE,
            headline=HEADLINE,
            currency_pair="USD/INR",
            currency_pairs=["EUR/USD"],
            published_at=FIXED_NOW,
        )
    )
    store = JsonlAnalysisHistoryStore(tmp_path / "history.jsonl")
    store.append(analysis)

    reloaded = store.load()[0]
    assert reloaded.pair_scores == analysis.pair_scores


def test_legacy_history_record_without_pair_scores_loads() -> None:
    legacy = {
        "event_id": "old",
        "sentiment_score": 0.1,
        "market_score": 0.0005,
        "affected_symbols": ["USD/INR"],
        "confidence": 0.5,
        "produced_at": "2026-04-23T05:00:00+00:00",
    }
    assert article_analysis_from_payload(legacy).pair_scores == {}


def test_api_accepts_and_normalises_currency_pairs() -> None:
    request = parse_analyze_article_request(
        {"article_text": HEADLINE, "currency_pair": "USD/INR", "currency_pairs": ["eur/usd", " USD/JPY "]}
    )
    assert request.to_article_input().currency_pairs == ["EUR/USD", "USD/JPY"]


@pytest.mark.parametrize("bad", ["EUR/USD", [""], [1, 2], ["EUR/USD"] * 13])
def test_api_rejects_malformed_or_oversized_pair_lists(bad: object) -> None:
    with pytest.raises(ApiRouteError):
        parse_analyze_article_request({"article_text": HEADLINE, "currency_pairs": bad})
