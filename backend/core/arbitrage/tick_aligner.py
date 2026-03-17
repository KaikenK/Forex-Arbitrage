"""
Tick Alignment / Micro-Batching Engine

Buffers normalized ticks from multiple data sources and aligns them
into small time windows for fair comparison. This is critical for
accurate arbitrage detection across sources with different latencies.

Design Decisions:
- Configurable window size (10ms-50ms typical for HFT)
- Async-safe using asyncio.Lock
- Efficient circular buffer per source/symbol
- Window emission based on wall clock, not tick timestamps
- Handles source arrival delays gracefully
"""

import asyncio
import time
import logging
from typing import Dict, List, Optional, Set, Callable, Any
from dataclasses import dataclass, field
from collections import defaultdict

from backend.core.interfaces.normalized_tick import NormalizedTick

logger = logging.getLogger(__name__)


@dataclass
class AlignedTickWindow:
    """
    A time-aligned window containing ticks from multiple sources.
    
    This represents a single comparison window where ticks from
    different sources can be fairly compared for arbitrage detection.
    
    Attributes:
        window_start_ms: Start of the window (Unix timestamp ms)
        window_end_ms: End of the window (Unix timestamp ms)
        symbol: Currency pair for this window
        ticks_by_source: Dict mapping source_id to list of ticks
        created_at_ms: When this window was created (wall clock)
    """
    window_start_ms: int
    window_end_ms: int
    symbol: str
    ticks_by_source: Dict[str, List[NormalizedTick]] = field(default_factory=dict)
    created_at_ms: int = field(default_factory=lambda: int(time.time() * 1000))
    
    @property
    def source_count(self) -> int:
        """Number of sources with ticks in this window."""
        return len([s for s, t in self.ticks_by_source.items() if t])
    
    @property
    def total_tick_count(self) -> int:
        """Total number of ticks across all sources."""
        return sum(len(t) for t in self.ticks_by_source.values())
    
    def get_best_tick_per_source(self) -> Dict[str, NormalizedTick]:
        """
        Get the most recent tick from each source in the window.
        
        For arbitrage detection, we typically want to compare
        the latest price from each source.
        
        Returns:
            Dict mapping source_id to the latest tick
        """
        result = {}
        for source_id, ticks in self.ticks_by_source.items():
            if ticks:
                # Get tick with latest timestamp
                result[source_id] = max(ticks, key=lambda t: t.timestamp_ms)
        return result
    
    def has_multiple_sources(self) -> bool:
        """Check if window has ticks from multiple sources."""
        return self.source_count >= 2
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "window_start_ms": self.window_start_ms,
            "window_end_ms": self.window_end_ms,
            "symbol": self.symbol,
            "source_count": self.source_count,
            "total_tick_count": self.total_tick_count,
            "sources": list(self.ticks_by_source.keys()),
            "created_at_ms": self.created_at_ms,
        }


class TickAligner:
    """
    Aligns ticks from multiple sources into time windows.
    
    The aligner buffers incoming ticks and periodically emits
    aligned windows for arbitrage analysis. Windows are emitted
    when:
    1. The window time expires (based on wall clock)
    2. All expected sources have contributed
    
    This enables fair comparison of prices that arrived at
    slightly different times due to network latency.
    
    Example:
        aligner = TickAligner(
            window_size_ms=20,  # 20ms windows
            expected_sources=["mt5_primary", "synthetic_bloomberg"],
            symbols=["EURUSD", "GBPUSD"]
        )
        
        # Register callback for aligned windows
        aligner.on_window_complete(my_callback)
        
        # Start alignment loop
        await aligner.start()
        
        # Add ticks as they arrive
        await aligner.add_tick(normalized_tick)
    """
    
    def __init__(
        self,
        window_size_ms: int = 20,
        expected_sources: Optional[List[str]] = None,
        symbols: Optional[List[str]] = None,
        max_window_age_ms: int = 100,
    ):
        """
        Initialize the tick aligner.
        
        Args:
            window_size_ms: Size of each alignment window in milliseconds (10-50 typical)
            expected_sources: List of source IDs we expect ticks from
            symbols: List of symbols to align
            max_window_age_ms: Maximum window age before forced emission
        """
        self.window_size_ms = window_size_ms
        self.expected_sources = set(expected_sources or [])
        self.symbols = set(symbols or [])
        self.max_window_age_ms = max_window_age_ms
        
        # Current windows being built: {symbol: {window_start_ms: AlignedTickWindow}}
        self._current_windows: Dict[str, Dict[int, AlignedTickWindow]] = defaultdict(dict)
        
        # Lock for thread-safe access
        self._lock = asyncio.Lock()
        
        # Callbacks for completed windows
        self._callbacks: List[Callable[[AlignedTickWindow], Any]] = []
        
        # Running state
        self._running = False
        self._alignment_task: Optional[asyncio.Task] = None
        
        # Stats
        self._windows_emitted = 0
        self._ticks_processed = 0
    
    def on_window_complete(self, callback: Callable[[AlignedTickWindow], Any]) -> None:
        """
        Register a callback for when a window is complete.
        
        The callback receives the AlignedTickWindow and can be
        sync or async.
        
        Args:
            callback: Function to call with completed window
        """
        self._callbacks.append(callback)
    
    def add_source(self, source_id: str) -> None:
        """Add an expected source."""
        self.expected_sources.add(source_id)
        logger.info(f"[TickAligner] Added expected source: {source_id}")
    
    def add_symbol(self, symbol: str) -> None:
        """Add a symbol to align."""
        self.symbols.add(symbol.upper())
        logger.info(f"[TickAligner] Added symbol: {symbol}")
    
    async def add_tick(self, tick: NormalizedTick) -> None:
        """
        Add a normalized tick to the aligner.
        
        The tick is placed into the appropriate time window
        based on its timestamp.
        
        Args:
            tick: Normalized tick to add
        """
        async with self._lock:
            symbol = tick.symbol.upper()
            
            # Skip if symbol not configured
            if self.symbols and symbol not in self.symbols:
                return
            
            # Calculate window start time
            window_start_ms = (tick.timestamp_ms // self.window_size_ms) * self.window_size_ms
            
            # Get or create window
            symbol_windows = self._current_windows[symbol]
            
            if window_start_ms not in symbol_windows:
                symbol_windows[window_start_ms] = AlignedTickWindow(
                    window_start_ms=window_start_ms,
                    window_end_ms=window_start_ms + self.window_size_ms,
                    symbol=symbol,
                    ticks_by_source={},
                )
            
            window = symbol_windows[window_start_ms]
            
            # Add tick to window
            if tick.source_id not in window.ticks_by_source:
                window.ticks_by_source[tick.source_id] = []
            window.ticks_by_source[tick.source_id].append(tick)
            
            self._ticks_processed += 1
    
    async def start(self) -> None:
        """Start the alignment loop."""
        if self._running:
            logger.warning("[TickAligner] Already running")
            return
        
        self._running = True
        self._alignment_task = asyncio.create_task(self._alignment_loop())
        logger.info(f"[TickAligner] Started with {self.window_size_ms}ms windows")
    
    async def stop(self) -> None:
        """Stop the alignment loop."""
        self._running = False
        if self._alignment_task:
            self._alignment_task.cancel()
            try:
                await self._alignment_task
            except asyncio.CancelledError:
                pass
        logger.info("[TickAligner] Stopped")
    
    async def _alignment_loop(self) -> None:
        """
        Main alignment loop.
        
        Periodically checks for windows that are ready to emit
        and triggers callbacks.
        """
        try:
            while self._running:
                await self._emit_ready_windows()
                
                # Sleep for half the window size for responsiveness
                await asyncio.sleep(self.window_size_ms / 2000.0)
                
        except asyncio.CancelledError:
            logger.info("[TickAligner] Alignment loop cancelled")
        except Exception as e:
            logger.error(f"[TickAligner] Error in alignment loop: {e}")
    
    async def _emit_ready_windows(self) -> None:
        """
        Check for and emit windows that are ready.
        
        A window is ready when:
        1. Current time > window_end_ms + max_window_age_ms (expired)
        2. All expected sources have contributed
        """
        current_time_ms = int(time.time() * 1000)
        windows_to_emit: List[AlignedTickWindow] = []
        
        async with self._lock:
            for symbol, symbol_windows in list(self._current_windows.items()):
                for window_start, window in list(symbol_windows.items()):
                    # Check if window is ready
                    window_expired = current_time_ms > (window.window_end_ms + self.max_window_age_ms)
                    all_sources = (
                        self.expected_sources and 
                        set(window.ticks_by_source.keys()) >= self.expected_sources
                    )
                    
                    if window_expired or all_sources:
                        # Window is ready to emit
                        if window.total_tick_count > 0:
                            windows_to_emit.append(window)
                        
                        # Remove from current windows
                        del symbol_windows[window_start]
        
        # Emit windows outside the lock
        for window in windows_to_emit:
            await self._emit_window(window)
    
    async def _emit_window(self, window: AlignedTickWindow) -> None:
        """
        Emit a completed window to all callbacks.
        
        Args:
            window: The completed window to emit
        """
        self._windows_emitted += 1
        
        for callback in self._callbacks:
            try:
                result = callback(window)
                # Handle async callbacks
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"[TickAligner] Error in callback: {e}")
    
    async def flush(self) -> List[AlignedTickWindow]:
        """
        Flush all pending windows immediately.
        
        Useful for shutdown or testing.
        
        Returns:
            List of flushed windows
        """
        flushed = []
        
        async with self._lock:
            for symbol, symbol_windows in list(self._current_windows.items()):
                for window in symbol_windows.values():
                    if window.total_tick_count > 0:
                        flushed.append(window)
                symbol_windows.clear()
        
        # Emit flushed windows
        for window in flushed:
            await self._emit_window(window)
        
        return flushed
    
    def get_stats(self) -> Dict[str, Any]:
        """Get alignment statistics."""
        return {
            "window_size_ms": self.window_size_ms,
            "expected_sources": list(self.expected_sources),
            "symbols": list(self.symbols),
            "windows_emitted": self._windows_emitted,
            "ticks_processed": self._ticks_processed,
            "running": self._running,
            "pending_windows": sum(
                len(ws) for ws in self._current_windows.values()
            ),
        }
