#!/usr/bin/env bash
# Build the standalone Paper Radar backend with PyInstaller so the Electron
# desktop app can ship it as a sidecar. Produces:
#   backend/dist/paper-radar-backend/   (onedir bundle)
#
# electron-builder.yml copies that directory into the app's resources/backend.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate

python -m pip install -r backend/requirements.txt
python -m pip install pyinstaller

# Clean previous build so stale files never leak into the bundle.
rm -rf backend/build backend/dist

pyinstaller \
  --noconfirm \
  --name paper-radar-backend \
  --distpath backend/dist \
  --workpath backend/build \
  --specpath backend \
  --collect-all sentence_transformers \
  --collect-all transformers \
  --collect-all torch \
  --collect-all tokenizers \
  --collect-all safetensors \
  --collect-all fitz \
  --collect-all pymupdf \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.protocols.websockets.auto \
  --hidden-import uvicorn.lifespan.on \
  run_backend.py

echo
echo "Backend bundle: backend/dist/paper-radar-backend/"
