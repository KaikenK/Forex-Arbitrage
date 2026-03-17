# MetaTrader 5 Market Data Engine with Arbitrage Detection

Production-ready real-time market data streaming engine using MetaTrader 5, FastAPI, and WebSockets. Extended with multi-source arbitrage detection, state machine-based confirmation, execution engine, and a unified dashboard.

## 🚀 Quick Start

```bash
# 1. Activate virtual environment
.\venv\Scripts\Activate.ps1   # Windows PowerShell
# or: source venv/bin/activate  # Mac/Linux

# 2. Start the server
uvicorn backend.server.main:app --host 0.0.0.0 --port 8000 --reload

# 3. Open the unified dashboard
# http://localhost:8000/dashboard
```

**Prerequisites:** MetaTrader 5 must be running and logged in.

## Features

### Core Features
- ✅ **Live MT5 Tick Stream** - Real-time tick data from MetaTrader 5
- ✅ **Live MT5 Candle Stream** - Custom intervals: 100ms, 500ms, 1s, 5s, 15s, 1m, 5m, 15m, 1h
- ✅ **FastAPI Backend** - Modern async Python web framework
- ✅ **WebSocket Endpoints** - Real-time data streaming to frontend
- ✅ **Automatic MT5 Initialization** - Connects on server startup
- ✅ **Auto-Reconnection** - Handles MT5 disconnections gracefully

### Arbitrage Detection Engine
- ✅ **Multi-Source Streaming** - MT5, Synthetic, REST API, and Playback data sources
- ✅ **Cross-Source Arbitrage** - Detect price discrepancies across data feeds
- ✅ **Time-Aligned Windows** - 20ms micro-batching for fair comparison
- ✅ **Opportunity Ranking** - Composite scoring with confidence levels
- ✅ **Arbitrage Diagnostics** - Explains WHY opportunities exist or don't

### v2.0 State Management & Execution
- ✅ **Centralized StateStore** - Single source of truth for all system state
- ✅ **ArbitrageStateMachine** - Confirmation-based detection (IDLE → CANDIDATE → CONFIRMED → COOLING)
- ✅ **Time-Weighted Metrics** - EMA-based profit tracking, stability scores, decay rates
- ✅ **ExecutionEngine** - Pluggable broker interface with paper trading
- ✅ **SemanticContextEngine** - NLP-based context risk assessment
- ✅ **Unified Dashboard** - Three-mode interface (Monitor, Research, Execution)

### Session Management
- ✅ **Session Detection** - Auto-detect Tokyo, London, New York, Sydney sessions
- ✅ **Overlap Analysis** - Track high-liquidity periods (London/NY overlap)
- ✅ **Session Statistics** - Per-session spread, latency, and arbitrage frequency

## Project Structure

```
.
├── backend/
│   ├── core/
│   │   ├── mt5_client.py              # MT5 connection & data access
│   │   ├── tick_streamer.py           # Infinite tick streaming
│   │   ├── bar_aggregator.py          # Tick-to-candle aggregation
│   │   ├── multi_source_streamer.py   # Multi-source arbitrage streaming
│   │   ├── interfaces/
│   │   │   ├── data_source.py         # Abstract data source interface
│   │   │   └── normalized_tick.py     # Normalized tick format
│   │   ├── data_sources/
│   │   │   ├── mt5_data_source.py     # MT5 data source plugin
│   │   │   └── synthetic_data_source.py # Synthetic/simulated feed
│   │   ├── arbitrage/
│   │   │   ├── arbitrage_engine.py    # Core arbitrage detection
│   │   │   ├── tick_aligner.py        # Time-aligned micro-batching
│   │   │   └── opportunity_ranker.py  # Opportunity scoring
│   │   ├── state/                     # v2.0 State Management
│   │   │   ├── state_store.py         # Centralized state store
│   │   │   ├── arbitrage_state_machine.py # State machine
│   │   │   └── metrics.py             # EMA & stability metrics
│   │   ├── execution/                 # v2.0 Execution Engine
│   │   │   ├── execution_engine.py    # Central execution coordinator
│   │   │   └── brokers.py             # Paper, MT5, REST brokers
│   │   └── semantic/                  # v2.0 Context Engine
│   │       └── context_engine.py      # NLP-based risk scoring
│   └── server/
│       ├── main.py                    # FastAPI app & startup
│       └── websocket_routes.py        # WebSocket endpoints
├── frontend/
│   ├── index.html                     # Main dashboard
│   ├── unified_dashboard.html         # v2.0 Unified dashboard
│   ├── arbitrage_dashboard.html       # Arbitrage monitoring
│   ├── research_dashboard.html        # Research-grade diagnostics
│   └── marketDataClient.js            # WebSocket client library
└── requirements.txt
```

## Installation

### Prerequisites

1. **MetaTrader 5** must be installed and running
2. **Python 3.12+**
3. **MT5 Account** (demo or live)

### Setup

1. **Create virtual environment:**
   ```bash
   python -m venv venv
   venv\Scripts\activate  # Windows
   # or
   source venv/bin/activate  # Mac/Linux
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure MT5 (optional):**
   
   Edit `backend/server/main.py` to set MT5 connection details:
   ```python
   MT5_PATH = None  # Auto-detect, or set path like "C:/Program Files/MetaTrader 5/terminal64.exe"
   MT5_LOGIN = None  # Your account number
   MT5_PASSWORD = None  # Your password
   MT5_SERVER = None  # Server name
   ```

4. **Start MetaTrader 5** (must be running before starting server)

## Running the Server

```bash
# Activate virtual environment first
venv\Scripts\activate  # Windows

# Run server
python -m backend.server.main

# Or using uvicorn directly
uvicorn backend.server.main:app --host 0.0.0.0 --port 8000 --reload
```

Server will start on: **http://localhost:8000**

## API Endpoints

### REST Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Main dashboard (serves index.html) |
| `GET /health` | Health check with detailed status |
| `GET /symbols` | List available symbols |
| `GET /intervals` | List available intervals |
| `GET /snapshot/{symbol}` | Current market snapshot |
| `GET /dashboard` | **v2.0 Unified Dashboard** |
| `GET /research` | Research-grade dashboard |
| `GET /session` | Current trading session info |
| `GET /sources` | List all data sources |
| `GET /sources/{symbol}/comparison` | Cross-source price comparison |
| `GET /arbitrage/stats` | Arbitrage detection statistics |
| `GET /arbitrage/recent` | Recent arbitrage opportunities |

### v2.0 State & Execution Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /v2/state` | Full system state from StateStore |
| `GET /v2/state/{symbol}` | Symbol-specific state |
| `GET /v2/execution/stats` | Execution engine statistics |
| `GET /v2/execution/history` | Execution audit log |
| `GET /v2/execution/positions` | Current broker positions |
| `POST /v2/execution/pause` | Pause paper trading |
| `POST /v2/execution/resume` | Resume paper trading |
| `GET /v2/context` | Semantic context analysis |
| `GET /v2/context/config` | Context engine configuration |
| `GET /v2/metrics` | Time-weighted metrics (all symbols) |
| `GET /v2/metrics/{symbol}` | Symbol-specific EMA metrics |

### WebSocket Endpoints

#### Tick Streaming

```
ws://localhost:8000/ws/ticks/{symbol}
```

**Example:**
```
ws://localhost:8000/ws/ticks/EURUSD
```

**Message Format (line-delimited JSON):**
```json
{"time": 1234567890123, "bid": 1.08523, "ask": 1.08525, "last": 1.08524, "volume": 100}
```

#### Candle Streaming

```
ws://localhost:8000/ws/candles/{symbol}/{interval}
```

**Example:**
```
ws://localhost:8000/ws/candles/EURUSD/1s
ws://localhost:8000/ws/candles/EURUSD/5s
ws://localhost:8000/ws/candles/EURUSD/1m
```

**Intervals:** `100ms`, `500ms`, `1s`, `5s`, `15s`, `1m`, `5m`, `15m`, `1h`

**Message Format (line-delimited JSON):**
```json
{"time": 1234567890000, "open": 1.08520, "high": 1.08530, "low": 1.08510, "close": 1.08525, "volume": 150}
```

#### Arbitrage Streaming

```
ws://localhost:8000/ws/arbitrage
ws://localhost:8000/ws/arbitrage/{symbol}
```

**Example:**
```
ws://localhost:8000/ws/arbitrage
ws://localhost:8000/ws/arbitrage/EURUSD
```

**Message Format:**
```json
{
  "opportunity": {
    "type": "cross_source",
    "symbol": "EURUSD",
    "buy_source": "synthetic_bloomberg",
    "sell_source": "mt5_primary",
    "estimated_profit_pips": 0.35,
    "confidence_score": 0.85
  },
  "composite_score": 72.5
}
```

#### Source Comparison Streaming

```
ws://localhost:8000/ws/sources/{symbol}
```

**Example:**
```
ws://localhost:8000/ws/sources/EURUSD
```

#### v2.0 Unified Dashboard Stream

```
ws://localhost:8000/ws/dashboard
```

Streams comprehensive state updates including ticks, arbitrage opportunities, execution events, and context risk.

**Client Commands:**
```json
{"type": "update_settings", "settings": {"min_confidence": 0.7}}
{"type": "start_execution"}
{"type": "stop_execution"}
{"type": "ping"}
```

## v2.0 Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Unified Dashboard                             │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────────────────┐ │
│  │Market Monitor│ │   Research   │ │ Strategy & Execution     │ │
│  └──────────────┘ └──────────────┘ └──────────────────────────┘ │
└───────────────────────────┬─────────────────────────────────────┘
                            │ WebSocket /ws/dashboard
┌───────────────────────────▼─────────────────────────────────────┐
│                       FastAPI Server                             │
│  ┌─────────────┐  ┌─────────────┐  ┌────────────────────────┐   │
│  │ StateStore  │◄─│ StateMachine │◄─│ MultiSourceStreamer    │   │
│  │ (central)   │  │ (IDLE→CONF) │  │ (MT5 + Synthetic)      │   │
│  └──────┬──────┘  └──────┬──────┘  └────────────────────────┘   │
│         │                │                                       │
│  ┌──────▼──────┐  ┌──────▼──────┐  ┌────────────────────────┐   │
│  │ Metrics     │  │ Execution   │  │ SemanticContextEngine  │   │
│  │ (EMA,Stab)  │  │ (PaperBkr)  │  │ (risk adjustment)      │   │
│  └─────────────┘  └─────────────┘  └────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### State Machine Flow

```
IDLE ──(profit detected)──► CANDIDATE ──(confirmed 100ms)──► CONFIRMED
  ▲                              │                              │
  │                              │                              │
  └──────(cooldown 500ms)────────┴────────(execute)─────────────┘
                                 │
                                 ▼
                            INVALIDATED ──(timeout)──► IDLE
```

### Key Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `confirmation_window_ms` | 100 | Time to confirm opportunity |
| `cooldown_period_ms` | 500 | Cooldown after execution |
| `min_profit_pips` | 0.1 | Minimum profit to detect |
| `min_confidence` | 0.7 | Minimum confidence for execution |
| `min_stability` | 0.5 | Minimum stability for execution |

## Frontend Integration (Next.js)

### JavaScript/TypeScript Example

```javascript
// Connect to tick stream
const ws = new WebSocket('ws://localhost:8000/ws/ticks/EURUSD');

ws.onmessage = (event) => {
  // Parse line-delimited JSON
  const lines = event.data.split('\n').filter(line => line.trim());
  
  for (const line of lines) {
    const tick = JSON.parse(line);
    console.log('Tick:', tick);
    // Update your chart/UI
  }
};

ws.onerror = (error) => {
  console.error('WebSocket error:', error);
};

ws.onclose = () => {
  console.log('WebSocket closed');
};
```

### React Hook Example

```typescript
import { useEffect, useState } from 'react';

function useMT5Ticks(symbol: string) {
  const [tick, setTick] = useState(null);
  
  useEffect(() => {
    const ws = new WebSocket(`ws://localhost:8000/ws/ticks/${symbol}`);
    
    ws.onmessage = (event) => {
      const lines = event.data.split('\n').filter(line => line.trim());
      if (lines.length > 0) {
        const latestTick = JSON.parse(lines[lines.length - 1]);
        setTick(latestTick);
      }
    };
    
    return () => ws.close();
  }, [symbol]);
  
  return tick;
}
```

### Candle Stream Example

```javascript
// Connect to candle stream
const ws = new WebSocket('ws://localhost:8000/ws/candles/EURUSD/1s');

ws.onmessage = (event) => {
  const lines = event.data.split('\n').filter(line => line.trim());
  
  for (const line of lines) {
    const candle = JSON.parse(line);
    console.log('Candle:', candle);
    // Update your chart
    // candle.time, candle.open, candle.high, candle.low, candle.close
  }
};
```

## Comparing MT5 vs TradingView

To compare MT5 data with TradingView:

1. **Connect to MT5 stream:**
   ```javascript
   const mt5Ws = new WebSocket('ws://localhost:8000/ws/ticks/EURUSD');
   ```

2. **Connect to TradingView** (using your TradingView integration)

3. **Compare in real-time:**
   ```javascript
   let mt5Price = null;
   let tvPrice = null;
   
   mt5Ws.onmessage = (event) => {
     const tick = JSON.parse(event.data);
     mt5Price = (tick.bid + tick.ask) / 2;
     comparePrices();
   };
   
   // Your TradingView price handler
   function onTradingViewPrice(price) {
     tvPrice = price;
     comparePrices();
   }
   
   function comparePrices() {
     if (mt5Price && tvPrice) {
       const diff = Math.abs(mt5Price - tvPrice);
       console.log(`MT5: ${mt5Price}, TV: ${tvPrice}, Diff: ${diff}`);
       
       if (diff > 0.00005) {
         console.warn('Price discrepancy detected!');
       }
     }
   }
   ```

## Configuration

Edit `backend/server/main.py`:

```python
# Default symbols to stream
DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY"]

# Default intervals for candles (including micro-candles)
DEFAULT_INTERVALS = ["100ms", "500ms", "1s", "5s", "15s", "1m"]

# Arbitrage Detection Settings
ENABLE_ARBITRAGE_DETECTION = True
ENABLE_SYNTHETIC_SOURCE = True
ALIGNMENT_WINDOW_MS = 20          # Time alignment window
MIN_PROFIT_PIPS = 0.1             # Minimum profit threshold
MIN_CONFIDENCE = 0.5              # Minimum confidence score
SYNTHETIC_LATENCY_MS = 100.0      # Simulated network latency
SYNTHETIC_NOISE_PIPS = 0.5        # Price noise for arbitrage testing
```

## Architecture

### Data Flow

```
                    ┌─────────────────┐
                    │   MT5 Terminal  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
    ┌─────────────────┐ ┌──────────┐ ┌──────────────┐
    │  MT5DataSource  │ │ Synthetic│ │ RESTDataSource│
    │   (Primary)     │ │  Source  │ │  (INR/APIs)   │
    └────────┬────────┘ └────┬─────┘ └───────┬───────┘
             │               │               │
             └───────────────┼───────────────┘
                             ▼
                ┌────────────────────────┐
                │  MultiSourceStreamer   │
                │  ┌──────────────────┐  │
                │  │   TickAligner    │  │
                │  │  (20ms windows)  │  │
                │  └────────┬─────────┘  │
                │           ▼            │
                │  ┌──────────────────┐  │
                │  │ ArbitrageEngine  │  │
                │  └────────┬─────────┘  │
                │           ▼            │
                │  ┌──────────────────┐  │
                │  │OpportunityRanker │  │
                │  └──────────────────┘  │
                └───────────┬────────────┘
                            ▼
                ┌───────────────────────┐
                │   WebSocketManager    │
                ├───────────────────────┤
                │ /ws/ticks/{symbol}    │
                │ /ws/candles/{s}/{i}   │
                │ /ws/arbitrage         │
                │ /ws/sources/{symbol}  │
                └───────────────────────┘
```

### Components

1. **MT5Client**: Handles MT5 connection, tick/bar retrieval, auto-reconnect
2. **TickStreamer**: Infinite async loop streaming ticks from MT5
3. **BarAggregator**: Converts ticks to candles with configurable intervals
4. **MultiSourceStreamer**: Coordinates multiple data sources for arbitrage
5. **TickAligner**: Time-aligns ticks in 20ms micro-batches
6. **ArbitrageEngine**: Detects cross-source arbitrage opportunities
7. **OpportunityRanker**: Scores and ranks opportunities by profitability
8. **SessionManager**: Detects and tracks FX trading sessions
9. **DiagnosticsEngine**: Explains why arbitrage does/doesn't exist
10. **WebSocketManager**: Manages client connections and broadcasts
11. **FastAPI Server**: REST API and WebSocket endpoints

### Trading Sessions

The system automatically detects and tracks FX trading sessions:

| Session | UTC Hours | Characteristics |
|---------|-----------|-----------------|
| Sydney | 21:00-06:00 | Low volatility, wider spreads |
| Tokyo | 00:00-09:00 | JPY pairs active |
| London | 07:00-16:00 | High liquidity, EUR/GBP active |
| New York | 12:00-21:00 | USD pairs, high volume |
| **London/NY Overlap** | 12:00-16:00 | **Highest liquidity** |

## Troubleshooting

**MT5 Connection Failed:**
- Ensure MetaTrader 5 is running
- Check MT5 path in configuration
- Verify account credentials if using login/password

**No Data Received:**
- Check that symbols are in Market Watch
- Verify MT5 is connected to broker
- Check server logs for errors

**WebSocket Connection Issues:**
- Ensure server is running on port 8000
- Check CORS settings if connecting from browser
- Verify WebSocket URL format

**No Arbitrage Opportunities:**
- This is normal - opportunities are rare in efficient markets
- Check the Research Dashboard at `/research` for diagnostics
- The diagnostics panel explains WHY no opportunities exist
- Try increasing `SYNTHETIC_NOISE_PIPS` for testing

## Dashboards

### Main Dashboard (`/`)
Basic market data display with tick and candle streaming.

### Arbitrage Dashboard (`/static/arbitrage_dashboard.html`)
Real-time arbitrage opportunity monitoring.

### Research Dashboard (`/research`)
Professional, diagnostic-first UI designed for research:
- **Metrics Bar**: Session, sources, latency, spread, ticks, opportunities
- **Source Comparison**: Aligned prices with difference highlighting
- **Time Series**: Mini charts for spread, mid-diff, and latency
- **Diagnostics Panel**: Explains WHY opportunities exist or don't
- **Threshold Display**: Current detection parameters
- **Opportunity History**: Recent detections with type badges

## Adding Custom Data Sources

The modular architecture supports adding new data sources:

```python
from backend.core.interfaces.data_source import DataSourceInterface, DataSourceConfig

class MyCustomSource(DataSourceInterface):
    def __init__(self, config: DataSourceConfig):
        super().__init__(config)
        
    def connect(self) -> bool:
        # Connect to your data feed
        return True
        
    async def get_tick(self, symbol: str):
        # Return NormalizedTick from your feed
        pass
```

### Built-in Data Sources

| Source | Description | Use Case |
|--------|-------------|----------|
| `MT5DataSource` | MetaTrader 5 feed | Primary production feed |
| `SyntheticDataSource` | Simulated feed with noise | Testing arbitrage detection |
| `RESTDataSource` | HTTP API polling | External APIs, INR pairs |
| `PlaybackDataSource` | Recorded data replay | Backtesting, research |

## Storage Pipeline

To add database persistence, modify `backend/core/tick_streamer.py`:

```python
# In _stream_loop method, after getting tick:
# Save to database
await save_tick_to_db(symbol, tick)
```

Or in `bar_aggregator.py`:

```python
# In process_tick method, after completing bar:
# Save to database
await save_candle_to_db(symbol, interval, completed_bar)
```

## Playback Mode

For research and backtesting, use the PlaybackDataSource:

```python
from backend.core.data_sources.playback_data_source import (
    PlaybackDataSource, 
    PlaybackConfig,
    generate_sample_data
)

# Generate sample data for testing
generate_sample_data("data/sample_eurusd.csv", symbol="EURUSD", duration_seconds=300)

# Configure playback source
config = PlaybackConfig(
    file_path="data/sample_eurusd.csv",
    file_format="csv",
    speed_multiplier=1.0,  # 1.0 = real-time, 10.0 = 10x speed
    loop=True
)
```

## INR/USD Multi-Source Handling

Since MT5 retail brokers don't reliably provide USDINR, the system supports composite instruments:

```python
from backend.core.inr_handler import INRInstrumentHandler

handler = INRInstrumentHandler()
handler.add_source("rbi", priority=1, weight=0.5)
handler.add_source("reuters", priority=2, weight=0.3)
handler.add_source("bloomberg", priority=3, weight=0.2)

# Get composite view with divergence analysis
analysis = handler.get_composite_analysis()
```

## License

Production-ready code for commercial use.




