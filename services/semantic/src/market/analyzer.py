from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC
from datetime import datetime
import re

import structlog

from news.models import UnifiedNewsEvent
from retrieval.interfaces import EvidenceRetriever
from retrieval.models import EvidenceType
from retrieval.models import RetrievedEvidence

from .interfaces import EventReactionScorer
from .interfaces import EventSentimentScorer
from .interfaces import MarketReactionAnalyzer
from .models import ArticleAnalysis
from .models import KeyDriver
from .models import MarketReactionSignal
from .models import RiskFlag
from .models import TradingSignal
from .schemas import AnalysisContext
from .schemas import SentimentInference

PAIR_SEPARATOR = "/"
DIRECT_PAIR_PATTERN = re.compile(r"\b([A-Z]{3})[/\-]([A-Z]{3})\b")
COMPACT_PAIR_PATTERN = re.compile(r"\b([A-Z]{6})\b")
# Confidence thresholds are percentile-calibrated against the live archive
# (1,308 analyses). The previous values (0.45 / 0.40 / 0.50 / 0.65) were tuned when
# the evidence term contributed a flat +0.20 to every score; now that the term
# reflects actual retrieval quality, the distribution sits ~0.10 lower. These values
# gate the same FRACTION of the tape as before -- 11% to WAIT, 25% below the entry
# gate -- so the operating point is preserved while the underlying number varies.
# Recalibrate these if the retrieval corpus or scoring weights change materially.
DEFAULT_LOW_CONFIDENCE_THRESHOLD = 0.34
HARD_CONFIDENCE_FLOOR = 0.30
SIGNAL_ENTRY_CONFIDENCE = 0.39
STRONG_SIGNAL_CONFIDENCE = 0.56
DEFAULT_MIN_EVIDENCE_COUNT = 2
DEFAULT_STALE_AFTER_SECONDS = 6 * 60 * 60
DEFAULT_AMBIGUOUS_SYMBOL_COUNT = 3

# A market score is a raw return (roughly +/-0.005). Comparing it directly against
# a sentiment score, which is a classifier output on a +/-1 scale, silently makes
# every such comparison false; rescale first. _trading_signal uses the same factor.
MARKET_SCORE_NORMALIZER = 200.0

# HIGH_EVENT_RISK must describe *this* article, not the shape of the evidence
# corpus. Every retrieved row is a scheduled calendar or central-bank event, so
# "any calendar evidence exists" was true for every single analysis and the flag
# carried no information. Require a materially surprising release instead.
HIGH_EVENT_RISK_SURPRISE_THRESHOLD = 0.25

# Relevance scores from the evidence retriever are cosine-style similarities that
# empirically top out near 0.6 on this corpus; treat that as "a strong match"
# rather than assuming 1.0 is reachable, or the term is permanently penalised.
EVIDENCE_RELEVANCE_REFERENCE = 0.6
# The retriever is configured to return 5 rows; fewer means a thin retrieval.
EVIDENCE_TARGET_COUNT = 5.0
# Evidence that is weak or purely neutral should damp confidence, not destroy it --
# the calibrated market model is still the dominant term at 50%.
EVIDENCE_FLOOR = 0.35
# Neutral stance sits below active agreement but above active contradiction.
NEUTRAL_AGREEMENT = 0.5
HIGH_RISK_KEYWORDS = (
    "central bank",
    "fed",
    "federal reserve",
    "ecb",
    "boe",
    "boj",
    "rbi",
    "rate decision",
    "interest rate",
    "inflation",
    "cpi",
    "jobs report",
    "nonfarm payroll",
    "war",
    "conflict",
    "sanction",
    "tariff",
    "crash",
    "crisis",
    "budget",
    "fiscal deficit",
)
NAME_TO_CURRENCY = {
    "euro": "EUR",
    "dollar": "USD",
    "usd": "USD",
    "yen": "JPY",
    "jpy": "JPY",
    "pound": "GBP",
    "sterling": "GBP",
    "gbp": "GBP",
    "franc": "CHF",
    "chf": "CHF",
    "aussie": "AUD",
    "aud": "AUD",
    "kiwi": "NZD",
    "nzd": "NZD",
    "cad": "CAD",
    "loonie": "CAD",
    "rupee": "INR",
    "inr": "INR",
    "sensex": "INR",
    "nifty": "INR",
    "mumbai": "INR",
    "india": "INR",
    "modi": "INR",
    "sebi": "INR",
}
SUPPORTED_CURRENCIES = frozenset({"AUD", "CAD", "CHF", "EUR", "GBP", "INR", "JPY", "NZD", "USD"})


class UnifiedArticleAnalyzer(MarketReactionAnalyzer):
    logger = structlog.get_logger().bind(component="UnifiedArticleAnalyzer")

    def __init__(
        self,
        sentiment_scorer: EventSentimentScorer,
        reaction_scorer: EventReactionScorer,
        evidence_retriever: EvidenceRetriever,
        clock: Callable[[], datetime] | None = None,
        low_confidence_threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
        min_evidence_count: int = DEFAULT_MIN_EVIDENCE_COUNT,
        stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
        ambiguous_symbol_count: int = DEFAULT_AMBIGUOUS_SYMBOL_COUNT,
    ) -> None:
        """Initializes the unified article analyzer dependencies."""
        self._sentiment_scorer = sentiment_scorer
        self._reaction_scorer = reaction_scorer
        self._evidence_retriever = evidence_retriever
        # Ask the scorer what it actually is, so the methodology panel reports the
        # served model instead of a hardcoded string that goes stale on retrain.
        self._model_descriptor = getattr(reaction_scorer, "model_descriptor", "") or "reaction model"
        self._clock = clock or _utc_now
        self._low_confidence_threshold = low_confidence_threshold
        self._min_evidence_count = min_evidence_count
        self._stale_after_seconds = stale_after_seconds
        self._ambiguous_symbol_count = ambiguous_symbol_count

    async def analyze(self, context: AnalysisContext) -> ArticleAnalysis:
        """Produces a unified forex article analysis for one prepared context."""
        produced_at = self._clock()
        resolved_symbols = _merge_symbols(context)
        prepared_event = replace(context.event, symbols=resolved_symbols)
        prepared_context = AnalysisContext(article=context.article, event=prepared_event)

        sentiment = await self._sentiment_scorer.score(prepared_context)
        scored_event = replace(
            prepared_event,
            raw_sentiment=_clamp(sentiment.score),
            sentiment_source=sentiment.source,
        )
        scored_context = AnalysisContext(article=context.article, event=scored_event)

        signals = await self._reaction_scorer.score(scored_context)
        evidence = await self._evidence_retriever.retrieve(scored_context, signals)

        affected_symbols = _ordered_unique([signal.symbol for signal in signals] + resolved_symbols)
        # Article-level fields describe the primary pair (the first symbol). With one
        # pair this is every signal, exactly as before. With several, averaging or
        # conflict-checking across them would be wrong: EUR/USD down and USD/JPY up are
        # the same dollar move, yet read as opposite directions.
        primary_signals = _primary_signals(signals, resolved_symbols)
        market_score = _market_score(primary_signals)
        confidence = _combined_confidence(sentiment, primary_signals, evidence)
        pair_scores = {
            signal.symbol: {
                "market_score": _market_score([signal]),
                "confidence": _combined_confidence(sentiment, [signal], evidence),
                "direction": signal.direction,
                "magnitude": signal.magnitude,
                "horizon_minutes": float(signal.horizon_minutes),
            }
            for signal in signals
        }
        # Pairs the caller asked for explicitly are deliberate, not ambiguous mappings.
        requested_extra = {pair.strip().upper() for pair in (context.article.currency_pairs or [])}
        requested_extra.discard(resolved_symbols[0] if resolved_symbols else "")
        risk_flags = _risk_flags(
            event=scored_event,
            confidence=confidence,
            evidence=evidence,
            signals=primary_signals,
            affected_symbols=[symbol for symbol in affected_symbols if symbol not in requested_extra],
            stale_after_seconds=self._stale_after_seconds,
            low_confidence_threshold=self._low_confidence_threshold,
            min_evidence_count=self._min_evidence_count,
            ambiguous_symbol_count=self._ambiguous_symbol_count,
            now=self._clock(),
        )

        signal, signal_rationale = _trading_signal(
            sentiment_score=_clamp(sentiment.score),
            market_score=market_score,
            confidence=confidence,
            risk_flags=risk_flags,
        )

        return ArticleAnalysis(
            event_id=scored_event.event_id,
            sentiment_score=_clamp(sentiment.score),
            market_score=market_score,
            affected_symbols=affected_symbols,
            confidence=confidence,
            evidence=sorted(evidence, key=lambda item: item.relevance_score, reverse=True),
            risk_flags=risk_flags,
            produced_at=produced_at,
            key_drivers=_key_drivers(sentiment, primary_signals, evidence),
            methodology=_methodology(sentiment, primary_signals, evidence, self._model_descriptor),
            trading_signal=signal,
            signal_rationale=signal_rationale,
            pair_scores=pair_scores,
        )


def _primary_signals(signals: list[MarketReactionSignal], resolved_symbols: list[str]) -> list[MarketReactionSignal]:
    """Signals for the primary (first) pair; all signals if it produced none."""
    if not signals or not resolved_symbols:
        return signals
    primary = [signal for signal in signals if signal.symbol == resolved_symbols[0]]
    return primary or signals


def _merge_symbols(context: AnalysisContext) -> list[str]:
    existing_symbols = [symbol.upper() for symbol in context.event.symbols if symbol.strip()]
    extracted_pairs = _extract_pairs(context.event, context.article.currency_pair)
    return _ordered_unique(existing_symbols + extracted_pairs)


def _extract_pairs(event: UnifiedNewsEvent, explicit_pair: str | None) -> list[str]:
    combined_text = " ".join(filter(None, [event.headline, event.body or ""]))
    pairs: list[str] = []
    if explicit_pair is not None and explicit_pair.strip():
        pairs.append(_normalize_pair(explicit_pair))

    for base_currency, quote_currency in DIRECT_PAIR_PATTERN.findall(combined_text.upper()):
        if base_currency in SUPPORTED_CURRENCIES and quote_currency in SUPPORTED_CURRENCIES:
            pairs.append(f"{base_currency}{PAIR_SEPARATOR}{quote_currency}")

    for compact_pair in COMPACT_PAIR_PATTERN.findall(combined_text.upper()):
        base_currency = compact_pair[:3]
        quote_currency = compact_pair[3:]
        if base_currency in SUPPORTED_CURRENCIES and quote_currency in SUPPORTED_CURRENCIES:
            pairs.append(f"{base_currency}{PAIR_SEPARATOR}{quote_currency}")

    currency_mentions: list[str] = []
    lowered_text = combined_text.casefold()
    for name, currency in NAME_TO_CURRENCY.items():
        if name in lowered_text:
            currency_mentions.append(currency)

    unique_mentions = _ordered_unique(currency_mentions)
    if len(unique_mentions) >= 2 and not pairs:
        for index, base_currency in enumerate(unique_mentions[:-1]):
            quote_currency = unique_mentions[index + 1]
            pairs.append(f"{base_currency}{PAIR_SEPARATOR}{quote_currency}")

    return _ordered_unique(pairs)


def _normalize_pair(value: str) -> str:
    cleaned_value = value.strip().upper().replace("-", PAIR_SEPARATOR)
    if PAIR_SEPARATOR in cleaned_value:
        return cleaned_value
    if len(cleaned_value) == 6:
        return f"{cleaned_value[:3]}{PAIR_SEPARATOR}{cleaned_value[3:]}"
    return cleaned_value


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered_values: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered_values.append(value)
    return ordered_values


def _market_score(signals: list[MarketReactionSignal]) -> float:
    if not signals:
        return 0.0
    weighted_score = sum(signal.direction * signal.magnitude for signal in signals) / len(signals)
    return _clamp(weighted_score)


def _combined_confidence(
    sentiment: SentimentInference,
    signals: list[MarketReactionSignal],
    evidence: list[RetrievedEvidence] | None = None,
) -> float:
    """Weighted confidence: target sentiment, calibrated market model, and stance-aware evidence."""
    if not signals:
        return _clamp(sentiment.confidence * 0.5, minimum=0.0, maximum=1.0)

    average_signal_confidence = sum(signal.confidence for signal in signals) / len(signals)
    evidence_factor = _evidence_quality_factor(evidence or [])

    weighted = (
        0.30 * sentiment.confidence
        + 0.50 * average_signal_confidence
        + 0.20 * evidence_factor
    )
    return _clamp(weighted, minimum=0.0, maximum=1.0)


def _risk_flags(
    event: UnifiedNewsEvent,
    confidence: float,
    evidence: list[RetrievedEvidence],
    signals: list[MarketReactionSignal],
    affected_symbols: list[str],
    stale_after_seconds: int,
    low_confidence_threshold: float,
    min_evidence_count: int,
    ambiguous_symbol_count: int,
    now: datetime,
) -> list[RiskFlag]:
    risk_flags: list[RiskFlag] = []
    if confidence < low_confidence_threshold:
        risk_flags.append(RiskFlag.LOW_CONFIDENCE)
    if len(evidence) < min_evidence_count:
        risk_flags.append(RiskFlag.LIMITED_EVIDENCE)
    if not affected_symbols or len(affected_symbols) > ambiguous_symbol_count:
        risk_flags.append(RiskFlag.AMBIGUOUS_PAIR_MAPPING)
    if (now - event.published_at).total_seconds() > stale_after_seconds:
        risk_flags.append(RiskFlag.STALE_ARTICLE)
    if (
        _has_conflicting_signal_directions(signals)
        or _has_sentiment_market_conflict(event.raw_sentiment, signals)
        or _has_contradictory_evidence(evidence)
    ):
        risk_flags.append(RiskFlag.CONFLICTING_EVIDENCE)
    if _is_high_event_risk(event, evidence):
        risk_flags.append(RiskFlag.HIGH_EVENT_RISK)
    return _ordered_risk_flags(risk_flags)


def _has_conflicting_signal_directions(signals: list[MarketReactionSignal]) -> bool:
    has_positive = any(signal.direction > 0 for signal in signals)
    has_negative = any(signal.direction < 0 for signal in signals)
    return has_positive and has_negative


def _has_sentiment_market_conflict(
    sentiment_score: float,
    signals: list[MarketReactionSignal],
) -> bool:
    """Deliberately always False: tone/direction divergence is not a conflict here.

    This previously compared a raw return (+/-0.005) against a >= 0.2 threshold on
    the sentiment scale, so it could never fire -- it was dead code. Rescaling it to
    make it fire would be worse than leaving it dead, because for an FX quote the
    two SHOULD diverge whenever the base currency drives the story: "rupee plunges"
    is negative tone and bullish USD/INR at the same time. Firing here routes those
    to HOLD via CONFLICTING_EVIDENCE, suppressing exactly the correct calls.

    Genuine conflict is still detected by _has_conflicting_signal_directions (the
    model disagreeing with itself across horizons) and _has_contradictory_evidence
    (retrieved history pointing the other way). Divergence between tone and
    direction is surfaced as explanatory copy instead -- see _tone_note.

    Kept as a named predicate rather than deleted so the reasoning stays discoverable
    at the call site in _risk_flags.
    """
    del sentiment_score, signals
    return False


def _is_high_event_risk(event: UnifiedNewsEvent, evidence: list[RetrievedEvidence]) -> bool:
    """True when the article itself sits near a materially surprising event.

    The evidence clause deliberately tests *surprise magnitude*, not evidence
    type: the retrieval corpus is entirely calendar and central-bank rows, so a
    type check is true for every analysis and the flag degenerates to a constant.
    """
    combined_text = " ".join(filter(None, [event.headline, event.body or ""])).casefold()
    if any(keyword in combined_text for keyword in HIGH_RISK_KEYWORDS):
        return True
    return any(
        evidence_item.evidence_type in {EvidenceType.CENTRAL_BANK, EvidenceType.ECONOMIC_CALENDAR}
        and evidence_item.standardized_surprise is not None
        and abs(evidence_item.standardized_surprise) >= HIGH_EVENT_RISK_SURPRISE_THRESHOLD
        for evidence_item in evidence
    )


def _ordered_risk_flags(risk_flags: list[RiskFlag]) -> list[RiskFlag]:
    seen: set[RiskFlag] = set()
    ordered_flags: list[RiskFlag] = []
    for risk_flag in risk_flags:
        if risk_flag in seen:
            continue
        seen.add(risk_flag)
        ordered_flags.append(risk_flag)
    return ordered_flags


def _evidence_quality_factor(evidence: list[RetrievedEvidence]) -> float:
    """How much the retrieved history actually backs this prediction, in [0, 1].

    This used to be driven by ``coverage_factor = min(1, len(evidence)/3)``. The
    retriever always returns 5 rows, so that saturated at 1.0 on 90%+ of analyses
    and the term contributed a flat 0.20 to every confidence score while ignoring
    how *relevant* the retrieved rows were. Confidence collapsed to a rescaled
    FinBERT confidence spanning barely 35 points.

    Quality is now the product of two things that genuinely vary:

    * relevance -- how close the retrieved events are, measured against
      EVIDENCE_RELEVANCE_REFERENCE rather than assumed perfect;
    * agreement -- whether supportive or contradictory rows dominate, weighted by
      their own relevance so a strong contradiction outweighs a weak agreement.

    Purely neutral evidence yields NEUTRAL_AGREEMENT: it neither confirms nor
    refutes, so it must not score the same as evidence that actively agrees.
    """
    if not evidence:
        return 0.0

    mean_relevance = sum(item.relevance_score for item in evidence) / len(evidence)
    relevance_factor = _clamp(mean_relevance / EVIDENCE_RELEVANCE_REFERENCE, 0.0, 1.0)

    supportive_weight = sum(item.relevance_score for item in evidence if item.stance == "supportive")
    contradictory_weight = sum(item.relevance_score for item in evidence if item.stance == "contradictory")
    stance_weight = supportive_weight + contradictory_weight
    if stance_weight <= 0.0:
        agreement = NEUTRAL_AGREEMENT
    else:
        # (-1, +1) -> (0, 1)
        agreement = _clamp(((supportive_weight - contradictory_weight) / stance_weight + 1.0) / 2.0, 0.0, 1.0)

    # Coverage still matters, but only as a floor-raiser for thin retrievals.
    coverage_factor = _clamp(len(evidence) / EVIDENCE_TARGET_COUNT, 0.0, 1.0)
    quality = relevance_factor * agreement * coverage_factor
    # Rescale onto [EVIDENCE_FLOOR, 1]. Weak evidence should reduce confidence,
    # not zero it: the market model is still the dominant term.
    return _clamp(EVIDENCE_FLOOR + (1.0 - EVIDENCE_FLOOR) * quality, 0.0, 1.0)


def _has_contradictory_evidence(evidence: list[RetrievedEvidence]) -> bool:
    return any(item.stance == "contradictory" and item.relevance_score >= 0.35 for item in evidence)


def _key_drivers(
    sentiment: SentimentInference,
    signals: list[MarketReactionSignal],
    evidence: list[RetrievedEvidence],
) -> list[KeyDriver]:
    evidence_ids = [evidence_item.evidence_id for evidence_item in evidence[:3]]
    key_drivers = list(sentiment.key_drivers)
    existing_labels = {driver.label for driver in key_drivers}
    for signal in signals:
        for driver_label in signal.key_drivers:
            if driver_label in existing_labels:
                continue
            key_drivers.append(
                KeyDriver(
                    label=driver_label,
                    rationale=f"Reaction model highlighted {driver_label}.",
                    evidence_ids=list(evidence_ids),
                ),
            )
            existing_labels.add(driver_label)
    for evidence_item in evidence[:2]:
        if evidence_item.stance not in {"supportive", "contradictory"}:
            continue
        label = f"evidence:{evidence_item.stance}"
        if label in existing_labels:
            continue
        key_drivers.append(
            KeyDriver(
                label=label,
                rationale=f"Historical evidence '{evidence_item.title}' is {evidence_item.stance} of the current reaction view.",
                evidence_ids=[evidence_item.evidence_id],
            ),
        )
        existing_labels.add(label)
    return key_drivers


def _clamp(value: float, minimum: float = -1.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))


def _methodology(
    sentiment: SentimentInference,
    signals: list[MarketReactionSignal],
    evidence: list[RetrievedEvidence],
    model_descriptor: str = "",
) -> dict[str, str]:
    """Builds methodology metadata for the frontend methodology panel."""
    is_finbert = sentiment.source.value == "FINBERT"
    sentiment_method = (
        "ProsusAI/FinBERT (110M-param financial BERT)"
        if is_finbert
        else "Keyword baseline (30 financial term patterns)"
    )
    if signals:
        signal = signals[0]
        # Read from the loaded artifact, never hardcoded: this line claimed
        # "k-NN (k=5)" for every analysis even after a linear model was trained
        # and served, so the panel was misreporting the model to the user.
        neighbors_info = model_descriptor or "reaction model"
        horizon = f"{signal.horizon_minutes}m"
        has_rag = any("rag_" in d for d in signal.key_drivers)
        if has_rag:
            neighbors_info += " + RAG confidence adjustment"
        market_method = f"{neighbors_info}, horizon={horizon}, confidence calibrated on holdout slice"
    else:
        market_method = "No market signals (no pair detected)"

    supportive_count = sum(1 for item in evidence if item.stance == "supportive")
    contradictory_count = sum(1 for item in evidence if item.stance == "contradictory")
    confidence_method = "Weighted: 30% target sentiment + 50% calibrated market model + 20% stance-aware evidence"
    evidence_method = (
        f"{len(evidence)} historical rows retrieved with event-aware tiers; "
        f"supportive={supportive_count}, contradictory={contradictory_count}"
    )

    return {
        "sentiment": sentiment_method,
        "market": market_method,
        "confidence": confidence_method,
        "evidence": evidence_method,
    }


def _trading_signal(
    sentiment_score: float,
    market_score: float,
    confidence: float,
    risk_flags: list[RiskFlag],
) -> tuple[TradingSignal, str]:
    """Derives an actionable trading signal from analysis outputs.

    The market model's direction is authoritative for the currency pair.
    Sentiment magnitude and confidence contribute to conviction level.
    """
    has_low_confidence = RiskFlag.LOW_CONFIDENCE in risk_flags
    has_conflicting = RiskFlag.CONFLICTING_EVIDENCE in risk_flags

    if has_low_confidence or confidence < HARD_CONFIDENCE_FLOOR:
        return TradingSignal.WAIT, (
            f"Confidence too low ({confidence:.0%}) for actionable signal. "
            "Wait for stronger confirmation from additional data sources."
        )

    if has_conflicting:
        return TradingSignal.HOLD, (
            "Sentiment and market model disagree on direction. "
            "Hold current positions until signals converge."
        )

    # Market score direction is authoritative for the currency pair.
    # Normalize market_score (typical ±0.005) to ±1 scale for thresholds.
    normalized_market = _clamp(market_score * MARKET_SCORE_NORMALIZER)
    sentiment_magnitude = abs(sentiment_score)
    conviction = 0.6 * abs(normalized_market) + 0.4 * sentiment_magnitude
    # Headline tone and pair direction diverge whenever the base currency is the
    # one in the news: "rupee plunges" is negative tone but bullish USD/INR. Say so
    # explicitly, otherwise the rationale reads as a contradiction.
    tone_note = _tone_note(sentiment_score, normalized_market)

    if normalized_market > 0.05:
        if conviction > 0.45 and confidence >= STRONG_SIGNAL_CONFIDENCE:
            return TradingSignal.STRONG_BUY, (
                f"Strong BUY: reaction model predicts {market_score:+.4f} for this pair "
                f"at {confidence:.0%} confidence. Headline sentiment {sentiment_score:+.2f}"
                f"{tone_note}"
            )
        if conviction > 0.20 and confidence >= SIGNAL_ENTRY_CONFIDENCE:
            return TradingSignal.BUY, (
                f"Moderate BUY: reaction model predicts {market_score:+.4f} for this pair. "
                f"Headline sentiment {sentiment_score:+.2f}{tone_note} "
                "Consider partial position entry."
            )
    elif normalized_market < -0.05:
        if conviction > 0.45 and confidence >= STRONG_SIGNAL_CONFIDENCE:
            return TradingSignal.STRONG_SELL, (
                f"Strong SELL: reaction model predicts {market_score:+.4f} for this pair "
                f"at {confidence:.0%} confidence. Headline sentiment {sentiment_score:+.2f}"
                f"{tone_note}"
            )
        if conviction > 0.20 and confidence >= SIGNAL_ENTRY_CONFIDENCE:
            return TradingSignal.SELL, (
                f"Moderate SELL: reaction model predicts {market_score:+.4f} for this pair. "
                f"Headline sentiment {sentiment_score:+.2f}{tone_note} "
                "Consider partial position exit."
            )

    return TradingSignal.HOLD, (
        f"HOLD: reaction model predicts {market_score:+.4f}, inside the neutral band, "
        f"so there is no directional edge. Headline sentiment {sentiment_score:+.2f}."
    )


def _tone_note(sentiment_score: float, normalized_market: float) -> str:
    """Explain a tone/direction divergence instead of printing a bare contradiction.

    Quote convention makes these disagree routinely: for USD/INR, news that is bad
    for the rupee is bullish for the pair. The reaction model learns that from
    realised returns; FinBERT, trained on equity text, cannot.
    """
    if abs(sentiment_score) < 0.2 or abs(normalized_market) < 0.05:
        return "."
    if sentiment_score * normalized_market < 0:
        return (
            " reflects the base currency, which moves opposite to the pair quote; "
            "the reaction model is trained on realised moves and takes precedence."
        )
    return " agrees with the predicted direction."


def _utc_now() -> datetime:
    return datetime.now(UTC)
