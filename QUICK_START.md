# Quick Start Guide

## Prerequisites

1. **MetaTrader 5** must be installed and running
2. **Python 3.12+** installed
3. **Virtual environment** (optional but recommended)

## Setup (One-Time)

```bash
# 1. Create virtual environment
python -m venv venv

# 2. Activate it
venv\Scripts\activate.bat  # Windows
# or
source venv/bin/activate   # Mac/Linux

# 3. Install dependencies
pip install -r requirements.txt
```

## Running the Server

### Option 1: Use the run script (Windows)

```bash
run_server.bat
```

### Option 2: Manual start

```bash
# Activate venv first
venv\Scripts\activate.bat

# Start server
python -m backend.server.main
```

### Option 3: Using uvicorn directly

```bash
uvicorn backend.server.main:app --host 0.0.0.0 --port 8000 --reload
```

## Verify It's Working

1. **Check health endpoint:**
   ```
   http://localhost:8000/health
   ```

2. **Test WebSocket connection** (using browser console or tool):
   ```javascript
   const ws = new WebSocket('ws://localhost:8000/ws/ticks/EURUSD');
   ws.onmessage = (event) => console.log(event.data);
   ```

## Default Configuration

- **Symbols:** EURUSD, GBPUSD, USDJPY
- **Intervals:** 1s, 5s, 15s, 1m
- **Port:** 8000

## Next Steps

See `README.md` for:
- Frontend integration examples
- WebSocket API details
- Configuration options
- Troubleshooting




