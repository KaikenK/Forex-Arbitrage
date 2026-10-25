@echo off
REM News/sentiment microservice (services/semantic) on :9000.
REM Not the same as run_semantic.bat, which starts the Redis opportunity scorer
REM (backend.core.semantic_engine) that consumes this service's window bias.
call "%~dp0services\semantic\run.bat" %*
