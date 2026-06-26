#!/usr/bin/env bash
# Run the traced agent with the vendored minisweagent.
# Usage: ./run.sh [--model mock|vibeproxy] [--task "..."]
# Prefers the project venv (Python 3.11, mini-swe-agent installed editable).
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY="python3"   # fall back if venv missing
export PYTHONPATH="upstream/src:${PYTHONPATH:-}"  # harmless with editable install; helps without venv
export MSWEA_SILENT_STARTUP=1   # suppress mini's startup banner
exec "$PY" harness/run_traced.py "$@"
