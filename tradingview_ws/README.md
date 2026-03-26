# TradingView WebSocket Client

Production-ready Python module for streaming live FX tick data from TradingView's WebSocket.

## Installation

```bash
pip install websocket-client
```

## Quick Start

### Basic Usage

```python
from tradingview_ws import TradingViewWS, TickData

def on_tick(tick: TickData):
    print(f"{tick.symbol}: bid={tick.bid}, ask={tick.ask}, last={tick.last}")

# Create client
client = TradingViewWS(
    symbol="FX_IDC:EURUSD",
    on_tick=on_tick
)

# Start streaming
client.start()
client.wait()  # Block until Ctrl+C
```

### Run as Module

```bash
python -m tradingview_ws.main
```

## Features

- ✅ Real-time FX tick data streaming
- ✅ Auto-reconnection on disconnect
- ✅ Heartbeat every 15 seconds
- ✅ Multiple symbol support
- ✅ CSV logging (automatic)
- ✅ Test mode (1 tick/second throttling)
- ✅ Production-ready error handling
- ✅ Graceful shutdown

## Configuration

Edit `client.py` to configure:

```python
# WebSocket settings
WS_URL = "wss://data.tradingview.com/socket.io/websocket"
HEARTBEAT_INTERVAL = 15  # seconds
RECONNECT_DELAY = 5  # seconds

# Test mode
TEST_MODE = False  # Set to True to limit to 1 tick/second

# CSV logging
ENABLE_CSV_LOG = True
CSV_LOG_DIR = "."
```

## Symbol Format

TradingView uses specific symbol formats:

- **Forex**: `FX_IDC:EURUSD`, `FX_IDC:GBPUSD`, `FX_IDC:USDJPY`
- Format: `FX_IDC:<BASE><QUOTE>`

## Multiple Symbols

```python
client = TradingViewWS(
    symbols=["FX_IDC:EURUSD", "FX_IDC:GBPUSD", "FX_IDC:USDJPY"],
    on_tick=on_tick
)
```

## Tick Data Format

Each tick is a `TickData` object:

```python
{
    "symbol": "EURUSD",
    "timestamp": 1234567890123,  # Unix timestamp in milliseconds
    "bid": 1.08523,
    "ask": 1.08525,
    "last": 1.08524
}
```

## CSV Logging

Ticks are automatically logged to CSV files:
- `ticks_FX_IDC_EURUSD.csv`
- Format: `timestamp,symbol,bid,ask,last`

## Test Mode

Enable test mode to throttle output:

```python
client = TradingViewWS(
    symbol="FX_IDC:EURUSD",
    on_tick=on_tick,
    test_mode=True  # Limits to 1 tick per second
)
```

## Architecture

```
tradingview_ws/
├── __init__.py      # Module exports
├── client.py        # WebSocket client & connection management
├── parser.py        # Message parsing & tick extraction
└── main.py          # Entry point script
```

## Error Handling

The client automatically:
- Reconnects on disconnect (up to 10 attempts)
- Handles network errors gracefully
- Logs all errors with timestamps
- Maintains connection health with heartbeats

## License

Production-ready code for commercial use.




