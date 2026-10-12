@echo off
rem pushd, not cd: it also works when the folder is on a network or WSL (\\wsl.localhost\...) path.
pushd "%~dp0.."
docker compose down
timeout /t 5 >nul
