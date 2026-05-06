#!/usr/bin/env bash
set -euo pipefail

# Configurable via environment variables.
APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BRANCH="${BRANCH:-main}"
PM2_APP_NAME="${PM2_APP_NAME:-discord-llm-bot}"
ENTRYPOINT="${ENTRYPOINT:-bot.py}"

cd "$APP_DIR"

echo "Deploying from branch: $BRANCH"
git fetch origin
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

# Use the virtual environment python if available.
if [[ -x ".venv/bin/pip" ]]; then
  .venv/bin/pip install -r requirements.txt
  PYTHON_CMD=".venv/bin/python"
else
  pip3 install -r requirements.txt
  PYTHON_CMD="python3"
fi

if pm2 describe "$PM2_APP_NAME" >/dev/null 2>&1; then
  pm2 restart "$PM2_APP_NAME" --update-env
else
  pm2 start "$PYTHON_CMD" --name "$PM2_APP_NAME" -- "$ENTRYPOINT"
fi

pm2 save
echo "Deployment complete."
