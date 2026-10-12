#!/usr/bin/env bash
# One-command local setup: Postgres, backend env, migrations, a seed game, both dev servers.
# Safe to re-run: finished steps are detected and skipped.
#   ./setup.sh [APPID]   seed game (default: SEED_APPID or 1145360, Hades)
#   ./setup.sh stop      stop the API and dashboard (Postgres stays up)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
RUN_DIR="$ROOT/.dev"
API_PORT=8000
WEB_PORT=3000
DB_PORT=5433
TOTAL=9
STEP=0
STEP_NAME="preflight"

step() { STEP=$((STEP + 1)); STEP_NAME="$1"; printf '\n[%d/%d] %s\n' "$STEP" "$TOTAL" "$1"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
trap 'printf "\nSetup stopped at step %d/%d (%s). Fix the error above and re-run ./setup.sh; finished steps are skipped.\n" "$STEP" "$TOTAL" "$STEP_NAME" >&2' ERR

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
pid_alive() { [[ -f "$RUN_DIR/$1.pid" ]] && kill -0 "$(<"$RUN_DIR/$1.pid")" 2>/dev/null; }

stop_server() {
  if pid_alive "$1"; then
    # The pid is a process-group leader (setsid), so this takes down uvicorn's reloader or npm's children too.
    kill -- "-$(<"$RUN_DIR/$1.pid")" 2>/dev/null || true
    echo "stopped $1"
  else
    echo "$1 not running"
  fi
  rm -f "$RUN_DIR/$1.pid"
}

if [[ "${1:-}" == "stop" ]]; then
  stop_server api
  stop_server web
  exit 0
fi

SEED="${1:-${SEED_APPID:-1145360}}"
[[ "$SEED" =~ ^[0-9]+$ ]] || die "seed appid must be a number, got '$SEED'"

# $1 name, $2 port, $3 health URL, $4 command (run via setsid, so it is its own process group)
start_server() {
  local name="$1" port="$2" url="$3" cmd="$4"
  if pid_alive "$name"; then echo "$name already running on :$port"; return; fi
  port_busy "$port" && die "port $port is already in use by another process; free it or stop it, then re-run ($name needs it)"
  mkdir -p "$RUN_DIR"
  setsid bash -c "$cmd" >"$RUN_DIR/$name.log" 2>&1 < /dev/null &
  echo $! >"$RUN_DIR/$name.pid"
  echo "starting $name on :$port (log: .dev/$name.log)"
  for _ in $(seq 120); do
    curl -fs -o /dev/null "$url" && return
    pid_alive "$name" || { tail -n 20 "$RUN_DIR/$name.log" >&2; die "$name exited during startup"; }
    sleep 1
  done
  tail -n 20 "$RUN_DIR/$name.log" >&2
  die "$name did not answer at $url within 120 s"
}

step "Checking required tools (instant)"
command -v docker >/dev/null || die "docker not found. Install Docker: https://docs.docker.com/get-docker/"
docker info >/dev/null 2>&1 || die "Docker is installed but its daemon is not running. Start Docker (Desktop or the service), then re-run"
docker compose version >/dev/null 2>&1 || die "'docker compose' (v2 plugin) not found. Update Docker"
command -v uv >/dev/null || die "uv not found. Install: https://docs.astral.sh/uv/getting-started/installation/"
command -v node >/dev/null || die "node not found. Install Node 22 or newer: https://nodejs.org/"
command -v npm >/dev/null || die "npm not found. It ships with Node: https://nodejs.org/"
command -v curl >/dev/null || die "curl not found. Install it with your package manager"
(( $(node -p 'process.versions.node.split(".")[0]') >= 22 )) || die "Node 22 or newer is required, found $(node -v)"
echo "docker, uv, node $(node -v): ok"

step "Settings file .env (instant)"
if [[ -f .env ]]; then echo ".env exists, left untouched"; else cp .env.example .env && echo "created .env from .env.example (local Postgres on :$DB_PORT)"; fi

step "Postgres container (first run pulls the image: up to a minute)"
if [[ -n "$(docker compose ps --status running --quiet db)" ]]; then
  echo "db already running"
else
  port_busy "$DB_PORT" && die "port $DB_PORT is already in use by another process; free it, then re-run (Postgres needs it)"
  docker compose up -d --wait db
fi

step "Backend Python environment and migrations (first run: 1-2 minutes of downloads)"
cd "$ROOT/backend"
[[ -x .venv/bin/python ]] || uv venv --python 3.12 .venv
uv pip install --quiet --python .venv/bin/python -r requirements/dev.txt
# shellcheck disable=SC1091
source .venv/bin/activate
alembic upgrade head

step "Seed game $SEED: fetch and cache its reviews from Steam (about 10-30 s)"
python scripts/ingest_game.py "$SEED"

step "Sentiment model"
if [[ -f models_store/sentiment.joblib ]]; then
  echo "committed model found, nothing to train"
else
  echo "no model found: training on the cached reviews (a few seconds)"
  python scripts/train_sentiment.py
fi

step "Analyze the seed game: sentiment and aspect tagging (first run downloads a ~90 MB model, then 15-40 s; skipped when done)"
python scripts/process_reviews.py "$SEED"

step "Dashboard dependencies (first run: 1-3 minutes)"
cd "$ROOT/frontend"
if [[ -d node_modules && node_modules -nt package-lock.json ]]; then echo "node_modules up to date"; else npm ci; fi

step "Starting the API and the dashboard (the dashboard's first compile takes up to a minute)"
cd "$ROOT/backend"
start_server api "$API_PORT" "http://localhost:$API_PORT/health" "exec uvicorn src.main:app --port $API_PORT"
cd "$ROOT/frontend"
start_server web "$WEB_PORT" "http://localhost:$WEB_PORT/" "exec npm run dev"

cd "$ROOT/backend"
ON_DEMAND="$(python -c 'from src.config import settings; print(settings.allow_on_demand_processing)')"
trap - ERR
echo
echo "Ready: http://localhost:$WEB_PORT"
if [[ "$ON_DEMAND" == "True" ]]; then
  echo "On-demand processing: ENABLED. Search any Steam game or enter any appid and it is analyzed live (this is not the restricted demo)."
else
  echo "WARNING: ALLOW_ON_DEMAND_PROCESSING is false in .env, so only already-analyzed games open. Set it to true and restart the API."
fi
echo "Logs: .dev/api.log, .dev/web.log. Stop with ./setup.sh stop"
