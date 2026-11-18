#!/usr/bin/env bash
# Run the combined rigid CRM + solid FEA production model.
# All arguments are forwarded to full_model.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${PYTHON:-}" && -x "$SCRIPT_DIR/chrono-env/bin/python" ]]; then
    PYTHON="$SCRIPT_DIR/chrono-env/bin/python"
fi
PYTHON="${PYTHON:-python3}"

exec "$PYTHON" "$SCRIPT_DIR/full_model.py" "$@"
