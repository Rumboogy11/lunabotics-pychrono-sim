#!/usr/bin/env bash
# Launch the PyChrono excavator study GUI.
# Prefers system python3 for proper display scaling on Wayland/X11; the GUI uses
# chrono-env/bin/python for simulations, Chrono/OCP, and other heavy tools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${PYTHON:-}" ]]; then
    if command -v python3 >/dev/null 2>&1; then
        PYTHON="python3"
    elif [[ -x "$SCRIPT_DIR/chrono-env/bin/python" ]]; then
        PYTHON="$SCRIPT_DIR/chrono-env/bin/python"
    else
        PYTHON="python3"
    fi
fi

exec "$PYTHON" "$SCRIPT_DIR/study_gui.py" "$@"
