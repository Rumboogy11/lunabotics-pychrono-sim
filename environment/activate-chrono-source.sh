#!/usr/bin/env bash
# Source this file to use the project-local Chrono build environment.
#   source environment/activate-chrono-source.sh

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export CHRONO_PROJECT_ROOT="$PROJECT_ROOT"
export CHRONO_ENV="$PROJECT_ROOT/chrono-env"
export CHRONO_SRC="$PROJECT_ROOT/chrono-src"
export CHRONO_BUILD="$PROJECT_ROOT/chrono-build"
export CHRONO_INSTALL="$PROJECT_ROOT/chrono-install"

export PATH="$CHRONO_ENV/bin:$PATH"
export CUDA_HOME="$CHRONO_ENV"
export CUDAToolkit_ROOT="$CHRONO_ENV"
export CUDACXX="$CHRONO_ENV/bin/nvcc"
export CUDAHOSTCXX="$CHRONO_ENV/bin/x86_64-conda-linux-gnu-g++"
export SWIG_LIB="$CHRONO_ENV/share/swig/4.5.1"
export CC="$CHRONO_ENV/bin/x86_64-conda-linux-gnu-gcc"
export CXX="$CHRONO_ENV/bin/x86_64-conda-linux-gnu-g++"
export CMAKE_PREFIX_PATH="$CHRONO_ENV${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
export LD_LIBRARY_PATH="$CHRONO_INSTALL/lib:$CHRONO_ENV/lib:$CHRONO_ENV/targets/x86_64-linux/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$CHRONO_INSTALL/share/chrono/python:$CHRONO_ENV/lib/python3.12/site-packages${PYTHONPATH:+:$PYTHONPATH}"
