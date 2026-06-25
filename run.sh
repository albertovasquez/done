#!/usr/bin/env bash
# Run the traced agent with the vendored minisweagent on PYTHONPATH.
# Usage: ./run.sh [--model mock|vibeproxy] [--task "..."]
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="upstream/src:${PYTHONPATH:-}"
export MSWEA_SILENT_STARTUP=1   # suppress mini's startup banner
exec python3 trace/run_traced.py "$@"
