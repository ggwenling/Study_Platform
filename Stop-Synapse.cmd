@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\windows\local-runtime.ps1" stop
if errorlevel 1 pause
