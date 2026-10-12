@echo off
rem Windows: double-click. Builds and starts the whole app in Docker, then opens the dashboard.
rem pushd, not cd: it also works when the folder is on a network or WSL (\\wsl.localhost\...) path.
pushd "%~dp0.."
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker is not installed or not running.
  echo Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and start it, then run this again.
  pause
  exit /b 1
)
echo Starting Steam Review Analyzer. The first run builds the app and can take several minutes; later starts take seconds.
docker compose up -d --build --wait
if errorlevel 1 (
  echo Start failed. The messages above say why.
  pause
  exit /b 1
)
start "" http://localhost:3000
echo Ready: http://localhost:3000. You can close this window. Run Stop.bat to stop the app.
timeout /t 10 >nul
