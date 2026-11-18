#!/usr/bin/env bash
# Run the spindle-driven FEA structural check coupled to Chrono::CRM.
# All arguments are forwarded to lunar_excavator_crm_fea.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "${PYTHON:-}" && -x "$PROJECT_ROOT/chrono-env/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/chrono-env/bin/python"
fi
PYTHON="${PYTHON:-python3}"

if [[ -d "$PROJECT_ROOT/chrono-install/share/chrono/python" ]]; then
    export PYTHONPATH="$PROJECT_ROOT/chrono-install/share/chrono/python${PYTHONPATH:+:$PYTHONPATH}"
fi
export LD_LIBRARY_PATH="$PROJECT_ROOT/chrono-install/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

exec "$PYTHON" "$SCRIPT_DIR/lunar_excavator_crm_fea.py" "$@"
