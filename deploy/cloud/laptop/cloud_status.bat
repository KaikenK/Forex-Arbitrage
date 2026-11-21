@echo off
REM Arbex cloud recorder: status (see cloud.ps1)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0cloud.ps1" status
pause
