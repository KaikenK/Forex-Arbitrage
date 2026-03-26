@echo off
echo ========================================
echo FX Arbitrage Research Engine
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
echo Starting FX Arbitrage Research Engine...
echo Server will be available at: http://localhost:8000
echo.
echo Arbitrage Research Terminal: http://localhost:8000
echo.
echo WebSocket endpoints:
echo   - ws://localhost:8000/ws/arbitrage
echo   - ws://localhost:8000/ws/sources/{symbol}
echo   - ws://localhost:8000/ws/semantic/{symbol}
echo   - ws://localhost:8000/ws/ticks/{symbol}
echo   - ws://localhost:8000/ws/candles/{symbol}/{interval}
echo.
echo Press Ctrl+C to stop
echo.

python -m backend.server.main

pause

