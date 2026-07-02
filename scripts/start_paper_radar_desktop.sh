#!/usr/bin/env bash
# Launch Paper Radar as a desktop app in development mode.
#
# The Electron main process spawns the FastAPI backend itself (using the repo
# .venv), so this script only needs to ensure dependencies are present and then
# start Vite + Electron together.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Backend deps live in the repo .venv (Electron's main.cjs calls into it).
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install -r backend/requirements.txt

# Frontend + Electron deps.
if [ ! -d "frontend/node_modules" ]; then
  (cd frontend && npm install)
fi

export PAPER_RADAR_DB="${PAPER_RADAR_DB:-data/paper_radar.sqlite}"

# Runs Vite dev server, waits for it, then launches Electron (which starts the
# backend). Ctrl-C tears everything down.
(cd frontend && npm run electron:dev)
