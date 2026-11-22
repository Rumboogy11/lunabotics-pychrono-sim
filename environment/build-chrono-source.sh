#!/usr/bin/env bash
# Configure, build, and install the project-local Chrono 10.0.0 source build.
#
# Usage:
#   ./environment/build-chrono-source.sh [--clean] [--jobs N] [--cuda-arch ARCH]
#
# Expected local layout (created by ./install.sh):
#   chrono-src/      custom Chrono fork checkout with the CRM fixes
#   chrono-env/      local Python/CMake/CUDA/SWIG toolchain
#   chrono-build/    CMake build tree (created here)
#   chrono-install/  install prefix (created here)
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_ROOT/environment/activate-chrono-source.sh"

CLEAN=0
JOBS="$(env -u OMP_NUM_THREADS nproc 2>/dev/null || printf '1\n')"
[[ "$JOBS" =~ ^[0-9]+$ ]] || JOBS=1
JOBS=$(( JOBS / 2 ))
(( JOBS < 1 )) && JOBS=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) CLEAN=1; shift ;;
    --jobs)
      JOBS="${2:?--jobs requires a number}"
      shift 2
      ;;
    --cuda-arch)
      CHRONO_CUDA_ARCHITECTURES="${2:?--cuda-arch requires a value such as 120}"
      CHRONO_CUDA_ARCHITECTURES="${CHRONO_CUDA_ARCHITECTURES//[^0-9]/}"
      [[ -n "$CHRONO_CUDA_ARCHITECTURES" ]] || {
        echo "--cuda-arch requires digits (for example 120)" >&2
        exit 2
      }
      export CHRONO_CUDA_ARCHITECTURES
      shift 2
      ;;
    -h|--help)
      sed -n '2,11p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$CHRONO_SRC/CMakeLists.txt" ]]; then
  echo "Missing Chrono source at $CHRONO_SRC (run ./install.sh first)" >&2
  exit 1
fi

if [[ "$CLEAN" -eq 1 ]]; then
  rm -rf "$CHRONO_BUILD" "$CHRONO_INSTALL"
fi

HOST_CXX="${CUDAHOSTCXX:-$CXX}"
if [[ ! -x "$HOST_CXX" ]]; then
  echo "CUDA host compiler not found: $HOST_CXX" >&2
  exit 1
fi

SWIG_DIR="${SWIG_LIB:-}"
if [[ ! -d "$SWIG_DIR" ]] && declare -F chrono_swig_dir >/dev/null; then
  SWIG_DIR="$(chrono_swig_dir || true)"
fi
if [[ ! -d "$SWIG_DIR" ]]; then
  echo "Could not determine SWIG library directory" >&2
  exit 1
fi

# GCC 13.4 is the tested conda-forge toolchain for this Chrono revision.
# GCC 14.4 has internal compiler errors in several sources even at -O2, so keep
# -O2 unless a different toolchain is known to handle another setting.
CHRONO_CXX_RELEASE_FLAGS="${CHRONO_CXX_RELEASE_FLAGS:--O2 -DNDEBUG}"

cmake -S "$CHRONO_SRC" -B "$CHRONO_BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_CXX_FLAGS_RELEASE="$CHRONO_CXX_RELEASE_FLAGS" \
  -DCMAKE_C_FLAGS_RELEASE="${CHRONO_C_RELEASE_FLAGS:--O2 -DNDEBUG}" \
  -DCMAKE_INSTALL_PREFIX="$CHRONO_INSTALL" \
  -DBUILD_DEMOS=OFF \
  -DBUILD_TESTING=OFF \
  -DBUILD_BENCHMARKING=OFF \
  -DCMAKE_DISABLE_FIND_PACKAGE_MPI=TRUE \
  -DCH_ENABLE_MODULE_FSI=ON \
  -DCH_ENABLE_MODULE_FSI_SPH=ON \
  -DCH_ENABLE_MODULE_VEHICLE=ON \
  -DCH_ENABLE_MODULE_PARSERS=ON \
  -DCH_ENABLE_MODULE_PYTHON=ON \
  -DCH_ENABLE_MODULE_IRRLICHT=OFF \
  -DCH_ENABLE_MODULE_VSG=OFF \
  -DCH_ENABLE_MODULE_SENSOR=OFF \
  -DCH_ENABLE_MODULE_POSTPROCESS=OFF \
  -DPython3_EXECUTABLE="$CHRONO_ENV/bin/python" \
  -DOpenMP_gomp_LIBRARY="$CHRONO_ENV/lib/libgomp.so" \
  -DSWIG_EXECUTABLE="$CHRONO_ENV/bin/swig" \
  -DSWIG_DIR="$SWIG_DIR" \
  -DCMAKE_CUDA_COMPILER="$CHRONO_ENV/bin/nvcc" \
  -DCMAKE_CUDA_HOST_COMPILER="$HOST_CXX" \
  -DCUDAToolkit_ROOT="$CHRONO_ENV" \
  -DCHRONO_CUDA_ARCHITECTURES="$CHRONO_CUDA_ARCHITECTURES"

cmake --build "$CHRONO_BUILD" --parallel "$JOBS"
cmake --install "$CHRONO_BUILD"

PYTHONPATH="$CHRONO_INSTALL/share/chrono/python${PYTHONPATH:+:$PYTHONPATH}" \
LD_LIBRARY_PATH="$CHRONO_INSTALL/lib:${LD_LIBRARY_PATH:-}" \
  "$CHRONO_ENV/bin/python" - <<'PY'
import pychrono.core as chrono
import pychrono.fsi as fsi
import pychrono.vehicle as veh

print("Chrono Python modules imported from source build")
print("  core:   ", chrono.__file__)
print("  fsi:    ", fsi.__file__)
print("  vehicle:", veh.__file__)
print("  CRMTerrain:", veh.CRMTerrain)
PY

echo "Local source build installed at $CHRONO_INSTALL"
echo "CUDA architecture: sm_${CHRONO_CUDA_ARCHITECTURES}"
