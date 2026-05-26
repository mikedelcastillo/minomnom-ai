#!/usr/bin/env bash
set -euo pipefail

VENV="bot/.venv"

# Create virtualenv if missing or broken (e.g. project was moved/renamed)
if [ ! -d "$VENV" ] || ! "$VENV/bin/python" -c "" &>/dev/null || ! "$VENV/bin/pip" --version &>/dev/null; then
  echo "Creating virtualenv..."
  rm -rf "$VENV"
  python3 -m venv "$VENV"
fi

# Install / sync dependencies
"$VENV/bin/pip" install -q -r bot/requirements.txt

# Launch bot — default OLLAMA_URL to localhost for non-Docker dev
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
exec "$VENV/bin/python" bot/main.py
