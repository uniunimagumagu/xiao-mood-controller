#!/usr/bin/env bash
# XIAO Mood Controller: one-shot bootstrap (macOS / Linux)
# Usage: tools/setup_env.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> creating venv (.venv)"
python3 -m venv .venv
.venv/bin/pip install --quiet -U pip
.venv/bin/pip install --quiet -r requirements.txt

echo "==> firmware bootstrap (board package + libraries + mbed patch + compile)"
.venv/bin/python tools/build_all.py --skip-deps

echo "==> copy .env example"
[ -f .env ] || cp config/env.example .env

cat <<'_MSG'
Done.
Next steps:
  .venv/bin/python tools/build_all.py --upload     # flash if the device is plugged
  .venv/bin/python tools/supervisor.py            # run the daemon (restarts on crash)
  # optional: .venv/bin/uvicorn server.http_api:app --host 127.0.0.1 --port 8765
_MSG
