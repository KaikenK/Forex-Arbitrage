"""
Research Configuration Module

Central configuration for the FX Arbitrage Detection Research System.
This module defines the operational mode and all session-specific parameters
for the synthetic USD/INR data generation pipeline.

Design Decisions:
- Single configuration flag (DATA_MODE) controls all data source behavior
- Session-specific parameters model real-world FX market microstructure
- Provider-specific parameters simulate Bloomberg vs Reuters characteristics
- Deterministic seeding enables reproducible research experiments
"""

from enum import Enum
from dataclasses import dataclass, field
from typing import Dict, List, Optional


class DataMode(str, Enum):
    """
    Operational modes for the data pipeline.
    
    LIVE_MT5: Uses MetaTrader 5 and real broker feeds
    SYNTHETIC_USDINR_ONLY: Research mode with synthetic USD/INR feeds only
    """
    LIVE_MT5 = "LIVE_MT5"
    SYNTHETIC_USDINR_ONLY = "SYNTHETIC_USDINR_ONLY"


class TradingSession(str, Enum):
    """
    FX trading sessions for USD/INR price formation analysis.
    
    Each session represents a distinct liquidity regime and price discovery
    mechanism in the global USD/INR market.
    """
    TOKYO = "TOKYO"
    LONDON = "LONDON"
    NEW_YORK = "NEW_YORK"


class DataProvider(str, Enum):
    """
    Institutional data providers modeled in synthetic feeds.
    
    Bloomberg and Reuters represent the two dominant FX data providers
    with distinct latency and noise characteristics.
    """
    BLOOMBERG = "Bloomberg"
    REUTERS = "Reuters"


# ============================================================================
# MASTER CONFIGURATION FLAG
# ============================================================================

# Set this to DataMode.SYNTHETIC_USDINR_ONLY for research mode
# Set to DataMode.LIVE_MT5 for live trading mode (requires MetaTrader 5)
DATA_MODE = DataMode.SYNTHETIC_USDINR_ONLY

# ============================================================================
# SYMBOL CONFIGURATION
# ============================================================================

# The only currency pair supported in research mode
RESEARCH_SYMBOL = "USDINR"

# Base price for USD/INR (close to real-world reference, Mar 2026)
USDINR_BASE_PRICE = 86.50

# ============================================================================
# SESSION-SPECIFIC MARKET PARAMETERS
# ============================================================================

@dataclass
class SessionConfig:
    """
    Configuration for session-specific USD/INR market behavior.
    
    Models the microstructure differences across trading sessions:
    - Tokyo: Primary price discovery, lower volatility, wider spreads
    - London: Cross-border activity, moderate volatility, price drift
    - New York: USD-driven re-pricing, higher volatility, noisy
    """
    session: TradingSession
    volatility_multiplier: float  # Base volatility scaling factor
    spread_pips: float  # Average bid-ask spread in pips
    drift_pips_per_tick: float  # Session-specific price drift
    drift_std: float  # Standard deviation of drift
    noise_amplitude: float  # Random noise amplitude in pips
    description: str


# Session configurations modeling real-world FX market behavior
SESSION_CONFIGS: Dict[TradingSession, SessionConfig] = {
    TradingSession.TOKYO: SessionConfig(
        session=TradingSession.TOKYO,
        volatility_multiplier=0.7,  # Lower volatility
        spread_pips=3.0,  # Wider spreads due to lower liquidity
        drift_pips_per_tick=0.1,  # Intentional drift for demonstration
        drift_std=0.8,  # Increased variance
        noise_amplitude=0.8,
        description="Primary USD/INR price discovery session with lower volatility"
    ),
    TradingSession.LONDON: SessionConfig(
        session=TradingSession.LONDON,
        volatility_multiplier=1.0,  # Moderate volatility
        spread_pips=2.0,  # Tighter spreads, better liquidity
        drift_pips_per_tick=-0.15,  # Negative drift for divergence
        drift_std=1.2,  # Moderate drift variance
        noise_amplitude=1.2,
        description="Cross-border activity session with moderate volatility"
    ),
    TradingSession.NEW_YORK: SessionConfig(
        session=TradingSession.NEW_YORK,
        volatility_multiplier=1.5,  # Higher volatility
        spread_pips=2.5,  # Moderate spreads
        drift_pips_per_tick=0.2,  # Strong positive drift
        drift_std=1.8,  # Higher drift variance - noisier
        noise_amplitude=1.8,
        description="USD-driven re-pricing with higher volatility and noise"
    ),
}

# ============================================================================
# PROVIDER-SPECIFIC PARAMETERS
# ============================================================================

@dataclass
class ProviderConfig:
    """
    Configuration for provider-specific tick characteristics.
    
    Models the differences between Bloomberg and Reuters feeds:
    - Bloomberg: Faster, smoother, lower latency
    - Reuters: Slightly delayed, more micro-noise
    """
    provider: DataProvider
    latency_ms: float  # Base network latency
    jitter_ms: float  # Latency jitter range (+/-)
    noise_pips: float  # Provider-specific price noise
    smoothing_factor: float  # Price smoothing (1.0 = no smoothing)
    reliability_score: float  # Historical reliability (0.0 - 1.0)
    description: str


# Provider configurations
PROVIDER_CONFIGS: Dict[DataProvider, ProviderConfig] = {
    DataProvider.BLOOMBERG: ProviderConfig(
        provider=DataProvider.BLOOMBERG,
        latency_ms=50.0,  # Faster
        jitter_ms=10.0,  # Lower jitter
        noise_pips=0.2,  # Smoother prices
        smoothing_factor=0.95,  # More smoothing
        reliability_score=0.98,
        description="Faster, smoother institutional feed"
    ),
    DataProvider.REUTERS: ProviderConfig(
        provider=DataProvider.REUTERS,
        latency_ms=80.0,  # Slightly slower
        jitter_ms=25.0,  # Higher jitter
        noise_pips=0.4,  # More micro-noise
        smoothing_factor=0.85,  # Less smoothing
        reliability_score=0.95,
        description="Slightly delayed feed with micro-noise"
    ),
}

# ============================================================================
# SYNTHETIC FEED DEFINITIONS
# ============================================================================

@dataclass
class SyntheticFeedConfig:
    """
    Complete configuration for a synthetic USD/INR data source.
    
    Combines session and provider parameters with source identification.
    """
    source_id: str
    display_name: str
    symbol: str
    session: TradingSession
    provider: DataProvider
    priority: int  # Lower = higher priority for arbitrage detection


# Define all six synthetic feeds
SYNTHETIC_FEEDS: List[SyntheticFeedConfig] = [
    # Tokyo Session
    SyntheticFeedConfig(
        source_id="bloomberg_tokyo_usdinr",
        display_name="Bloomberg Tokyo USD/INR",
        symbol=RESEARCH_SYMBOL,
        session=TradingSession.TOKYO,
        provider=DataProvider.BLOOMBERG,
        priority=1
    ),
    SyntheticFeedConfig(
        source_id="reuters_tokyo_usdinr",
        display_name="Reuters Tokyo USD/INR",
        symbol=RESEARCH_SYMBOL,
        session=TradingSession.TOKYO,
        provider=DataProvider.REUTERS,
        priority=2
    ),
    # London Session
    SyntheticFeedConfig(
        source_id="bloomberg_london_usdinr",
        display_name="Bloomberg London USD/INR",
        symbol=RESEARCH_SYMBOL,
        session=TradingSession.LONDON,
        provider=DataProvider.BLOOMBERG,
        priority=3
    ),
    SyntheticFeedConfig(
        source_id="reuters_london_usdinr",
        display_name="Reuters London USD/INR",
        symbol=RESEARCH_SYMBOL,
        session=TradingSession.LONDON,
        provider=DataProvider.REUTERS,
        priority=4
    ),
    # New York Session
    SyntheticFeedConfig(
        source_id="bloomberg_newyork_usdinr",
        display_name="Bloomberg New York USD/INR",
        symbol=RESEARCH_SYMBOL,
        session=TradingSession.NEW_YORK,
        provider=DataProvider.BLOOMBERG,
        priority=5
    ),
    SyntheticFeedConfig(
        source_id="reuters_newyork_usdinr",
        display_name="Reuters New York USD/INR",
        symbol=RESEARCH_SYMBOL,
        session=TradingSession.NEW_YORK,
        provider=DataProvider.REUTERS,
        priority=6
    ),
]

# ============================================================================
# ARBITRAGE DETECTION PARAMETERS
# ============================================================================

@dataclass
class ArbitrageResearchConfig:
    """
    Configuration for arbitrage detection in research mode.
    
    Tuned for detecting both cross-provider and cross-session opportunities.
    """
    # Minimum profit thresholds (in pips) - higher = fewer opportunities
    min_profit_pips: float = 3.0  # Increased to filter out ALL background noise!
    
    # Confidence thresholds - higher = more selective
    min_confidence: float = 0.3  # Restored to 0.3
    
    # Time alignment
    alignment_window_ms: int = 200  # Wider window for synthetic data
    
    # Cross-provider detection
    enable_cross_provider: bool = True
    
    # Cross-session detection
    enable_cross_session: bool = True
    
    # Session inefficiency detection
    enable_session_inefficiency: bool = True


ARBITRAGE_RESEARCH_CONFIG = ArbitrageResearchConfig()

# ============================================================================
# SYNTHETIC GENERATION PARAMETERS
# ============================================================================

@dataclass
class SyntheticGenerationConfig:
    """
    Parameters for synthetic price generation.
    Modeled after Bloomberg/Reuters FX terminal update rates.
    """
    # Tick generation rate (milliseconds between ticks)
    # Bloomberg/Reuters FX terminals typically update 2-4 times per second
    tick_interval_ms: int = 50  # Much faster for demo/research feeling (20 ticks/sec)
    
    # Random seed for reproducibility (None for random behavior)
    random_seed: Optional[int] = 42
    
    # Reference price update frequency (how often base price drifts)
    reference_update_interval_ms: int = 250  # Drift every 250ms
    
    # Maximum divergence from reference (in pips) - keep prices realistic
    max_divergence_pips: float = 10.0  # Increased for wider spread demo
    
    # Arbitrage opportunity injection rate (probability per tick)
    # Lower = more realistic, opportunities are rare in real markets
    arbitrage_injection_rate: float = 0.0025  # ~1.5 opps/sec total
    
    # Spread anomaly rate (probability of wider spread)
    spread_anomaly_rate: float = 0.15


SYNTHETIC_GENERATION_CONFIG = SyntheticGenerationConfig()

# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def is_synthetic_mode() -> bool:
    """Check if system is in synthetic USD/INR only mode."""
    return DATA_MODE == DataMode.SYNTHETIC_USDINR_ONLY


def get_active_symbol() -> str:
    """Get the active symbol for the current mode."""
    if is_synthetic_mode():
        return RESEARCH_SYMBOL
    return "EURUSD"  # Default for live mode


def get_synthetic_feed_configs() -> List[SyntheticFeedConfig]:
    """Get all synthetic feed configurations."""
    return SYNTHETIC_FEEDS.copy()


def get_session_config(session: TradingSession) -> SessionConfig:
    """Get configuration for a specific session."""
    return SESSION_CONFIGS[session]


def get_provider_config(provider: DataProvider) -> ProviderConfig:
    """Get configuration for a specific provider."""
    return PROVIDER_CONFIGS[provider]
