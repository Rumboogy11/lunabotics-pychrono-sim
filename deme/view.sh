#!/usr/bin/env bash
# Viewer entry point for DEME output.
#
# Default: a lightweight Matplotlib/Tk playback viewer (view_frames.py).  It
# reads the CSV frames directly and does not need ParaView or PyChrono's GUI
# modules.  ParaView remains available explicitly with --paraview because the
# original MercuryDPM helper used it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODE="frames"
OUTPUT_DIR="Output_DEME_LunarExcavator"
FRAME_ARGS=()
PARAVIEW_ARGS=()

usage() {
    cat <<EOF
Usage: $0 [options] [viewer options]

Default viewer: Matplotlib/Tk frame playback.

Common options:
  --output-dir DIR   frame directory (default: $OUTPUT_DIR)
  --paraview         use the optional ParaView GUI instead
  --backend NAME     frames (default) or paraview
  --no-render        headless scene check; no window is opened
  -h, --help         show this message

Matplotlib viewer options (passed through when using the default viewer):
  --stride N         plot every Nth grain (default: 8)
  --interval MS      nominal ms per frame (default: 100)
  --start N          first frame index
  --stop N           stop before this frame index
  --fps F            output fps when summary.csv is unavailable
  --point-size S     grain marker size
  --mesh NAME        coarse (default), medium, or deme (legacy simplified) visualization OBJ
  --drum-source NAME auto (default), vtk (force recorded pose), or analytic
  --drum-stride N    plot every Nth drum face (default: 1)
  --save PATH        save the selected frame as PNG and exit
  --no-drum          hide the drum
  --no-walls         hide the retaining-wall box
  --no-repeat        stop at the last frame

ParaView is optional and may be unstable on Qt 6 / Wayland desktops:
  $0 --paraview --output-dir $OUTPUT_DIR
EOF
}

usage_error() {
    echo "$1" >&2
    usage >&2
    exit 2
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --paraview)
            MODE="paraview"
            shift
            ;;
        --backend)
            MODE="${2:?--backend needs a value}"
            shift 2
            ;;
        --backend=*)
            MODE="${1#*=}"
            shift
            ;;
        --output-dir)
            OUTPUT_DIR="${2:?--output-dir needs a value}"
            FRAME_ARGS+=(--output-dir "$OUTPUT_DIR")
            PARAVIEW_ARGS+=(--output-dir "$OUTPUT_DIR")
            shift 2
            ;;
        --output-dir=*)
            OUTPUT_DIR="${1#*=}"
            FRAME_ARGS+=(--output-dir "$OUTPUT_DIR")
            PARAVIEW_ARGS+=(--output-dir "$OUTPUT_DIR")
            shift
            ;;
        --no-render)
            FRAME_ARGS+=(--no-render)
            PARAVIEW_ARGS+=(--no-render)
            shift
            ;;
        --stride|--interval|--start|--stop|--fps|--point-size|--drum-stride|--mesh|--drum-source|--save)
            FRAME_ARGS+=("$1" "${2:?$1 needs a value}")
            shift 2
            ;;
        --no-drum|--no-walls|--no-repeat)
            FRAME_ARGS+=("$1")
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            if [[ "$MODE" == "frames" ]]; then
                FRAME_ARGS+=("$1")
                shift
            else
                usage_error "Unsupported $MODE viewer argument: $1"
            fi
            ;;
    esac
done

case "$MODE" in
    frames)
        # Prefer the project-local DEME environment, then the old /tmp fallback.
        DEME_PYTHON="$(cd "$SCRIPT_DIR/.." && pwd)/deme-env/bin/python"
        if [[ ! -x "$DEME_PYTHON" ]]; then
            DEME_PYTHON="/tmp/opencode/deme-venv/bin/python"
        fi
        PYTHON="${PYTHON:-$([ -x "$DEME_PYTHON" ] && echo "$DEME_PYTHON" || echo python3)}"
        export MPLBACKEND="${MPLBACKEND:-TkAgg}"
        exec "$PYTHON" "$SCRIPT_DIR/view_frames.py" "${FRAME_ARGS[@]}"
        ;;
    paraview)
        exec "$SCRIPT_DIR/view_paraview.sh" "${PARAVIEW_ARGS[@]}"
        ;;
    *)
        usage_error "Unknown backend: $MODE"
        ;;
esac
