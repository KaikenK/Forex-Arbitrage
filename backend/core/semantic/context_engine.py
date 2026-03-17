"""
Semantic Context Engine

Embedding-based NLP engine for context risk assessment.
Analyzes session, volatility regime, spread regime, and external signals
to produce a context risk score that adjusts arbitrage confidence.

Design Principles:
- Adjusts confidence but NEVER overrides core arbitrage logic
- Fully explainable outputs with reasoning
- Graceful degradation when models unavailable
- Supports multiple embedding backends
"""

import time
import math
import logging
import hashlib
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from threading import RLock

logger = logging.getLogger(__name__)


class VolatilityRegime(Enum):
    """Volatility regime classification."""
    LOW = "LOW"           # Below average volatility
    NORMAL = "NORMAL"     # Average volatility
    HIGH = "HIGH"         # Above average volatility
    EXTREME = "EXTREME"   # Crisis-level volatility


class SpreadRegime(Enum):
    """Spread regime classification."""
    TIGHT = "TIGHT"       # Better than average spreads
    NORMAL = "NORMAL"     # Average spreads
    WIDE = "WIDE"         # Worse than average spreads
    ILLIQUID = "ILLIQUID" # Very wide, potential liquidity issues


class SessionContext(Enum):
    """Trading session context."""
    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"
    SYDNEY = "SYDNEY"
    OVERLAP_LONDON_NY = "OVERLAP_LONDON_NY"
    OVERLAP_TOKYO_LONDON = "OVERLAP_TOKYO_LONDON"
    OFF_HOURS = "OFF_HOURS"


@dataclass
class ContextConfig:
    """
    Configuration for semantic context engine.
    
    Attributes:
        enable_embeddings: Whether to use embedding models
        embedding_model: Model name for embeddings
        max_text_signals: Maximum external text signals to process
        volatility_window: Window for volatility calculation (seconds)
        spread_window: Window for spread calculation (seconds)
        risk_weight_session: Weight for session risk
        risk_weight_volatility: Weight for volatility risk
        risk_weight_spread: Weight for spread risk
        risk_weight_sentiment: Weight for sentiment risk
        max_risk_adjustment: Maximum adjustment to confidence (0-1)
    """
    enable_embeddings: bool = False  # Disabled by default (optional feature)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    max_text_signals: int = 10
    volatility_window: int = 300
    spread_window: int = 60
    risk_weight_session: float = 0.2
    risk_weight_volatility: float = 0.3
    risk_weight_spread: float = 0.3
    risk_weight_sentiment: float = 0.2
    max_risk_adjustment: float = 0.3
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "enable_embeddings": self.enable_embeddings,
            "embedding_model": self.embedding_model,
            "max_text_signals": self.max_text_signals,
            "volatility_window": self.volatility_window,
            "spread_window": self.spread_window,
            "risk_weight_session": self.risk_weight_session,
            "risk_weight_volatility": self.risk_weight_volatility,
            "risk_weight_spread": self.risk_weight_spread,
            "risk_weight_sentiment": self.risk_weight_sentiment,
            "max_risk_adjustment": self.max_risk_adjustment,
        }


@dataclass
class ContextInput:
    """
    Input to the semantic context engine.
    
    Attributes:
        symbol: Currency pair
        session: Current trading session
        volatility_samples: Recent volatility samples
        spread_samples: Recent spread samples
        text_signals: External text signals (news, tweets, etc.)
        metadata: Additional context
    """
    symbol: str
    session: Optional[SessionContext] = None
    volatility_samples: List[float] = field(default_factory=list)
    spread_samples: List[float] = field(default_factory=list)
    text_signals: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "session": self.session.value if self.session else None,
            "volatility_sample_count": len(self.volatility_samples),
            "spread_sample_count": len(self.spread_samples),
            "text_signal_count": len(self.text_signals),
        }


@dataclass
class ContextResult:
    """
    Result from the semantic context engine.
    
    Attributes:
        risk_score: Overall risk score (0 = low risk, 1 = high risk)
        confidence_adjustment: Adjustment to apply to confidence (-max to +small)
        volatility_regime: Detected volatility regime
        spread_regime: Detected spread regime
        session_risk: Risk component from session
        volatility_risk: Risk component from volatility
        spread_risk: Risk component from spreads
        sentiment_risk: Risk component from text signals
        reasoning: Human-readable explanation
    """
    risk_score: float
    confidence_adjustment: float
    volatility_regime: VolatilityRegime
    spread_regime: SpreadRegime
    session_risk: float
    volatility_risk: float
    spread_risk: float
    sentiment_risk: float
    reasoning: List[str]
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_score": round(self.risk_score, 3),
            "confidence_adjustment": round(self.confidence_adjustment, 3),
            "volatility_regime": self.volatility_regime.value,
            "spread_regime": self.spread_regime.value,
            "session_risk": round(self.session_risk, 3),
            "volatility_risk": round(self.volatility_risk, 3),
            "spread_risk": round(self.spread_risk, 3),
            "sentiment_risk": round(self.sentiment_risk, 3),
            "reasoning": self.reasoning,
            "computed_at": self.computed_at.isoformat(),
        }


class EmbeddingCache:
    """
    Simple LRU cache for text embeddings.
    """
    
    def __init__(self, max_size: int = 1000):
        self._cache: Dict[str, Any] = {}
        self._access_order: List[str] = []
        self._max_size = max_size
        self._lock = RLock()
    
    def _hash_text(self, text: str) -> str:
        """Create hash key for text."""
        return hashlib.md5(text.encode()).hexdigest()
    
    def get(self, text: str) -> Optional[Any]:
        """Get cached embedding."""
        with self._lock:
            key = self._hash_text(text)
            if key in self._cache:
                # Update access order
                self._access_order.remove(key)
                self._access_order.append(key)
                return self._cache[key]
            return None
    
    def set(self, text: str, embedding: Any) -> None:
        """Cache an embedding."""
        with self._lock:
            key = self._hash_text(text)
            
            # Evict if necessary
            while len(self._cache) >= self._max_size:
                oldest = self._access_order.pop(0)
                del self._cache[oldest]
            
            self._cache[key] = embedding
            self._access_order.append(key)
    
    def clear(self) -> None:
        """Clear the cache."""
        with self._lock:
            self._cache.clear()
            self._access_order.clear()


class SemanticContextEngine:
    """
    Semantic context engine for FX arbitrage risk assessment.
    
    Analyzes:
    - Trading session (time-of-day effects)
    - Volatility regime (recent price movement)
    - Spread regime (recent spread conditions)
    - External signals (news, sentiment via embeddings)
    
    Output: Risk score that adjusts arbitrage confidence
    
    IMPORTANT: This engine ADJUSTS confidence but NEVER overrides
    the core arbitrage detection logic. All outputs are fully
    explainable with reasoning chains.
    
    Usage:
        engine = SemanticContextEngine()
        result = engine.analyze(ContextInput(
            symbol="EURUSD",
            session=SessionContext.LONDON,
            volatility_samples=[...],
            spread_samples=[...],
            text_signals=["ECB rate decision expected..."],
        ))
        
        # Apply adjustment
        adjusted_confidence = original_confidence + result.confidence_adjustment
    """
    
    # Session risk factors (empirically derived)
    SESSION_RISK = {
        SessionContext.TOKYO: 0.2,           # Typically calm
        SessionContext.LONDON: 0.4,          # High activity
        SessionContext.NEW_YORK: 0.5,        # Highest volatility
        SessionContext.SYDNEY: 0.1,          # Very calm
        SessionContext.OVERLAP_LONDON_NY: 0.6,  # Maximum activity
        SessionContext.OVERLAP_TOKYO_LONDON: 0.5,
        SessionContext.OFF_HOURS: 0.3,       # Less liquid but calmer
    }
    
    # Risk keywords for simple sentiment (fallback when embeddings disabled)
    RISK_KEYWORDS = {
        "high": ["crisis", "crash", "volatility", "panic", "emergency", 
                 "intervention", "flash", "collapse", "plunge", "surge"],
        "medium": ["uncertainty", "concern", "risk", "warning", "caution",
                  "unexpected", "surprise", "shock", "decision", "announcement"],
        "low": ["stable", "steady", "calm", "normal", "expected", "unchanged"],
    }
    
    def __init__(self, config: Optional[ContextConfig] = None):
        """
        Initialize semantic context engine.
        
        Args:
            config: Configuration options
        """
        self._config = config or ContextConfig()
        self._lock = RLock()
        self._embedding_cache = EmbeddingCache()
        self._embedding_model = None
        self._model_loaded = False
        
        # Statistics
        self._analysis_count = 0
        self._avg_processing_time_ms = 0.0
        
        logger.info("[SemanticContextEngine] Initialized")
    
    @property
    def config(self) -> ContextConfig:
        return self._config
    
    def load_embedding_model(self) -> bool:
        """
        Load the embedding model (lazy loading).
        
        Returns:
            True if model loaded successfully
        """
        if not self._config.enable_embeddings:
            logger.info("[SemanticContextEngine] Embeddings disabled in config")
            return False
        
        if self._model_loaded:
            return True
        
        try:
            from sentence_transformers import SentenceTransformer
            self._embedding_model = SentenceTransformer(
                self._config.embedding_model
            )
            self._model_loaded = True
            logger.info(f"[SemanticContextEngine] Loaded model: {self._config.embedding_model}")
            return True
        except ImportError:
            logger.warning("[SemanticContextEngine] sentence-transformers not installed")
            return False
        except Exception as e:
            logger.error(f"[SemanticContextEngine] Failed to load model: {e}")
            return False
    
    def analyze(self, context_input: ContextInput) -> ContextResult:
        """
        Analyze context and compute risk score.
        
        Args:
            context_input: Context data for analysis
        
        Returns:
            ContextResult with risk scores and reasoning
        """
        start_time = time.time()
        reasoning = []
        
        # 1. Session risk
        session_risk = self._compute_session_risk(context_input, reasoning)
        
        # 2. Volatility regime
        volatility_regime, volatility_risk = self._compute_volatility_risk(
            context_input, reasoning
        )
        
        # 3. Spread regime
        spread_regime, spread_risk = self._compute_spread_risk(
            context_input, reasoning
        )
        
        # 4. Sentiment risk (from text signals)
        sentiment_risk = self._compute_sentiment_risk(context_input, reasoning)
        
        # 5. Combine risks with weights
        cfg = self._config
        total_weight = (cfg.risk_weight_session + cfg.risk_weight_volatility + 
                       cfg.risk_weight_spread + cfg.risk_weight_sentiment)
        
        risk_score = (
            cfg.risk_weight_session * session_risk +
            cfg.risk_weight_volatility * volatility_risk +
            cfg.risk_weight_spread * spread_risk +
            cfg.risk_weight_sentiment * sentiment_risk
        ) / total_weight
        
        # 6. Compute confidence adjustment
        # High risk = negative adjustment, low risk = slight positive
        confidence_adjustment = self._compute_confidence_adjustment(
            risk_score, reasoning
        )
        
        # Update stats
        elapsed_ms = (time.time() - start_time) * 1000
        with self._lock:
            self._analysis_count += 1
            alpha = 2 / (min(self._analysis_count, 100) + 1)
            self._avg_processing_time_ms = (
                alpha * elapsed_ms + (1 - alpha) * self._avg_processing_time_ms
            )
        
        reasoning.append(
            f"Final risk: {risk_score:.3f}, confidence adjustment: {confidence_adjustment:+.3f}"
        )
        
        return ContextResult(
            risk_score=risk_score,
            confidence_adjustment=confidence_adjustment,
            volatility_regime=volatility_regime,
            spread_regime=spread_regime,
            session_risk=session_risk,
            volatility_risk=volatility_risk,
            spread_risk=spread_risk,
            sentiment_risk=sentiment_risk,
            reasoning=reasoning,
        )
    
    def _compute_session_risk(self, ctx: ContextInput, 
                              reasoning: List[str]) -> float:
        """Compute risk from trading session."""
        if ctx.session is None:
            reasoning.append("Session: Unknown (default risk 0.3)")
            return 0.3
        
        risk = self.SESSION_RISK.get(ctx.session, 0.3)
        reasoning.append(f"Session: {ctx.session.value} (risk={risk:.2f})")
        return risk
    
    def _compute_volatility_risk(self, ctx: ContextInput,
                                 reasoning: List[str]) -> Tuple[VolatilityRegime, float]:
        """Compute risk from volatility regime."""
        if not ctx.volatility_samples:
            reasoning.append("Volatility: No samples (default NORMAL, risk=0.3)")
            return VolatilityRegime.NORMAL, 0.3
        
        samples = ctx.volatility_samples[-100:]  # Use recent samples
        mean_vol = sum(samples) / len(samples)
        
        # Standard deviation
        if len(samples) > 1:
            variance = sum((x - mean_vol) ** 2 for x in samples) / len(samples)
            std_vol = math.sqrt(variance)
        else:
            std_vol = 0
        
        # Classify regime (thresholds are symbol-dependent in practice)
        # Here we use normalized z-score approach
        recent_vol = samples[-1] if samples else mean_vol
        
        if std_vol > 0:
            z_score = (recent_vol - mean_vol) / std_vol
        else:
            z_score = 0
        
        if z_score < -1:
            regime = VolatilityRegime.LOW
            risk = 0.1
        elif z_score < 1:
            regime = VolatilityRegime.NORMAL
            risk = 0.3
        elif z_score < 2:
            regime = VolatilityRegime.HIGH
            risk = 0.6
        else:
            regime = VolatilityRegime.EXTREME
            risk = 0.9
        
        reasoning.append(
            f"Volatility: {regime.value} (z={z_score:.2f}, risk={risk:.2f})"
        )
        return regime, risk
    
    def _compute_spread_risk(self, ctx: ContextInput,
                            reasoning: List[str]) -> Tuple[SpreadRegime, float]:
        """Compute risk from spread regime."""
        if not ctx.spread_samples:
            reasoning.append("Spread: No samples (default NORMAL, risk=0.3)")
            return SpreadRegime.NORMAL, 0.3
        
        samples = ctx.spread_samples[-50:]
        mean_spread = sum(samples) / len(samples)
        recent_spread = samples[-1]
        
        # Classify based on ratio to mean
        ratio = recent_spread / mean_spread if mean_spread > 0 else 1.0
        
        if ratio < 0.8:
            regime = SpreadRegime.TIGHT
            risk = 0.1  # Good conditions
        elif ratio < 1.5:
            regime = SpreadRegime.NORMAL
            risk = 0.3
        elif ratio < 3.0:
            regime = SpreadRegime.WIDE
            risk = 0.6
        else:
            regime = SpreadRegime.ILLIQUID
            risk = 0.9
        
        reasoning.append(
            f"Spread: {regime.value} (ratio={ratio:.2f}, risk={risk:.2f})"
        )
        return regime, risk
    
    def _compute_sentiment_risk(self, ctx: ContextInput,
                               reasoning: List[str]) -> float:
        """Compute risk from text signals (sentiment analysis)."""
        if not ctx.text_signals:
            reasoning.append("Sentiment: No text signals (default risk=0.2)")
            return 0.2
        
        signals = ctx.text_signals[-self._config.max_text_signals:]
        
        # Try embedding-based analysis first
        if self._model_loaded and self._embedding_model is not None:
            risk = self._analyze_with_embeddings(signals, reasoning)
        else:
            # Fallback to keyword-based analysis
            risk = self._analyze_with_keywords(signals, reasoning)
        
        return risk
    
    def _analyze_with_keywords(self, signals: List[str],
                               reasoning: List[str]) -> float:
        """Simple keyword-based sentiment analysis."""
        combined = " ".join(signals).lower()
        
        high_count = sum(1 for kw in self.RISK_KEYWORDS["high"] if kw in combined)
        med_count = sum(1 for kw in self.RISK_KEYWORDS["medium"] if kw in combined)
        low_count = sum(1 for kw in self.RISK_KEYWORDS["low"] if kw in combined)
        
        total = high_count + med_count + low_count
        if total == 0:
            reasoning.append("Sentiment: No keywords found (default risk=0.3)")
            return 0.3
        
        # Weighted score
        risk = (high_count * 0.8 + med_count * 0.5 + low_count * 0.1) / total
        
        reasoning.append(
            f"Sentiment (keywords): high={high_count}, med={med_count}, "
            f"low={low_count} (risk={risk:.2f})"
        )
        return risk
    
    def _analyze_with_embeddings(self, signals: List[str],
                                reasoning: List[str]) -> float:
        """Embedding-based semantic analysis."""
        try:
            # Risk reference phrases for semantic similarity
            risk_phrases = [
                "market crash and financial crisis",      # High risk
                "extreme volatility and uncertainty",     # High risk
                "stable market conditions expected",      # Low risk
                "normal trading activity",                # Low risk
            ]
            
            # Get embeddings (with caching)
            signal_embeddings = []
            for signal in signals:
                cached = self._embedding_cache.get(signal)
                if cached is not None:
                    signal_embeddings.append(cached)
                else:
                    embedding = self._embedding_model.encode(signal)
                    self._embedding_cache.set(signal, embedding)
                    signal_embeddings.append(embedding)
            
            reference_embeddings = []
            for phrase in risk_phrases:
                cached = self._embedding_cache.get(phrase)
                if cached is not None:
                    reference_embeddings.append(cached)
                else:
                    embedding = self._embedding_model.encode(phrase)
                    self._embedding_cache.set(phrase, embedding)
                    reference_embeddings.append(embedding)
            
            # Compute similarities
            import numpy as np
            
            avg_signal = np.mean(signal_embeddings, axis=0)
            
            # Similarity to high-risk phrases (first 2)
            high_risk_sim = max(
                np.dot(avg_signal, reference_embeddings[0]) / 
                (np.linalg.norm(avg_signal) * np.linalg.norm(reference_embeddings[0])),
                np.dot(avg_signal, reference_embeddings[1]) / 
                (np.linalg.norm(avg_signal) * np.linalg.norm(reference_embeddings[1])),
            )
            
            # Similarity to low-risk phrases (last 2)
            low_risk_sim = max(
                np.dot(avg_signal, reference_embeddings[2]) / 
                (np.linalg.norm(avg_signal) * np.linalg.norm(reference_embeddings[2])),
                np.dot(avg_signal, reference_embeddings[3]) / 
                (np.linalg.norm(avg_signal) * np.linalg.norm(reference_embeddings[3])),
            )
            
            # Convert to risk score
            if high_risk_sim > low_risk_sim:
                risk = 0.5 + 0.4 * (high_risk_sim - low_risk_sim)
            else:
                risk = 0.5 - 0.4 * (low_risk_sim - high_risk_sim)
            
            risk = max(0.0, min(1.0, risk))
            
            reasoning.append(
                f"Sentiment (embeddings): high_sim={high_risk_sim:.3f}, "
                f"low_sim={low_risk_sim:.3f} (risk={risk:.2f})"
            )
            return risk
            
        except Exception as e:
            logger.warning(f"[SemanticContextEngine] Embedding analysis failed: {e}")
            return self._analyze_with_keywords(signals, reasoning)
    
    def _compute_confidence_adjustment(self, risk_score: float,
                                       reasoning: List[str]) -> float:
        """
        Compute confidence adjustment from risk score.
        
        High risk -> negative adjustment (reduce confidence)
        Low risk -> slight positive adjustment (increase confidence)
        """
        max_adj = self._config.max_risk_adjustment
        
        # Risk 0.5 = neutral (no adjustment)
        # Risk 1.0 = maximum negative adjustment
        # Risk 0.0 = small positive adjustment
        
        if risk_score >= 0.5:
            # Linear negative adjustment
            adjustment = -max_adj * (risk_score - 0.5) / 0.5
        else:
            # Smaller positive adjustment (capped at +0.1)
            adjustment = min(0.1, max_adj * 0.3 * (0.5 - risk_score) / 0.5)
        
        reasoning.append(
            f"Confidence adjustment: risk {risk_score:.3f} -> {adjustment:+.3f}"
        )
        return adjustment
    
    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics."""
        with self._lock:
            return {
                "analysis_count": self._analysis_count,
                "avg_processing_time_ms": round(self._avg_processing_time_ms, 2),
                "embeddings_enabled": self._config.enable_embeddings,
                "model_loaded": self._model_loaded,
                "cache_size": len(self._embedding_cache._cache),
            }
    
    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return self._config.to_dict()
    
    def update_config(self, **kwargs) -> None:
        """Update configuration dynamically."""
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self._config, key):
                    setattr(self._config, key, value)
                    logger.info(f"[SemanticContextEngine] Updated {key} = {value}")
    
    def clear_cache(self) -> None:
        """Clear the embedding cache."""
        self._embedding_cache.clear()
        logger.info("[SemanticContextEngine] Cache cleared")


# ============================================================================
# ML SCAFFOLD - Placeholder for Future ML Models
# ============================================================================

@dataclass
class MLFeatureVector:
    """
    Feature vector for ML models.
    
    This is the scaffold for future ML integration. Currently provides
    rule-based defaults, but the structure is ready for trained models.
    """
    # Price features
    price_deviation_from_mean: float = 0.0
    price_velocity: float = 0.0
    price_acceleration: float = 0.0
    
    # Spread features
    spread_zscore: float = 0.0
    spread_percentile: float = 0.5
    
    # Session features  
    session_minutes: int = 0
    is_overlap: bool = False
    is_market_open: bool = True
    
    # Opportunity features
    persistence_ms: int = 0
    detection_count: int = 0
    profit_pips: float = 0.0
    provider_diversity: int = 0
    
    # Historical features
    similar_opportunities_last_hour: int = 0
    success_rate_similar: float = 0.5
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "price_deviation_from_mean": round(self.price_deviation_from_mean, 4),
            "price_velocity": round(self.price_velocity, 4),
            "price_acceleration": round(self.price_acceleration, 4),
            "spread_zscore": round(self.spread_zscore, 3),
            "spread_percentile": round(self.spread_percentile, 3),
            "session_minutes": self.session_minutes,
            "is_overlap": self.is_overlap,
            "is_market_open": self.is_market_open,
            "persistence_ms": self.persistence_ms,
            "detection_count": self.detection_count,
            "profit_pips": round(self.profit_pips, 3),
            "provider_diversity": self.provider_diversity,
            "similar_opportunities_last_hour": self.similar_opportunities_last_hour,
            "success_rate_similar": round(self.success_rate_similar, 3),
        }
    
    def to_array(self) -> List[float]:
        """Convert to numeric array for ML model input."""
        return [
            self.price_deviation_from_mean,
            self.price_velocity,
            self.price_acceleration,
            self.spread_zscore,
            self.spread_percentile,
            self.session_minutes / 1440.0,  # Normalize to 0-1
            1.0 if self.is_overlap else 0.0,
            1.0 if self.is_market_open else 0.0,
            min(self.persistence_ms / 1000.0, 5.0),  # Capped at 5s
            min(self.detection_count / 10.0, 1.0),  # Capped
            min(self.profit_pips / 5.0, 1.0),  # Capped
            self.provider_diversity / 6.0,  # Normalize by max providers
            min(self.similar_opportunities_last_hour / 20.0, 1.0),
            self.success_rate_similar,
        ]


@dataclass
class MLPrediction:
    """
    ML model prediction output.
    
    Scaffold for future ML predictions. Currently uses rule-based logic.
    """
    # Core predictions
    execution_success_probability: float  # P(successful execution)
    expected_profit_pips: float           # Expected profit if executed
    optimal_hold_time_ms: int             # Suggested time to wait
    
    # Confidence in prediction
    model_confidence: float               # How confident is the model
    
    # Explanation
    top_contributing_features: List[str]
    reasoning: str
    
    # Model metadata
    model_name: str = "rule_based_v1"
    model_version: str = "1.0.0"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_success_probability": round(self.execution_success_probability, 3),
            "expected_profit_pips": round(self.expected_profit_pips, 3),
            "optimal_hold_time_ms": self.optimal_hold_time_ms,
            "model_confidence": round(self.model_confidence, 3),
            "top_contributing_features": self.top_contributing_features,
            "reasoning": self.reasoning,
            "model_name": self.model_name,
            "model_version": self.model_version,
        }


class SemanticContextInterface:
    """
    High-level interface for semantic context analysis.
    
    This is the ML scaffold that provides a clean interface for:
    1. Rule-based analysis (current implementation)
    2. Future ML model integration
    
    The interface is designed so that swapping in a trained ML model
    requires minimal code changes.
    
    Example (rule-based - current):
        interface = SemanticContextInterface()
        prediction = interface.predict_opportunity_outcome(features)
        
    Example (future ML):
        interface = SemanticContextInterface(model_path="models/arb_classifier.pkl")
        prediction = interface.predict_opportunity_outcome(features)
    """
    
    def __init__(
        self,
        context_engine: Optional[SemanticContextEngine] = None,
        model_path: Optional[str] = None,
    ):
        """
        Initialize the semantic context interface.
        
        Args:
            context_engine: SemanticContextEngine instance (or creates new)
            model_path: Path to trained ML model (future use)
        """
        self._context_engine = context_engine or SemanticContextEngine()
        self._model_path = model_path
        self._model = None
        self._use_ml = False
        
        # Statistics
        self._prediction_count = 0
        self._avg_latency_ms = 0.0
        
        # Load ML model if provided
        if model_path:
            self._load_model(model_path)
        
        logger.info(f"[SemanticContextInterface] Initialized (ML={self._use_ml})")
    
    def _load_model(self, model_path: str) -> bool:
        """
        Load a trained ML model.
        
        Placeholder for future ML integration. Currently logs and returns False.
        
        Args:
            model_path: Path to model file
        
        Returns:
            True if model loaded successfully
        """
        try:
            # Future: Load sklearn/pytorch/tensorflow model
            # self._model = joblib.load(model_path)
            # self._use_ml = True
            logger.info(f"[SemanticContextInterface] ML model loading not yet implemented: {model_path}")
            return False
        except Exception as e:
            logger.error(f"[SemanticContextInterface] Failed to load model: {e}")
            return False
    
    def extract_features(
        self,
        opportunity_data: Dict[str, Any],
        context_data: Optional[Dict[str, Any]] = None,
    ) -> MLFeatureVector:
        """
        Extract ML feature vector from opportunity and context data.
        
        This method standardizes feature extraction for both rule-based
        and ML-based prediction.
        
        Args:
            opportunity_data: Dictionary with opportunity details
            context_data: Optional additional context
        
        Returns:
            MLFeatureVector ready for prediction
        """
        context_data = context_data or {}
        
        features = MLFeatureVector(
            # Price features
            price_deviation_from_mean=opportunity_data.get("price_deviation", 0.0),
            price_velocity=opportunity_data.get("price_velocity", 0.0),
            price_acceleration=opportunity_data.get("price_acceleration", 0.0),
            
            # Spread features
            spread_zscore=opportunity_data.get("spread_zscore", 0.0),
            spread_percentile=opportunity_data.get("spread_percentile", 0.5),
            
            # Session features
            session_minutes=context_data.get("session_minutes", 0),
            is_overlap=context_data.get("is_overlap", False),
            is_market_open=context_data.get("is_market_open", True),
            
            # Opportunity features
            persistence_ms=opportunity_data.get("persistence_ms", 0),
            detection_count=opportunity_data.get("detection_count", 1),
            profit_pips=opportunity_data.get("profit_pips", 0.0),
            provider_diversity=opportunity_data.get("provider_diversity", 1),
            
            # Historical features
            similar_opportunities_last_hour=context_data.get("similar_last_hour", 0),
            success_rate_similar=context_data.get("success_rate_similar", 0.5),
        )
        
        return features
    
    def predict_opportunity_outcome(
        self,
        features: MLFeatureVector,
    ) -> MLPrediction:
        """
        Predict outcome for an arbitrage opportunity.
        
        Currently uses rule-based logic. Will use ML model when available.
        
        Args:
            features: Feature vector for the opportunity
        
        Returns:
            MLPrediction with success probability and recommendations
        """
        start_time = time.time()
        
        if self._use_ml and self._model is not None:
            prediction = self._predict_with_ml(features)
        else:
            prediction = self._predict_rule_based(features)
        
        # Update stats
        elapsed_ms = (time.time() - start_time) * 1000
        self._prediction_count += 1
        alpha = 2 / (min(self._prediction_count, 100) + 1)
        self._avg_latency_ms = alpha * elapsed_ms + (1 - alpha) * self._avg_latency_ms
        
        return prediction
    
    def _predict_rule_based(self, features: MLFeatureVector) -> MLPrediction:
        """
        Rule-based prediction (baseline implementation).
        
        This provides sensible defaults until ML models are trained.
        """
        # Base probability starts at 50%
        success_prob = 0.5
        contributing_features = []
        reasons = []
        
        # Persistence is the strongest signal
        if features.persistence_ms > 300:
            success_prob += 0.2
            contributing_features.append("persistence_ms")
            reasons.append(f"Persistent opportunity ({features.persistence_ms}ms)")
        elif features.persistence_ms > 50:
            success_prob += 0.1
            contributing_features.append("persistence_ms")
        elif features.persistence_ms < 30:
            success_prob -= 0.1
        
        # Detection count builds confidence
        if features.detection_count >= 3:
            success_prob += 0.1
            contributing_features.append("detection_count")
            reasons.append(f"Confirmed {features.detection_count}x")
        
        # Provider diversity is good
        if features.provider_diversity >= 4:
            success_prob += 0.1
            contributing_features.append("provider_diversity")
            reasons.append(f"Seen by {features.provider_diversity} providers")
        
        # Profit size affects feasibility
        if features.profit_pips > 2.0:
            success_prob += 0.1
            contributing_features.append("profit_pips")
            reasons.append(f"Strong profit potential ({features.profit_pips:.1f} pips)")
        elif features.profit_pips < 0.5:
            success_prob -= 0.15
            reasons.append("Marginal profit")
        
        # Session overlap is favorable
        if features.is_overlap:
            success_prob += 0.05
            contributing_features.append("is_overlap")
        
        # Wide spreads are dangerous
        if features.spread_zscore > 2:
            success_prob -= 0.15
            contributing_features.append("spread_zscore")
            reasons.append("Wide spread conditions")
        
        # Historical context
        if features.success_rate_similar > 0.7:
            success_prob += 0.1
            contributing_features.append("success_rate_similar")
        elif features.success_rate_similar < 0.3:
            success_prob -= 0.1
        
        # Clamp probability
        success_prob = max(0.1, min(0.95, success_prob))
        
        # Expected profit adjusted by probability
        expected_profit = features.profit_pips * success_prob * 0.8  # 20% haircut
        
        # Optimal hold time based on persistence
        if features.persistence_ms > 300:
            optimal_hold = 500  # Can afford to wait
        elif features.persistence_ms > 50:
            optimal_hold = 200
        else:
            optimal_hold = 50  # Act fast
        
        # Model confidence based on feature quality
        model_confidence = 0.6  # Base confidence for rule-based
        if features.detection_count >= 3 and features.persistence_ms > 100:
            model_confidence = 0.75
        if len(contributing_features) >= 4:
            model_confidence = 0.8
        
        reasoning = " | ".join(reasons) if reasons else "Standard conditions"
        
        return MLPrediction(
            execution_success_probability=success_prob,
            expected_profit_pips=expected_profit,
            optimal_hold_time_ms=optimal_hold,
            model_confidence=model_confidence,
            top_contributing_features=contributing_features[:5],
            reasoning=reasoning,
            model_name="rule_based_v1",
            model_version="1.0.0",
        )
    
    def _predict_with_ml(self, features: MLFeatureVector) -> MLPrediction:
        """
        ML-based prediction (placeholder for future).
        
        When a model is loaded, this will use the trained model for prediction.
        """
        # Future implementation:
        # X = np.array([features.to_array()])
        # proba = self._model.predict_proba(X)[0, 1]
        # ...
        
        # For now, fallback to rule-based
        return self._predict_rule_based(features)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get interface statistics."""
        return {
            "prediction_count": self._prediction_count,
            "avg_latency_ms": round(self._avg_latency_ms, 2),
            "use_ml": self._use_ml,
            "model_loaded": self._model is not None,
            "context_engine_stats": self._context_engine.get_stats(),
        }
    
    def analyze_context(self, context_input: ContextInput) -> ContextResult:
        """
        Passthrough to underlying context engine for backward compatibility.
        """
        return self._context_engine.analyze(context_input)
