#!/usr/bin/env bash
set -euo pipefail

MODEL="${OLLAMA_MODEL:-phi3.5}"
VENV="bot/.venv"

# Require Ollama to be installed
if ! command -v ollama &>/dev/null; then
  echo "Error: ollama not found. Install from https://ollama.com" >&2
  exit 1
fi

# Start ollama serve if not already listening
if ! curl -sf http://localhost:11434 &>/dev/null; then
  echo "Starting Ollama..."
  ollama serve &>/tmp/ollama.log &
  for i in $(seq 1 20); do
    curl -sf http://localhost:11434 &>/dev/null && break
    sleep 0.5
  done
fi

# Pull model if not already present
if ! ollama list | grep -q "^$MODEL"; then
  echo "Pulling model $MODEL (first run only)..."
  ollama pull "$MODEL"
fi

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
