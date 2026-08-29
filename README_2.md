# FX Arbitrage Detection Research Engine (Arbex)

A comprehensive, real-time market data streaming and arbitrage detection research environment. 

Arbex is designed to simulate, detect, and analyze high-frequency FX arbitrage opportunities (Latency and Triangular) across multiple liquidity providers. It features a robust multi-source streaming architecture, a state machine-based execution coordinator, semantic context risk analysis, and a modern Next.js visualization dashboard.

**Current Mode:** The system is currently configured as a **Synthetic USD/INR Research Environment**. It generates highly realistic, session-aware tick data simulating Bloomberg and Reuters terminal feeds across global trading sessions to test the arbitrage engine without requiring live institutional data feeds.

---

## 🌟 Key Capabilities

### 1. Multi-Source Arbitrage Detection
- **Cross-Source (Latency) Arbitrage:** Detects micro-second price discrepancies between different simulated data providers (e.g., Bloomberg vs. Reuters).
- **Time-Aligned Micro-Batching:** Uses a 20ms alignment window (`TickAligner`) to ensure fair comparison between asynchronous data streams.
- **Opportunity Ranking:** Scores opportunities based on estimated profit, execution confidence, and latency risk.

### 2. v2.0 State & Execution Management
- **Centralized StateStore:** Single source of truth for tracking active arbitrage candidates.
- **Arbitrage State Machine:** Implements a rigorous lifecycle for opportunities: `IDLE` → `CANDIDATE` → `CONFIRMED` (persists for >100ms) → `COOLING`.
- **Execution Engine:** Gates all paper trades behind configurable risk limits (max exposure, daily loss limit, minimum stability scores).
- **Time-Weighted Metrics:** Calculates Exponential Moving Averages (EMA) of spreads and profits to determine market stability before execution.

### 3. Semantic Context Engine
- **Risk Assessment:** Modulates arbitrage confidence scores based on broader market contexts (Volatility Regimes, Spread Regimes, and Trading Sessions).
- *Note: In the current synthetic mode, this engine analyzes the generated regimes, but it is architected to ingest real NLP data (e.g., central bank policy text).*

### 4. Synthetic Market Microstructure
The core of the research mode (`config.py`) generates 6 distinct, highly-configurable feeds:
- **Providers:** Models **Bloomberg** (faster, smoother) vs. **Reuters** (slightly delayed, micro-noise).
- **Sessions:** Models **Tokyo** (primary price discovery, wider spreads), **London** (cross-border, moderate volatility), and **New York** (USD-driven re-pricing, high noise).
- **Injection:** Deterministically injects realistic spread anomalies and arbitrage opportunities at configurable rates (`arbitrage_injection_rate: 0.0025`).

---

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.12+
- Node.js 18+

### 2. Backend Setup
```bash
# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate  # On Windows

# Install dependencies
pip install -r requirements.txt

# Start the Core FastAPI Server
# This launches the synthetic feeds, arbitrage engine, and WebSockets
.\run_server.bat
# (Server runs on http://localhost:8000)

# In a separate terminal, start the Semantic Context Engine
.\run_semantic.bat
```

### 3. Frontend Setup (Next.js Dashboard)
```bash
cd arbex-web
npm install
npm run dev
# (Dashboard runs on http://localhost:3001 or 3000)
```

---

## 📂 Architecture & Project Structure

The project follows a clean, modular architecture separating data ingestion, arbitrage logic, state management, and execution.

```text
.
├── backend/
│   ├── config.py                      # Master config (DataMode, Session/Provider tuning)
│   ├── core/
│   │   ├── interfaces/                # Plugin interfaces
│   │   │   ├── data_source.py         # Abstract interface for ALL data feeds
│   │   │   └── normalized_tick.py     # Standardized tick structure
│   │   ├── data_sources/              # Concrete implementations
│   │   │   ├── synthetic_data_source.py # Synthetic market generator
│   │   │   ├── rest_data_source.py    # REST API polling (e.g., AlphaVantage)
│   │   │   ├── mt5_data_source.py     # MetaTrader 5 integration
│   │   │   └── playback_data_source.py# CSV historical replay
│   │   ├── arbitrage/                 # Detection Core
│   │   │   ├── arbitrage_engine.py    # Cross-source logic
│   │   │   ├── tick_aligner.py        # 20ms alignment windowing
│   │   │   └── opportunity_ranker.py  # Confidence scoring
│   │   ├── state/                     # v2.0 State Management
│   │   │   ├── state_store.py         # Global state registry
│   │   │   ├── arbitrage_state_machine.py # IDLE/CANDIDATE/CONFIRMED tracking
│   │   │   └── metrics.py             # EMA and stability calculations
│   │   ├── execution/                 # v2.0 Execution Core
│   │   │   ├── execution_engine.py    # Pre-trade risk checks
│   │   │   └── brokers.py             # PaperBroker implementation
│   │   ├── semantic/                  # Context & Risk Modulation
│   │   │   └── context_engine.py      # Regime classification
│   │   ├── multi_source_streamer.py   # Coordinates multiple active data sources
│   │   └── mt5_client.py              # Low-level MT5 wrapper
│   └── server/
│       ├── main.py                    # FastAPI application initialization
│       └── websocket_routes.py        # Real-time data broadcasting
├── arbex-web/                         # Next.js Trading Terminal
│   ├── src/components/                # UI components (Lightweight Charts, Framer Motion)
│   └── src/lib/store.ts               # Zustand global state
├── run_server.bat                     # Backend startup script
└── run_semantic.bat                   # Semantic engine startup script
```

---

## ⚙️ Configuration & Tuning

The entire synthetic research environment is controlled via `backend/config.py`. 

**To adjust how often arbitrage opportunities appear:**
Modify `SYNTHETIC_GENERATION_CONFIG` in `config.py`:
```python
arbitrage_injection_rate: float = 0.0025  # Increase to see more opportunities
tick_interval_ms: int = 50                # Update frequency (20 ticks/sec)
```

**To adjust detection strictness:**
Modify `ARBITRAGE_RESEARCH_CONFIG`:
```python
min_profit_pips: float = 3.0    # Minimum spread difference required to trigger
min_confidence: float = 0.3     # Minimum composite confidence score
alignment_window_ms: int = 200  # How long to wait to compare async feeds
```

---

## 📡 API Reference

### REST Endpoints
| Endpoint | Description |
|----------|-------------|
| `GET /v2/state` | Full system state dump (opportunities, metrics) |
| `GET /v2/execution/positions` | Current open paper trades |
| `GET /v2/context` | Semantic context regime analysis |
| `GET /sources/{symbol}/comparison`| Current spread comparisons across active sources |

### WebSocket Endpoints
| Endpoint | Usage | Format |
|----------|-------|--------|
| `ws://localhost:8000/ws/dashboard` | Unified v2.0 Dashboard Stream | JSON (Ticks, Opps, State) |
| `ws://localhost:8000/ws/ticks/{sym}` | Raw tick streaming | Line-delimited JSON |
| `ws://localhost:8000/ws/arbitrage` | Detected opportunities | JSON |

---

## 🖥️ The Arbitrage Dashboard (`arbex-web`)

The frontend is a Bloomberg-inspired, 3-column Next.js application tailored for high-density data visualization:

1. **Market Sessions (Left):** Real-time candlestick charts (`Lightweight Charts`) alongside live Bid/Ask strips showing the spread differences across Tokyo, London, and NY synthetic feeds.
2. **Arbitrage Feed (Center):** A streaming, animated feed of detected opportunities. It highlights whether an opportunity is just a "Candidate" (flickering) or "Confirmed" (persisted through the state machine).
3. **Execution & Context (Right):** The Level-2 aggregated order book, global ranking of best opportunities, and the Semantic Workbench showing the current regime classifications.

---

## 🧩 Extending the System

Arbex uses a plugin architecture. To integrate a real data provider (like OANDA, FXCM, or AlphaVantage), you only need to subclass `DataSourceInterface`:

```python
from backend.core.interfaces.data_source import DataSourceInterface, DataSourceConfig, RawTick

class MyNewBrokerSource(DataSourceInterface):
    def connect(self) -> bool:
        # Establish WebSocket or REST connection
        return True
        
    async def get_tick(self, symbol: str) -> RawTick:
        # Fetch, parse, and return standardized RawTick
        pass
```
Once created, register it in `MultiSourceStreamer` to immediately pit it against other feeds in the arbitrage engine.
