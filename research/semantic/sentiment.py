"""Sentiment scorers — the baseline that H1's novelty measure must beat.

H1 claims semantic *novelty* predicts reaction magnitude better than sentiment *polarity*.
That claim is only meaningful against a sentiment baseline that is given a fair chance, so
two are provided behind one interface:

  LexiconSentiment - deterministic, dependency-free, directional. Fast enough to score the
                     whole archive, and fully auditable (every score traces to matched terms).
  FinBertSentiment - ProsusAI/finbert, a domain-adapted transformer. The strong baseline.
                     Optional: falls back rather than failing when transformers is absent.

Both return a polarity in [-1, 1] and a confidence in [0, 1]. Polarity is expressed *toward
the base currency of the pair*, matching how the production engine scores sentiment.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, Sequence

# Terms that move a currency's valuation. Weights are deliberately coarse: this is a baseline,
# and over-tuning a lexicon on the same data that tests H1 would bias the comparison.
POSITIVE_TERMS: dict[str, float] = {
    "surge": 1.0, "surges": 1.0, "rally": 1.0, "rallies": 1.0, "jump": 0.8, "jumps": 0.8,
    "gain": 0.7, "gains": 0.7, "rise": 0.6, "rises": 0.6, "climb": 0.6, "climbs": 0.6,
    "strengthen": 0.9, "strengthens": 0.9, "strong": 0.7, "stronger": 0.8, "boost": 0.8,
    "beat": 0.8, "beats": 0.8, "upgrade": 0.9, "upgraded": 0.9, "recovery": 0.7,
    "rebound": 0.8, "rebounds": 0.8, "optimism": 0.7, "inflow": 0.8, "inflows": 0.8,
    "hawkish": 0.7, "tighten": 0.6, "expansion": 0.7, "surplus": 0.7, "resilient": 0.6,
    "stabilize": 0.5, "stabilise": 0.5, "steady": 0.4, "record high": 0.9, "accelerate": 0.7,
}
NEGATIVE_TERMS: dict[str, float] = {
    "plunge": -1.0, "plunges": -1.0, "crash": -1.0, "crashes": -1.0, "slump": -0.9,
    "slumps": -0.9, "tumble": -0.9, "tumbles": -0.9, "fall": -0.6, "falls": -0.6,
    "drop": -0.6, "drops": -0.6, "decline": -0.6, "declines": -0.6, "weaken": -0.9,
    "weakens": -0.9, "weak": -0.7, "weaker": -0.8, "miss": -0.8, "misses": -0.8,
    "downgrade": -0.9, "downgraded": -0.9, "recession": -1.0, "slowdown": -0.8,
    "outflow": -0.8, "outflows": -0.8, "deficit": -0.6, "crisis": -1.0, "dovish": -0.6,
    "contraction": -0.8, "pessimism": -0.7, "volatility": -0.5, "record low": -0.9,
    "pressure": -0.5, "concerns": -0.5, "fears": -0.7, "risk-off": -0.8, "selloff": -0.9,
}

# Multi-word patterns whose meaning inverts their component words. Checked first; the words
# they consume are then excluded, so "deficit narrows" does not also score "deficit".
PHRASE_OVERRIDES: tuple[tuple[str, float], ...] = (
    ("deficit narrows", 0.8), ("deficit narrowed", 0.8), ("deficit shrinks", 0.8),
    ("inflation eases", 0.7), ("inflation cools", 0.7), ("inflation slows", 0.7),
    ("unemployment falls", 0.8), ("unemployment drops", 0.8),
    ("outflows slow", 0.6), ("growth accelerates", 0.9),
    ("deficit widens", -0.8), ("deficit widened", -0.8), ("surplus narrows", -0.7),
    ("growth slows", -0.8), ("recession fears", -1.0), ("rate cut fears", -0.7),
)

_PATTERN_CACHE: dict[str, re.Pattern] = {}


def _boundary(term: str) -> re.Pattern:
    cached = _PATTERN_CACHE.get(term)
    if cached is None:
        cached = re.compile(rf"(?<![a-z]){re.escape(term)}(?![a-z])", re.IGNORECASE)
        _PATTERN_CACHE[term] = cached
    return cached


@dataclass(frozen=True)
class SentimentScore:
    polarity: float          # -1..1
    confidence: float        # 0..1, rises with the amount of matched evidence
    matched: int             # number of contributing terms/phrases


class SentimentScorer(Protocol):
    name: str

    def score_many(self, texts: Sequence[str]) -> list[SentimentScore]:
        ...


class LexiconSentiment:
    """Deterministic weighted-lexicon scorer with phrase-level negation handling."""

    name = "lexicon"

    def score(self, text: str) -> SentimentScore:
        lowered = str(text).casefold()
        weights: list[float] = []
        consumed: set[str] = set()

        for phrase, weight in PHRASE_OVERRIDES:
            if phrase in lowered:
                weights.append(weight)
                consumed.update(phrase.split())

        for table in (POSITIVE_TERMS, NEGATIVE_TERMS):
            for term, weight in table.items():
                if term in consumed:
                    continue
                if _boundary(term).search(text):
                    weights.append(weight)

        if not weights:
            return SentimentScore(polarity=0.0, confidence=0.0, matched=0)

        # Mean weight gives direction; density gives confidence. Averaging (rather than
        # summing) stops long headlines from scoring artificially extreme.
        polarity = sum(weights) / len(weights)
        confidence = min(1.0, 0.3 + 0.14 * len(weights))
        return SentimentScore(
            polarity=max(-1.0, min(1.0, polarity)),
            confidence=confidence,
            matched=len(weights),
        )

    def score_many(self, texts: Sequence[str]) -> list[SentimentScore]:
        return [self.score(text) for text in texts]


class FinBertSentiment:
    """ProsusAI/finbert. The strong sentiment baseline; requires `transformers`."""

    name = "finbert"

    def __init__(self, model_name: str = "ProsusAI/finbert", batch_size: int = 32) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self._pipeline = None

    def _ensure_pipeline(self):
        if self._pipeline is None:
            from transformers import pipeline
            self._pipeline = pipeline(
                "sentiment-analysis", model=self.model_name, truncation=True, max_length=128
            )
        return self._pipeline

    def score_many(self, texts: Sequence[str]) -> list[SentimentScore]:
        classifier = self._ensure_pipeline()
        outputs = classifier(list(texts), batch_size=self.batch_size)
        scores: list[SentimentScore] = []
        for output in outputs:
            label = str(output["label"]).lower()
            confidence = float(output["score"])
            sign = 1.0 if label.startswith("pos") else (-1.0 if label.startswith("neg") else 0.0)
            scores.append(
                SentimentScore(polarity=sign * confidence, confidence=confidence, matched=1)
            )
        return scores


def build_scorer(name: str) -> SentimentScorer:
    """Resolve a scorer by name, degrading to the lexicon when FinBERT is unavailable."""
    if name == "finbert":
        try:
            import transformers  # noqa: F401
            return FinBertSentiment()
        except ImportError:
            print("transformers unavailable; falling back to the lexicon scorer")
    return LexiconSentiment()
