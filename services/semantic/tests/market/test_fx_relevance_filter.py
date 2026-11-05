"""Phase 2 SEMANTIC_FX_RELEVANCE_FILTER (default off): HIGH_EVENT_RISK only for a headline tagged with
a study currency, and only from evidence for a pair containing one of its currencies. Also the
per-pair orientation the currency index relies on ("Dollar Index falls")."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from market import currency_bias_core as core
from market.analyzer import FX_RELEVANCE_FILTER_ENV, _is_high_event_risk
from news.models import SentimentSource, SourceType, UnifiedNewsEvent
from retrieval.models import EvidenceType, RetrievedEvidence


def _event(headline: str) -> UnifiedNewsEvent:
    stamp = datetime(2026, 11, 5, tzinfo=UTC)
    return UnifiedNewsEvent(event_id="e", schema_version=1, source=SourceType.RSS, headline=headline, body=None,
                            published_at=stamp, symbols=["USD/INR"], raw_sentiment=0.0,
                            sentiment_source=SentimentSource.NONE, ingested_at=stamp)


def _surprise(pair: str) -> RetrievedEvidence:
    return RetrievedEvidence(evidence_id="ev", source="calendar", evidence_type=EvidenceType.ECONOMIC_CALENDAR,
                             title="release", snippet="", url=None, published_at=None, relevance_score=0.9,
                             pair=pair, standardized_surprise=1.2)


@pytest.fixture(autouse=True)
def _off(monkeypatch):
    monkeypatch.delenv(FX_RELEVANCE_FILTER_ENV, raising=False)


SENSEX = "Sensex, Nifty close higher led by HDFC Bank and Infosys"
KORUNA_CRISIS = "Czech koruna slides as budget crisis deepens"
DOLLAR = "Dollar slips after weaker US payrolls"


def test_off_keeps_todays_rule(monkeypatch):
    assert _is_high_event_risk(_event(SENSEX), [_surprise("USD/INR")]) is True       # surprise evidence alone
    assert _is_high_event_risk(_event(KORUNA_CRISIS), []) is True                    # keyword "crisis"


def test_on_untagged_headlines_never_raise_the_flag(monkeypatch):
    monkeypatch.setenv(FX_RELEVANCE_FILTER_ENV, "1")
    assert _is_high_event_risk(_event(SENSEX), [_surprise("USD/INR")]) is False
    assert _is_high_event_risk(_event(KORUNA_CRISIS), []) is False


def test_on_evidence_must_share_a_currency_with_the_headline(monkeypatch):
    monkeypatch.setenv(FX_RELEVANCE_FILTER_ENV, "1")
    assert _is_high_event_risk(_event(DOLLAR), [_surprise("EUR/USD")]) is True
    assert _is_high_event_risk(_event(DOLLAR), [_surprise("EUR/GBP")]) is False
    assert _is_high_event_risk(_event(DOLLAR), [_surprise(None)]) is False


def test_dollar_index_falls_orientation():
    usd, other = -2.0, 0.0                                   # USD bearish, the rest flat
    assert core.pair_bias(other, usd) > 0                    # EUR/USD, GBP/USD (USD is the quote): up
    assert core.pair_bias(usd, other) < 0                    # USD/JPY, USD/CHF (USD is the base): down
    assert core.bias_label(core.pair_bias(other, usd), threshold=0.5) == "bullish"
    assert core.bias_label(core.pair_bias(usd, other), threshold=0.5) == "bearish"
