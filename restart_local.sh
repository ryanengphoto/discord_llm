#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

if [[ ! -f ".venv/bin/activate" ]]; then
  echo "Missing virtual environment at .venv. Create it first." >&2
  exit 1
fi

source ".venv/bin/activate"

pkill -f "\.venv/bin/python bot\.py" || true
sleep 1

exec python bot.py
