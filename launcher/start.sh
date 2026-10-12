#!/usr/bin/env bash
# Double-click entry point for macOS/Linux (via Start.command / Start.desktop): builds and starts
# the whole app in Docker, then opens the dashboard. The first run builds the images (about 3 min on a fast desktop).
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

fail() {
  printf '\n%s\n' "$1" >&2
  read -r -p "Press Enter to close." _ || true
  exit 1
}

command -v docker >/dev/null || fail "Docker is not installed. Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again."
docker info >/dev/null 2>&1 || fail "Docker is installed but not running. Start Docker Desktop, wait until it says it is running, then run this again."

echo "Starting Steam Review Analyzer. The first run builds the app and can take several minutes; later starts take seconds."
docker compose up -d --build --wait || fail "Start failed. The messages above say why. 'docker compose logs' shows more."

URL="http://localhost:3000"
echo "Ready: $URL"
(xdg-open "$URL" || open "$URL") >/dev/null 2>&1 || echo "Open $URL in your browser."
echo "You can close this window. To stop the app, run launcher/stop.sh (or Stop.bat on Windows)."
