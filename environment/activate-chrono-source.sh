#!/usr/bin/env bash
# Source this file to use the project-local Chrono build environment.
#   source environment/activate-chrono-source.sh
#
# The installer (./install.sh) creates all directories referenced here.

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CHRONO_PROJECT_ROOT="$PROJECT_ROOT"

# On NTFS/FUSE checkouts the installer places these directories on a native
# filesystem and symlinks them here. Resolve them so CMake sees real paths.
chrono_realpath() {
  if command -v readlink >/dev/null 2>&1 && readlink -f "$1" >/dev/null 2>&1; then
    readlink -f "$1"
  else
    printf '%s\n' "$1"
  fi
}
export CHRONO_ENV="$(chrono_realpath "$PROJECT_ROOT/chrono-env")"
export CHRONO_SRC="$(chrono_realpath "$PROJECT_ROOT/chrono-src")"
export CHRONO_BUILD="$(chrono_realpath "$PROJECT_ROOT/chrono-build")"
export CHRONO_INSTALL="$(chrono_realpath "$PROJECT_ROOT/chrono-install")"

export PATH="$CHRONO_ENV/bin:$PATH"

# Use half of the available logical CPUs for OpenMP work by default. An explicit
# OMP_NUM_THREADS from the caller always wins.
if [[ -z "${OMP_NUM_THREADS:-}" ]]; then
  _chrono_cpu_count="$(nproc 2>/dev/null || printf '1\n')"
  [[ "$_chrono_cpu_count" =~ ^[0-9]+$ ]] || _chrono_cpu_count=1
  OMP_NUM_THREADS=$(( _chrono_cpu_count / 2 ))
  (( OMP_NUM_THREADS < 1 )) && OMP_NUM_THREADS=1
  export OMP_NUM_THREADS
fi
unset _chrono_cpu_count

# CUDA 12.9 toolkit lives inside chrono-env, so the driver is not needed at
# build time (only a compatible NVIDIA driver is needed to run simulations).
export CUDA_HOME="$CHRONO_ENV"
export CUDAToolkit_ROOT="$CHRONO_ENV"
export CUDACXX="${CUDACXX:-$CHRONO_ENV/bin/nvcc}"

chrono_swig_dir() {
  local swig="$CHRONO_ENV/bin/swig" reported version candidate
  [[ -x "$swig" ]] || return 1
  reported="$("$swig" -swiglib 2>/dev/null || true)"
  if [[ -n "$reported" && -d "$reported" ]]; then
    printf '%s\n' "$reported"
    return 0
  fi
  version="$("$swig" -version 2>/dev/null | awk '/^SWIG Version/ {print $3; exit}')"
  for candidate in "$CHRONO_ENV/share/swig/$version" "$CHRONO_ENV/share/swig"; do
    if [[ -n "$candidate" && -d "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

# SWIG's reported -swiglib path can be a nonexistent build-tree path in conda
# packages, so only trust it when it exists; otherwise derive it locally.
if [[ -z "${SWIG_LIB:-}" || ! -d "${SWIG_LIB:-}" ]]; then
  if resolved_swig_dir="$(chrono_swig_dir)"; then
    export SWIG_LIB="$resolved_swig_dir"
  else
    unset SWIG_LIB
  fi
fi

# Prefer the conda-forge compiler wrappers when present, otherwise fall back
# to the system compiler. Chrono's CMake configuration accepts either.
if [[ -x "$CHRONO_ENV/bin/x86_64-conda-linux-gnu-gcc" ]]; then
  export CC="$CHRONO_ENV/bin/x86_64-conda-linux-gnu-gcc"
fi
if [[ -x "$CHRONO_ENV/bin/x86_64-conda-linux-gnu-g++" ]]; then
  export CXX="$CHRONO_ENV/bin/x86_64-conda-linux-gnu-g++"
fi
if [[ -z "${CC:-}" && -x /usr/bin/gcc ]]; then
  export CC=/usr/bin/gcc
fi
if [[ -z "${CXX:-}" && -x /usr/bin/g++ ]]; then
  export CXX=/usr/bin/g++
fi
if [[ -z "${CUDAHOSTCXX:-}" && -n "${CXX:-}" ]]; then
  export CUDAHOSTCXX="$CXX"
fi

export CMAKE_PREFIX_PATH="$CHRONO_ENV${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export LD_LIBRARY_PATH="$CHRONO_INSTALL/lib:$CHRONO_ENV/lib:$CHRONO_ENV/targets/x86_64-linux/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$CHRONO_INSTALL/share/chrono/python:$CHRONO_ENV/lib/python3.12/site-packages${PYTHONPATH:+:$PYTHONPATH}"

# GPU architecture for Chrono's CUDA kernels. The installer detects the local
# GPU compute capability and writes this value into chrono-env/etc; fall back
# to sm_120 (RTX 5070 Ti) when no value was detected.
if [[ -z "${CHRONO_CUDA_ARCHITECTURES:-}" && -f "$CHRONO_ENV/etc/chrono-cuda-arch" ]]; then
  CHRONO_CUDA_ARCHITECTURES="$(<"$CHRONO_ENV/etc/chrono-cuda-arch")"
fi
export CHRONO_CUDA_ARCHITECTURES="${CHRONO_CUDA_ARCHITECTURES:-120}"
