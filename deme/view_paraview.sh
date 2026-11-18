#!/usr/bin/env bash
# Optional ParaView GUI launcher for DEME output.
#
# This is intentionally separate from view.sh: ParaView is only one possible
# post-processing viewer and is not required by either solver.  The ParaView
# executable is used (not plain python3) so its Qt event loop stays alive after
# visualise.py has built the pipeline.  Arguments are forwarded to the script
# through environment variables because ParaView's --script option does not
# forward script arguments.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARAVIEW="${PARAVIEW:-paraview}"
OUTPUT_DIR="Output_DEME_LunarExcavator"
NO_RENDER=0

usage() {
    cat <<EOF
Usage: $0 [--output-dir DIR] [--no-render]

Optional ParaView viewer for output written by lunar_excavator_deme.py.
Set PARAVIEW=/path/to/paraview if it is not on PATH.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output-dir)
            OUTPUT_DIR="${2:?--output-dir needs a value}"
            shift 2
            ;;
        --output-dir=*)
            OUTPUT_DIR="${1#*=}"
            shift
            ;;
        --no-render)
            NO_RENDER=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unsupported view_paraview.sh argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if ! command -v "$PARAVIEW" >/dev/null 2>&1; then
    echo "ParaView executable not found: $PARAVIEW" >&2
    echo "Install ParaView or set PARAVIEW=/path/to/paraview." >&2
    echo "This is optional; postprocess.sh does not need it." >&2
    exit 127
fi

# ParaView 6.x warns about Wayland and can crash in Qt 6 style code on some
# desktops.  Prefer XCB when a Wayland session is detected; users can override
# with QT_QPA_PLATFORM (or QT_STYLE_OVERRIDE) in their shell.
if [[ -n "${WAYLAND_DISPLAY:-}" && -z "${QT_QPA_PLATFORM:-}" ]]; then
    export QT_QPA_PLATFORM=xcb
    echo "Note: QT_QPA_PLATFORM=xcb selected for ParaView (override if needed)." >&2
fi

LUNAR_OUTPUT_DIR="$OUTPUT_DIR" LUNAR_NO_RENDER="$NO_RENDER" \
    exec "$PARAVIEW" --script="$SCRIPT_DIR/visualise.py"
