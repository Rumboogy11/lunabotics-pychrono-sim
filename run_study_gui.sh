#!/usr/bin/env bash
# Launch the PyChrono excavator study GUI.
# Prefer system python3 for proper display scaling on Wayland/X11 when it has
# Tkinter; otherwise use the project-local env. Simulations, Chrono/OCP, and
# other heavy tools are still executed via chrono-env/bin/python by the GUI.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHRONO_PYTHON="$SCRIPT_DIR/chrono-env/bin/python"

# Expose project-local tools such as pvpython without requiring activation.
if [[ -d "$SCRIPT_DIR/chrono-env/bin" ]]; then
    PATH="$SCRIPT_DIR/chrono-env/bin:$PATH"
    export PATH
fi

python_has_tkinter() {
    "$1" -c 'import tkinter' >/dev/null 2>&1
}

if [[ -z "${PYTHON:-}" ]]; then
    if command -v python3 >/dev/null 2>&1 && python_has_tkinter python3; then
        PYTHON="$(command -v python3)"
    elif [[ -x "$CHRONO_PYTHON" ]]; then
        PYTHON="$CHRONO_PYTHON"
    else
        PYTHON="python3"
    fi
fi

exec "$PYTHON" "$SCRIPT_DIR/study_gui.py" "$@"
