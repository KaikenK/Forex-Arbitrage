from __future__ import annotations

import re

from .interfaces import EventSentimentScorer
from .models import KeyDriver
from .schemas import AnalysisContext
from .schemas import SentimentInference
from news.models import SentimentSource

POSITIVE_KEYWORDS = {
    "hawkish": "hawkish tone",
    "strong": "strong macro impulse",
    "stronger": "strong macro impulse",
    "beat": "positive surprise",
    "beats": "positive surprise",
    "gain": "upside move",
    "gains": "upside move",
    "rise": "upside move",
    "rises": "upside move",
    "rising": "upside move",
    "rally": "bullish follow-through",
    "rallies": "bullish follow-through",
    "surge": "upside momentum",
    "surges": "upside momentum",
    "boost": "positive impulse",
    "boosts": "positive impulse",
    "stabilize": "stabilization signal",
    "stabilise": "stabilization signal",
    "strengthen": "bullish pressure",
    "strengthens": "bullish pressure",
    "recovery": "recovery signal",
    "rebound": "recovery signal",
    "upgrade": "positive revision",
    "higher": "upside bias",
    "optimism": "bullish sentiment",
    "outperform": "positive surprise",
    "accelerate": "growth impulse",
    "expansion": "growth impulse",
    "surplus": "positive balance",
    "tighten": "hawkish signal",
    "support": "stabilization signal",
    "inflow": "capital inflow",
    "inflows": "capital inflow",
    "appreciation": "currency appreciation",
    "peace deal": "geopolitical relief",
    "ceasefire": "geopolitical relief",
    "reform": "structural reform",
    "record high": "bullish momentum",
    "bull market": "bullish trend",
    "secures": "supply security",
    "secure": "supply security",
    "reserves climb": "reserve accumulation",
    "reserves rise": "reserve accumulation",
    "supply deal": "supply security",
    "deal": "positive deal outcome",
    "agreement": "positive deal outcome",
    "eases": "easing pressure",
    "steady": "stability signal",
    "resilient": "resilience signal",
    "accumulation": "reserve accumulation",
}
NEGATIVE_KEYWORDS = {
    "dovish": "dovish tone",
    "weak": "weak macro impulse",
    "weaker": "weak macro impulse",
    "miss": "negative surprise",
    "misses": "negative surprise",
    "drop": "downside move",
    "drops": "downside move",
    "fall": "downside move",
    "falls": "downside move",
    "falling": "downside move",
    "slump": "downside momentum",
    "crash": "downside shock",
    "plunge": "downside shock",
    "decline": "downside move",
    "declines": "downside move",
    "retreat": "downside pressure",
    "retreats": "downside pressure",
    "weaken": "bearish pressure",
    "weakens": "bearish pressure",
    "cut": "dovish signal",
    "deficit": "negative balance",
    "pessimism": "bearish sentiment",
    "downgrade": "negative revision",
    "lower": "downside bias",
    "underperform": "negative surprise",
    "contraction": "economic slowdown",
    "recession": "economic slowdown",
    "slowdown": "economic slowdown",
    "easing": "dovish signal",
    "tension": "risk-off signal",
    "crisis": "risk-off signal",
    "uncertainty": "risk-off signal",
    "outflow": "capital outflow",
    "outflows": "capital outflow",
    "depreciation": "currency depreciation",
    "devaluation": "currency devaluation",
    "war": "geopolitical risk",
    "conflict": "geopolitical risk",
    "sanction": "sanctions pressure",
    "sanctions": "sanctions pressure",
    "missile": "geopolitical escalation",
    "crash": "market crash",
    "bear market": "bearish trend",
    "correction": "market correction",
    "tariff": "trade friction",
    "trade war": "trade friction",
    "disruption": "supply disruption",
    "shortage": "supply shortage",
    "blockade": "supply disruption",
    "embargo": "trade embargo",
    "default": "default risk",
    "instability": "instability risk",
    "volatility": "elevated volatility",
    "escalation": "geopolitical escalation",
}


# Phrase overrides: multi-word patterns that reverse misleading single-keyword signals.
# Each entry: (phrase, sentiment_direction, driver_label)
# sentiment_direction: +1 means the phrase is positive, -1 means negative.
PHRASE_OVERRIDES: list[tuple[str, int, str]] = [
    ("deficit narrows", +1, "trade improvement"),
    ("deficit narrowing", +1, "trade improvement"),
    ("deficit narrowed", +1, "trade improvement"),
    ("trade deficit narrows", +1, "trade improvement"),
    ("trade deficit shrinks", +1, "trade improvement"),
    ("lower oil imports", +1, "import cost reduction"),
    ("lower imports", +1, "import cost reduction"),
    ("falling imports", +1, "import cost reduction"),
    ("lower oil prices", +1, "commodity cost relief"),
    ("oil prices fall", +1, "commodity cost relief"),
    ("crude drops", +1, "commodity cost relief"),
    ("crude falls", +1, "commodity cost relief"),
    ("inflation eases", +1, "disinflation signal"),
    ("inflation cools", +1, "disinflation signal"),
    ("inflation drops", +1, "disinflation signal"),
    ("inflation falls", +1, "disinflation signal"),
    ("inflation slows", +1, "disinflation signal"),
    ("yields fall", +1, "dovish signal"),
    ("yields drop", +1, "dovish signal"),
    ("yields decline", +1, "dovish signal"),
    ("unemployment falls", +1, "labor market strength"),
    ("unemployment drops", +1, "labor market strength"),
    ("outflows slow", +1, "flow stabilization"),
    ("growth accelerates", +1, "growth impulse"),
    ("dollar weakens", +1, "INR supportive"),
    ("dollar falls", +1, "INR supportive"),
    ("dollar drops", +1, "INR supportive"),
    ("rupee strengthens", +1, "INR appreciation"),
    ("rupee appreciates", +1, "INR appreciation"),
    ("rupee gains", +1, "INR appreciation"),
    ("deficit widens", -1, "trade deterioration"),
    ("deficit widening", -1, "trade deterioration"),
    ("deficit widened", -1, "trade deterioration"),
    ("surplus narrows", -1, "trade deterioration"),
    ("surplus shrinks", -1, "trade deterioration"),
    ("growth slows", -1, "growth slowdown"),
    ("growth decelerates", -1, "growth slowdown"),
    ("rupee weakens", -1, "INR depreciation"),
    ("rupee falls", -1, "INR depreciation"),
    ("rupee drops", -1, "INR depreciation"),
    ("rupee hits record low", -1, "INR depreciation"),
    ("dollar strengthens", -1, "USD strength"),
    ("dollar rallies", -1, "USD strength"),
    ("dollar surges", -1, "USD strength"),
]

PAIR_TOKEN_MAP = {
    "USD": ["usd", "dollar"],
    "INR": ["inr", "rupee"],
    "EUR": ["eur", "euro"],
    "JPY": ["jpy", "yen"],
    "GBP": ["gbp", "pound", "sterling"],
}
SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+|\n+")


class KeywordSentimentScorer(EventSentimentScorer):
    async def score(self, context: AnalysisContext) -> SentimentInference:
        """Scores article sentiment with a deterministic keyword baseline."""
        text = _target_focus_text(context).casefold()

        # Phase 1: Check phrase overrides first — they take priority.
        phrase_pos_hits: list[str] = []
        phrase_neg_hits: list[str] = []
        suppressed_keywords: set[str] = set()
        for phrase, direction, driver_label in PHRASE_OVERRIDES:
            if phrase in text:
                if direction > 0:
                    phrase_pos_hits.append(driver_label)
                else:
                    phrase_neg_hits.append(driver_label)
                # Suppress individual keywords that appear inside this phrase
                for word in phrase.split():
                    suppressed_keywords.add(word)

        # Phase 2: Individual keywords, but skip those suppressed by phrases.
        positive_hits = [
            driver for keyword, driver in POSITIVE_KEYWORDS.items()
            if keyword in text and keyword not in suppressed_keywords
        ]
        negative_hits = [
            driver for keyword, driver in NEGATIVE_KEYWORDS.items()
            if keyword in text and keyword not in suppressed_keywords
        ]

        # Merge phrase and keyword hits. Phrase hits get 1.5x weight.
        pos_count = len(positive_hits) + len(phrase_pos_hits) * 1.5
        neg_count = len(negative_hits) + len(phrase_neg_hits) * 1.5
        hit_count = pos_count + neg_count

        # Continuous scoring: base polarity scaled by keyword density
        if hit_count == 0:
            score = 0.0
        else:
            polarity = (pos_count - neg_count) / hit_count  # -1.0 to 1.0
            density = min(1.0, hit_count / 5.0)  # saturates at 5 keyword hits
            score = polarity * (0.3 + 0.7 * density)  # range: ~0.3 to 1.0 magnitude

        confidence = min(0.85, 0.35 + hit_count * 0.08)
        all_pos_labels = phrase_pos_hits + positive_hits
        all_neg_labels = phrase_neg_hits + negative_hits
        key_drivers = [
            KeyDriver(
                label=driver_label,
                rationale=f"Matched sentiment keyword pattern for {driver_label}.",
                evidence_ids=[],
            )
            for driver_label in _ordered_unique(all_pos_labels[:2] + all_neg_labels[:2])
        ]

        return SentimentInference(
            score=max(-1.0, min(1.0, score)),
            confidence=confidence,
            source=SentimentSource.NONE,
            key_drivers=key_drivers,
        )


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered_values.append(value)
    return ordered_values


def _target_focus_text(context: AnalysisContext) -> str:
    text = " ".join(filter(None, [context.event.headline, context.event.body or ""]))
    sentences = [segment.strip() for segment in SENTENCE_SPLIT_PATTERN.split(text) if segment.strip()]
    if not sentences:
        return text

    focus_tokens = _focus_tokens(context)
    focused_sentences = [
        sentence for sentence in sentences
        if any(token in sentence.casefold() for token in focus_tokens)
    ]
    return " ".join(focused_sentences) if focused_sentences else text


def _focus_tokens(context: AnalysisContext) -> list[str]:
    tokens: list[str] = []
    if context.article.currency_pair:
        tokens.extend(part.casefold() for part in context.article.currency_pair.replace("-", "/").split("/") if part)
    for symbol in context.event.symbols:
        tokens.extend(part.casefold() for part in symbol.replace("-", "/").split("/") if part)
    for code in list(tokens):
        tokens.extend(PAIR_TOKEN_MAP.get(code.upper(), []))
    if context.article.event_category:
        tokens.extend(token.casefold() for token in context.article.event_category.replace("-", " ").split())
    if context.article.macro_event_type:
        tokens.extend(token.casefold() for token in context.article.macro_event_type.replace("-", " ").split())
    return _ordered_unique([token for token in tokens if token])
