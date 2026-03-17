#!/bin/bash

echo "========================================"
echo "MT5 Market Data Engine - Server"
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
echo "Starting FastAPI server..."
echo "Server will be available at: http://localhost:8000"
echo "WebSocket endpoints:"
echo "  - ws://localhost:8000/ws/ticks/{symbol}"
echo "  - ws://localhost:8000/ws/candles/{symbol}/{interval}"
echo ""
echo "Press Ctrl+C to stop"
echo ""

python -m backend.server.main




