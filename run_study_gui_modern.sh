#!/usr/bin/env bash
# Launch the modern-styled PyChrono excavator study GUI.
# The classic launcher (run_study_gui.sh / study_gui.py) is unchanged. This
# script uses the same interpreter policy: prefer a system Python with Tkinter
# for display quality, while simulations and tools still run through chrono-env.
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

exec "$PYTHON" "$SCRIPT_DIR/study_gui_modern.py" "$@"
