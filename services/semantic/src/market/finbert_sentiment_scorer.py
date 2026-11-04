"""FinBERT-backed sentiment scorer with keyword fallback and forex-domain correction."""
from __future__ import annotations

import structlog

from news.enricher.finbert_enricher import FinBERTEnricher
from news.enricher.finbert_enricher import shared_enricher
from news.models import SentimentSource

from .interfaces import EventSentimentScorer
from .keyword_sentiment_scorer import KeywordSentimentScorer
from .keyword_sentiment_scorer import _target_focus_text
from .models import KeyDriver
from .schemas import AnalysisContext
from .schemas import SentimentInference

# Forex-specific phrases that indicate bearish/bullish sentiment even when
# FinBERT (trained on general financial text) classifies them incorrectly.
FOREX_BEARISH_PHRASES = (
    "outflow", "outflows", "capital flight", "net selling",
    "fii selling", "sell-off", "selloff", "sell off",
    "depreciat",  # depreciation, depreciating
    "devaluat",  # devaluation, devaluing
    "widening deficit", "trade deficit", "current account deficit",
    "import bill", "portfolio outflow", "foreign selling",
    "forex drain", "reserve drawdown",
)
FOREX_BULLISH_PHRASES = (
    "inflow", "inflows", "capital inflow", "net buying",
    "fii buying", "appreciat", "trade surplus",
    "current account surplus", "forex reserves climb",
    "reserves rise", "reserves climb", "reserve accumulation",
    "secures oil", "secure supply", "secures supply",
    "oil supply deal", "supply agreement", "energy security",
    "oil stockpile", "strategic reserve",
)

# Contextual phrase overrides: these take absolute priority over individual
# bearish/bullish keyword counts.  Each entry is (phrase, forced_direction).
# +1 forces bullish (positive score), -1 forces bearish (negative score).
CONTEXTUAL_OVERRIDES: tuple[tuple[str, int], ...] = (
    ("deficit narrows", +1),
    ("deficit narrowing", +1),
    ("deficit narrowed", +1),
    ("deficit shrinks", +1),
    ("deficit shrinking", +1),
    ("lower oil imports", +1),
    ("lower imports", +1),
    ("falling imports", +1),
    ("reduced imports", +1),
    ("inflation eases", +1),
    ("inflation cools", +1),
    ("inflation drops", +1),
    ("inflation falls", +1),
    ("inflation slows", +1),
    ("surplus widens", +1),
    ("surplus rises", +1),
    ("rupee strengthens", +1),
    ("rupee appreciates", +1),
    ("rupee gains", +1),
    ("dollar weakens", +1),
    ("deficit widens", -1),
    ("deficit widening", -1),
    ("deficit widened", -1),
    ("surplus narrows", -1),
    ("surplus shrinks", -1),
    ("rupee weakens", -1),
    ("rupee falls", -1),
    ("rupee drops", -1),
    ("rupee hits record low", -1),
    ("dollar strengthens", -1),
    ("dollar rallies", -1),
    ("dollar surges", -1),
    ("growth slows", -1),
    ("growth decelerates", -1),
)


MIN_CONFIDENCE = 0.05


class FinBERTSentimentScorer(EventSentimentScorer):
    """Scores sentiment using ProsusAI/FinBERT with keyword fallback.

    score      = P(positive) - P(negative), the expected polarity over all three labels (the
                 top label alone threw away the rest and made every NEUTRAL call exactly 0);
    confidence = 1 - P(neutral), how polar the text is. It is no longer |score| itself, which
                 counted the same number twice wherever score and confidence are combined.
    """

    logger = structlog.get_logger().bind(component="FinBERTSentimentScorer")

    def __init__(self, enricher: FinBERTEnricher | None = None) -> None:
        """Initializes the scorer on the process-wide enricher (one model per process)."""
        self._enricher = enricher or shared_enricher()
        self._fallback = KeywordSentimentScorer()

    async def score(self, context: AnalysisContext) -> SentimentInference:
        """Scores article sentiment via FinBERT; falls back to keywords on failure."""
        text = _target_focus_text(context)
        if not text.strip():
            return await self._fallback.score(context)

        probabilities = await self._enricher.probabilities(text)
        if probabilities is None:
            self.logger.info("finbert_unavailable_using_keyword_fallback")
            return await self._fallback.score(context)
        p_positive, p_negative, p_neutral = probabilities
        finbert_score = _forex_domain_correction(text, p_positive - p_negative)
        confidence = max(MIN_CONFIDENCE, min(1.0, 1.0 - p_neutral))

        if finbert_score > 0.05:
            sentiment_label = "POSITIVE"
        elif finbert_score < -0.05:
            sentiment_label = "NEGATIVE"
        else:
            sentiment_label = "NEUTRAL"

        key_drivers = [
            KeyDriver(
                label=f"FinBERT: {sentiment_label}",
                rationale=f"ProsusAI/FinBERT: P(positive) {p_positive:.2f}, P(negative) {p_negative:.2f}, "
                f"P(neutral) {p_neutral:.2f}; expected polarity {finbert_score:+.3f}.",
                evidence_ids=[],
            ),
        ]

        # Blend with keyword signals for richer key_drivers
        keyword_result = await self._fallback.score(context)
        if keyword_result.key_drivers:
            key_drivers.extend(keyword_result.key_drivers[:2])

        return SentimentInference(
            score=max(-1.0, min(1.0, finbert_score)),
            confidence=confidence,
            source=SentimentSource.FINBERT,
            key_drivers=key_drivers,
        )


def _forex_domain_correction(text: str, finbert_score: float) -> float:
    """Flips the sign of the expected polarity when forex-specific phrases contradict it."""
    lowered = text.casefold()

    # Phase 1: Contextual overrides take absolute priority.
    override_direction = 0
    for phrase, direction in CONTEXTUAL_OVERRIDES:
        if phrase in lowered:
            override_direction += direction
    if override_direction > 0 and finbert_score < -0.1:
        return abs(finbert_score)
    if override_direction < 0 and finbert_score > 0.1:
        return -abs(finbert_score)
    if override_direction != 0:
        return finbert_score

    # Phase 2: Fallback to single-phrase bearish/bullish counting.
    bearish_hits = sum(1 for phrase in FOREX_BEARISH_PHRASES if phrase in lowered)
    bullish_hits = sum(1 for phrase in FOREX_BULLISH_PHRASES if phrase in lowered)

    if bearish_hits > bullish_hits and finbert_score > 0.1:
        return -abs(finbert_score)
    if bullish_hits > bearish_hits and finbert_score < -0.1:
        return abs(finbert_score)
    return finbert_score
