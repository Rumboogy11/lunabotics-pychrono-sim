#!/usr/bin/env bash
# Launch the interactive CRM -> one-way FEA study wizard.
# All arguments are forwarded to study_wizard.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${PYTHON:-}" && -x "$SCRIPT_DIR/chrono-env/bin/python" ]]; then
    PYTHON="$SCRIPT_DIR/chrono-env/bin/python"
fi
PYTHON="${PYTHON:-python3}"

exec "$PYTHON" "$SCRIPT_DIR/study_wizard.py" "$@"
