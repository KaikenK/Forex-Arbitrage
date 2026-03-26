"""
Playback Data Source Plugin

Replays recorded tick data from CSV/JSON files for research and backtesting.
Enables reproducible arbitrage detection experiments without live data.

Design Decisions:
- Supports both CSV and JSON formats
- Configurable playback speed (real-time, accelerated, instant)
- Loop mode for continuous testing
- Timestamp normalization for consistent timing
- Session tagging preserved from recorded data
"""

import time
import json
import csv
import logging
from typing import Optional, List, Dict, Any, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import threading

from backend.core.interfaces.data_source import (
    DataSourceInterface,
    DataSourceConfig,
    RawTick,
)

logger = logging.getLogger(__name__)


@dataclass
class PlaybackConfig:
    """
    Configuration for playback data source.
    
    Attributes:
        file_path: Path to data file (CSV or JSON)
        file_format: File format ("csv", "json", "jsonl")
        speed_multiplier: Playback speed (1.0 = real-time, 0 = instant)
        loop: Whether to loop when reaching end of file
        start_time: Optional start timestamp filter (ms)
        end_time: Optional end timestamp filter (ms)
        normalize_timestamps: Adjust timestamps relative to current time
        column_mapping: Map file columns to tick fields
    """
    file_path: str = ""
    file_format: str = "csv"  # "csv", "json", "jsonl"
    speed_multiplier: float = 1.0  # 1.0 = real-time, 0 = instant
    loop: bool = False
    start_time: Optional[int] = None
    end_time: Optional[int] = None
    normalize_timestamps: bool = True
    column_mapping: Dict[str, str] = field(default_factory=lambda: {
        "symbol": "symbol",
        "bid": "bid",
        "ask": "ask",
        "timestamp_ms": "timestamp_ms",
        "volume": "volume",
    })


class PlaybackDataSource(DataSourceInterface):
    """
    Playback data source for recorded tick data.
    
    Replays historical tick data from files for:
    - Backtesting arbitrage detection algorithms
    - Reproducible research experiments
    - Demo/presentation without live feeds
    - Unit testing with known data
    
    Supported formats:
    - CSV with headers
    - JSON array of tick objects
    - JSONL (JSON Lines) - one tick per line
    
    Example:
        config = DataSourceConfig(
            source_id="playback_historical",
            source_type="playback",
            display_name="Historical Data Replay",
            symbols=["EURUSD", "USDINR"]
        )
        
        playback_config = PlaybackConfig(
            file_path="data/ticks_2024.csv",
            speed_multiplier=10.0,  # 10x speed
            loop=True,
        )
        
        source = PlaybackDataSource(config, playback_config)
        source.connect()
    """
    
    def __init__(
        self,
        config: DataSourceConfig,
        playback_config: Optional[PlaybackConfig] = None,
    ):
        """
        Initialize playback data source.
        
        Args:
            config: Data source configuration
            playback_config: Playback-specific configuration
        """
        super().__init__(config)
        
        self._playback_config = playback_config or PlaybackConfig()
        
        # Loaded data
        self._ticks: List[Dict[str, Any]] = []
        self._tick_index = 0
        
        # Current ticks per symbol
        self._current_ticks: Dict[str, RawTick] = {}
        
        # Timing
        self._playback_start_time: Optional[float] = None
        self._data_start_time: Optional[int] = None
        self._last_tick_index: Dict[str, int] = {}
        
        # Threading
        self._lock = threading.Lock()
        self._playing = False
        
        # Stats
        self._ticks_played = 0
        self._loops_completed = 0
    
    def connect(self) -> bool:
        """
        Load data file and prepare for playback.
        """
        file_path = Path(self._playback_config.file_path)
        
        if not file_path.exists():
            logger.error(f"[{self.source_id}] File not found: {file_path}")
            return False
        
        try:
            if self._playback_config.file_format == "csv":
                self._load_csv(file_path)
            elif self._playback_config.file_format == "json":
                self._load_json(file_path)
            elif self._playback_config.file_format == "jsonl":
                self._load_jsonl(file_path)
            else:
                logger.error(f"[{self.source_id}] Unknown format: {self._playback_config.file_format}")
                return False
            
            # Filter by time range if specified
            self._filter_by_time()
            
            # Sort by timestamp
            self._ticks.sort(key=lambda t: t.get("timestamp_ms", 0))
            
            if not self._ticks:
                logger.error(f"[{self.source_id}] No ticks loaded from {file_path}")
                return False
            
            # Store data start time for normalization
            self._data_start_time = self._ticks[0].get("timestamp_ms", 0)
            
            self._is_connected = True
            logger.info(
                f"[{self.source_id}] Loaded {len(self._ticks)} ticks from {file_path}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[{self.source_id}] Error loading file: {e}")
            return False
    
    def _load_csv(self, file_path: Path) -> None:
        """Load ticks from CSV file."""
        mapping = self._playback_config.column_mapping
        
        with open(file_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                tick = self._map_row(row, mapping)
                if tick:
                    self._ticks.append(tick)
    
    def _load_json(self, file_path: Path) -> None:
        """Load ticks from JSON array file."""
        mapping = self._playback_config.column_mapping
        
        with open(file_path, "r") as f:
            data = json.load(f)
            if isinstance(data, list):
                for row in data:
                    tick = self._map_row(row, mapping)
                    if tick:
                        self._ticks.append(tick)
    
    def _load_jsonl(self, file_path: Path) -> None:
        """Load ticks from JSON Lines file."""
        mapping = self._playback_config.column_mapping
        
        with open(file_path, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    row = json.loads(line)
                    tick = self._map_row(row, mapping)
                    if tick:
                        self._ticks.append(tick)
    
    def _map_row(self, row: Dict, mapping: Dict[str, str]) -> Optional[Dict]:
        """Map file row to tick dict using column mapping."""
        try:
            tick = {
                "symbol": row.get(mapping.get("symbol", "symbol")),
                "bid": float(row.get(mapping.get("bid", "bid"), 0)),
                "ask": float(row.get(mapping.get("ask", "ask"), 0)),
                "timestamp_ms": int(row.get(mapping.get("timestamp_ms", "timestamp_ms"), 0)),
            }
            
            # Optional fields
            volume = row.get(mapping.get("volume", "volume"))
            if volume:
                tick["volume"] = int(volume)
            
            # Validate required fields
            if tick["symbol"] and tick["bid"] > 0 and tick["ask"] > 0:
                return tick
            return None
            
        except (ValueError, TypeError) as e:
            return None
    
    def _filter_by_time(self) -> None:
        """Filter ticks by configured time range."""
        if self._playback_config.start_time:
            self._ticks = [
                t for t in self._ticks
                if t.get("timestamp_ms", 0) >= self._playback_config.start_time
            ]
        
        if self._playback_config.end_time:
            self._ticks = [
                t for t in self._ticks
                if t.get("timestamp_ms", 0) <= self._playback_config.end_time
            ]
    
    def disconnect(self) -> None:
        """Stop playback and cleanup."""
        self._playing = False
        self._is_connected = False
        logger.info(f"[{self.source_id}] Playback stopped")
    
    def start_playback(self) -> None:
        """Start playback from current position."""
        self._playing = True
        self._playback_start_time = time.time()
    
    def stop_playback(self) -> None:
        """Pause playback."""
        self._playing = False
    
    def reset_playback(self) -> None:
        """Reset to beginning."""
        with self._lock:
            self._tick_index = 0
            self._current_ticks.clear()
            self._playback_start_time = None
            self._ticks_played = 0
    
    def get_tick(self, symbol: str) -> Optional[RawTick]:
        """
        Get current tick for symbol based on playback position.
        """
        if not self._is_connected or not self._ticks:
            return None
        
        # Update playback position
        self._advance_playback()
        
        return self._current_ticks.get(symbol)
    
    def _advance_playback(self) -> None:
        """Advance playback position based on elapsed time."""
        if not self._playing or not self._playback_start_time:
            return
        
        with self._lock:
            # Calculate how much data time has passed
            real_elapsed = time.time() - self._playback_start_time
            
            if self._playback_config.speed_multiplier == 0:
                # Instant mode - advance one tick per call
                self._advance_one_tick()
            else:
                # Time-based playback
                data_elapsed_ms = real_elapsed * 1000 * self._playback_config.speed_multiplier
                target_data_time = self._data_start_time + data_elapsed_ms
                
                # Advance to ticks up to target time
                while (
                    self._tick_index < len(self._ticks) and
                    self._ticks[self._tick_index].get("timestamp_ms", 0) <= target_data_time
                ):
                    self._advance_one_tick()
    
    def _advance_one_tick(self) -> None:
        """Advance by one tick."""
        if self._tick_index >= len(self._ticks):
            if self._playback_config.loop:
                self._tick_index = 0
                self._playback_start_time = time.time()
                self._loops_completed += 1
                logger.info(f"[{self.source_id}] Looping playback (loop {self._loops_completed})")
            else:
                return
        
        tick_data = self._ticks[self._tick_index]
        symbol = tick_data.get("symbol")
        
        # Normalize timestamp if configured
        if self._playback_config.normalize_timestamps:
            timestamp_ms = int(time.time() * 1000)
        else:
            timestamp_ms = tick_data.get("timestamp_ms", int(time.time() * 1000))
        
        # Create RawTick
        tick = RawTick(
            symbol=symbol,
            bid=tick_data.get("bid"),
            ask=tick_data.get("ask"),
            timestamp_ms=timestamp_ms,
            source_id=self.source_id,
            volume=tick_data.get("volume"),
        )
        
        self._current_ticks[symbol] = tick
        self._tick_index += 1
        self._ticks_played += 1
        self._tick_count += 1
        self._last_tick_time = timestamp_ms
    
    def get_supported_symbols(self) -> List[str]:
        """Get list of symbols available in playback data."""
        symbols = set()
        for tick in self._ticks:
            if tick.get("symbol"):
                symbols.add(tick["symbol"])
        return list(symbols)
    
    def is_healthy(self) -> bool:
        """Check if source is healthy."""
        return self._is_connected and len(self._ticks) > 0
    
    def get_progress(self) -> Dict[str, Any]:
        """Get playback progress info."""
        return {
            "current_index": self._tick_index,
            "total_ticks": len(self._ticks),
            "progress_pct": (self._tick_index / len(self._ticks) * 100) if self._ticks else 0,
            "ticks_played": self._ticks_played,
            "loops_completed": self._loops_completed,
            "is_playing": self._playing,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get source statistics."""
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "is_connected": self._is_connected,
            "tick_count": self._tick_count,
            "error_count": self._error_count,
            "last_tick_time": self._last_tick_time,
            "latency_estimate_ms": self._config.latency_estimate_ms,
            "reliability_score": self._config.reliability_score,
            "file_path": self._playback_config.file_path,
            "total_ticks_in_file": len(self._ticks),
            "playback_progress": self.get_progress(),
            "symbols_available": self.get_supported_symbols(),
        }


def generate_sample_data(
    output_path: str,
    symbols: List[str] = None,
    duration_minutes: int = 60,
    tick_interval_ms: int = 100,
) -> None:
    """
    Generate sample tick data file for testing.
    
    Args:
        output_path: Path for output CSV file
        symbols: List of symbols to generate
        duration_minutes: Duration of data to generate
        tick_interval_ms: Interval between ticks
    """
    import random
    
    symbols = symbols or ["EURUSD", "GBPUSD", "USDJPY", "USDINR"]
    
    base_prices = {
        "EURUSD": 1.0850,
        "GBPUSD": 1.2650,
        "USDJPY": 149.50,
        "USDINR": 83.25,
    }
    
    start_time = int(time.time() * 1000) - (duration_minutes * 60 * 1000)
    
    ticks = []
    current_prices = dict(base_prices)
    
    for t in range(0, duration_minutes * 60 * 1000, tick_interval_ms):
        timestamp = start_time + t
        
        for symbol in symbols:
            # Random walk
            volatility = 0.00005 if "JPY" not in symbol else 0.005
            current_prices[symbol] *= (1 + random.gauss(0, volatility))
            
            # Calculate bid/ask
            spread = 0.0001 if "JPY" not in symbol else 0.01
            mid = current_prices[symbol]
            bid = mid - spread / 2
            ask = mid + spread / 2
            
            ticks.append({
                "symbol": symbol,
                "bid": round(bid, 5),
                "ask": round(ask, 5),
                "timestamp_ms": timestamp,
                "volume": random.randint(1, 100),
            })
    
    # Write to CSV
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["symbol", "bid", "ask", "timestamp_ms", "volume"])
        writer.writeheader()
        writer.writerows(ticks)
    
    logger.info(f"Generated {len(ticks)} ticks to {output_path}")
