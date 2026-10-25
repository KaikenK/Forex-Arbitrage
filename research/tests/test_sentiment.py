"""Tests for the sentiment baselines used in the H1 comparison."""
from __future__ import annotations

from research.semantic.sentiment import LexiconSentiment, build_scorer


def test_directional_scoring():
    scorer = LexiconSentiment()
    assert scorer.score("Rupee surges as inflows strengthen the currency").polarity > 0
    assert scorer.score("Rupee plunges to record low amid outflows").polarity < 0
    assert scorer.score("RBI meeting scheduled for Thursday").polarity == 0.0


def test_phrase_override_inverts_component_words():
    """'deficit narrows' is positive even though 'deficit' alone is negative."""
    scorer = LexiconSentiment()
    assert scorer.score("India trade deficit narrows sharply").polarity > 0
    assert scorer.score("India trade deficit widens sharply").polarity < 0
    assert scorer.score("India trade deficit hits new high").polarity < 0


def test_confidence_rises_with_evidence():
    scorer = LexiconSentiment()
    weak = scorer.score("Rupee gains")
    strong = scorer.score("Rupee surges and rallies as inflows boost a strong recovery")
    assert strong.confidence > weak.confidence
    assert strong.matched > weak.matched


def test_word_boundaries_prevent_false_matches():
    scorer = LexiconSentiment()
    # "fall" must not match inside "fallout"; "gain" must not match "against"
    assert scorer.score("Nuclear fallout discussed against a backdrop").matched == 0


def test_polarity_is_bounded_and_averaged():
    scorer = LexiconSentiment()
    result = scorer.score("crash crash crash plunge slump tumble recession crisis")
    assert -1.0 <= result.polarity <= 1.0
    # averaging, not summing, keeps a long negative headline from exceeding the bound
    assert result.polarity < -0.5


def test_mixed_sentiment_lands_near_neutral():
    scorer = LexiconSentiment()
    result = LexiconSentiment().score("Rupee gains but outflows weaken the recovery outlook")
    assert result.matched >= 2
    assert -0.6 < result.polarity < 0.6


def test_build_scorer_falls_back_without_transformers():
    scorer = build_scorer("finbert")
    assert scorer.name in {"finbert", "lexicon"}   # either is acceptable
    assert scorer.score_many(["Rupee surges"])[0].polarity != 0.0


def test_score_many_matches_score():
    scorer = LexiconSentiment()
    texts = ["Rupee surges", "Rupee plunges", "No signal here"]
    batch = scorer.score_many(texts)
    assert [b.polarity for b in batch] == [scorer.score(t).polarity for t in texts]
