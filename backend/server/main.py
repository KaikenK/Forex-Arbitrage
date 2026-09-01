"""
FastAPI Server - FX Arbitrage Detection Research Engine

Main server file with FastAPI app, WebSocket routes, and startup tasks.
Supports both live MT5 mode and synthetic USD/INR research mode.

Features:
- Multi-source tick streaming (MT5 + Synthetic feeds)
- Synthetic USD/INR session-aware data generation (research mode)
- Real-time arbitrage detection across sources
- Time-aligned tick windows for fair comparison
- Opportunity ranking and scoring
- Extended WebSocket API with arbitrage channels
- State machine-based arbitrage confirmation (v2.0)
- ExecutionEngine with paper trading
- SemanticContextEngine for risk assessment

Research Mode (DATA_MODE = SYNTHETIC_USDINR_ONLY):
- Six synthetic USD/INR feeds: Bloomberg/Reuters × Tokyo/London/New York
- Session-specific market microstructure modeling
- Provider-specific latency and noise characteristics
- Deterministic reproducible experiments
"""

import asyncio
import logging
import os
import time
from datetime import timezone, datetime, timedelta
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from typing import Any, Dict, Optional, List

# Research configuration - check mode FIRST
from backend.config import (
    DATA_MODE,
    DataMode,
    is_synthetic_mode,
    get_active_symbol,
    RESEARCH_SYMBOL,
    SYNTHETIC_FEEDS,
    ARBITRAGE_RESEARCH_CONFIG,
    TradingSession,
    DataProvider,
)

# NEW: Data source plugin architecture
from backend.core.interfaces.data_source import DataSourceConfig
from backend.core.data_sources.synthetic_data_source import SyntheticDataSource, SyntheticConfig

# NEW: Session-aware synthetic sources for research
from backend.core.data_sources.session_synthetic_source import (
    SessionAwareSyntheticSource,
    create_synthetic_sources,
    reset_synthetic_state,
)

# NEW: Arbitrage detection components
from backend.core.arbitrage.arbitrage_engine import ArbitrageConfig
from backend.core.arbitrage.opportunity_ranker import RankingConfig
from backend.core.multi_source_streamer import MultiSourceStreamer

# NEW v2.0: State management, execution, and semantic context
from backend.core.state import (
    StateStore,
    ArbitrageStateMachine,
    ArbitrageState,
    StateMachineConfig,
    TimeWeightedMetrics,
)
from backend.core.execution import (
    ExecutionEngine,
    ExecutionConfig,
    ExecutionState,
    ExecutionRequest,
    RiskLimits,
    PaperBroker,
)
from backend.core.semantic import (
    SemanticContextEngine,
    ContextConfig,
    ContextInput,
    SessionContext,
)

# NEW: Centralized control state management
from backend.core.state.control_state import (
    get_control_state_manager,
    get_control_state,
    ControlState,
    ControlStateManager,
)

# NEW: Orderbook Streaming Service
from backend.core.streaming.service import OrderbookService
from backend.core.streaming.adapters.synthetic import SyntheticStreamAdapter
from backend.core.streaming.adapters.tradingview import TradingViewStreamAdapter
from backend.core.streaming.adapters.mt5 import MT5StreamAdapter
from backend.core.redis_client import redis_client

# NEW: Experimental metrics collection for research output
from backend.core.analytics.metrics_collector import MetricsCollector
from backend.core.sentiment_bridge import SentimentBridge

# WebSocket routes (extended with arbitrage endpoints)
from backend.server.websocket_routes import (
    ws_manager,
    websocket_tick_endpoint,
    websocket_candle_endpoint,
    websocket_market_state_endpoint,
    websocket_arbitrage_endpoint,
    websocket_sources_endpoint,
    websocket_basis_endpoint,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION
# ============================================================================

# Determine symbols based on mode
from backend.config import is_basis_mode  # noqa: E402

if is_synthetic_mode():
    DEFAULT_SYMBOLS = [RESEARCH_SYMBOL]  # USD/INR only
    logger.info("=" * 60)
    logger.info("RESEARCH MODE: SYNTHETIC_USDINR_ONLY")
    logger.info(f"Symbol: {RESEARCH_SYMBOL}")
    logger.info("MT5 and live data sources are DISABLED")
    logger.info("=" * 60)
elif is_basis_mode():
    DEFAULT_SYMBOLS = [RESEARCH_SYMBOL]  # USD/INR only
    logger.info("=" * 60)
    logger.info("PHASE 3 MODE: LIVE_USDINR_BASIS (onshore-offshore)")
    logger.info("Comparison basis: Option A (futures <-> futures)")
    logger.info("=" * 60)
else:
    DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY"]

# Default intervals for candles (including micro-candles)
DEFAULT_INTERVALS = ["100ms", "500ms", "1s", "5s", "15s", "1m"]

# ============================================================================
# MT5 CONFIGURATION (Only used in LIVE_MT5 mode)
# ============================================================================
# Default fallback credentials
MT5_LOGIN = 102447207
MT5_PASSWORD = "6q*aOaQj"
MT5_SERVER = "MetaQuotes-Demo"
MT5_PATH = r"C:\Program Files\MetaTrader 5\terminal64.exe"

# Try to load securely from Supabase
SUPABASE_URL = os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
SUPABASE_KEY = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY")
SUPABASE_USER_ID = os.environ.get("SUPABASE_USER_ID") # Set this when running locally

if SUPABASE_URL and SUPABASE_KEY and SUPABASE_USER_ID:
    from backend.core.supabase_client import supabase_db
    supabase_db.initialize(SUPABASE_URL, SUPABASE_KEY)
    creds = supabase_db.get_mt5_credentials(SUPABASE_USER_ID)
    if creds:
        MT5_LOGIN = int(creds.get("mt5_login", MT5_LOGIN))
        MT5_PASSWORD = creds.get("mt5_password", MT5_PASSWORD)
        MT5_SERVER = creds.get("mt5_server", MT5_SERVER)
        logger.info(f"Loaded secure MT5 credentials from Supabase for user {SUPABASE_USER_ID}")

# ============================================================================
# ARBITRAGE ENGINE CONFIGURATION
# ============================================================================

# Always enable arbitrage detection
ENABLE_ARBITRAGE_DETECTION = True

# Time alignment window size in milliseconds
ALIGNMENT_WINDOW_MS = ARBITRAGE_RESEARCH_CONFIG.alignment_window_ms if is_synthetic_mode() else 20

# Minimum profit in pips to report an opportunity
MIN_PROFIT_PIPS = ARBITRAGE_RESEARCH_CONFIG.min_profit_pips if is_synthetic_mode() else 0.1

# Minimum confidence score (0.0 to 1.0)
MIN_CONFIDENCE = ARBITRAGE_RESEARCH_CONFIG.min_confidence if is_synthetic_mode() else 0.5

# ============================================================================

# Global instances (conditionally initialized based on mode)
mt5_client = None
tick_streamer = None
bar_aggregators: Dict[str, Any] = {}

# Multi-source components
mt5_data_source = None
synthetic_sources: List[SessionAwareSyntheticSource] = []
multi_source_streamer: Optional[MultiSourceStreamer] = None

# v2.0: Advanced state management and execution
state_store: Optional[StateStore] = None
arbitrage_state_machine: Optional[ArbitrageStateMachine] = None
time_weighted_metrics: Optional[TimeWeightedMetrics] = None
execution_engine: Optional[ExecutionEngine] = None
semantic_context_engine: Optional[SemanticContextEngine] = None

# v3.0: Experimental metrics collection
metrics_collector: Optional[MetricsCollector] = None

# v3.1: Sentiment bridge for live article evidence and historical bias
sentiment_bridge = SentimentBridge()

# v4.0: Orderbook Streaming Service
orderbook_service: Optional[OrderbookService] = None

# Phase 3: onshore/offshore USD/INR basis recorder task
basis_recorder_task: Optional[asyncio.Task] = None




@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for startup/shutdown tasks.
    
    In SYNTHETIC_USDINR_ONLY mode:
    - Skips MT5 initialization entirely
    - Creates six session-aware synthetic USD/INR sources
    - Initializes arbitrage engine for cross-session/cross-provider detection
    
    In LIVE_MT5 mode:
    - Initializes MT5 connection (primary data source)
    - Creates synthetic source for comparison
    - Full arbitrage pipeline
    """
    global mt5_client, tick_streamer, bar_aggregators
    global mt5_data_source, synthetic_sources, multi_source_streamer
    global state_store, arbitrage_state_machine, time_weighted_metrics
    global execution_engine, semantic_context_engine
    global metrics_collector
    
    # Import bar aggregator here to avoid circular imports
    from backend.core.bar_aggregator import MultiIntervalAggregator
    
    # Startup
    logger.info("=" * 60)
    if is_synthetic_mode():
        logger.info("Starting FX Arbitrage Research Engine")
        logger.info("MODE: SYNTHETIC_USDINR_ONLY (Research Grade)")
        logger.info(f"Symbol: {RESEARCH_SYMBOL}")
        logger.info("MT5 Connection: DISABLED")
    else:
        logger.info("Starting MT5 Market Data Engine with Arbitrage Detection")
        logger.info("MODE: LIVE_MT5")
    logger.info("=" * 60)
    
    # Initialize bar aggregators for each symbol
    for symbol in DEFAULT_SYMBOLS:
        aggregator = MultiIntervalAggregator(DEFAULT_INTERVALS, ws_manager)
        bar_aggregators[symbol] = aggregator
    
    # =========================================================================
    # MODE-SPECIFIC INITIALIZATION
    # =========================================================================
    
    if is_synthetic_mode():
        # =====================================================================
        # SYNTHETIC USD/INR RESEARCH MODE
        # =====================================================================
        logger.info("-" * 60)
        logger.info("Initializing Synthetic USD/INR Research Pipeline")
        logger.info("-" * 60)
        
        # Reset synthetic state for reproducible session
        reset_synthetic_state(seed=42)
        
        # Create arbitrage configuration for research
        arbitrage_config = ArbitrageConfig(
            min_profit_pips=MIN_PROFIT_PIPS,
            min_confidence=MIN_CONFIDENCE,
            enable_cross_source=True,  # Cross-provider detection
            enable_triangular=False,    # Not applicable for single pair
            enable_session_analysis=True,  # Session inefficiency detection
        )
        
        # Create ranking configuration
        ranking_config = RankingConfig(
            min_composite_score=20.0,  # Lower threshold for research
            max_results=100,
        )
        
        # Initialize multi-source streamer
        multi_source_streamer = MultiSourceStreamer(
            ws_manager=ws_manager,
            bar_aggregators=bar_aggregators,
            arbitrage_config=arbitrage_config,
            ranking_config=ranking_config,
            alignment_window_ms=ALIGNMENT_WINDOW_MS,
        )
        
        # Create all six synthetic sources
        synthetic_sources = create_synthetic_sources()
        
        for source in synthetic_sources:
            multi_source_streamer.add_source(source)
            logger.info(f"Added synthetic source: {source.source_id} "
                       f"(session={source.session.value}, provider={source.provider.value})")
        
        # Start multi-source streaming for USD/INR only
        await multi_source_streamer.start(symbols=DEFAULT_SYMBOLS)
        
        logger.info(f"Synthetic research pipeline initialized with "
                   f"{len(synthetic_sources)} data sources")
        logger.info(f"Arbitrage detection enabled for cross-provider and cross-session analysis")

    elif is_basis_mode():
        # =====================================================================
        # PHASE 3 — LIVE ONSHORE/OFFSHORE USD/INR BASIS (Option A)
        # =====================================================================
        global basis_recorder_task

        _broker = os.environ.get("ARBEX_ONSHORE_BROKER", "upstox").lower()
        _have_onshore = bool(os.environ.get("UPSTOX_ACCESS_TOKEN")) if _broker != "dhan" \
            else bool(os.environ.get("DHAN_CLIENT_ID") and os.environ.get("DHAN_ACCESS_TOKEN"))
        _replay = os.environ.get("BASIS_REPLAY") == "1" or not _have_onshore

        if _replay:
            from backend.core.basis.basis_replay import BasisReplayer
            replayer = BasisReplayer(ws_manager)
            app.state.basis_ctx = replayer
            basis_recorder_task = asyncio.create_task(replayer.run())
            logger.info("Basis dashboard REPLAY mode (no onshore creds / BASIS_REPLAY=1) "
                        "— streaming the EOD run over /ws/basis. Dashboard: /basis")
        else:
            from backend.core.data_sources.basis_pipeline import (
                build_basis_pipeline, build_retail_arb_pipeline)
            _retail = os.environ.get("ARBEX_RETAIL_ARB") == "1"
            basis_ctx = build_retail_arb_pipeline() if _retail else build_basis_pipeline()
            basis_ctx.ws_manager = ws_manager
            app.state.basis_ctx = basis_ctx
            basis_recorder_task = asyncio.create_task(basis_ctx.run())
            logger.info(
                f"Basis pipeline LIVE: legs={[s.source_id for s in basis_ctx.sources]} "
                f"T*={basis_ctx.normalizer.target_expiry} "
                f"carry={basis_ctx.normalizer.carry_rate_annual}. Dashboard: /basis"
            )

    else:
        # =====================================================================
        # LIVE MT5 MODE (Original behavior)
        # =====================================================================
        # Import MT5 components only when needed
        from backend.core.mt5_client import MT5Client
        from backend.core.tick_streamer import TickStreamer
        from backend.core.data_sources.mt5_data_source import MT5DataSource
        
        # Initialize MT5 client
        mt5_client = MT5Client(
            path=MT5_PATH,
            login=MT5_LOGIN,
            password=MT5_PASSWORD,
            server=MT5_SERVER
        )
        
        if not mt5_client.connect():
            logger.error("Failed to connect to MT5. Please ensure MetaTrader 5 is running.")
            raise RuntimeError("MT5 connection failed")
        
        logger.info("-" * 60)
        logger.info("Initializing Arbitrage Detection Engine")
        logger.info("-" * 60)
        
        # Create arbitrage configuration
        arbitrage_config = ArbitrageConfig(
            min_profit_pips=MIN_PROFIT_PIPS,
            min_confidence=MIN_CONFIDENCE,
            enable_cross_source=True,
            enable_triangular=True,
            enable_session_analysis=True,
        )
        
        # Create ranking configuration
        ranking_config = RankingConfig(
            min_composite_score=30.0,
            max_results=50,
        )
        
        # Initialize multi-source streamer
        multi_source_streamer = MultiSourceStreamer(
            ws_manager=ws_manager,
            bar_aggregators=bar_aggregators,
            arbitrage_config=arbitrage_config,
            ranking_config=ranking_config,
            alignment_window_ms=ALIGNMENT_WINDOW_MS,
        )
        
        # Create MT5 data source plugin
        mt5_config = DataSourceConfig(
            source_id="mt5_primary",
            source_type="mt5",
            display_name="MT5 Primary Feed",
            priority=1,
            latency_estimate_ms=50.0,
            reliability_score=0.95,
            symbols=DEFAULT_SYMBOLS,
        )
        mt5_data_source = MT5DataSource(
            config=mt5_config,
            path=MT5_PATH,
            login=MT5_LOGIN,
            password=MT5_PASSWORD,
            server=MT5_SERVER,
        )
        multi_source_streamer.add_source(mt5_data_source)
        logger.info(f"Added data source: MT5 Primary Feed")
        
        # Start multi-source streaming
        await multi_source_streamer.start(symbols=DEFAULT_SYMBOLS)
        
        logger.info(f"Arbitrage detection enabled with {len(multi_source_streamer.get_sources())} data sources")
    
    # =========================================================================
    # STATE MANAGEMENT & EXECUTION ENGINE SETUP (v2.0) - Both Modes
    # =========================================================================
    
    logger.info("-" * 60)
    logger.info("Initializing v2.0 State Management & Execution")
    logger.info("-" * 60)
    
    # Initialize StateStore - central state management
    state_store = StateStore(alignment_window_ms=ALIGNMENT_WINDOW_MS)
    logger.info("Initialized StateStore")
    
    # Initialize ArbitrageStateMachine with confirmation windows
    state_machine_config = StateMachineConfig(
        confirmation_window_ms=100,
        cooldown_period_ms=500,
        min_profit_pips=MIN_PROFIT_PIPS,
        min_confidence=MIN_CONFIDENCE,
        min_stability=0.3,
    )
    
    # Define callback for confirmed opportunities
    def on_opportunity_confirmed(confirmed):
        """Handle confirmed arbitrage opportunities."""
        logger.info(f"[CONFIRMED] {confirmed.symbol}: {confirmed.profit_pips:.2f} pips "
                   f"(conf={confirmed.confidence:.2f}, stab={confirmed.stability:.2f})")
        # Update state store
        state_store.update_arbitrage_state(
            symbol=confirmed.symbol,
            state=ArbitrageState.CONFIRMED.value,
            profit_pips=confirmed.profit_pips,
            confidence=confirmed.confidence,
            stability=confirmed.stability,
            best_buy_source=confirmed.buy_source,
            best_sell_source=confirmed.sell_source,
        )
    
    arbitrage_state_machine = ArbitrageStateMachine(
        config=state_machine_config,
        on_confirmed=on_opportunity_confirmed,
    )
    
    # Add symbols to the state machine
    for symbol in DEFAULT_SYMBOLS:
        arbitrage_state_machine.add_symbol(symbol)
    
    logger.info("Initialized ArbitrageStateMachine with confirmation windows")
    
    # Initialize TimeWeightedMetrics for EMA-based scoring
    time_weighted_metrics = TimeWeightedMetrics(
        profit_ema_span=10,
        confidence_ema_span=5,
    )
    # Add symbols to the metrics tracker
    for symbol in DEFAULT_SYMBOLS:
        time_weighted_metrics.add_symbol(symbol)
    logger.info("Initialized TimeWeightedMetrics")
    
    # Initialize ExecutionEngine with PaperBroker
    risk_limits = RiskLimits(
        max_position_size=0.1,
        max_daily_loss=1000.0,
        min_confidence=0.7,
        min_stability=0.5,
        min_profit_pips=0.3,
    )
    execution_config = ExecutionConfig(
        mode=ExecutionState.PAPER,
        risk_limits=risk_limits,
        enable_audit_log=True,
    )
    execution_engine = ExecutionEngine(config=execution_config)
    execution_engine.set_broker(PaperBroker())
    await execution_engine.start()
    logger.info("Initialized ExecutionEngine in PAPER mode")
    
    # Initialize SemanticContextEngine (embeddings disabled by default)
    context_config = ContextConfig(
        enable_embeddings=False,
        max_risk_adjustment=0.3,
    )
    semantic_context_engine = SemanticContextEngine(config=context_config)
    logger.info("Initialized SemanticContextEngine")
    
    # Initialize MetricsCollector for research analytics
    metrics_collector = MetricsCollector()
    logger.info("Initialized MetricsCollector for experimental output")
    
    # Initialize OrderbookService
    global orderbook_service
    orderbook_service = OrderbookService(redis_client=redis_client)
    orderbook_service.register_adapter(SyntheticStreamAdapter())
    orderbook_service.register_adapter(TradingViewStreamAdapter())
    orderbook_service.register_adapter(MT5StreamAdapter())
    await orderbook_service.start()
    logger.info("Initialized OrderbookService with modular adapters")

    sentiment_bridge.start_live_ingest()
    logger.info("Started SentimentBridge live feed ingest")
    
    logger.info("=" * 60)
    logger.info("Server startup complete")
    if is_synthetic_mode():
        logger.info("RESEARCH MODE ACTIVE - Synthetic USD/INR feeds only")
        logger.info(f"Data sources: {len(synthetic_sources)}")
        for src in synthetic_sources:
            logger.info(f"  - {src.source_id}: {src.session.value} / {src.provider.value}")
    logger.info("=" * 60)
    logger.info(f"WebSocket endpoints:")
    logger.info(f"  - ws://localhost:8000/ws/ticks/{{symbol}}")
    logger.info(f"  - ws://localhost:8000/ws/candles/{{symbol}}/{{interval}}")
    logger.info(f"  - ws://localhost:8000/ws/market/{{symbol}}")
    logger.info(f"  - ws://localhost:8000/ws/arbitrage")
    logger.info(f"  - ws://localhost:8000/ws/arbitrage/{{symbol}}")
    logger.info(f"  - ws://localhost:8000/ws/sources/{{symbol}}")
    logger.info(f"  - ws://localhost:8000/ws/dashboard (unified dashboard)")
    logger.info("-" * 60)
    
    # Start WebSocketManager background tasks (Redis listeners)
    ws_manager.start_background_tasks()
    
    # Start background task for periodic state updates
    state_broadcast_task = None
    
    async def broadcast_state_periodically():
        """Broadcast state machine states to dashboard every 2 seconds."""
        while True:
            try:
                await asyncio.sleep(2)
                if arbitrage_state_machine:
                    states = arbitrage_state_machine.get_all_states()
                    await ws_manager.broadcast_state_update(states)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Error broadcasting state update: {e}")
    
    state_broadcast_task = asyncio.create_task(broadcast_state_periodically())
    logger.info("Started periodic state broadcast task")
    
    yield
    
    # Shutdown
    logger.info("Shutting down...")
    
    # Cancel state broadcast task
    if state_broadcast_task:
        state_broadcast_task.cancel()
        try:
            await state_broadcast_task
        except asyncio.CancelledError:
            pass

    # Stop the Phase-3 basis recorder
    if basis_recorder_task:
        basis_recorder_task.cancel()
        try:
            await basis_recorder_task
        except asyncio.CancelledError:
            pass
        logger.info("Stopped basis pipeline")

    # Stop execution engine
    if execution_engine:
        await execution_engine.stop()
        logger.info("Stopped ExecutionEngine")
        
    # Stop orderbook service
    if orderbook_service:
        await orderbook_service.stop()
        logger.info("Stopped OrderbookService")

    sentiment_bridge.stop_live_ingest()
    logger.info("Stopped SentimentBridge live feed ingest")
    
    # Stop multi-source streamer
    if multi_source_streamer:
        await multi_source_streamer.stop()
    
    # Stop original tick streams (if used)
    if tick_streamer:
        await tick_streamer.stop_all()
    
    # Disconnect data sources based on mode
    if is_synthetic_mode():
        for source in synthetic_sources:
            source.disconnect()
        logger.info(f"Disconnected {len(synthetic_sources)} synthetic sources")
    else:
        if mt5_data_source:
            mt5_data_source.disconnect()
        if mt5_client:
            mt5_client.disconnect()
    
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="FX Arbitrage Research Engine" if is_synthetic_mode() else "MT5 Market Data Engine",
    description=f"""
{"Research-grade FX arbitrage detection system with synthetic USD/INR feeds." if is_synthetic_mode() else "Real-time MetaTrader 5 tick and candle streaming API."}

## Current Mode: {"SYNTHETIC_USDINR_ONLY (Research)" if is_synthetic_mode() else "LIVE_MT5"}

{"### Synthetic Data Sources" if is_synthetic_mode() else ""}
{"- Bloomberg Tokyo USD/INR" if is_synthetic_mode() else ""}
{"- Reuters Tokyo USD/INR" if is_synthetic_mode() else ""}
{"- Bloomberg London USD/INR" if is_synthetic_mode() else ""}
{"- Reuters London USD/INR" if is_synthetic_mode() else ""}
{"- Bloomberg New York USD/INR" if is_synthetic_mode() else ""}
{"- Reuters New York USD/INR" if is_synthetic_mode() else ""}

## Features
- Multi-source FX data streaming {"(6 synthetic USD/INR feeds)" if is_synthetic_mode() else "(MT5 + Synthetic feeds)"}
- Real-time arbitrage opportunity detection
- Cross-source {"and cross-session" if is_synthetic_mode() else ", triangular, and session-based"} arbitrage
- Configurable time alignment windows
- Opportunity ranking and scoring

## WebSocket Endpoints
- `/ws/ticks/{{symbol}}` - Real-time tick stream
- `/ws/candles/{{symbol}}/{{interval}}` - Real-time candle stream
- `/ws/market/{{symbol}}` - Aggregated market state
- `/ws/arbitrage` - All arbitrage opportunities
- `/ws/arbitrage/{{symbol}}` - Symbol-specific opportunities
- `/ws/sources/{{symbol}}` - Source price comparisons
    """,
    version="3.0.0-research" if is_synthetic_mode() else "2.0.0",
    lifespan=lifespan
)

# CORS middleware for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (frontend)
# Get the project root directory (2 levels up from this file)
PROJECT_ROOT = Path(__file__).parent.parent.parent
FRONTEND_DIR = PROJECT_ROOT / "frontend"
if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ============================================================================
# REST Endpoints
# ============================================================================

@app.get("/")
async def root():
    """Serve the Arbitrage Research terminal (or the basis dashboard in basis mode)."""
    if is_basis_mode():
        basis_file = FRONTEND_DIR / "basis_dashboard.html"
        if basis_file.exists():
            return FileResponse(str(basis_file))
    # Serve the Arbitrage Research dashboard
    research_file = FRONTEND_DIR / "arbitrage_research.html"
    if research_file.exists():
        return FileResponse(str(research_file))
    
    # Fallback to API info
    endpoints = {
        "health": "/health",
        "symbols": "/symbols",
        "intervals": "/intervals",
        "snapshot": "/snapshot/{symbol}",
        "ticks_ws": "/ws/ticks/{symbol}",
        "candles_ws": "/ws/candles/{symbol}/{interval}",
        "market_state_ws": "/ws/market/{symbol}",
        "arbitrage_ws": "/ws/arbitrage",
        "arbitrage_symbol_ws": "/ws/arbitrage/{symbol}",
        "sources_ws": "/ws/sources/{symbol}",
        "arbitrage_stats": "/arbitrage/stats",
        "arbitrage_recent": "/arbitrage/recent",
        "sources": "/sources",
    }
    
    if is_synthetic_mode():
        endpoints["research_config"] = "/research/config"
        endpoints["research_reset"] = "/research/reset"
    
    return {
        "service": "FX Arbitrage Research Engine" if is_synthetic_mode() else "MT5 Market Data Engine",
        "version": "3.0.0-research" if is_synthetic_mode() else "2.0.0",
        "status": "running",
        "mode": DATA_MODE.value,
        "symbols": DEFAULT_SYMBOLS,
        "data_sources": len(synthetic_sources) if is_synthetic_mode() else "MT5 + Synthetic",
        "arbitrage_enabled": ENABLE_ARBITRAGE_DETECTION,
        "endpoints": endpoints,
    }


@app.get("/health")
async def health():
    """Health check endpoint with detailed status."""
    health_data = {
        "status": "healthy",
        "mode": DATA_MODE.value,
        "active_channels": len(ws_manager.get_all_channels()),
        "symbols": DEFAULT_SYMBOLS,
        "intervals": DEFAULT_INTERVALS,
        "arbitrage_enabled": ENABLE_ARBITRAGE_DETECTION,
    }
    
    if is_synthetic_mode():
        health_data["synthetic_sources"] = len(synthetic_sources)
        health_data["mt5"] = "disabled"
        health_data["research_mode"] = True
        
        # Add source details
        health_data["source_details"] = [
            {
                "id": src.source_id,
                "session": src.session.value,
                "provider": src.provider.value,
                "connected": src.is_connected,
            }
            for src in synthetic_sources
        ]
    else:
        mt5_status = "connected" if (mt5_client and mt5_client.is_connected) else "disconnected"
        health_data["mt5"] = mt5_status
        health_data["research_mode"] = False
    
    # Add arbitrage-specific health info
    if multi_source_streamer:
        stats = multi_source_streamer.get_stats()
        health_data["data_sources"] = stats.get("sources", 0)
        health_data["ticks_processed"] = stats.get("ticks_processed", 0)
        health_data["arbitrage_opportunities"] = stats.get("arbitrage_opportunities_detected", 0)
    
    return health_data


@app.get("/sentiment/status")
async def get_sentiment_status(pair: str = "USD/INR"):
    """Sentiment service status plus local dataset metadata for a pair."""
    return await asyncio.to_thread(sentiment_bridge.get_status, pair)


@app.get("/sentiment/live")
async def get_sentiment_live(pair: str = "USD/INR", window_minutes: int = 30):
    """Return the live rolling news sentiment window used by the Arbex sentiment UI."""
    return await asyncio.to_thread(sentiment_bridge.get_live_sentiment, pair, window_minutes)


@app.get("/sentiment/articles/live")
async def get_sentiment_articles(pair: str = "USD/INR", window_minutes: int = 30, limit: int = 12):
    """Return the live article impact tape derived from the current sentiment window."""
    return await asyncio.to_thread(sentiment_bridge.get_live_articles, pair, window_minutes, limit)


@app.get("/sentiment/feed/live")
async def get_sentiment_feed(pair: str = "USD/INR", limit: int = 20):
    """Return the raw live news feed mirrored into Arbex with provenance and source drill-down."""
    return await asyncio.to_thread(sentiment_bridge.get_feed_snapshot, pair, limit)


@app.get("/sentiment/history/live")
async def get_sentiment_history(
    pair: str = "USD/INR",
    limit: int = 20,
    q: str | None = None,
    min_confidence: float | None = None,
    has_evidence: bool | None = None,
    source: str | None = None,
    event_category: str | None = None,
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
):
    """Return evidence-rich analyzed live article history mirrored into Arbex."""
    return await asyncio.to_thread(
        sentiment_bridge.get_analysis_history,
        pair,
        limit,
        q=q,
        min_confidence=min_confidence,
        has_evidence=has_evidence,
        source=source,
        event_category=event_category,
        from_timestamp=from_timestamp,
        to_timestamp=to_timestamp,
    )


@app.get("/sentiment/explanation/{event_id}")
async def get_sentiment_explanation(event_id: str, pair: str = "USD/INR"):
    """Return the full RAG-backed semantic explanation for one live or recent article."""
    return await asyncio.to_thread(sentiment_bridge.get_explanation, pair, event_id)


@app.get("/sentiment/bias")
async def get_sentiment_bias(
    pair: str = "USD/INR",
    from_timestamp: datetime | None = None,
    to_timestamp: datetime | None = None,
    aggregate_interval_minutes: int = 60,
    horizon_minutes: int = 60,
):
    """Return historical sentiment-bias bins from the local Arbex sentiment dataset copy."""
    now = datetime.now(timezone.utc)
    resolved_to = to_timestamp.astimezone(timezone.utc) if to_timestamp is not None else now
    resolved_from = from_timestamp.astimezone(timezone.utc) if from_timestamp is not None else (resolved_to - timedelta(hours=24))
    return await asyncio.to_thread(
        sentiment_bridge.get_historical_bias,
        pair=pair,
        from_timestamp=resolved_from,
        to_timestamp=resolved_to,
        aggregate_interval_minutes=aggregate_interval_minutes,
        horizon_minutes=horizon_minutes,
    )


@app.get("/symbols")
async def get_symbols():
    """Get list of available symbols."""
    return {
        "symbols": DEFAULT_SYMBOLS,
        "active": DEFAULT_SYMBOLS
    }


@app.get("/intervals")
async def get_intervals():
    """Get list of available intervals."""
    return {
        "intervals": DEFAULT_INTERVALS
    }


@app.get("/snapshot/{symbol}")
async def get_snapshot(symbol: str):
    """
    Get current market snapshot for a symbol.
    
    Returns:
        Current bid/ask, spread, time, and last candles for all intervals
    """
    symbol = symbol.upper()
    
    # Ensure symbol is being streamed
    if symbol not in DEFAULT_SYMBOLS:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not available")
    
    # Get last tick from WebSocket manager
    last_tick = ws_manager.last_ticks.get(symbol)
    if not last_tick:
        return {
            "symbol": symbol,
            "error": "No tick data available yet"
        }
    
    # Get last candles for all intervals
    last_candles = {}
    if symbol in bar_aggregators:
        aggregator = bar_aggregators[symbol]
        completed_bars = aggregator.get_last_completed_bars(symbol)
        for interval in DEFAULT_INTERVALS:
            if interval in completed_bars and completed_bars[interval]:
                last_candles[interval] = completed_bars[interval]
    
    return {
        "symbol": symbol,
        "bid": last_tick.get("bid"),
        "ask": last_tick.get("ask"),
        "spread": last_tick.get("spread"),
        "time": last_tick.get("time"),
        "last_candles": last_candles
    }


# ============================================================================
# WebSocket Endpoints
# ============================================================================

@app.websocket("/ws/ticks/{symbol}")
async def websocket_ticks(websocket: WebSocket, symbol: str):
    """
    WebSocket endpoint for tick streaming.
    
    Example: ws://localhost:8000/ws/ticks/EURUSD
    """
    # Ensure symbol is being streamed
    if symbol not in DEFAULT_SYMBOLS:
        await websocket.close(code=1008, reason=f"Symbol {symbol} not available")
        return
    
    await websocket_tick_endpoint(websocket, symbol.upper())


@app.websocket("/ws/candles/{symbol}/{interval}")
async def websocket_candles(websocket: WebSocket, symbol: str, interval: str):
    """
    WebSocket endpoint for candle streaming.
    
    Example: ws://localhost:8000/ws/candles/EURUSD/1s
    
    Intervals: 1s, 5s, 15s, 1m, 5m, 15m, 1h
    """
    # Validate interval (including micro-candles)
    valid_intervals = ["100ms", "500ms", "1s", "5s", "15s", "1m", "5m", "15m", "1h"]
    if interval not in valid_intervals:
        await websocket.close(code=1008, reason=f"Invalid interval: {interval}")
        return
    
    # Ensure symbol is being streamed
    if symbol not in DEFAULT_SYMBOLS:
        await websocket.close(code=1008, reason=f"Symbol {symbol} not available")
        return
    
    await websocket_candle_endpoint(websocket, symbol.upper(), interval)


@app.websocket("/ws/market/{symbol}")
async def websocket_market_state(websocket: WebSocket, symbol: str):
    """
    WebSocket endpoint for aggregated market state streaming.
    
    Example: ws://localhost:8000/ws/market/EURUSD
    
    Provides: spread, trend, momentum, volatility_rank, last_tick, last_candles
    """
    # Ensure symbol is being streamed
    if symbol not in DEFAULT_SYMBOLS:
        await websocket.close(code=1008, reason=f"Symbol {symbol} not available")
        return
    
    await websocket_market_state_endpoint(websocket, symbol.upper())


# ============================================================================
# ARBITRAGE ENDPOINTS
# ============================================================================

@app.get("/arbitrage/stats")
async def get_arbitrage_stats():
    """
    Get arbitrage detection statistics.
    
    Returns counts of detected opportunities by type, processing stats, etc.
    """
    if not ENABLE_ARBITRAGE_DETECTION:
        return {"error": "Arbitrage detection is not enabled"}
    
    stats = {
        "websocket_stats": ws_manager.get_arbitrage_stats(),
    }
    
    if multi_source_streamer:
        stats["engine_stats"] = multi_source_streamer.get_stats()
    
    return stats


@app.get("/arbitrage/recent")
async def get_recent_arbitrage(limit: int = 20):
    """
    Get recent arbitrage opportunities.
    
    Args:
        limit: Maximum number of opportunities to return (default 20)
    
    Returns:
        List of recent opportunities with scores and details
    """
    if not ENABLE_ARBITRAGE_DETECTION:
        return {"error": "Arbitrage detection is not enabled"}
    
    return {
        "opportunities": ws_manager.get_recent_arbitrage(limit=limit),
        "total_available": len(ws_manager.recent_arbitrage),
    }


@app.get("/sources")
async def get_data_sources():
    """
    Get information about all data sources.
    
    Returns source IDs, types, connection status, and stats.
    """
    if not ENABLE_ARBITRAGE_DETECTION or not multi_source_streamer:
        return {"error": "Multi-source streaming is not enabled"}
    
    return {
        "sources": multi_source_streamer.get_sources(),
        "count": len(multi_source_streamer.get_sources()),
    }


@app.get("/api/price-data")
async def get_price_data(limit: int = 500):
    """
    Get recent price data for all symbols.
    
    Returns the latest ticks from the WebSocket manager's cache.
    """
    prices = {}
    for symbol in DEFAULT_SYMBOLS:
        tick = ws_manager.last_ticks.get(symbol)
        if tick:
            prices[symbol] = {
                "bid": tick.get("bid"),
                "ask": tick.get("ask"),
                "mid": tick.get("mid"),
                "spread": tick.get("spread"),
                "time": tick.get("time_ms"),
                "source": tick.get("source_id"),
            }
    return {
        "prices": prices,
        "count": len(prices),
    }


@app.get("/api/stats")
async def get_stats():
    """
    Get aggregate system statistics.
    
    Returns tick counts, latency stats, and source health.
    """
    stats = {
        "ticks_processed": 0,
        "sources_active": 0,
        "avg_latency_ms": 0,
        "symbols": DEFAULT_SYMBOLS,
        "mode": DATA_MODE.value,
    }
    
    if multi_source_streamer:
        streamer_stats = multi_source_streamer.get_stats()
        stats["ticks_processed"] = streamer_stats.get("ticks_processed", 0)
        stats["sources_active"] = streamer_stats.get("sources", 0)
        stats["arbitrage_opportunities"] = streamer_stats.get("arbitrage_opportunities_detected", 0)
    
    if ws_manager:
        arb_stats = ws_manager.get_arbitrage_stats()
        stats["total_opportunities"] = arb_stats.get("total_opportunities", 0)
    
    return stats


# ============================================================================
# RESEARCH MODE ENDPOINTS
# ============================================================================

@app.get("/research/config")
async def get_research_config():
    """
    Get current research configuration.
    
    Returns session configs, provider configs, and generation parameters.
    Only available in SYNTHETIC_USDINR_ONLY mode.
    """
    if not is_synthetic_mode():
        return {"error": "Research endpoints only available in SYNTHETIC_USDINR_ONLY mode"}
    
    from backend.config import (
        SESSION_CONFIGS,
        PROVIDER_CONFIGS,
        SYNTHETIC_GENERATION_CONFIG,
        ARBITRAGE_RESEARCH_CONFIG,
    )
    
    return {
        "mode": DATA_MODE.value,
        "symbol": RESEARCH_SYMBOL,
        "sessions": {
            session.value: {
                "volatility_multiplier": cfg.volatility_multiplier,
                "spread_pips": cfg.spread_pips,
                "drift_pips_per_tick": cfg.drift_pips_per_tick,
                "description": cfg.description,
            }
            for session, cfg in SESSION_CONFIGS.items()
        },
        "providers": {
            provider.value: {
                "latency_ms": cfg.latency_ms,
                "jitter_ms": cfg.jitter_ms,
                "noise_pips": cfg.noise_pips,
                "smoothing_factor": cfg.smoothing_factor,
                "description": cfg.description,
            }
            for provider, cfg in PROVIDER_CONFIGS.items()
        },
        "generation": {
            "tick_interval_ms": SYNTHETIC_GENERATION_CONFIG.tick_interval_ms,
            "random_seed": SYNTHETIC_GENERATION_CONFIG.random_seed,
            "arbitrage_injection_rate": SYNTHETIC_GENERATION_CONFIG.arbitrage_injection_rate,
        },
        "arbitrage": {
            "min_profit_pips": ARBITRAGE_RESEARCH_CONFIG.min_profit_pips,
            "min_confidence": ARBITRAGE_RESEARCH_CONFIG.min_confidence,
            "alignment_window_ms": ARBITRAGE_RESEARCH_CONFIG.alignment_window_ms,
        },
    }


@app.post("/research/reset")
async def reset_research_session(seed: Optional[int] = None):
    """
    Reset synthetic data state for a new research session.
    
    Args:
        seed: Optional random seed for reproducibility
    
    Returns:
        Confirmation of reset
    """
    if not is_synthetic_mode():
        return {"error": "Research endpoints only available in SYNTHETIC_USDINR_ONLY mode"}
    
    reset_synthetic_state(seed=seed)
    
    return {
        "status": "reset",
        "new_seed": seed,
        "message": "Synthetic price state reset for new research session",
    }


@app.get("/research/sources")
async def get_research_sources():
    """
    Get detailed information about all synthetic sources.
    
    Returns session, provider, and current state for each source.
    """
    if not is_synthetic_mode():
        return {"error": "Research endpoints only available in SYNTHETIC_USDINR_ONLY mode"}
    
    sources_info = []
    for source in synthetic_sources:
        stats = source.get_stats()
        sources_info.append({
            "source_id": source.source_id,
            "session": source.session.value,
            "provider": source.provider.value,
            "connected": source.is_connected,
            "current_price": stats.get("current_price"),
            "current_bid": stats.get("current_bid"),
            "current_ask": stats.get("current_ask"),
            "session_drift": stats.get("session_drift"),
            "ticks_generated": stats.get("generated_count"),
        })
    
    # Group by session
    by_session = {}
    for src in sources_info:
        session = src["session"]
        if session not in by_session:
            by_session[session] = []
        by_session[session].append(src)
    
    return {
        "total_sources": len(synthetic_sources),
        "sources": sources_info,
        "by_session": by_session,
    }


@app.get("/research/price-comparison")
async def get_research_price_comparison():
    """
    Get current prices from all sources for cross-session/cross-provider comparison.
    
    Returns price matrix organized by session and provider.
    """
    if not is_synthetic_mode():
        return {"error": "Research endpoints only available in SYNTHETIC_USDINR_ONLY mode"}
    
    # Get reference price
    from backend.core.data_sources.session_synthetic_source import SharedReferencePrice
    ref_price = SharedReferencePrice().get_price()
    
    # Build comparison matrix
    comparison = {
        "reference_price": round(ref_price, 4),
        "symbol": RESEARCH_SYMBOL,
        "timestamp_ms": int(time.time() * 1000),
        "sessions": {},
    }
    
    for source in synthetic_sources:
        session = source.session.value
        provider = source.provider.value
        stats = source.get_stats()
        
        if session not in comparison["sessions"]:
            comparison["sessions"][session] = {}
        
        comparison["sessions"][session][provider] = {
            "bid": stats.get("current_bid"),
            "ask": stats.get("current_ask"),
            "mid": stats.get("current_price"),
            "drift_from_ref": round(stats.get("current_price", 0) - ref_price, 4),
            "spread_pips": round((stats.get("current_ask", 0) - stats.get("current_bid", 0)) / 0.01, 2),
        }
    
    # Calculate cross-session spreads
    sessions = list(comparison["sessions"].keys())
    cross_session = {}
    for i, s1 in enumerate(sessions):
        for s2 in sessions[i+1:]:
            # Compare Bloomberg prices across sessions
            if "Bloomberg" in comparison["sessions"][s1] and "Bloomberg" in comparison["sessions"][s2]:
                diff = comparison["sessions"][s1]["Bloomberg"]["mid"] - comparison["sessions"][s2]["Bloomberg"]["mid"]
                cross_session[f"{s1}_vs_{s2}_bloomberg"] = round(diff / 0.01, 2)  # in pips
    
    comparison["cross_session_diff_pips"] = cross_session
    
    return comparison


@app.get("/session")
async def get_current_session():
    """
    Get current trading session information.
    
    Returns session name, overlap status, and characteristics.
    """
    from datetime import datetime, timezone
    
    now = datetime.now(timezone.utc)
    hour = now.hour
    
    # Session detection (UTC hours)
    # Sydney: 21:00-06:00 UTC
    # Tokyo: 23:00-08:00 UTC  
    # London: 07:00-16:00 UTC
    # New York: 12:00-21:00 UTC
    
    sessions = []
    
    if 21 <= hour or hour < 6:
        sessions.append("Sydney")
    if 23 <= hour or hour < 8:
        sessions.append("Tokyo")
    if 7 <= hour < 16:
        sessions.append("London")
    if 12 <= hour < 21:
        sessions.append("New York")
    
    # Determine overlap
    overlap = None
    if "London" in sessions and "New York" in sessions:
        overlap = "London/NY"
    elif "Tokyo" in sessions and "London" in sessions:
        overlap = "Tokyo/London"
    elif "Sydney" in sessions and "Tokyo" in sessions:
        overlap = "Sydney/Tokyo"
    
    primary_session = overlap if overlap else (sessions[0] if sessions else "Off-Hours")
    
    return {
        "primary_session": primary_session,
        "active_sessions": sessions,
        "overlap": overlap,
        "display_name": primary_session,
        "utc_hour": hour,
        "characteristics": {
            "high_liquidity": overlap is not None,
            "expected_spread": "tight" if overlap else "normal",
            "volatility": "high" if overlap == "London/NY" else "moderate" if overlap else "low",
        }
    }


@app.get("/research")
async def research_dashboard():
    """Serve the research-grade dashboard."""
    research_file = FRONTEND_DIR / "research_dashboard.html"
    if research_file.exists():
        return FileResponse(str(research_file))
    return {"error": "Research dashboard not found"}


@app.get("/dashboard")
async def unified_dashboard():
    """Serve the unified dashboard (v2.0)."""
    dashboard_file = FRONTEND_DIR / "unified_dashboard.html"
    if dashboard_file.exists():
        return FileResponse(str(dashboard_file))
    return {"error": "Unified dashboard not found"}


@app.get("/overview")
async def market_overview_dashboard():
    """Serve the institutional market overview dashboard."""
    overview_file = FRONTEND_DIR / "market_overview.html"
    if overview_file.exists():
        return FileResponse(str(overview_file))
    return {"error": "Market overview dashboard not found"}


@app.get("/basis")
async def basis_dashboard():
    """Serve the Phase-3 onshore/offshore USD/INR basis dashboard."""
    f = FRONTEND_DIR / "basis_dashboard.html"
    if f.exists():
        return FileResponse(str(f))
    return {"error": "basis_dashboard.html not found"}


@app.websocket("/ws/basis")
async def websocket_basis(websocket: WebSocket):
    """Live onshore/offshore USD/INR basis stream (snapshots + dislocation events)."""
    await websocket_basis_endpoint(websocket)


@app.get("/sources/{symbol}/comparison")
async def get_source_comparison(symbol: str):
    """
    Get price comparison across sources for a symbol.
    
    Shows current prices from each source and the differences.
    
    Args:
        symbol: Currency pair symbol
    """
    symbol = symbol.upper()
    
    if symbol not in DEFAULT_SYMBOLS:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not available")
    
    comparison = ws_manager.get_source_comparison(symbol)
    if not comparison:
        return {
            "symbol": symbol,
            "error": "Insufficient data for comparison",
        }
    
    return comparison


# ============================================================================
# V2.0 STATE & EXECUTION ENDPOINTS
# ============================================================================

@app.get("/v2/state")
async def get_full_state():
    """
    Get full system state from StateStore.
    
    Returns comprehensive state including:
    - Symbol states (sources, batches, arbitrage)
    - Session information
    - Metrics and rankings
    """
    if not state_store:
        return {"error": "StateStore not initialized"}
    
    state = state_store.get_full_state()
    
    # Add state machine states
    if arbitrage_state_machine:
        state["state_machine"] = arbitrage_state_machine.get_all_states()
    
    # Add metrics rankings
    if time_weighted_metrics:
        state["opportunity_rankings"] = time_weighted_metrics.get_opportunity_rankings(limit=10)
    
    return state


@app.get("/v2/state/{symbol}")
async def get_symbol_state(symbol: str):
    """Get state for a specific symbol."""
    symbol = symbol.upper()
    
    if not state_store:
        return {"error": "StateStore not initialized"}
    
    state = state_store.get_symbol_state(symbol)
    if not state:
        return {"symbol": symbol, "error": "No state available"}
    
    result = state.to_dict()
    
    # Add state machine state
    if arbitrage_state_machine:
        sm_state = arbitrage_state_machine.get_state(symbol)
        result["state_machine"] = sm_state
    
    # Add metrics
    if time_weighted_metrics:
        metrics = time_weighted_metrics.get_metrics(symbol)
        if metrics:
            result["metrics"] = metrics.to_dict()
    
    return result


@app.get("/v2/execution/stats")
async def get_execution_stats():
    """Get execution engine statistics."""
    if not execution_engine:
        return {"error": "ExecutionEngine not initialized"}
    
    return {
        "stats": execution_engine.get_stats(),
        "config": execution_engine.get_config(),
    }


# ============================================================================
# CONTROL STATE ENDPOINTS - Centralized Configuration Management
# ============================================================================

@app.get("/control/state")
async def get_current_control_state():
    """
    Get the current centralized control state.
    
    Returns all runtime configuration that affects detection, filtering,
    and display of arbitrage opportunities. Components consume this state
    for runtime behavior.
    
    Returns:
        Full ControlState with:
        - active_symbols: List of symbols being monitored
        - detection: Arbitrage detection thresholds
        - persistence: Opportunity persistence thresholds  
        - execution: Execution feasibility filter settings
        - session: Session-based filter settings
        - display: UI display preferences
        - version: Config version for cache invalidation
        - last_updated_ts: Timestamp of last update
    """
    manager = get_control_state_manager()
    return {
        "state": manager.get_state_dict(),
        "status": "active",
    }


@app.post("/control/update")
async def update_control_state(updates: dict):
    """
    Update the centralized control state.
    
    Accepts partial updates - only include the sections/fields you want to change.
    All updates are validated and atomic. Observers are notified of changes.
    
    Args:
        updates: Dictionary with sections to update. Example:
            {
                "detection": {"min_profit_pips": 1.5},
                "persistence": {"ephemeral_max_ms": 100},
                "execution": {"enabled": true, "viable_threshold": 75},
                "display": {"show_ephemeral": false}
            }
    
    Returns:
        Dictionary of actual changes made and new version
    """
    manager = get_control_state_manager()
    
    try:
        changes = manager.update(updates, updated_by="api")
        new_state = manager.get_state_dict()
        
        return {
            "status": "updated",
            "changes": changes,
            "new_version": new_state["version"],
            "timestamp": new_state["last_updated_ts"],
        }
    except ValueError as e:
        return {"error": str(e), "status": "validation_failed"}
    except Exception as e:
        logger.error(f"Control state update error: {e}")
        return {"error": "Internal error", "status": "failed"}


@app.post("/control/reset")
async def reset_control_state():
    """
    Reset control state to defaults.
    
    Use this to restore all detection, persistence, execution, and display
    settings to their default values.
    
    Returns:
        Confirmation of reset with new version
    """
    manager = get_control_state_manager()
    changes = manager.reset_to_defaults(updated_by="api")
    new_state = manager.get_state_dict()
    
    return {
        "status": "reset",
        "new_version": new_state["version"],
        "message": "Control state reset to defaults",
    }


@app.get("/control/history")
async def get_control_state_history(limit: int = 20):
    """
    Get recent control state change history.
    
    Useful for auditing configuration changes and debugging.
    
    Args:
        limit: Maximum number of history entries to return
    
    Returns:
        List of recent changes with timestamps and what changed
    """
    manager = get_control_state_manager()
    history = manager.get_change_history(limit=limit)
    
    return {
        "history": history,
        "count": len(history),
    }


@app.get("/control/thresholds")
async def get_detection_thresholds():
    """
    Get just the detection and persistence thresholds.
    
    Convenience endpoint for components that only need threshold values.
    """
    state = get_control_state()
    return {
        "detection": state.detection.to_dict(),
        "persistence": state.persistence.to_dict(),
    }


@app.get("/v2/execution/history")
async def get_execution_history(limit: int = 50):
    """Get execution history."""
    if not execution_engine:
        return {"error": "ExecutionEngine not initialized"}
    
    return {
        "executions": execution_engine.get_execution_history(limit=limit),
        "audit_log": execution_engine.get_audit_log(limit=limit),
    }


@app.get("/v2/execution/positions")
async def get_positions():
    """Get current positions from broker."""
    if not execution_engine:
        return {"error": "ExecutionEngine not initialized"}
    
    return {
        "positions": await execution_engine.get_positions(),
        "account": await execution_engine.get_account_info(),
    }


@app.post("/v2/execution/pause")
async def pause_execution():
    """Pause the execution engine."""
    if not execution_engine:
        return {"error": "ExecutionEngine not initialized"}
    
    execution_engine.pause()
    return {"status": "paused", "state": execution_engine.state.value}


@app.post("/v2/execution/resume")
async def resume_execution():
    """Resume the execution engine."""
    if not execution_engine:
        return {"error": "ExecutionEngine not initialized"}
    
    execution_engine.resume()
    return {"status": "resumed", "state": execution_engine.state.value}


@app.get("/v2/context")
async def get_context_analysis():
    """Get current semantic context analysis."""
    if not semantic_context_engine:
        return {"error": "SemanticContextEngine not initialized"}
    
    # Get current session
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    hour = now.hour
    
    if 7 <= hour < 16:
        session = SessionContext.LONDON
    elif 12 <= hour < 21:
        session = SessionContext.NEW_YORK
    elif 23 <= hour or hour < 8:
        session = SessionContext.TOKYO
    elif 21 <= hour or hour < 6:
        session = SessionContext.SYDNEY
    else:
        session = SessionContext.OFF_HOURS
    
    # Use appropriate symbol based on mode
    analysis_symbol = RESEARCH_SYMBOL if is_synthetic_mode() else "EURUSD"
    
    # Perform analysis
    context_input = ContextInput(
        symbol=analysis_symbol,
        session=session,
        volatility_samples=[],  # Would be populated from state store
        spread_samples=[],
        text_signals=[],
    )
    
    result = semantic_context_engine.analyze(context_input)
    
    return {
        "result": result.to_dict(),
        "engine_stats": semantic_context_engine.get_stats(),
        "mode": DATA_MODE.value,
        "symbol": analysis_symbol,
    }


@app.get("/v2/context/config")
async def get_context_config():
    """Get semantic context engine configuration."""
    if not semantic_context_engine:
        return {"error": "SemanticContextEngine not initialized"}
    
    return semantic_context_engine.get_config()


@app.get("/v2/metrics")
async def get_all_metrics():
    """Get time-weighted metrics for all symbols."""
    if not time_weighted_metrics:
        return {"error": "TimeWeightedMetrics not initialized"}
    
    return {
        "rankings": time_weighted_metrics.get_opportunity_rankings(limit=20),
        "symbols": {
            symbol: time_weighted_metrics.get_metrics(symbol).to_dict()
            if time_weighted_metrics.get_metrics(symbol) else None
            for symbol in DEFAULT_SYMBOLS
        }
    }


@app.get("/v2/metrics/{symbol}")
async def get_symbol_metrics(symbol: str):
    """Get time-weighted metrics for a specific symbol."""
    symbol = symbol.upper()
    
    if not time_weighted_metrics:
        return {"error": "TimeWeightedMetrics not initialized"}
    
    metrics = time_weighted_metrics.get_metrics(symbol)
    if not metrics:
        return {"symbol": symbol, "error": "No metrics available"}
    
    return metrics.to_dict()


# ============================================================================
# ANALYTICS ENDPOINTS — Experimental Research Output
# ============================================================================

@app.get("/analytics/summary")
async def get_analytics_summary():
    """
    Get experimental analytics summary.
    
    Returns aggregate research metrics including:
    - Total opportunities detected
    - Average opportunity duration
    - Persistence class distribution (% ephemeral vs flickering vs persistent)
    - Session-wise opportunity counts and average spreads
    - System uptime
    """
    if not metrics_collector:
        return {"error": "MetricsCollector not initialized"}
    
    summary = metrics_collector.get_summary()
    
    # Enrich with engine stats if available
    if multi_source_streamer:
        summary["engine_stats"] = multi_source_streamer.get_stats()
    
    return summary


@app.post("/analytics/reset")
async def reset_analytics():
    """
    Reset analytics metrics for a new research session.
    
    Clears all accumulated opportunity counts, session metrics,
    and persistence distributions. Does not affect the data pipeline.
    """
    if not metrics_collector:
        return {"error": "MetricsCollector not initialized"}
    
    metrics_collector.reset()
    return {"status": "reset", "message": "Analytics metrics cleared"}

# ============================================================================
# SEMANTIC INTELLIGENCE ENDPOINTS
# ============================================================================

def _get_current_session_context():
    """Helper to determine current SessionContext from UTC hour."""
    from datetime import datetime, timezone
    hour = datetime.now(timezone.utc).hour

    if 0 <= hour < 7:
        return SessionContext.TOKYO
    elif 7 <= hour < 12:
        return SessionContext.LONDON
    elif 12 <= hour < 16:
        # London/NY overlap — use London for semantic
        return SessionContext.LONDON
    elif 16 <= hour < 21:
        return SessionContext.NEW_YORK
    else:
        return SessionContext.SYDNEY


def _run_semantic_analysis(symbol: str):
    """Run semantic analysis for a symbol, returns dict."""
    if not semantic_context_engine:
        return {"error": "SemanticContextEngine not initialized"}

    session = _get_current_session_context()

    context_input = ContextInput(
        symbol=symbol,
        session=session,
        volatility_samples=[],
        spread_samples=[],
        text_signals=[],
    )

    result = semantic_context_engine.analyze(context_input)
    result_dict = result.to_dict()

    # Derive human-readable market regime and session bias
    risk = result.risk_score
    if risk < 0.3:
        market_regime = "low_risk"
        risk_label = "Low"
    elif risk < 0.6:
        market_regime = "moderate_risk"
        risk_label = "Moderate"
    else:
        market_regime = "high_risk"
        risk_label = "High"

    session_bias = session.value if hasattr(session, 'value') else str(session)

    return {
        "symbol": symbol,
        "market_regime": market_regime,
        "session_bias": session_bias,
        "risk_level": risk_label,
        "risk_score": result_dict["risk_score"],
        "confidence_adjustment": result_dict["confidence_adjustment"],
        "confidence_adjustment_pct": round(result_dict["confidence_adjustment"] * 100, 1),
        "volatility_regime": result_dict["volatility_regime"],
        "spread_regime": result_dict["spread_regime"],
        "explanation": result_dict["reasoning"],
        "components": {
            "session_risk": result_dict["session_risk"],
            "volatility_risk": result_dict["volatility_risk"],
            "spread_risk": result_dict["spread_risk"],
            "sentiment_risk": result_dict["sentiment_risk"],
        },
        "computed_at": result_dict["computed_at"],
    }


@app.get("/semantic/{symbol}")
async def get_semantic_analysis(symbol: str):
    """
    Get semantic intelligence analysis for a symbol.

    Returns market regime, session bias, risk level, confidence adjustment,
    and AI explanation for the current market context.

    Args:
        symbol: Currency pair (e.g., "USDINR")
    """
    symbol = symbol.upper()
    if symbol not in DEFAULT_SYMBOLS:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not available")

    return _run_semantic_analysis(symbol)


@app.websocket("/ws/semantic/{symbol}")
async def websocket_semantic(websocket: WebSocket, symbol: str):
    """
    WebSocket endpoint for real-time semantic intelligence streaming.

    Streams market regime, risk level, confidence adjustments, and
    AI explanations every 2 seconds.

    Example: ws://localhost:8000/ws/semantic/USDINR
    """
    symbol = symbol.upper()
    if symbol not in DEFAULT_SYMBOLS:
        await websocket.close(code=1008, reason=f"Symbol {symbol} not available")
        return

    await websocket.accept()
    channel = f"semantic:{symbol}"

    try:
        ws_manager.connections[channel].add(websocket)
        logger.info(f"Semantic client connected for {symbol}")

        while True:
            try:
                # Run analysis
                analysis = _run_semantic_analysis(symbol)
                analysis["type"] = "semantic_update"

                await websocket.send_json(analysis)

                # Wait 2 seconds before the next evaluation
                await asyncio.sleep(2.0)

            except WebSocketDisconnect:
                break
            except Exception as e:
                logger.warning(f"Semantic WS error for {symbol}: {e}")
                break

    except WebSocketDisconnect:
        logger.info(f"Semantic client disconnected for {symbol}")
    finally:
        ws_manager.disconnect(websocket, channel)


# ============================================================================
# ARBITRAGE WEBSOCKET ENDPOINTS
# ============================================================================

@app.websocket("/ws/arbitrage")
async def websocket_arbitrage_all(websocket: WebSocket):
    """
    WebSocket endpoint for all arbitrage opportunities.
    
    Example: ws://localhost:8000/ws/arbitrage
    
    Streams all detected arbitrage opportunities in real-time.
    """
    if not ENABLE_ARBITRAGE_DETECTION:
        await websocket.close(code=1008, reason="Arbitrage detection not enabled")
        return
    
    await websocket_arbitrage_endpoint(websocket, symbol=None)


@app.websocket("/ws/arbitrage/{symbol}")
async def websocket_arbitrage_symbol(websocket: WebSocket, symbol: str):
    """
    WebSocket endpoint for symbol-specific arbitrage opportunities.
    
    Example: ws://localhost:8000/ws/arbitrage/EURUSD
    
    Streams arbitrage opportunities for a specific symbol.
    """
    if not ENABLE_ARBITRAGE_DETECTION:
        await websocket.close(code=1008, reason="Arbitrage detection not enabled")
        return
    
    symbol = symbol.upper()
    if symbol not in DEFAULT_SYMBOLS:
        await websocket.close(code=1008, reason=f"Symbol {symbol} not available")
        return
    
    await websocket_arbitrage_endpoint(websocket, symbol=symbol)


@app.websocket("/ws/sources/{symbol}")
async def websocket_sources(websocket: WebSocket, symbol: str):
    """
    WebSocket endpoint for source price comparison streaming.
    
    Example: ws://localhost:8000/ws/sources/EURUSD
    
    Streams real-time price comparisons across data sources.
    """
    if not ENABLE_ARBITRAGE_DETECTION:
        await websocket.close(code=1008, reason="Arbitrage detection not enabled")
        return
    
    symbol = symbol.upper()
    if symbol not in DEFAULT_SYMBOLS:
        await websocket.close(code=1008, reason=f"Symbol {symbol} not available")
        return
    
    await websocket_sources_endpoint(websocket, symbol)


@app.websocket("/ws/dashboard")
async def websocket_dashboard(websocket: WebSocket):
    """
    WebSocket endpoint for the unified dashboard.
    
    Streams comprehensive state updates including:
    - Tick data from all sources
    - Arbitrage opportunities with state machine status
    - Execution events
    - Context risk updates
    """
    await websocket.accept()
    channel = "dashboard:unified"
    
    try:
        ws_manager.connections[channel].add(websocket)
        logger.info(f"Dashboard client connected to {channel}")
        
        # Send initial state
        initial_state = {
            "type": "state_update",
            "states": {},
        }
        if arbitrage_state_machine:
            initial_state["states"] = arbitrage_state_machine.get_all_states()
        if state_store:
            initial_state["store"] = state_store.get_full_state()
        await websocket.send_json(initial_state)
        
        # Keep connection alive and handle client messages
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive_text(), 
                    timeout=30.0
                )
                
                # Handle client commands
                import json
                try:
                    data = json.loads(message)
                    msg_type = data.get("type")
                    
                    if msg_type == "update_settings":
                        # Update execution/state machine settings
                        settings = data.get("settings", {})
                        if execution_engine:
                            execution_engine.update_config(**settings)
                        await websocket.send_json({
                            "type": "settings_updated",
                            "settings": settings
                        })
                    
                    elif msg_type == "start_execution":
                        if execution_engine:
                            execution_engine.resume()
                            await websocket.send_json({
                                "type": "execution_status",
                                "state": execution_engine.state.value
                            })
                    
                    elif msg_type == "stop_execution":
                        if execution_engine:
                            execution_engine.pause()
                            await websocket.send_json({
                                "type": "execution_status",
                                "state": execution_engine.state.value
                            })
                    
                    elif msg_type == "ping":
                        await websocket.send_json({"type": "pong"})
                        
                except json.JSONDecodeError:
                    pass
                    
            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({"type": "heartbeat"})
                
    except WebSocketDisconnect:
        logger.info(f"Dashboard client disconnected from {channel}")
    finally:
        ws_manager.disconnect(websocket, channel)


if __name__ == "__main__":
    import os
    import sys

    os.execv(
        sys.executable,
        [
            sys.executable,
            "-m",
            "uvicorn",
            "backend.server.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            "8000",
            "--log-level",
            "info",
        ],
    )
