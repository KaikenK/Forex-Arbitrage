from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from enum import Enum

from retrieval.models import RetrievedEvidence


class RiskFlag(str, Enum):
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    AMBIGUOUS_PAIR_MAPPING = "AMBIGUOUS_PAIR_MAPPING"
    STALE_ARTICLE = "STALE_ARTICLE"
    LIMITED_EVIDENCE = "LIMITED_EVIDENCE"
    HIGH_EVENT_RISK = "HIGH_EVENT_RISK"


class TradingSignal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"
    WAIT = "WAIT"


@dataclass(frozen=True)
class KeyDriver:
    label: str
    rationale: str
    evidence_ids: list[str]


@dataclass(frozen=True)
class MarketReactionSignal:
    event_id: str
    symbol: str
    direction: float
    magnitude: float
    confidence: float
    horizon_minutes: int
    key_drivers: list[str]
    produced_at: datetime


@dataclass(frozen=True)
class ArticleAnalysis:
    event_id: str
    sentiment_score: float
    market_score: float
    affected_symbols: list[str]
    confidence: float
    evidence: list[RetrievedEvidence]
    risk_flags: list[RiskFlag]
    produced_at: datetime
    key_drivers: list[KeyDriver] = field(default_factory=list)
    methodology: dict[str, str] = field(default_factory=dict)
    trading_signal: TradingSignal = TradingSignal.HOLD
    signal_rationale: str = ""
    # Every scored pair's own reaction, keyed by pair. Article-level fields describe the
    # primary pair only: averaging across pairs is meaningless when USD sits on opposite
    # sides (EUR/USD down and USD/JPY up are the same dollar move).
    pair_scores: dict[str, dict[str, float]] = field(default_factory=dict)


ArticleAnalysisResult = ArticleAnalysis
