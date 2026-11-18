#!/usr/bin/env bash
# Run the CPU PyChrono demo.  All arguments are forwarded to the Python script.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "${PYTHON:-}" && -x "$PROJECT_ROOT/chrono-env/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/chrono-env/bin/python"
elif [[ -z "${PYTHON:-}" && -x "$PROJECT_ROOT/pychrono-env/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/pychrono-env/bin/python"
fi
PYTHON="${PYTHON:-python3}"

if [[ -d "$PROJECT_ROOT/chrono-install/share/chrono/python" ]]; then
    export PYTHONPATH="$PROJECT_ROOT/chrono-install/share/chrono/python${PYTHONPATH:+:$PYTHONPATH}"
fi
export LD_LIBRARY_PATH="$PROJECT_ROOT/chrono-install/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec "$PYTHON" "$SCRIPT_DIR/lunar_excavator_pychrono.py" "$@"
