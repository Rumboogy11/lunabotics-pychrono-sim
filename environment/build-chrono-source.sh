#!/usr/bin/env bash
# Configure, build, and install the project-local Chrono 10.0.0 source build.
#
# Usage:
#   ./environment/build-chrono-source.sh [--clean] [--jobs N]
#
# Expected local layout:
#   chrono-src/      Chrono 10.0.0 source checkout
#   chrono-env/      local Python/CMake/CUDA/SWIG toolchain
#   chrono-build/    CMake build tree (created here)
#   chrono-install/  install prefix (created here)
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$PROJECT_ROOT/environment/activate-chrono-source.sh"

CLEAN=0
JOBS="$(nproc)"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --clean) CLEAN=1; shift ;;
    --jobs)
      JOBS="${2:?--jobs requires a number}"
      shift 2
      ;;
    -h|--help)
      sed -n '2,12p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$CHRONO_SRC/CMakeLists.txt" ]]; then
  echo "Missing Chrono source at $CHRONO_SRC" >&2
  exit 1
fi

if [[ "$CLEAN" -eq 1 ]]; then
  rm -rf "$CHRONO_BUILD" "$CHRONO_INSTALL"
fi

cmake -S "$CHRONO_SRC" -B "$CHRONO_BUILD" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
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
  -DSWIG_DIR="$CHRONO_ENV/share/swig/4.5.1" \
  -DCMAKE_CUDA_COMPILER="$CHRONO_ENV/bin/nvcc" \
  -DCMAKE_CUDA_HOST_COMPILER="$CHRONO_ENV/bin/x86_64-conda-linux-gnu-g++" \
  -DCUDAToolkit_ROOT="$CHRONO_ENV" \
  -DCHRONO_CUDA_ARCHITECTURES=120

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
