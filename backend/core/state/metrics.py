"""
Time-Weighted Metrics

Provides EMA-based arbitrage metrics, stability scoring, and decay rates
for more robust opportunity assessment.

Design Principles:
- Exponential moving averages for noise reduction
- Stability scores based on variance and persistence
- Configurable decay rates for different time horizons
- Thread-safe for concurrent access
"""

import time
import math
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timezone
from collections import deque
from threading import RLock

logger = logging.getLogger(__name__)


class EMACalculator:
    """
    Exponential Moving Average calculator with configurable span.
    
    EMA = (current_value * k) + (previous_EMA * (1-k))
    where k = 2 / (span + 1)
    
    Usage:
        ema = EMACalculator(span=10)
        ema.update(1.0850)
        ema.update(1.0852)
        print(ema.value)
    """
    
    def __init__(self, span: int = 10, initial_value: Optional[float] = None):
        """
        Initialize EMA calculator.
        
        Args:
            span: EMA span (number of periods)
            initial_value: Optional initial value
        """
        self._span = span
        self._k = 2.0 / (span + 1)
        self._value: Optional[float] = initial_value
        self._count = 0
        self._last_update: Optional[float] = None
    
    @property
    def value(self) -> Optional[float]:
        return self._value
    
    @property
    def count(self) -> int:
        return self._count
    
    @property
    def is_primed(self) -> bool:
        """Whether EMA has enough data to be reliable."""
        return self._count >= self._span
    
    def update(self, value: float, timestamp: Optional[float] = None) -> float:
        """
        Update EMA with a new value.
        
        Args:
            value: New value to incorporate
            timestamp: Optional timestamp for time-weighted decay
        
        Returns:
            Updated EMA value
        """
        if self._value is None:
            self._value = value
        else:
            # Apply time decay if timestamps provided
            if timestamp is not None and self._last_update is not None:
                time_delta = timestamp - self._last_update
                # Adjust k based on time gap (larger gaps = more weight on new value)
                adjusted_k = min(1.0, self._k * (1 + time_delta))
            else:
                adjusted_k = self._k
            
            self._value = (value * adjusted_k) + (self._value * (1 - adjusted_k))
        
        self._count += 1
        self._last_update = timestamp or time.time()
        return self._value
    
    def reset(self, initial_value: Optional[float] = None) -> None:
        """Reset the EMA calculator."""
        self._value = initial_value
        self._count = 0
        self._last_update = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "span": self._span,
            "value": round(self._value, 6) if self._value else None,
            "count": self._count,
            "is_primed": self.is_primed,
        }


@dataclass
class StabilityScore:
    """
    Calculates stability score based on value consistency over time.
    
    Stability is measured by:
    1. Coefficient of variation (CV) - lower is more stable
    2. Trend consistency - values moving in same direction
    3. Duration - longer in stable state = higher score
    
    Score range: 0.0 (unstable) to 1.0 (highly stable)
    """
    window_size: int = 20
    min_samples: int = 3
    
    # Internal state
    _values: deque = field(default_factory=lambda: deque(maxlen=20))
    _timestamps: deque = field(default_factory=lambda: deque(maxlen=20))
    _score: float = 0.0
    
    def __post_init__(self):
        self._values = deque(maxlen=self.window_size)
        self._timestamps = deque(maxlen=self.window_size)
    
    @property
    def score(self) -> float:
        return self._score
    
    def add_sample(self, value: float, timestamp: Optional[float] = None) -> float:
        """
        Add a sample and recalculate stability score.
        
        Args:
            value: New value sample
            timestamp: Optional timestamp
        
        Returns:
            Updated stability score
        """
        self._values.append(value)
        self._timestamps.append(timestamp or time.time())
        
        if len(self._values) < self.min_samples:
            self._score = 0.0
            return self._score
        
        # Calculate coefficient of variation
        values = list(self._values)
        mean = sum(values) / len(values)
        
        if mean != 0:
            variance = sum((v - mean) ** 2 for v in values) / len(values)
            std_dev = math.sqrt(variance)
            cv = abs(std_dev / mean)
        else:
            cv = 0.0
        
        # CV score: lower CV = higher stability
        cv_score = max(0, 1 - cv * 10)  # Scale: CV of 0.1 = score of 0
        
        # Trend consistency score
        if len(values) >= 3:
            trends = []
            for i in range(1, len(values)):
                if values[i] > values[i-1]:
                    trends.append(1)
                elif values[i] < values[i-1]:
                    trends.append(-1)
                else:
                    trends.append(0)
            
            # Consistency: how many consecutive same-direction moves
            if trends:
                same_direction = sum(1 for i in range(1, len(trends)) 
                                    if trends[i] == trends[i-1]) / len(trends)
            else:
                same_direction = 0.5
            
            trend_score = same_direction
        else:
            trend_score = 0.5
        
        # Sample count score
        count_score = min(len(self._values) / self.window_size, 1.0)
        
        # Combined score
        self._score = 0.5 * cv_score + 0.3 * trend_score + 0.2 * count_score
        self._score = max(0.0, min(1.0, self._score))
        
        return self._score
    
    def reset(self) -> None:
        """Reset the stability calculator."""
        self._values.clear()
        self._timestamps.clear()
        self._score = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "score": round(self._score, 3),
            "sample_count": len(self._values),
            "is_stable": self._score >= 0.5,
        }


@dataclass
class ArbitrageMetrics:
    """
    Comprehensive time-weighted metrics for arbitrage opportunities.
    
    Tracks:
    - EMA of profit pips
    - EMA of confidence scores
    - Stability score
    - Decay-adjusted opportunity score
    """
    symbol: str
    profit_ema_span: int = 10
    confidence_ema_span: int = 5
    stability_window: int = 20
    decay_rate: float = 0.95  # Per-second decay
    
    # Internal state
    profit_ema: EMACalculator = None
    confidence_ema: EMACalculator = None
    stability: StabilityScore = None
    last_update_time: float = 0.0
    opportunity_score: float = 0.0
    
    def __post_init__(self):
        self.profit_ema = EMACalculator(span=self.profit_ema_span)
        self.confidence_ema = EMACalculator(span=self.confidence_ema_span)
        self.stability = StabilityScore(window_size=self.stability_window)
        self.last_update_time = time.time()
    
    def update(self, profit_pips: float, confidence: float,
               cross_spread_pips: float) -> Dict[str, float]:
        """
        Update metrics with new values.
        
        Args:
            profit_pips: Current profit potential in pips
            confidence: Current confidence score
            cross_spread_pips: Current cross-source spread
        
        Returns:
            Dictionary of updated metric values
        """
        now = time.time()
        
        # Apply time decay to existing metrics
        time_delta = now - self.last_update_time
        decay_factor = self.decay_rate ** time_delta
        
        # Update EMAs
        ema_profit = self.profit_ema.update(profit_pips, now)
        ema_confidence = self.confidence_ema.update(confidence, now)
        
        # Update stability (based on profit consistency)
        stability_score = self.stability.add_sample(profit_pips, now)
        
        # Calculate composite opportunity score
        # Higher profit, higher confidence, higher stability = higher score
        if profit_pips > 0:
            raw_score = (
                0.4 * min(profit_pips / 1.0, 1.0) +  # Cap at 1 pip
                0.3 * confidence +
                0.3 * stability_score
            ) * 100  # Scale to 0-100
            
            # Apply decay
            self.opportunity_score = raw_score * decay_factor
        else:
            self.opportunity_score *= decay_factor * 0.5  # Faster decay when no opportunity
        
        self.last_update_time = now
        
        return self.get_current_values()
    
    def get_current_values(self) -> Dict[str, float]:
        """Get current metric values."""
        return {
            "symbol": self.symbol,
            "profit_ema": round(self.profit_ema.value or 0, 4),
            "profit_ema_primed": self.profit_ema.is_primed,
            "confidence_ema": round(self.confidence_ema.value or 0, 3),
            "stability_score": round(self.stability.score, 3),
            "opportunity_score": round(self.opportunity_score, 2),
            "decay_rate": self.decay_rate,
        }
    
    def reset(self) -> None:
        """Reset all metrics."""
        self.profit_ema.reset()
        self.confidence_ema.reset()
        self.stability.reset()
        self.opportunity_score = 0.0
        self.last_update_time = time.time()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            **self.get_current_values(),
            "profit_ema_details": self.profit_ema.to_dict(),
            "confidence_ema_details": self.confidence_ema.to_dict(),
            "stability_details": self.stability.to_dict(),
        }


class TimeWeightedMetrics:
    """
    Manager for time-weighted metrics across all symbols.
    
    Provides centralized access to EMA-based metrics, stability scores,
    and decay-adjusted opportunity scoring.
    
    Usage:
        metrics = TimeWeightedMetrics()
        metrics.add_symbol("EURUSD")
        
        # Update on each batch
        values = metrics.update("EURUSD", profit_pips=0.3, confidence=0.8, ...)
        
        # Get all metrics
        all_metrics = metrics.get_all()
    """
    
    def __init__(self, profit_ema_span: int = 10, confidence_ema_span: int = 5,
                 stability_window: int = 20, decay_rate: float = 0.95):
        """
        Initialize the metrics manager.
        
        Args:
            profit_ema_span: Span for profit EMA
            confidence_ema_span: Span for confidence EMA
            stability_window: Window for stability calculation
            decay_rate: Per-second decay rate for opportunity scores
        """
        self._lock = RLock()
        self._metrics: Dict[str, ArbitrageMetrics] = {}
        self._profit_ema_span = profit_ema_span
        self._confidence_ema_span = confidence_ema_span
        self._stability_window = stability_window
        self._decay_rate = decay_rate
        
        logger.info(f"[TimeWeightedMetrics] Initialized with decay_rate={decay_rate}")
    
    def add_symbol(self, symbol: str) -> ArbitrageMetrics:
        """Add a symbol to track."""
        with self._lock:
            if symbol not in self._metrics:
                self._metrics[symbol] = ArbitrageMetrics(
                    symbol=symbol,
                    profit_ema_span=self._profit_ema_span,
                    confidence_ema_span=self._confidence_ema_span,
                    stability_window=self._stability_window,
                    decay_rate=self._decay_rate,
                )
                logger.debug(f"[TimeWeightedMetrics] Added symbol: {symbol}")
            return self._metrics[symbol]
    
    def update(self, symbol: str, profit_pips: float, confidence: float,
               cross_spread_pips: float) -> Dict[str, float]:
        """
        Update metrics for a symbol.
        
        Returns current metric values.
        """
        with self._lock:
            if symbol not in self._metrics:
                self.add_symbol(symbol)
            
            return self._metrics[symbol].update(
                profit_pips=profit_pips,
                confidence=confidence,
                cross_spread_pips=cross_spread_pips,
            )
    
    def get(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get metrics for a symbol."""
        with self._lock:
            if symbol in self._metrics:
                return self._metrics[symbol].to_dict()
            return None
    
    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """Get metrics for all symbols."""
        with self._lock:
            return {s: m.to_dict() for s, m in self._metrics.items()}
    
    def get_opportunity_rankings(self) -> List[Dict[str, Any]]:
        """
        Get symbols ranked by opportunity score.
        
        Returns list of (symbol, score, metrics) sorted by score descending.
        """
        with self._lock:
            rankings = []
            for symbol, metrics in self._metrics.items():
                rankings.append({
                    "symbol": symbol,
                    "opportunity_score": metrics.opportunity_score,
                    "profit_ema": metrics.profit_ema.value or 0,
                    "stability": metrics.stability.score,
                })
            
            rankings.sort(key=lambda x: x["opportunity_score"], reverse=True)
            return rankings
    
    def reset_symbol(self, symbol: str) -> None:
        """Reset metrics for a symbol."""
        with self._lock:
            if symbol in self._metrics:
                self._metrics[symbol].reset()
    
    def reset_all(self) -> None:
        """Reset all metrics."""
        with self._lock:
            for metrics in self._metrics.values():
                metrics.reset()
    
    def update_decay_rate(self, decay_rate: float) -> None:
        """Update decay rate for all symbols."""
        with self._lock:
            self._decay_rate = decay_rate
            for metrics in self._metrics.values():
                metrics.decay_rate = decay_rate
            logger.info(f"[TimeWeightedMetrics] Updated decay_rate to {decay_rate}")
