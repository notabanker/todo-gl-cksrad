#!/usr/bin/env bash
# One-command launcher for TYCHE.
# Creates the virtualenv on first run, installs deps, then starts the app.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ ! -d .venv ]; then
  echo "Creating virtual environment (.venv)..."
  "$PY" -m venv .venv
fi

echo "Installing dependencies..."
./.venv/bin/pip install -q -r requirements.txt

echo "Starting TYCHE..."
exec ./.venv/bin/python main.py
