@echo off
echo ========================================
echo Arbex Semantic Engine Microservice
echo ========================================
echo.

REM Check if virtual environment exists
if exist "venv\Scripts\activate.bat" (
    echo Activating virtual environment...
    call venv\Scripts\activate.bat
)

echo.
echo Starting Semantic Engine...
echo Listening to Redis channel: arbex.raw_opps
echo Broadcasting to Redis channel: arbex.scored_opps
echo.
echo Press Ctrl+C to stop
echo.

python -m backend.core.semantic_engine

pause
