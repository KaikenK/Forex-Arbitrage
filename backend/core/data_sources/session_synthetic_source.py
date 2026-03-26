"""
Session-Aware Synthetic USD/INR Data Source

Generates synthetic USD/INR tick data with session-specific and provider-specific
characteristics for research-grade arbitrage detection testing.

This module implements a sophisticated synthetic data generation pipeline that:
1. Maintains a shared reference USD/INR price across all sources
2. Applies session-dependent drift and volatility (Tokyo, London, New York)
3. Applies provider-specific noise and latency (Bloomberg vs Reuters)
4. Introduces realistic bid/ask discrepancies for arbitrage detection

Design Decisions:
- Thread-safe reference price shared across all synthetic sources
- Deterministic mode available for reproducible research experiments
- Session labels embedded in every tick for analysis
- Natural arbitrage opportunities through price divergence
"""

import random
import time
import math
import logging
import threading
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime

from backend.core.interfaces.data_source import (
    DataSourceInterface,
    DataSourceConfig,
    RawTick,
)
from backend.config import (
    TradingSession,
    DataProvider,
    SessionConfig,
    ProviderConfig,
    SyntheticFeedConfig,
    SESSION_CONFIGS,
    PROVIDER_CONFIGS,
    USDINR_BASE_PRICE,
    SYNTHETIC_GENERATION_CONFIG,
)

logger = logging.getLogger(__name__)


class SharedReferencePrice:
    """
    Thread-safe shared reference price for USD/INR.
    
    All synthetic sources track this reference price with their own
    session-specific and provider-specific modifications. This ensures
    realistic price correlation across sources while allowing divergence.
    
    The reference price follows a random walk with mean-reversion to
    prevent unbounded drift during long research sessions.
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        """Singleton pattern for shared reference."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._price = USDINR_BASE_PRICE
        self._base_price = USDINR_BASE_PRICE
        self._last_update_ms = 0
        self._price_lock = threading.Lock()
        self._rng = random.Random(SYNTHETIC_GENERATION_CONFIG.random_seed)
        
        # Mean-reversion parameters
        self._mean_reversion_strength = 0.001  # Gentle pull back to base
        self._volatility = 0.0002  # Base volatility for INR pairs
        
        # Track price history for analysis
        self._price_history: List[tuple] = []  # (timestamp_ms, price)
        self._max_history_size = 10000
        
        self._initialized = True
        logger.info(f"SharedReferencePrice initialized at {self._price}")
    
    def get_price(self) -> float:
        """Get current reference price."""
        with self._price_lock:
            return self._price
    
    def update(self, timestamp_ms: int) -> float:
        """
        Update reference price with random walk and mean-reversion.
        
        Called periodically to evolve the base USD/INR rate.
        
        Args:
            timestamp_ms: Current timestamp in milliseconds
        
        Returns:
            Updated reference price
        """
        with self._price_lock:
            # Only update at configured intervals
            update_interval = SYNTHETIC_GENERATION_CONFIG.reference_update_interval_ms
            if timestamp_ms - self._last_update_ms < update_interval:
                return self._price
            
            self._last_update_ms = timestamp_ms
            
            # Random walk component
            random_change = self._rng.gauss(0, self._volatility * self._price)
            
            # Mean-reversion component (pulls price back toward base)
            reversion = self._mean_reversion_strength * (self._base_price - self._price)
            
            # Apply changes
            self._price += random_change + reversion
            
            # Ensure price stays positive and within reasonable bounds
            min_price = self._base_price * 0.95  # -5% from base
            max_price = self._base_price * 1.05  # +5% from base
            self._price = max(min_price, min(max_price, self._price))
            
            # Record history
            self._price_history.append((timestamp_ms, self._price))
            if len(self._price_history) > self._max_history_size:
                self._price_history.pop(0)
            
            return self._price
    
    def reset(self, seed: Optional[int] = None) -> None:
        """
        Reset reference price to base for new research session.
        
        Args:
            seed: Optional new random seed for reproducibility
        """
        with self._price_lock:
            self._price = self._base_price
            self._price_history.clear()
            self._last_update_ms = 0
            if seed is not None:
                self._rng = random.Random(seed)
            logger.info(f"SharedReferencePrice reset to {self._price}")
    
    def get_history(self) -> List[tuple]:
        """Get price history for analysis."""
        with self._price_lock:
            return self._price_history.copy()


class SessionAwareSyntheticSource(DataSourceInterface):
    """
    Synthetic USD/INR data source with session and provider awareness.
    
    Generates realistic tick data that models:
    - Session-specific market microstructure (Tokyo/London/New York)
    - Provider-specific characteristics (Bloomberg/Reuters)
    - Natural arbitrage opportunities through controlled divergence
    
    Each source maintains its own price state that tracks the shared
    reference with session/provider-specific modifications.
    
    Example:
        config = DataSourceConfig(
            source_id="bloomberg_tokyo_usdinr",
            source_type="synthetic_session",
            display_name="Bloomberg Tokyo USD/INR",
            symbols=["USDINR"]
        )
        
        source = SessionAwareSyntheticSource(
            config=config,
            session=TradingSession.TOKYO,
            provider=DataProvider.BLOOMBERG
        )
        source.connect()
        tick = source.get_tick("USDINR")
    """
    
    def __init__(
        self,
        config: DataSourceConfig,
        session: TradingSession,
        provider: DataProvider,
        feed_config: Optional[SyntheticFeedConfig] = None,
    ):
        """
        Initialize session-aware synthetic source.
        
        Args:
            config: Data source configuration
            session: Trading session (TOKYO, LONDON, NEW_YORK)
            provider: Data provider (BLOOMBERG, REUTERS)
            feed_config: Optional complete feed configuration
        """
        super().__init__(config)
        
        self._session = session
        self._provider = provider
        self._feed_config = feed_config
        
        # Get session and provider configs
        self._session_config = SESSION_CONFIGS[session]
        self._provider_config = PROVIDER_CONFIGS[provider]
        
        # Shared reference price singleton
        self._reference = SharedReferencePrice()
        
        # Local price state (tracks reference with modifications)
        self._local_price: float = 0.0
        self._local_bid: float = 0.0
        self._local_ask: float = 0.0
        
        # Random number generator (seeded for reproducibility)
        seed = SYNTHETIC_GENERATION_CONFIG.random_seed
        if seed is not None:
            # Each source gets a unique but deterministic seed
            seed = seed + hash(config.source_id) % 10000
        self._rng = random.Random(seed)
        
        # Thread lock for concurrent access
        self._lock = threading.Lock()
        
        # Generation stats
        self._generated_count = 0
        self._last_tick_time_ms: int = 0
        
        # Price smoothing state (for Bloomberg's smoother prices)
        self._ema_price: Optional[float] = None
        
        # Session drift accumulator
        self._session_drift: float = 0.0
        
        logger.info(
            f"[{self.source_id}] SessionAwareSyntheticSource initialized: "
            f"session={session.value}, provider={provider.value}"
        )
    
    @property
    def session(self) -> TradingSession:
        """Get the trading session."""
        return self._session
    
    @property
    def provider(self) -> DataProvider:
        """Get the data provider."""
        return self._provider
    
    def connect(self) -> bool:
        """
        Initialize the synthetic data source.
        
        Returns:
            True if initialized successfully
        """
        try:
            # Initialize local price from reference
            self._local_price = self._reference.get_price()
            self._ema_price = self._local_price
            
            # Calculate initial spread
            pip_value = 0.01  # INR pairs use 0.01 pip value
            half_spread = (self._session_config.spread_pips * pip_value) / 2
            
            self._local_bid = self._local_price - half_spread
            self._local_ask = self._local_price + half_spread
            
            self._is_connected = True
            
            logger.info(
                f"[{self.source_id}] Connected - "
                f"Initial price: {self._local_price:.4f}, "
                f"Spread: {self._session_config.spread_pips} pips"
            )
            return True
            
        except Exception as e:
            logger.error(f"[{self.source_id}] Failed to connect: {e}")
            self._record_error()
            return False
    
    def disconnect(self) -> None:
        """Disconnect the synthetic data source."""
        self._is_connected = False
        logger.info(f"[{self.source_id}] Disconnected")
    
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Get the next synthetic tick for USD/INR.
        
        Generates a tick with:
        - Session-specific volatility and spread
        - Provider-specific latency and noise
        - Potential arbitrage-creating price divergence
        
        Rate limited by SYNTHETIC_GENERATION_CONFIG.tick_interval_ms
        
        Args:
            symbol: Currency pair symbol (must be USDINR)
        
        Returns:
            RawTick with synthetic price data, or None if too soon
        """
        if not self._is_connected:
            return None
        
        symbol = symbol.upper()
        if symbol != "USDINR":
            logger.warning(f"[{self.source_id}] Only USDINR supported, got {symbol}")
            return None
        
        # Rate limit: only generate new tick if enough time has passed
        current_time_ms = int(time.time() * 1000)
        tick_interval = SYNTHETIC_GENERATION_CONFIG.tick_interval_ms
        if self._last_tick_time_ms > 0 and (current_time_ms - self._last_tick_time_ms) < tick_interval:
            return None  # Too soon, return cached None
        
        with self._lock:
            try:
                return self._generate_tick(symbol)
            except Exception as e:
                logger.error(f"[{self.source_id}] Error generating tick: {e}")
                self._record_error()
                return None
    
    def _generate_tick(self, symbol: str) -> RawTick:
        """
        Generate a synthetic tick with full session/provider characteristics.
        
        The generation process:
        1. Get/update shared reference price
        2. Apply session-specific drift
        3. Apply session-specific volatility
        4. Apply provider-specific noise
        5. Calculate bid/ask with session spread
        6. Optionally inject arbitrage opportunity
        7. Apply provider-specific smoothing
        8. Add simulated latency
        """
        current_time_ms = int(time.time() * 1000)
        
        # Update reference price (may not change if too soon)
        ref_price = self._reference.update(current_time_ms)
        
        # Pip value for INR pairs
        pip_value = 0.01
        
        # ============================================================
        # 1. Apply session-specific drift
        # ============================================================
        session_cfg = self._session_config
        
        # Random drift with session-specific magnitude
        drift_change = self._rng.gauss(
            session_cfg.drift_pips_per_tick * pip_value,
            session_cfg.drift_std * pip_value
        )
        self._session_drift += drift_change
        
        # Limit cumulative drift
        max_drift = SYNTHETIC_GENERATION_CONFIG.max_divergence_pips * pip_value
        self._session_drift = max(-max_drift, min(max_drift, self._session_drift))
        
        # ============================================================
        # 2. Apply session-specific volatility
        # ============================================================
        base_volatility = 0.0002  # Base volatility
        session_volatility = base_volatility * session_cfg.volatility_multiplier
        volatility_change = self._rng.gauss(0, session_volatility * ref_price)
        
        # ============================================================
        # 3. Apply session-specific noise
        # ============================================================
        session_noise = self._rng.uniform(
            -session_cfg.noise_amplitude,
            session_cfg.noise_amplitude
        ) * pip_value
        
        # ============================================================
        # 4. Apply provider-specific noise
        # ============================================================
        provider_cfg = self._provider_config
        provider_noise = self._rng.uniform(
            -provider_cfg.noise_pips,
            provider_cfg.noise_pips
        ) * pip_value
        
        # ============================================================
        # 5. Calculate new mid price
        # ============================================================
        new_mid = (
            ref_price +           # Base reference
            self._session_drift + # Session drift
            volatility_change +   # Random volatility
            session_noise +       # Session noise
            provider_noise        # Provider noise
        )
        
        # ============================================================
        # 6. Apply provider-specific smoothing (EMA)
        # ============================================================
        if self._ema_price is None:
            self._ema_price = new_mid
        else:
            alpha = 1.0 - provider_cfg.smoothing_factor
            self._ema_price = alpha * new_mid + provider_cfg.smoothing_factor * self._ema_price
        
        smoothed_price = self._ema_price
        
        # ============================================================
        # 7. Inject arbitrage opportunity (probabilistic)
        # ============================================================
        arbitrage_bonus = 0.0
        if self._rng.random() < SYNTHETIC_GENERATION_CONFIG.arbitrage_injection_rate:
            # Create a temporary price divergence
            arbitrage_bonus = self._rng.choice([-1, 1]) * self._rng.uniform(0.5, 2.0) * pip_value
        
        final_mid = smoothed_price + arbitrage_bonus
        
        # ============================================================
        # 8. Calculate bid/ask with session spread
        # ============================================================
        base_spread_pips = session_cfg.spread_pips
        
        # Occasionally widen spread (spread anomaly)
        if self._rng.random() < SYNTHETIC_GENERATION_CONFIG.spread_anomaly_rate:
            spread_multiplier = self._rng.uniform(1.5, 2.5)
            base_spread_pips *= spread_multiplier
        
        half_spread = (base_spread_pips * pip_value) / 2
        
        # Add asymmetric spread noise (creates bid/ask opportunities)
        bid_skew = self._rng.uniform(-0.2, 0.2) * pip_value
        ask_skew = self._rng.uniform(-0.2, 0.2) * pip_value
        
        bid = final_mid - half_spread + bid_skew
        ask = final_mid + half_spread + ask_skew
        
        # Ensure bid < ask
        if bid >= ask:
            bid = final_mid - pip_value * 0.5
            ask = final_mid + pip_value * 0.5
        
        # Update local state
        self._local_price = final_mid
        self._local_bid = bid
        self._local_ask = ask
        
        # ============================================================
        # 9. Apply provider latency and jitter
        # ============================================================
        latency = provider_cfg.latency_ms
        jitter = self._rng.uniform(-provider_cfg.jitter_ms, provider_cfg.jitter_ms)
        
        # Tick timestamp is "delayed" by latency
        tick_time_ms = int(current_time_ms - latency - jitter)
        
        # ============================================================
        # 10. Create raw tick with extended metadata
        # ============================================================
        raw_tick = RawTick(
            symbol=symbol,
            bid=round(bid, 4),
            ask=round(ask, 4),
            timestamp_ms=tick_time_ms,
            source_id=self.source_id,
            volume=self._rng.randint(100, 10000),  # Synthetic volume
            extra={
                "mode": "synthetic_session",
                "session": self._session.value,
                "provider": self._provider.value,
                "reference_price": round(ref_price, 4),
                "session_drift": round(self._session_drift, 4),
                "spread_pips": round(base_spread_pips, 2),
                "latency_ms": round(latency + jitter, 1),
                "arbitrage_injected": arbitrage_bonus != 0,
            },
        )
        
        self._record_tick(tick_time_ms)
        self._generated_count += 1
        self._last_tick_time_ms = tick_time_ms
        
        return raw_tick
    
    def is_healthy(self) -> bool:
        """Check if synthetic source is healthy."""
        return self._is_connected
    
    def get_supported_symbols(self) -> List[str]:
        """Get list of supported symbols (USDINR only)."""
        return ["USDINR"]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for synthetic data source."""
        base_stats = super().get_stats()
        base_stats.update({
            "session": self._session.value,
            "provider": self._provider.value,
            "generated_count": self._generated_count,
            "current_price": round(self._local_price, 4),
            "current_bid": round(self._local_bid, 4),
            "current_ask": round(self._local_ask, 4),
            "session_drift": round(self._session_drift, 4),
            "reference_price": round(self._reference.get_price(), 4),
            "session_config": {
                "volatility_multiplier": self._session_config.volatility_multiplier,
                "spread_pips": self._session_config.spread_pips,
            },
            "provider_config": {
                "latency_ms": self._provider_config.latency_ms,
                "noise_pips": self._provider_config.noise_pips,
            },
        })
        return base_stats


def create_synthetic_sources() -> List[SessionAwareSyntheticSource]:
    """
    Factory function to create all six USD/INR synthetic sources.
    
    Creates:
    - bloomberg_tokyo_usdinr
    - reuters_tokyo_usdinr
    - bloomberg_london_usdinr
    - reuters_london_usdinr
    - bloomberg_newyork_usdinr
    - reuters_newyork_usdinr
    
    Returns:
        List of configured SessionAwareSyntheticSource instances
    """
    from backend.config import SYNTHETIC_FEEDS
    
    sources = []
    
    for feed_cfg in SYNTHETIC_FEEDS:
        # Get provider config for latency/reliability
        provider_cfg = PROVIDER_CONFIGS[feed_cfg.provider]
        
        # Create data source config
        ds_config = DataSourceConfig(
            source_id=feed_cfg.source_id,
            source_type="synthetic_session",
            display_name=feed_cfg.display_name,
            priority=feed_cfg.priority,
            latency_estimate_ms=provider_cfg.latency_ms,
            reliability_score=provider_cfg.reliability_score,
            symbols=[feed_cfg.symbol],
            extra_config={
                "session": feed_cfg.session.value,
                "provider": feed_cfg.provider.value,
            }
        )
        
        # Create synthetic source
        source = SessionAwareSyntheticSource(
            config=ds_config,
            session=feed_cfg.session,
            provider=feed_cfg.provider,
            feed_config=feed_cfg,
        )
        
        sources.append(source)
        logger.info(f"Created synthetic source: {feed_cfg.source_id}")
    
    return sources


def reset_synthetic_state(seed: Optional[int] = None) -> None:
    """
    Reset all synthetic price state for a new research session.
    
    Args:
        seed: Optional random seed for reproducibility
    """
    SharedReferencePrice().reset(seed)
    logger.info("Reset synthetic state for new research session")
