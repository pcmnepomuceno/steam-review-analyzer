@echo off
cd /d "%~dp0.."
docker compose down
timeout /t 5 >nul
