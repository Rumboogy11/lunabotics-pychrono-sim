#!/usr/bin/env bash
# Run the Chrono::CRM excavator design driver.  All arguments are forwarded.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/chrono_runtime_env.sh"

# Expose project-local tools such as pvpython without requiring activation.
if [[ -d "$PROJECT_ROOT/chrono-env/bin" ]]; then
    PATH="$PROJECT_ROOT/chrono-env/bin:$PATH"
    export PATH
fi

if [[ -z "${PYTHON:-}" && -x "$PROJECT_ROOT/chrono-env/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/chrono-env/bin/python"
fi
PYTHON="${PYTHON:-python3}"

if [[ -d "$PROJECT_ROOT/chrono-install/share/chrono/python" ]]; then
    export PYTHONPATH="$PROJECT_ROOT/chrono-install/share/chrono/python${PYTHONPATH:+:$PYTHONPATH}"
fi
export LD_LIBRARY_PATH="$PROJECT_ROOT/chrono-install/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
chrono_use_local_runtime

exec "$PYTHON" "$SCRIPT_DIR/lunar_excavator_crm.py" "$@"
