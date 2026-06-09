#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BACKEND_HOST="${PAPER_RADAR_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${PAPER_RADAR_BACKEND_PORT:-8000}"
FRONTEND_HOST="${PAPER_RADAR_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${PAPER_RADAR_FRONTEND_PORT:-5173}"

stop_port_processes() {
  local port="$1"
  local label="$2"
  shift 2
  local patterns=("$@")

  if ! command -v lsof >/dev/null 2>&1; then
    return
  fi

  local pids
  pids="$(lsof -ti "tcp:${port}" 2>/dev/null || true)"
  if [ -z "$pids" ]; then
    return
  fi

  for pid in $pids; do
    local command_line
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    local matched=0
    for pattern in "${patterns[@]}"; do
      if [[ "$command_line" == *"$pattern"* ]]; then
        matched=1
        break
      fi
    done

    if [ "$matched" -eq 1 ]; then
      echo "Stopping old ${label} on port ${port}: PID ${pid}"
      kill "$pid" >/dev/null 2>&1 || true
    else
      echo "Port ${port} is occupied by another process:"
      echo "  PID ${pid}: ${command_line}"
      echo "Stop it yourself or set a different port, then rerun this script."
      exit 1
    fi
  done
}

stop_port_processes "$BACKEND_PORT" "Paper Radar backend" "uvicorn backend.app.main:app"
stop_port_processes "$FRONTEND_PORT" "Paper Radar frontend" "vite --host" "frontend/node_modules"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install -r backend/requirements.txt

if [ ! -d "frontend/node_modules" ]; then
  (cd frontend && npm install)
fi

export PAPER_RADAR_DB="${PAPER_RADAR_DB:-data/paper_radar.sqlite}"

python -m uvicorn backend.app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT" &
BACKEND_PID=$!

cleanup() {
  echo
  echo "Stopping Paper Radar backend: PID ${BACKEND_PID}"
  kill "$BACKEND_PID" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo
echo "Paper Radar backend:  http://${BACKEND_HOST}:${BACKEND_PORT}"
echo "Paper Radar frontend: http://${FRONTEND_HOST}:${FRONTEND_PORT}"
echo

(cd frontend && npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT")
