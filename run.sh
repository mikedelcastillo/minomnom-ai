#!/usr/bin/env bash
set -euo pipefail

VENV="bot/.venv"

# python-telegram-bot 21.9 calls asyncio.get_event_loop() inside run_polling().
# Python 3.14 made that raise instead of auto-creating a loop, so polling exits
# immediately with "coroutine 'Updater.start_polling' was never awaited".
# Pin to a 3.10-3.13 interpreter; prefer 3.12.
PY=""
for candidate in python3.12 python3.13 python3.11 python3.10 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    ver=$("$candidate" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
    case "$ver" in
      3.10|3.11|3.12|3.13)
        PY=$(command -v "$candidate")
        break
        ;;
    esac
  fi
done
if [ -z "$PY" ]; then
  echo "error: need Python 3.10-3.13 on PATH (python-telegram-bot 21.9 is not compatible with 3.14+)" >&2
  exit 1
fi

# Recreate the venv if missing, broken, or built with a different Python minor.
need_recreate=0
if [ ! -d "$VENV" ] || ! "$VENV/bin/python" -c "" &>/dev/null || ! "$VENV/bin/pip" --version &>/dev/null; then
  need_recreate=1
else
  want=$("$PY" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  have=$("$VENV/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  [ "$want" = "$have" ] || need_recreate=1
fi
if [ "$need_recreate" -eq 1 ]; then
  echo "Creating virtualenv with $PY..."
  rm -rf "$VENV"
  "$PY" -m venv "$VENV"
fi

# Install / sync dependencies
"$VENV/bin/pip" install -q -r bot/requirements.txt

# Launch bot — default OLLAMA_URL to localhost for non-Docker dev
export OLLAMA_URL="${OLLAMA_URL:-http://localhost:11434}"
exec "$VENV/bin/python" bot/main.py
