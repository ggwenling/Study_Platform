@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0infra\windows\backup-restore.ps1" -Action Backup %*
if errorlevel 1 pause
