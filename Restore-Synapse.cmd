@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\windows\backup-restore.ps1" -Action Restore %*
if errorlevel 1 pause
