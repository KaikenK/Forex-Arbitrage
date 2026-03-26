"""
Multi-Source Streamer Module

Streams ticks from multiple data sources simultaneously, normalizes them,
and feeds them into the arbitrage detection pipeline. Extends the original
TickStreamer concept to support plugin-based data sources.

Design Decisions:
- Maintains backward compatibility with existing WebSocket broadcasting
- Supports dynamic addition/removal of data sources
- Async-first design for non-blocking operation
- Integrates tick normalization and alignment automatically
- Emits arbitrage opportunities to dedicated WebSocket channel
- NEW: Integrates OpportunityTracker for persistence tracking
- NEW: Integrates SimulatedExecutionFilter for feasibility assessment
- NEW: Uses enhanced OpportunityRanker with institutional scoring
"""

import asyncio
import logging
import time
import statistics
from typing import Dict, List, Optional, Any, Callable, Set
from collections import deque
from datetime import datetime

from backend.core.interfaces.data_source import DataSourceInterface, RawTick
from backend.core.interfaces.normalized_tick import NormalizedTick, TickNormalizer
from backend.core.arbitrage.tick_aligner import TickAligner, AlignedTickWindow
from backend.core.arbitrage.arbitrage_engine import (
    ArbitrageEngine, 
    ArbitrageOpportunity,
    ArbitrageType,
    ArbitrageConfig,
)
from backend.core.arbitrage.opportunity_ranker import (
    OpportunityRanker, 
    RankingConfig,
    RankedOpportunity,
)

# NEW: Import institutional components
from backend.core.arbitrage.opportunity_tracker import (
    OpportunityTracker,
    TrackerConfig,
    TrackedOpportunity,
    PersistenceClass,
)
from backend.core.execution.execution_filter import (
    SimulatedExecutionFilter,
    ExecutionFilterConfig,
    ExecutionAssessment,
    ExecutionVerdict,
)
from backend.core.state.control_state import get_control_state, get_control_state_manager

logger = logging.getLogger(__name__)


def detect_session(timestamp_ms: int) -> str:
    """
    Detect trading session based on UTC time.
    (Kept for backward compatibility with existing code)
    """
    dt = datetime.utcfromtimestamp(timestamp_ms / 1000.0)
    hour = dt.hour
    
    if 0 <= hour < 9:
        return "Tokyo"
    elif 8 <= hour < 17:
        return "London"
    elif 13 <= hour < 22:
        return "New York"
    else:
        return "Closed"


class MultiSourceStreamer:
    """
    Streams and processes ticks from multiple FX data sources.
    
    This class orchestrates the entire data pipeline:
    1. Polls multiple data sources for ticks
    2. Normalizes ticks into unified format
    3. Broadcasts to WebSocket clients (backward compatible)
    4. Feeds ticks to alignment engine
    5. Detects arbitrage opportunities
    6. Tracks opportunity persistence (NEW)
    7. Assesses execution feasibility (NEW)
    8. Ranks with institutional scoring (NEW)
    9. Broadcasts opportunities
    
    Example:
        streamer = MultiSourceStreamer(
            ws_manager=ws_manager,
            bar_aggregators=bar_aggregators
        )
        
        # Add data sources
        streamer.add_source(mt5_source)
        streamer.add_source(synthetic_source)
        
        # Start streaming
        await streamer.start(symbols=["EURUSD", "GBPUSD"])
    """
    
    def __init__(
        self,
        ws_manager,
        bar_aggregators: Optional[Dict[str, Any]] = None,
        arbitrage_config: Optional[ArbitrageConfig] = None,
        ranking_config: Optional[RankingConfig] = None,
        tracker_config: Optional[TrackerConfig] = None,
        execution_filter_config: Optional[ExecutionFilterConfig] = None,
        alignment_window_ms: int = 20,
    ):
        """
        Initialize multi-source streamer.
        
        Args:
            ws_manager: WebSocket manager for broadcasting
            bar_aggregators: Dict of bar aggregators per symbol {symbol: MultiIntervalAggregator}
            arbitrage_config: Configuration for arbitrage detection
            ranking_config: Configuration for opportunity ranking
            tracker_config: Configuration for opportunity persistence tracking (NEW)
            execution_filter_config: Configuration for execution feasibility filter (NEW)
            alignment_window_ms: Size of tick alignment windows
        """
        self.ws_manager = ws_manager
        self.bar_aggregators = bar_aggregators or {}
        
        # Data sources registry
        self._sources: Dict[str, DataSourceInterface] = {}
        
        # Tick normalizer
        self._normalizer = TickNormalizer()
        
        # Tick aligner
        self._aligner = TickAligner(
            window_size_ms=alignment_window_ms,
            expected_sources=[],
            symbols=[],
        )
        
        # Arbitrage detection
        self._arbitrage_engine = ArbitrageEngine(
            config=arbitrage_config or ArbitrageConfig()
        )
        self._opportunity_ranker = OpportunityRanker(
            config=ranking_config or RankingConfig()
        )
        
        # NEW: Opportunity persistence tracking
        self._opportunity_tracker = OpportunityTracker(
            config=tracker_config or TrackerConfig()
        )
        
        # NEW: Execution feasibility filter
        self._execution_filter = SimulatedExecutionFilter(
            config=execution_filter_config or ExecutionFilterConfig()
        )
        
        # Streaming state
        self._running = False
        self._streams: Dict[str, asyncio.Task] = {}  # {source_id: task}
        self._symbols: Set[str] = set()
        
        # Tick buffers for metrics (per source per symbol)
        self._tick_buffers: Dict[str, Dict[str, deque]] = {}  # {source_id: {symbol: deque}}
        
        # Velocity EMA per source/symbol
        self._velocity_ema: Dict[str, Dict[str, float]] = {}
        self._prev_velocity: Dict[str, Dict[str, float]] = {}
        self._ema_alpha = 0.1
        
        # Arbitrage callbacks
        self._arbitrage_callbacks: List[Callable[[ArbitrageOpportunity], Any]] = []
        
        # Stats
        self._ticks_processed = 0
        self._arbitrage_opportunities_detected = 0
        
        # Register alignment callback
        self._aligner.on_window_complete(self._on_aligned_window)
    
    def add_source(self, source: DataSourceInterface) -> None:
        """
        Add a data source to the streamer.
        
        Args:
            source: Data source implementing DataSourceInterface
        """
        source_id = source.source_id
        
        if source_id in self._sources:
            logger.warning(f"Source {source_id} already registered, replacing")
        
        self._sources[source_id] = source
        self._aligner.add_source(source_id)
        
        # Set latency for normalizer
        latency = source.config.latency_estimate_ms
        self._normalizer.set_source_latency(source_id, latency)
        
        # Set reliability for ranker
        reliability = source.config.reliability_score
        self._opportunity_ranker.set_source_reliability(source_id, reliability)
        
        # Update arbitrage engine config
        self._arbitrage_engine.config.source_latencies[source_id] = latency
        self._arbitrage_engine.config.source_reliabilities[source_id] = reliability
        
        # Initialize tick buffers
        self._tick_buffers[source_id] = {}
        self._velocity_ema[source_id] = {}
        self._prev_velocity[source_id] = {}
        
        logger.info(f"Added data source: {source_id} ({source.source_type})")
    
    def remove_source(self, source_id: str) -> None:
        """
        Remove a data source from the streamer.
        
        Args:
            source_id: ID of source to remove
        """
        if source_id in self._sources:
            # Stop streaming from this source
            if source_id in self._streams:
                self._streams[source_id].cancel()
                del self._streams[source_id]
            
            del self._sources[source_id]
            
            # Clean up buffers
            if source_id in self._tick_buffers:
                del self._tick_buffers[source_id]
            
            logger.info(f"Removed data source: {source_id}")
    
    def on_arbitrage(self, callback: Callable[[ArbitrageOpportunity], Any]) -> None:
        """
        Register a callback for arbitrage opportunities.
        
        Args:
            callback: Function to call with each opportunity
        """
        self._arbitrage_callbacks.append(callback)
    
    async def start(self, symbols: Optional[List[str]] = None) -> None:
        """
        Start streaming from all registered sources.
        
        Args:
            symbols: Symbols to stream (uses source defaults if not specified)
        """
        if self._running:
            logger.warning("MultiSourceStreamer already running")
            return
        
        self._running = True
        
        # Collect symbols from all sources if not specified
        if symbols:
            self._symbols = set(s.upper() for s in symbols)
        else:
            for source in self._sources.values():
                self._symbols.update(source.get_supported_symbols())
        
        # Add symbols to aligner
        for symbol in self._symbols:
            self._aligner.add_symbol(symbol)
        
        # Start alignment engine
        await self._aligner.start()
        
        # Start streaming from each source
        for source_id, source in self._sources.items():
            if not source.is_connected:
                if not source.connect():
                    logger.error(f"Failed to connect source: {source_id}")
                    continue
            
            task = asyncio.create_task(self._stream_source(source))
            self._streams[source_id] = task
        
        logger.info(f"MultiSourceStreamer started with {len(self._sources)} sources, {len(self._symbols)} symbols")
    
    async def stop(self) -> None:
        """Stop all streaming."""
        self._running = False
        
        # Stop all stream tasks
        for source_id, task in self._streams.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._streams.clear()
        
        # Stop aligner
        await self._aligner.stop()
        
        logger.info("MultiSourceStreamer stopped")
    
    async def _stream_source(self, source: DataSourceInterface) -> None:
        """
        Stream loop for a single data source.
        
        Args:
            source: Data source to stream from
        """
        source_id = source.source_id
        logger.info(f"[{source_id}] Starting stream loop")
        
        last_tick_times: Dict[str, int] = {}
        
        try:
            while self._running:
                # Get ticks for each symbol
                for symbol in self._symbols:
                    if symbol not in source.get_supported_symbols():
                        continue
                    
                    try:
                        raw_tick = source.get_tick(symbol)
                        
                        if raw_tick and raw_tick.timestamp_ms != last_tick_times.get(symbol, 0):
                            last_tick_times[symbol] = raw_tick.timestamp_ms
                            
                            # Process the tick
                            await self._process_tick(raw_tick)
                            
                    except Exception as e:
                        logger.error(f"[{source_id}] Error getting tick for {symbol}: {e}")
                
                # Polling delay - match realistic tick rate
                await asyncio.sleep(0.1)  # 100ms polling (faster than tick interval)
                
        except asyncio.CancelledError:
            logger.info(f"[{source_id}] Stream cancelled")
        except Exception as e:
            logger.error(f"[{source_id}] Stream error: {e}", exc_info=True)
    
    async def _process_tick(self, raw_tick: RawTick) -> None:
        """
        Process a raw tick through the pipeline.
        
        Args:
            raw_tick: Raw tick from a data source
        """
        # Normalize the tick
        normalized = self._normalizer.normalize(raw_tick)
        if normalized is None:
            return
        
        source_id = normalized.source_id
        symbol = normalized.symbol
        
        self._ticks_processed += 1
        
        # Initialize buffers if needed
        if symbol not in self._tick_buffers.get(source_id, {}):
            if source_id not in self._tick_buffers:
                self._tick_buffers[source_id] = {}
            self._tick_buffers[source_id][symbol] = deque(maxlen=500)
            self._velocity_ema[source_id][symbol] = 0.0
            self._prev_velocity[source_id][symbol] = 0.0
        
        # Enrich tick with metrics
        enriched = self._enrich_tick(normalized)
        
        # Add to tick buffer
        self._tick_buffers[source_id][symbol].append({
            "time_ms": normalized.timestamp_ms,
            "mid": normalized.mid,
            "bid": normalized.bid,
            "ask": normalized.ask,
        })
        
        # Broadcast to WebSocket clients (backward compatible)
        await self.ws_manager.broadcast_tick(symbol, enriched)
        
        # Update tick by source for source comparison
        if hasattr(self.ws_manager, 'update_tick_by_source'):
            self.ws_manager.update_tick_by_source(symbol, source_id, enriched)
        
        # Broadcast source comparison periodically
        if self._ticks_processed % 5 == 0 and hasattr(self.ws_manager, 'broadcast_source_comparison'):
            comparison = self.ws_manager.get_source_comparison(symbol)
            if comparison:
                await self.ws_manager.broadcast_source_comparison(symbol, comparison)
        
        # Feed to alignment engine for arbitrage detection
        await self._aligner.add_tick(normalized)
        
        # Process through bar aggregators for candle building
        if symbol in self.bar_aggregators:
            aggregator = self.bar_aggregators[symbol]
            completed_bars = aggregator.process_tick(symbol, enriched)
            
            # Broadcast completed candles
            for interval, candle in completed_bars.items():
                if candle.get("high", 0) > candle.get("low", 0):
                    await self.ws_manager.broadcast_candle(symbol, interval, candle)
        
        # Broadcast market state periodically
        if self._ticks_processed % 10 == 0:
            await self.ws_manager.broadcast_market_state(symbol)
    
    def _enrich_tick(self, normalized: NormalizedTick) -> Dict[str, Any]:
        """
        Enrich a normalized tick with calculated metrics.
        
        Maintains backward compatibility with existing enriched tick format.
        
        Args:
            normalized: Normalized tick
        
        Returns:
            Enriched tick dict
        """
        source_id = normalized.source_id
        symbol = normalized.symbol
        time_ms = normalized.timestamp_ms
        mid = normalized.mid
        
        # Calculate velocity
        velocity = 0.0
        buffer = self._tick_buffers.get(source_id, {}).get(symbol)
        if buffer and len(buffer) > 0:
            last_tick = buffer[-1]
            velocity = mid - last_tick["mid"]
        
        # Calculate acceleration
        prev_vel = self._prev_velocity.get(source_id, {}).get(symbol, 0.0)
        acceleration = velocity - prev_vel
        self._prev_velocity[source_id][symbol] = velocity
        
        # Update velocity EMA
        current_ema = self._velocity_ema.get(source_id, {}).get(symbol, 0.0)
        new_ema = (self._ema_alpha * velocity) + ((1 - self._ema_alpha) * current_ema)
        self._velocity_ema[source_id][symbol] = new_ema
        
        # Direction bias
        direction_bias = 1 if new_ema > 0 else -1
        
        # Calculate volatilities
        vol_5s = self._calculate_volatility(source_id, symbol, 5)
        vol_30s = self._calculate_volatility(source_id, symbol, 30)
        vol_1m = self._calculate_volatility(source_id, symbol, 60)
        
        # Detect session
        session = detect_session(time_ms)
        
        # Build enriched tick (compatible with original format)
        # Calculate latency as time since tick was created
        current_time_ms = int(time.time() * 1000)
        latency_ms = max(0, current_time_ms - time_ms)
        
        return {
            "symbol": symbol,
            "bid": normalized.bid,
            "ask": normalized.ask,
            "mid": mid,
            "spread": normalized.spread,
            "source_id": source_id,
            "time": time_ms,
            "time_ms": time_ms,
            "velocity": velocity,
            "acceleration": acceleration,
            "direction_bias": direction_bias,
            "vol_5s": vol_5s,
            "vol_30s": vol_30s,
            "vol_1m": vol_1m,
            "session": session,
            "volume_real": normalized.volume or 0,
            "broker_time": datetime.utcfromtimestamp(time_ms / 1000.0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "latency_ms": latency_ms,
        }
    
    def _calculate_volatility(self, source_id: str, symbol: str, window_seconds: int) -> float:
        """Calculate volatility over a time window."""
        buffer = self._tick_buffers.get(source_id, {}).get(symbol)
        if not buffer or len(buffer) < 2:
            return 0.0
        
        ticks = list(buffer)
        current_time_ms = ticks[-1]["time_ms"]
        window_start_ms = current_time_ms - (window_seconds * 1000)
        
        window_ticks = [t for t in ticks if t["time_ms"] >= window_start_ms]
        if len(window_ticks) < 2:
            return 0.0
        
        mid_prices = [t["mid"] for t in window_ticks]
        return float(statistics.stdev(mid_prices))
    
    async def _on_aligned_window(self, window: AlignedTickWindow) -> None:
        """
        Callback for when a tick alignment window is complete.
        
        Full institutional pipeline:
        1. Update tracker state
        2. Detect arbitrage opportunities
        3. Track persistence for each opportunity
        4. Assess execution feasibility
        5. Rank with institutional scoring
        6. Broadcast best opportunity
        
        Args:
            window: The aligned window
        """
        # Step 1: Update tracker state (prevents TTL freezing)
        self._opportunity_tracker.tick()
        
        # Step 2: Detect arbitrage opportunities
        opportunities = self._arbitrage_engine.detect(window)
        
        if not opportunities:
            return
        
        # Step 2: Track persistence for each opportunity
        # Build a mapping from opportunity key to tracked data
        tracked_by_key: Dict[str, TrackedOpportunity] = {}
        persistence_data: Dict[str, Dict[str, Any]] = {}
        
        for opp in opportunities:
            tracked = self._opportunity_tracker.update(opp)
            key = tracked.key
            tracked_by_key[key] = tracked
            
            # Build persistence data dict for ranking
            persistence_data[key] = {
                "persistence_class": tracked.persistence_class.value,
                "stability_score": tracked.stability_score,
                "detection_count": tracked.detection_count,
                "first_seen_ts": tracked.first_seen_ts,
                "cumulative_duration_ms": tracked.cumulative_duration_ms,
            }
        
        # Step 3: Assess execution feasibility for each
        execution_assessments: Dict[str, ExecutionAssessment] = {}
        execution_data: Dict[str, Dict[str, Any]] = {}
        
        for key, tracked in tracked_by_key.items():
            assessment = self._execution_filter.assess(tracked)
            execution_assessments[key] = assessment
            
            # Build execution data dict for ranking
            execution_data[key] = {
                "verdict": assessment.execution_verdict.value,
                "feasibility_score": assessment.execution_feasibility_score,
                "expected_slippage_pips": assessment.expected_slippage_pips,
                "verdict_reasons": assessment.verdict_reasons,
            }
        
        # Step 4: Rank with institutional scoring (full context)
        ranked = self._opportunity_ranker.rank_with_context(
            opportunities=opportunities,
            persistence_data=persistence_data,
            execution_assessments=execution_data,
        )
        
        # Only emit the BEST opportunity (rank #1)
        if ranked:
            best = ranked[0]  # Highest ranked opportunity
            opp = best.opportunity
            
            # Get tracked opportunity by reconstructing key (matches tracker's _make_key)
            symbols = "|".join(sorted(opp.symbols))
            if opp.type == ArbitrageType.SESSION_INEFFICIENCY:
                key = f"{symbols}|{opp.session}|SESSION_INEFFICIENCY"
            else:
                key = f"{symbols}|{opp.buy_source}|{opp.sell_source}|{opp.type.value}"
            tracked = tracked_by_key.get(key)
            assessment = execution_assessments.get(key)
            
            self._arbitrage_opportunities_detected += 1
            
            # Build persistence badge
            persistence_badge = "⚡"  # Default ephemeral
            persistence_class_value = "ephemeral"
            persistence_ms = 0
            if tracked:
                persistence_class_value = tracked.persistence_class.value
                persistence_ms = tracked.cumulative_duration_ms
                if tracked.persistence_class == PersistenceClass.PERSISTENT:
                    persistence_badge = "🧱"
                elif tracked.persistence_class == PersistenceClass.FLICKERING:
                    persistence_badge = "🔄"
            
            # Build execution badge
            execution_badge = "⚠️"  # Default risky
            execution_verdict = "risky"
            if assessment:
                execution_verdict = assessment.execution_verdict.value
                if assessment.execution_verdict == ExecutionVerdict.VIABLE:
                    execution_badge = "✅"
                elif assessment.execution_verdict == ExecutionVerdict.UNLIKELY:
                    execution_badge = "❌"
            
            # Enhanced log with institutional context
            logger.info(
                f"[ARBITRAGE BEST] {persistence_badge}{execution_badge} {opp.type.value}: {opp.symbols} | "
                f"Profit: {opp.estimated_profit_pips:.2f} pips | "
                f"Confidence: {opp.confidence_score:.2f} | "
                f"Score: {best.composite_score:.1f} | "
                f"Persistence: {persistence_class_value} ({persistence_ms}ms) | "
                f"Execution: {execution_verdict} | "
                f"(1 of {len(ranked)} opportunities)"
            )
            
            # Call registered callbacks with best opportunity only
            for callback in self._arbitrage_callbacks:
                try:
                    result = callback(opp)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    logger.error(f"Error in arbitrage callback: {e}")
            
            # Broadcast best opportunity via WebSocket with full institutional context
            if hasattr(self.ws_manager, 'broadcast_arbitrage'):
                broadcast_data = best.to_dict()
                broadcast_data['total_opportunities'] = len(ranked)
                broadcast_data['is_best'] = True
                
                # Add persistence tracking data
                if tracked:
                    broadcast_data['persistence'] = {
                        'class': tracked.persistence_class.value,
                        'duration_ms': tracked.cumulative_duration_ms,
                        'detection_count': tracked.detection_count,
                        'stability_score': tracked.stability_score,
                        'first_seen_ts': tracked.first_seen_ts,
                        'badge': persistence_badge,
                    }
                
                # Add execution assessment data
                if assessment:
                    broadcast_data['execution'] = {
                        'verdict': assessment.execution_verdict.value,
                        'feasibility_score': assessment.execution_feasibility_score,
                        'expected_slippage_pips': assessment.expected_slippage_pips,
                        'verdict_reasons': assessment.verdict_reasons,
                        'net_expected_profit_pips': assessment.net_expected_profit_pips,
                        'badge': execution_badge,
                    }
                
                # Add ranking reason if available
                if hasattr(best, 'ranking_reason') and best.ranking_reason:
                    broadcast_data['ranking_reason'] = best.ranking_reason
                
                await self.ws_manager.broadcast_arbitrage(broadcast_data)
    
    def get_sources(self) -> Dict[str, Dict[str, Any]]:
        """Get info about all registered sources."""
        return {
            source_id: source.get_stats()
            for source_id, source in self._sources.items()
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get streamer statistics."""
        return {
            "running": self._running,
            "sources": len(self._sources),
            "symbols": list(self._symbols),
            "ticks_processed": self._ticks_processed,
            "arbitrage_opportunities_detected": self._arbitrage_opportunities_detected,
            "aligner_stats": self._aligner.get_stats(),
            "arbitrage_engine_stats": self._arbitrage_engine.get_stats(),
            "ranker_stats": self._opportunity_ranker.get_stats(),
            "tracker_stats": self._opportunity_tracker.get_stats(),
            "execution_filter_stats": self._execution_filter.get_stats(),
            "normalizer_stats": self._normalizer.get_stats(),
        }
