# Step-by-Step Guide to Run MT5 Market Data Engine

## Prerequisites Checklist

- [ ] **MetaTrader 5** installed and running (must be logged in)
- [ ] **Python 3.12+** installed
- [ ] **Virtual environment** (optional but recommended)

---

## Step 1: Ensure MetaTrader 5 is Running

1. **Open MetaTrader 5** on your computer
2. **Log in** to your account (demo or live)
3. **Keep MT5 open** - the server needs it running to fetch data
4. **Verify symbols are available** - Make sure EURUSD, GBPUSD, USDJPY are in your Market Watch

> ⚠️ **Important:** The server will attach to your existing MT5 session. You don't need to provide login credentials if MT5 is already logged in.

---

## Step 2: Set Up Python Environment (First Time Only)

### Option A: Using Virtual Environment (Recommended)

```bash
# Create virtual environment
python -m venv venv

# Activate it
# Windows:
venv\Scripts\activate.bat

# Mac/Linux:
source venv/bin/activate
```

### Option B: Use System Python (Not Recommended)

Skip this step if you're using system Python (not recommended for production).

---

## Step 3: Install Dependencies

```bash
# Make sure virtual environment is activated (you should see (venv) in your prompt)
pip install -r requirements.txt
```

This installs:
- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `MetaTrader5` - MT5 Python API
- `pydantic` - Data validation
- `python-dotenv` - Environment variables

---

## Step 4: Run the Server

> **Start the semantic microservice first.** It now lives in this repo at
> `services/semantic/` (formerly the sibling `news_stream` repo). It hosts FinBERT,
> the reaction model and the RAG evidence retriever, and serves `/v1/feed`,
> `/v1/analyze`, `/v1/sentiment/live`, `/v1/health` on **port 9000**. The backend
> reaches it only over HTTP via `SEMANTIC_SERVICE_URL`. See
> `services/semantic/README.md` for the contract, env vars and tests.
>
> ```bash
> run_semantic_service.bat           # Windows (or: services/semantic/run.sh)
> # first time: pip install -r services/semantic/requirements.txt
> #   (+ requirements-ml.txt for --use-finbert / --use-sentence-transformer)
> ```
>
> Without it, the Sentiment Intelligence panel still renders from the mirrored
> runtime cache in `sentiment_assets/runtime/` (and direct RSS headlines, see
> `backend/core/news_fallback.py`); the status chip reads "service degraded" —
> nothing blanks, but no model-scored articles arrive.
>
> **Start order:** semantic service (:9000) → `run_server.bat` (:8000) →
> `run_semantic.bat` (Redis opportunity scorer) → dashboard. Note the two names:
> `run_semantic_service.bat` is the news NLP microservice; `run_semantic.bat` is
> the scorer inside the backend that *consumes* its window bias.

### Live semantic stack (live exchange feeds + recorder)

Two processes, in this order. Neither needs MetaTrader 5.

```powershell
# 1. Semantic microservice on :9000, with the per-currency bias and live calendar
$env:SEMANTIC_CURRENCY_BIAS = '1'
.\run_semantic_service.bat

# 2. Backend on :8000: live exchange legs, quotes + opportunities recorded
$env:ARBEX_DATA_MODE = 'LIVE_USDINR_EXCHANGES'
$env:ARBEX_SYMBOLS = 'USDINR,EURUSD,GBPUSD,USDJPY,USDCHF'
$env:ARBEX_RECORD_DIR = "$env:LOCALAPPDATA\arbex\recordings"
python -m backend.server.main
```

Check: `http://127.0.0.1:9000/v1/health`, `http://127.0.0.1:9000/v1/currency-index`,
`http://127.0.0.1:9000/v1/calendar/upcoming?currencies=USD&hours=48`,
`http://127.0.0.1:8000/live/status` (legs, skipped feeds, `recorded_rows`).

- **Legs.** The four majors come from free spot venues (Kraken, TrueFX). USD/INR needs
  NSE/BSE futures through Upstox (`UPSTOX_ACCESS_TOKEN`) and/or Kite (`KITE_API_KEY`,
  `KITE_ACCESS_TOKEN`) in `.env`; without them `/live/status` lists USDINR as skipped.
  FX spot is closed from Friday ~21:00 UTC to Sunday ~21:00 UTC, so TrueFX shows
  "no quote yet" and no crosses appear over the weekend.
- **Recorder.** Every quote poll goes to `<ARBEX_RECORD_DIR>/<UTC date>/<hub>.jsonl`
  and every detected opportunity to `opportunities.jsonl` next to it, each with a
  `semantic_snapshot` (window bias, currency bias, both channels, `applied`). Roughly
  0.3 GB a day for four pairs. `ARBEX_RECORD_DIR=off` disables it.
- **Shadow vs applied.** With `ARBEX_CURRENCY_BIAS` unset the ranking is exactly as
  before; the channels are only logged. Set `ARBEX_CURRENCY_BIAS=1` on the backend to
  apply them. The backend reads the currency bias only when the service runs with
  `SEMANTIC_CURRENCY_BIAS=1`.
- **Redis.** The dashboard's scored-opportunity stream needs Redis plus
  `run_semantic.bat`; the recorder does not.
- **Reverting the model.** The service serves the pooled multi-pair model by default.
  `SEMANTIC_REACTION_ARTIFACT=services\semantic\artifacts\usdinr_targeted_expanded\reaction_model_artifact.json`
  restores the USD/INR-only model.

| Variable | Process | Default | Effect |
|---|---|---|---|
| `SEMANTIC_HOST`, `SEMANTIC_PORT` | service | `127.0.0.1`, `9000` | Bind address |
| `SEMANTIC_CURRENCY_BIAS` | service | off | `/v1/currency-index`, `/v1/calendar/upcoming`, live calendar |
| `SEMANTIC_CALENDAR` | service | on with the bias | `0` = no live calendar (release proximity stays null) |
| `SEMANTIC_CALENDAR_DIR` | service | `%LOCALAPPDATA%\arbex\calendar` | Calendar cache |
| `SEMANTIC_CURRENCY_BIAS_HORIZON` | service | `services/semantic/artifacts/h6_horizon.json` | Horizon written by `research/run_h6.py` |
| `SEMANTIC_REACTION_ARTIFACT` | service | pooled multi-pair model | Served reaction model |
| `NEWS_STREAM_USE_FINBERT`, `NEWS_STREAM_USE_RAG`, `NEWS_STREAM_USE_SENTENCE_TRANSFORMER` | service | off | Heavier models (one heavy process at a time on a laptop) |
| `SEMANTIC_SERVICE_URL` | backend | `http://127.0.0.1:9000` | Where the bridge finds the service (legacy `NEWS_STREAM_BASE_URL`) |
| `SEMANTIC_API_PREFIX`, `SEMANTIC_TIMEOUT_MS` | backend | `/v1`, `1200` | Bridge protocol |
| `ARBEX_CURRENCY_BIAS` | backend | off | Apply the risk and direction channels to scores |
| `ARBEX_DATA_MODE` | backend | `SYNTHETIC_USDINR_ONLY` | `LIVE_USDINR_EXCHANGES` = live legs |
| `ARBEX_SYMBOLS` | backend | `USDINR` | Pairs to trade (`all` = every listed pair) |
| `ARBEX_SPOT_VENUES` | backend | `kraken,truefx` | Free spot venues for non-INR pairs (`off`) |
| `ARBEX_RECORD_DIR` | backend | `research/recordings` | Recorder root (`off` disables) |
| `ARBEX_LIVE_REPLAY`, `ARBEX_REPLAY_SPEED`, `ARBEX_REPLAY_LOOP` | backend | unset, `1.0`, loop | Replay a recorded session instead of live |
| `ARBEX_CACHE_DIR` | both | `%LOCALAPPDATA%\arbex` | Regenerable caches outside OneDrive |

**Known live gaps** (each is reported in the `/v1/currency-index` payload, not filled in):
no consensus-vs-actual surprise reaches the service (the calendar has forecasts but no
actuals), so the live currency bias is the news component only; the calendar covers the
current week only; novelty uses the hashing embedder unless the sentence-transformer is
on; the horizon reads "none established" until `research/run_h6.py` has run; USD/INR live
legs need broker credentials.

### Option 1: Quick Start (Windows) - Easiest

```bash
# 1. Start the main trading server:
run_server.bat

# 2. Start the Semantic NLP Engine (REQUIRED — without it no arbitrage
#    opportunities reach the dashboard at all: the streamer publishes to
#    arbex.raw_opps and the WebSocket layer only subscribes to arbex.scored_opps):
run_semantic.bat
```

These scripts will automatically:
- ✅ Activate the virtual environment
- ✅ Install dependencies if needed
- ✅ Start the respective server/engine

### Option 2: Manual Start (Windows)

```bash
# 1. Activate virtual environment
venv\Scripts\activate.bat

# 2. Start server
python -m backend.server.main
```

### Option 3: Manual Start (Mac/Linux)

```bash
# 1. Activate virtual environment
source venv/bin/activate

# 2. Start server
python -m backend.server.main
```

### Option 4: Using Uvicorn Directly (Development)

```bash
# With auto-reload (restarts on code changes)
uvicorn backend.server.main:app --host 0.0.0.0 --port 8000 --reload
```

---

## Step 5: Verify Server is Running

### Check 1: Look for Success Messages

You should see output like:
```
============================================================
Starting MT5 Market Data Engine
============================================================
MT5 initialized successfully
No login credentials provided — attaching to existing MT5 session
MT5 attached to existing session
Account: 5042721323, Server: MetaQuotes-Demo
Starting tick streams for symbols: ['EURUSD', 'GBPUSD', 'USDJPY']
[EURUSD] Stream loop started, polling for ticks...
[GBPUSD] Stream loop started, polling for ticks...
[USDJPY] Stream loop started, polling for ticks...
Server startup complete
WebSocket endpoints:
  - ws://localhost:8000/ws/ticks/{symbol}
  - ws://localhost:8000/ws/candles/{symbol}/{interval}
  - ws://localhost:8000/ws/market/{symbol}
INFO:     Uvicorn running on http://0.0.0.0:8000
```

### Check 2: Test Health Endpoint

Open in browser or use curl:
```
http://localhost:8000/health
```

Expected response:
```json
{
  "status": "healthy",
  "mt5": "connected",
  "active_channels": 0,
  "symbols": ["EURUSD", "GBPUSD", "USDJPY"],
  "intervals": ["100ms", "500ms", "1s", "5s", "15s", "1m"]
}
```

### Check 3: Watch for Tick Data

You should see log lines like:
```
[EURUSD] Tick #1: mid=1.08523 spread=0.00001 vel=-0.000002 vol_5s=0.000120 dir=1 session=London
[EURUSD] Tick #2: mid=1.08524 spread=0.00001 vel=0.000001 vol_5s=0.000125 dir=1 session=London
```

If you see "No new ticks received" warnings, check:
- Is the market open? (Forex markets close on weekends)
- Are the symbols available in your MT5 Market Watch?

---

## Step 6: Test WebSocket Connections

### Test 1: Browser Console (Easiest)

1. Open browser (Chrome/Firefox)
2. Press F12 to open Developer Tools
3. Go to Console tab
4. Paste this code:

```javascript
// Test tick stream
const ws = new WebSocket('ws://localhost:8000/ws/ticks/EURUSD');
ws.onopen = () => console.log('✅ Connected to tick stream');
ws.onmessage = (event) => {
    const tick = JSON.parse(event.data);
    console.log('Tick:', {
        time: new Date(tick.time).toLocaleTimeString(),
        bid: tick.bid,
        ask: tick.ask,
        mid: tick.mid,
        spread: tick.spread,
        velocity: tick.velocity,
        session: tick.session
    });
};
ws.onerror = (error) => console.error('❌ Error:', error);
```

### Test 2: Test Candle Stream

```javascript
// Test candle stream
const candleWs = new WebSocket('ws://localhost:8000/ws/candles/EURUSD/1s');
candleWs.onopen = () => console.log('✅ Connected to candle stream');
candleWs.onmessage = (event) => {
    const candle = JSON.parse(event.data);
    console.log('Candle:', candle);
};
```

### Test 3: Test Market State

```javascript
// Test market state
const marketWs = new WebSocket('ws://localhost:8000/ws/market/EURUSD');
marketWs.onopen = () => console.log('✅ Connected to market state');
marketWs.onmessage = (event) => {
    const state = JSON.parse(event.data);
    console.log('Market State:', {
        trend: state.trend,
        momentum: state.momentum,
        volatility_rank: state.volatility_rank,
        spread: state.spread
    });
};
```

---

## Step 7: Launch the Next.js Dashboard

The system features a modern, real-time React/Next.js dashboard (`arbex-web`) for monitoring the arbitrage engine.

1. Open a new terminal window
2. Navigate to the `arbex-web` directory
3. Install dependencies and start the development server:

```bash
cd arbex-web
npm install
npm run dev
```

4. Open in your browser:
```
http://localhost:3001
```

This opens the modern Bloomberg-style 3-column Arbitrage Research terminal with:
- **Column 1**: Per-session candlestick charts (Tokyo/London/New York)
- **Column 2**: Live Arbitrage Feed with animated reordering and persistence tracking
- **Column 3**: Real-time Level 2 Market Depth and Top-5 Global Opportunity Leaderboard

---

## Troubleshooting

### Problem: "MT5 initialization failed"

**Solution:**
- Make sure MetaTrader 5 is running
- Make sure you're logged into MT5
- Try restarting MT5

### Problem: "No new ticks received"

**Solution:**
- Check if market is open (forex markets close on weekends)
- Verify symbols are in MT5 Market Watch
- Check MT5 connection status

### Problem: "Module not found" errors

**Solution:**
- Make sure virtual environment is activated
- Run: `pip install -r requirements.txt`

### Problem: "Port 8000 already in use"

**Solution:**
- Close other applications using port 8000
- Or change port in `main.py` or uvicorn command

### Problem: WebSocket connection fails

**Solution:**
- Make sure server is running
- Check firewall settings
- Verify URL: `ws://localhost:8000/ws/ticks/EURUSD`

---

## Configuration Options

### Change Default Symbols

Edit `backend/server/main.py`:
```python
DEFAULT_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]  # Add more
```

### Change Default Intervals

Edit `backend/server/main.py`:
```python
DEFAULT_INTERVALS = ["100ms", "500ms", "1s", "5s", "15s", "1m", "5m"]  # Add more
```

### Configure MT5 & Supabase Authentication

The system uses Supabase to securely fetch your MetaTrader 5 credentials from a database.

1. Create a `.env` file in the root directory (or `backend/` folder):
```env
NEXT_PUBLIC_SUPABASE_URL=your_supabase_project_url
NEXT_PUBLIC_SUPABASE_ANON_KEY=your_supabase_anon_key
SUPABASE_USER_ID=your_user_id
```

2. Alternatively, hardcode in `backend/server/main.py` (not recommended):
```python
MT5_LOGIN = 12345678
MT5_PASSWORD = "your_password"
MT5_SERVER = "YourBroker-Demo"
```

---

## Next Steps

1. ✅ Server is running
2. ✅ WebSocket connections tested
3. 📖 Open the **Arbitrage Research terminal** at `http://localhost:8000/research`
4. 📖 Read `README.md` for full API documentation
5. 🚀 Build your trading dashboard!

---

## Quick Reference

| Endpoint | URL | Description |
|----------|-----|-------------|
| Health | `http://localhost:8000/health` | Server status |
| Ticks | `ws://localhost:8000/ws/ticks/{symbol}` | Real-time tick stream |
| Candles | `ws://localhost:8000/ws/candles/{symbol}/{interval}` | Candle stream |
| Market State | `ws://localhost:8000/ws/market/{symbol}` | Aggregated market data |

| Semantic health | `http://localhost:9000/v1/health` | Microservice status, served model, uptime |
| Semantic analyze | `POST http://localhost:9000/v1/analyze` | One article, any number of `currency_pairs` |
| Window bias | `http://localhost:8000/sentiment/window-bias?pair=EUR/USD` | Per-pair bias the engine consumes |

**Default Symbols:** EURUSD, GBPUSD, USDJPY  
**Default Intervals:** 100ms, 500ms, 1s, 5s, 15s, 1m  
**Ports:** 8000 (backend), 9000 (semantic service)

### Running the tests

```bash
python -m pytest tests -q                          # backend (no network)
cd services/semantic && python -m pytest tests -q  # semantic service (calendar tests use a recorded fixture)
cd arbex-web && npx tsc --noEmit                   # dashboard type-check
python -m pytest research/tests -q                 # research pipeline (from the repo that holds research/)
python -m research.reproduce --list                # every paper result, in order, with its gate
```

---

## Support

If you encounter issues:
1. Check the logs in the terminal
2. Verify MT5 is running and connected
3. Check `README.md` for detailed troubleshooting
4. Ensure all dependencies are installed

Happy trading! 🚀




