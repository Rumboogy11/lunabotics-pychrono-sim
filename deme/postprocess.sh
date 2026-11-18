#!/usr/bin/env bash
# Summarise DEME output frames.  Arguments are forwarded to postprocess.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "${PYTHON:-}" && -x "$PROJECT_ROOT/deme-env/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/deme-env/bin/python"
elif [[ -z "${PYTHON:-}" && -x "/tmp/opencode/deme-venv/bin/python" ]]; then
    PYTHON="/tmp/opencode/deme-venv/bin/python"
fi
PYTHON="${PYTHON:-python3}"

exec "$PYTHON" "$SCRIPT_DIR/postprocess.py" "$@"
