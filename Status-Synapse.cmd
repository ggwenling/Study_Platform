@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\windows\local-runtime.ps1" status
pause
