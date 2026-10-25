@echo off
REM Arbex semantic microservice: news NLP + reaction model + live feed.
REM Default http://127.0.0.1:9000 (API also under /v1). Extra args go to serve.py,
REM e.g.  run.bat --use-finbert --port 9100
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

if not exist "config.toml" (
    echo [info] No config.toml - running on defaults. Copy config.toml.example to config.toml to add API keys.
)

echo Starting Arbex semantic service on %SEMANTIC_HOST%:%SEMANTIC_PORT% ^(defaults 127.0.0.1:9000^)
"%PY%" serve.py %*
