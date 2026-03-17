@echo off
echo ========================================
echo MT5 Market Data Engine - Server
echo ========================================
echo.

REM Check if virtual environment exists
if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
)

echo.
echo Installing dependencies if needed...
pip install -q -r requirements.txt

echo.
echo Starting FastAPI server...
echo Server will be available at: http://localhost:8000
echo WebSocket endpoints:
echo   - ws://localhost:8000/ws/ticks/{symbol}
echo   - ws://localhost:8000/ws/candles/{symbol}/{interval}
echo   - ws://localhost:8000/ws/market/{symbol}
echo.
echo Press Ctrl+C to stop
echo.

python -m backend.server.main

pause

