#!/usr/bin/env bash
# Run the GPU DEME port.  All arguments are forwarded to lunar_excavator_deme.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "${PYTHON:-}" && -x "$PROJECT_ROOT/deme-env/bin/python" ]]; then
    PYTHON="$PROJECT_ROOT/deme-env/bin/python"
elif [[ -z "${PYTHON:-}" && -x "/tmp/opencode/deme-venv/bin/python" ]]; then
    PYTHON="/tmp/opencode/deme-venv/bin/python"
fi
PYTHON="${PYTHON:-python3}"

# Optional DEME caches.  Create the directories once for faster cold starts.
export DEME_JIT_CACHE_DIR="${DEME_JIT_CACHE_DIR:-$HOME/.cache/deme/kernels}"
export DEME_PERSISTENT_JITIFY_CACHE="${DEME_PERSISTENT_JITIFY_CACHE:-$HOME/.cache/deme/jitify_header_cache.bin}"
mkdir -p "$(dirname "$DEME_JIT_CACHE_DIR")" "$(dirname "$DEME_PERSISTENT_JITIFY_CACHE")"

exec "$PYTHON" "$SCRIPT_DIR/lunar_excavator_deme.py" "$@"
