"""
Synthetic Data Source Plugin

Generates simulated FX tick data for testing and development.
Mimics institutional feeds like Bloomberg/Reuters with configurable
latency, jitter, and price noise to enable realistic arbitrage testing.

Design Decisions:
- Multiple modes: random walk, CSV replay, reference-based
- Configurable latency and jitter for realistic simulation
- Price noise to create synthetic spreads vs reference source
- Thread-safe for concurrent access
- Deterministic mode available for reproducible testing
"""

import random
import time
import math
import csv
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
import threading

from backend.core.interfaces.data_source import (
    DataSourceInterface,
    DataSourceConfig,
    RawTick,
)

logger = logging.getLogger(__name__)


@dataclass
class SyntheticConfig:
    """
    Configuration for synthetic data generation.
    
    Attributes:
        mode: Generation mode ("random_walk", "csv_replay", "reference")
        base_prices: Starting prices for each symbol {symbol: price}
        volatility: Price volatility factor (standard deviation per tick)
        spread_pips: Base spread in pips
        latency_ms: Simulated network latency in milliseconds
        jitter_ms: Random jitter range (+/- milliseconds)
        noise_pips: Random price noise in pips (creates arbitrage opportunities)
        tick_interval_ms: Interval between ticks in ms (for replay mode)
        csv_path: Path to CSV file for replay mode
        reference_source: Source ID to shadow (for reference mode)
        seed: Random seed for reproducibility (None for random)
    """
    mode: str = "random_walk"
    base_prices: Dict[str, float] = field(default_factory=lambda: {
        "EURUSD": 1.0850,
        "GBPUSD": 1.2650,
        "USDJPY": 149.50,
        "USDCHF": 0.8750,
        "AUDUSD": 0.6550,
        "USDCAD": 1.3550,
        "NZDUSD": 0.5950,
        "EURJPY": 162.20,
        "GBPJPY": 189.10,
        "EURGBP": 0.8580,
    })
    volatility: float = 0.00005  # 0.5 pips standard deviation
    spread_pips: float = 1.0  # 1 pip base spread
    latency_ms: float = 100.0  # 100ms simulated latency
    jitter_ms: float = 20.0  # +/- 20ms jitter
    noise_pips: float = 0.5  # 0.5 pip noise for arbitrage opportunities
    tick_interval_ms: int = 100  # 100ms between generated ticks
    csv_path: Optional[str] = None
    reference_source: Optional[str] = None
    seed: Optional[int] = None


class SyntheticDataSource(DataSourceInterface):
    """
    Synthetic FX data source for testing and development.
    
    Generates realistic tick data with configurable characteristics.
    Can operate in three modes:
    
    1. random_walk: Generates prices using random walk model
    2. csv_replay: Replays ticks from a CSV file
    3. reference: Shadows another source with noise (creates arbitrage)
    
    Example:
        config = DataSourceConfig(
            source_id="synthetic_bloomberg",
            source_type="synthetic",
            display_name="Synthetic Bloomberg Feed",
            latency_estimate_ms=100.0,
            symbols=["EURUSD", "GBPUSD"]
        )
        
        synthetic_config = SyntheticConfig(
            mode="random_walk",
            noise_pips=1.0,  # 1 pip difference from "true" price
            latency_ms=150.0,  # Simulate 150ms delay
        )
        
        source = SyntheticDataSource(config, synthetic_config)
        source.connect()
        tick = source.get_tick("EURUSD")
    """
    
    def __init__(
        self,
        config: DataSourceConfig,
        synthetic_config: Optional[SyntheticConfig] = None,
    ):
        """
        Initialize synthetic data source.
        
        Args:
            config: Data source configuration
            synthetic_config: Synthetic generation configuration
        """
        super().__init__(config)
        
        self._synthetic_config = synthetic_config or SyntheticConfig()
        
        # Current prices for each symbol
        self._current_prices: Dict[str, float] = {}
        
        # CSV replay state
        self._csv_data: Dict[str, List[Dict]] = {}  # {symbol: [rows]}
        self._csv_index: Dict[str, int] = {}  # {symbol: current_index}
        
        # Reference source for shadowing
        self._reference_source: Optional[DataSourceInterface] = None
        
        # Random number generator (seeded for reproducibility)
        self._rng = random.Random(self._synthetic_config.seed)
        
        # Thread lock for concurrent access
        self._lock = threading.Lock()
        
        # Last tick time per symbol
        self._last_tick_time: Dict[str, int] = {}
        
        # Generation stats
        self._generated_count = 0
    
    def connect(self) -> bool:
        """
        Initialize the synthetic data source.
        
        For random_walk mode, initializes prices from config.
        For csv_replay mode, loads the CSV file.
        
        Returns:
            True if initialized successfully
        """
        try:
            mode = self._synthetic_config.mode
            
            if mode == "random_walk":
                # Initialize prices from base prices
                for symbol in self._config.symbols:
                    if symbol in self._synthetic_config.base_prices:
                        self._current_prices[symbol] = self._synthetic_config.base_prices[symbol]
                    else:
                        logger.warning(f"[{self.source_id}] No base price for {symbol}, using 1.0")
                        self._current_prices[symbol] = 1.0
                
                logger.info(f"[{self.source_id}] Random walk mode initialized with {len(self._current_prices)} symbols")
            
            elif mode == "csv_replay":
                # Load CSV file
                csv_path = self._synthetic_config.csv_path
                if not csv_path or not Path(csv_path).exists():
                    logger.error(f"[{self.source_id}] CSV file not found: {csv_path}")
                    return False
                
                self._load_csv(csv_path)
                logger.info(f"[{self.source_id}] CSV replay mode initialized")
            
            elif mode == "reference":
                # Reference mode requires external setup
                logger.info(f"[{self.source_id}] Reference mode - waiting for reference source")
            
            else:
                logger.error(f"[{self.source_id}] Unknown mode: {mode}")
                return False
            
            self._is_connected = True
            return True
            
        except Exception as e:
            logger.error(f"[{self.source_id}] Failed to initialize: {e}")
            self._record_error()
            return False
    
    def disconnect(self) -> None:
        """Disconnect the synthetic data source."""
        self._is_connected = False
        self._current_prices.clear()
        self._csv_data.clear()
        self._csv_index.clear()
        logger.info(f"[{self.source_id}] Synthetic data source disconnected")
    
    def set_reference_source(self, source: DataSourceInterface) -> None:
        """
        Set the reference source for reference mode.
        
        In reference mode, this source shadows the reference source
        with added noise to simulate price differences.
        
        Args:
            source: Reference data source to shadow
        """
        self._reference_source = source
        logger.info(f"[{self.source_id}] Reference source set: {source.source_id}")
    
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Get the next synthetic tick for a symbol.
        
        Args:
            symbol: Currency pair symbol
        
        Returns:
            RawTick with synthetic price data
        """
        if not self._is_connected:
            return None
        
        symbol = symbol.upper()
        if symbol not in self._config.symbols:
            logger.warning(f"[{self.source_id}] Symbol {symbol} not configured")
            return None
        
        mode = self._synthetic_config.mode
        
        with self._lock:
            try:
                if mode == "random_walk":
                    return self._generate_random_walk_tick(symbol)
                elif mode == "csv_replay":
                    return self._get_csv_tick(symbol)
                elif mode == "reference":
                    return self._get_reference_tick(symbol)
                else:
                    return None
            except Exception as e:
                logger.error(f"[{self.source_id}] Error generating tick for {symbol}: {e}")
                self._record_error()
                return None
    
    def _generate_random_walk_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Generate a tick using random walk price model.
        
        The price follows: P(t+1) = P(t) + N(0, volatility)
        where N is a normal distribution.
        
        Args:
            symbol: Currency pair symbol
        
        Returns:
            RawTick with synthetic price
        """
        if symbol not in self._current_prices:
            return None
        
        # Get current price
        current_price = self._current_prices[symbol]
        
        # Apply random walk step
        volatility = self._synthetic_config.volatility
        price_change = self._rng.gauss(0, volatility)
        new_mid_price = current_price + price_change
        
        # Ensure price stays positive
        new_mid_price = max(0.00001, new_mid_price)
        
        # Update stored price
        self._current_prices[symbol] = new_mid_price
        
        # Calculate pip value for spread calculation
        pip_value = 0.01 if "JPY" in symbol else 0.0001
        
        # Calculate bid/ask with spread
        spread_pips = self._synthetic_config.spread_pips
        half_spread = (spread_pips * pip_value) / 2
        
        # Add noise to create arbitrage opportunities
        noise_pips = self._synthetic_config.noise_pips
        noise = self._rng.uniform(-noise_pips, noise_pips) * pip_value
        
        bid = new_mid_price - half_spread + noise
        ask = new_mid_price + half_spread + noise
        
        # Ensure bid < ask
        if bid >= ask:
            spread_adj = pip_value * 0.1
            bid = new_mid_price - spread_adj
            ask = new_mid_price + spread_adj
        
        # Apply latency and jitter to timestamp
        current_time_ms = int(time.time() * 1000)
        latency = self._synthetic_config.latency_ms
        jitter = self._rng.uniform(-self._synthetic_config.jitter_ms, 
                                    self._synthetic_config.jitter_ms)
        
        # The tick timestamp is "delayed" by latency + jitter
        # This simulates when the tick was generated vs when we received it
        tick_time_ms = int(current_time_ms - latency - jitter)
        
        # Create raw tick
        raw_tick = RawTick(
            symbol=symbol,
            bid=round(bid, 5),
            ask=round(ask, 5),
            timestamp_ms=tick_time_ms,
            source_id=self.source_id,
            volume=self._rng.randint(1, 100),  # Random volume
            extra={"mode": "random_walk", "noise_applied": noise},
        )
        
        self._record_tick(tick_time_ms)
        self._generated_count += 1
        
        return raw_tick
    
    def _get_reference_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Get a tick based on reference source with added noise.
        
        This creates realistic arbitrage opportunities by taking
        the reference price and adding noise/latency.
        
        Args:
            symbol: Currency pair symbol
        
        Returns:
            RawTick with noisy price based on reference
        """
        if self._reference_source is None:
            logger.warning(f"[{self.source_id}] No reference source configured")
            return None
        
        # Get tick from reference source
        ref_tick = self._reference_source.get_tick(symbol)
        if ref_tick is None:
            return None
        
        # Calculate pip value
        pip_value = 0.01 if "JPY" in symbol else 0.0001
        
        # Add noise to create price difference
        noise_pips = self._synthetic_config.noise_pips
        noise = self._rng.uniform(-noise_pips, noise_pips) * pip_value
        
        # Apply noise to both bid and ask
        noisy_bid = ref_tick.bid + noise
        noisy_ask = ref_tick.ask + noise
        
        # Sometimes widen the spread slightly
        if self._rng.random() < 0.3:  # 30% chance
            spread_widen = self._rng.uniform(0, 0.5) * pip_value
            noisy_bid -= spread_widen / 2
            noisy_ask += spread_widen / 2
        
        # Apply latency delay
        latency = self._synthetic_config.latency_ms
        jitter = self._rng.uniform(-self._synthetic_config.jitter_ms,
                                    self._synthetic_config.jitter_ms)
        
        current_time_ms = int(time.time() * 1000)
        tick_time_ms = int(current_time_ms - latency - jitter)
        
        raw_tick = RawTick(
            symbol=symbol,
            bid=round(noisy_bid, 5),
            ask=round(noisy_ask, 5),
            timestamp_ms=tick_time_ms,
            source_id=self.source_id,
            volume=ref_tick.volume,
            extra={
                "mode": "reference",
                "reference_source": self._reference_source.source_id,
                "noise_applied": noise,
            },
        )
        
        self._record_tick(tick_time_ms)
        self._generated_count += 1
        
        return raw_tick
    
    def _load_csv(self, csv_path: str) -> None:
        """
        Load tick data from CSV file.
        
        Expected CSV format:
        symbol,timestamp_ms,bid,ask,volume
        EURUSD,1706745600000,1.0850,1.0851,100
        
        Args:
            csv_path: Path to CSV file
        """
        with open(csv_path, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = row.get('symbol', '').upper()
                if symbol and symbol in self._config.symbols:
                    if symbol not in self._csv_data:
                        self._csv_data[symbol] = []
                        self._csv_index[symbol] = 0
                    self._csv_data[symbol].append(row)
        
        logger.info(f"[{self.source_id}] Loaded CSV data: {[(s, len(d)) for s, d in self._csv_data.items()]}")
    
    def _get_csv_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Get next tick from CSV replay.
        
        Args:
            symbol: Currency pair symbol
        
        Returns:
            RawTick from CSV data
        """
        if symbol not in self._csv_data or not self._csv_data[symbol]:
            return None
        
        rows = self._csv_data[symbol]
        index = self._csv_index.get(symbol, 0)
        
        if index >= len(rows):
            # Loop back to start
            index = 0
        
        row = rows[index]
        self._csv_index[symbol] = index + 1
        
        # Adjust timestamp to current time (replay with current timing)
        current_time_ms = int(time.time() * 1000)
        latency = self._synthetic_config.latency_ms
        jitter = self._rng.uniform(-self._synthetic_config.jitter_ms,
                                    self._synthetic_config.jitter_ms)
        tick_time_ms = int(current_time_ms - latency - jitter)
        
        raw_tick = RawTick(
            symbol=symbol,
            bid=float(row.get('bid', 0)),
            ask=float(row.get('ask', 0)),
            timestamp_ms=tick_time_ms,
            source_id=self.source_id,
            volume=int(row.get('volume', 0)) if row.get('volume') else None,
            extra={"mode": "csv_replay", "original_time": row.get('timestamp_ms')},
        )
        
        self._record_tick(tick_time_ms)
        self._generated_count += 1
        
        return raw_tick
    
    def is_healthy(self) -> bool:
        """Check if synthetic source is healthy."""
        return self._is_connected
    
    def get_supported_symbols(self) -> List[str]:
        """Get list of supported symbols."""
        return self._config.symbols.copy()
    
    def inject_price(self, symbol: str, bid: float, ask: float) -> None:
        """
        Manually inject a specific price for testing.
        
        Useful for creating specific arbitrage scenarios in tests.
        
        Args:
            symbol: Currency pair symbol
            bid: Bid price to inject
            ask: Ask price to inject
        """
        with self._lock:
            mid = (bid + ask) / 2
            self._current_prices[symbol] = mid
            logger.debug(f"[{self.source_id}] Injected price for {symbol}: {bid}/{ask}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for synthetic data source."""
        base_stats = super().get_stats()
        base_stats.update({
            "mode": self._synthetic_config.mode,
            "generated_count": self._generated_count,
            "current_prices": self._current_prices.copy(),
            "noise_pips": self._synthetic_config.noise_pips,
            "latency_ms": self._synthetic_config.latency_ms,
        })
        return base_stats
