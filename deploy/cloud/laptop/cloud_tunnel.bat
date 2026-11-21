@echo off
REM Arbex cloud recorder: tunnel (see cloud.ps1)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cloud.ps1" tunnel
pause
