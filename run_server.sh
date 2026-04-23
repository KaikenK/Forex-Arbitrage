#!/bin/bash

echo "========================================"
echo "FX Arbitrage Research Engine"
echo "========================================"
echo ""

# Check if virtual environment exists
if [ -d "venv" ]; then
    echo "Activating virtual environment..."
    source venv/bin/activate
fi

echo ""
echo "Installing dependencies if needed..."
pip install -q -r requirements.txt

echo ""
echo "Starting FX Arbitrage Research Engine..."
echo "Server will be available at: http://localhost:8000"
echo ""
echo "Arbitrage Research Terminal: http://localhost:8000"
echo ""
echo "WebSocket endpoints:"
echo "  - ws://localhost:8000/ws/arbitrage"
echo "  - ws://localhost:8000/ws/sources/{symbol}"
echo "  - ws://localhost:8000/ws/semantic/{symbol}"
echo "  - ws://localhost:8000/ws/ticks/{symbol}"
echo "  - ws://localhost:8000/ws/candles/{symbol}/{interval}"
echo ""
echo "Press Ctrl+C to stop"
echo ""

python3 -m backend.server.run




