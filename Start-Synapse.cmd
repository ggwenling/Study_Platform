@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\windows\local-runtime.ps1" start
if errorlevel 1 pause
