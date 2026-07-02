"""Standalone entry point for the Paper Radar backend.

Used both for `python run_backend.py` and as the PyInstaller bundle target
that the Electron desktop app spawns. Kept at the repo root so the
``backend`` package (which uses relative imports) is importable as-is.
"""
from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Paper Radar backend server")
    parser.add_argument("--host", default=os.environ.get("PAPER_RADAR_BACKEND_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PAPER_RADAR_BACKEND_PORT", "8000")))
    args = parser.parse_args()

    # Import the app object directly (not the "module:app" string) so it works
    # inside a frozen PyInstaller bundle where import-by-string can fail.
    from backend.app.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
