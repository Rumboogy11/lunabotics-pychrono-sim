#!/usr/bin/env bash
# Replay a recorded rigid-CRM soil wrench on the solid FEA drum.
# All arguments are forwarded to lunar_excavator_oneway.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "${PYTHON:-}" && -x "$PROJECT_ROOT/chrono-env/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/chrono-env/bin/python"
fi
PYTHON="${PYTHON:-python3}"

# PardisoMKL is provided by the conda-forge PyChrono package in chrono-env, not
# by the local chrono-install build.  Do not shadow it with the local Python
# package when that solver is requested.
solver=""
prev=""
for arg in "$@"; do
    if [[ "$prev" == "--solver" ]]; then
        solver="$arg"
        break
    fi
    case "$arg" in
        --solver=*) solver="${arg#*=}"; break ;;
    esac
    prev="$arg"
done

# The driver defaults to PardisoMKL, so only add the local build when the user
# explicitly requests one of the solvers it provides.
if [[ "$solver" == "gmres" || "$solver" == "sparse-lu" || "$solver" == "sparse-qr" ]]; then
    if [[ -d "$PROJECT_ROOT/chrono-install/share/chrono/python" ]]; then
        export PYTHONPATH="$PROJECT_ROOT/chrono-install/share/chrono/python${PYTHONPATH:+:$PYTHONPATH}"
    fi
    export LD_LIBRARY_PATH="$PROJECT_ROOT/chrono-install/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

exec "$PYTHON" "$SCRIPT_DIR/lunar_excavator_oneway.py" "$@"
