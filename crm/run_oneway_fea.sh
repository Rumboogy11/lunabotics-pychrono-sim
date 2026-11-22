#!/usr/bin/env bash
# Replay a recorded rigid-CRM soil wrench on the solid FEA drum.
# All arguments are forwarded to lunar_excavator_oneway.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Expose project-local tools such as pvpython without requiring activation.
if [[ -d "$PROJECT_ROOT/chrono-env/bin" ]]; then
    PATH="$PROJECT_ROOT/chrono-env/bin:$PATH"
    export PATH
fi

if [[ -z "${PYTHON:-}" && -x "$PROJECT_ROOT/chrono-env/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/chrono-env/bin/python"
fi
PYTHON="${PYTHON:-python3}"

# The local Chrono 11 build provides all supported solvers, including
# PardisoMKL.  Keep the one-way replay on the same bindings as rigid CRM so a
# stale conda PyChrono copy cannot mix versions.
if [[ -d "$PROJECT_ROOT/chrono-install/share/chrono/python" ]]; then
    export PYTHONPATH="$PROJECT_ROOT/chrono-install/share/chrono/python${PYTHONPATH:+:$PYTHONPATH}"
fi
export LD_LIBRARY_PATH="$PROJECT_ROOT/chrono-install/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
source "$SCRIPT_DIR/chrono_runtime_env.sh"
chrono_use_local_runtime

exec "$PYTHON" "$SCRIPT_DIR/lunar_excavator_oneway.py" "$@"
