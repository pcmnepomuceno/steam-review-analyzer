#!/usr/bin/env bash
# Stops the app. Analyzed games are kept in a Docker volume and are there on the next start.
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
docker compose down
