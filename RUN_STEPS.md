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

### Option 1: Quick Start (Windows) - Easiest

```bash
# Just double-click or run:
run_server.bat
```

This script will:
- ✅ Activate virtual environment automatically
- ✅ Install dependencies if needed
- ✅ Start the server

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

## Step 7: Use with Frontend

### Option 1: Open the Arbitrage Research Terminal

Open in your browser:
```
http://localhost:8000/research
```

This opens the Bloomberg-style 3-column Arbitrage Research terminal with:
- Per-session candlestick charts (Tokyo/London/New York)
- Real-time confidence scoring and arbitrage detection
- Execution integration with ranked opportunities

### Option 2: Custom Integration

The `arbitrage_research.html` source code provides a reference implementation for:
- WebSocket connection management (auto-reconnect)
- Lightweight Charts candlestick rendering
- Real-time opportunity card rendering
- Status icon tooltips

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

### Use MT5 Login Credentials (Optional)

If you want to login programmatically instead of using existing session:

Edit `backend/server/main.py`:
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

**Default Symbols:** EURUSD, GBPUSD, USDJPY  
**Default Intervals:** 100ms, 500ms, 1s, 5s, 15s, 1m  
**Port:** 8000

---

## Support

If you encounter issues:
1. Check the logs in the terminal
2. Verify MT5 is running and connected
3. Check `README.md` for detailed troubleshooting
4. Ensure all dependencies are installed

Happy trading! 🚀




