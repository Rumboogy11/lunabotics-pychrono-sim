#!/usr/bin/env bash
# Install the Lunar bucket-drum excavator simulation tools on a new Linux host.
#
# Supported distributions: Arch Linux (and derivatives such as CachyOS) and
# Debian/Ubuntu. Everything except a few small system packages is installed
# project-locally, so no global Python environment is modified.
#
# Usage:
#   ./install.sh                       # full install (system packages + env + Chrono build)
#   ./install.sh --no-system-packages  # only use tools already on PATH
#   ./install.sh --with-paraview       # also install ParaView via the system package manager
#   ./install.sh --clean               # rebuild Chrono from scratch (keeps chrono-env)
#   ./install.sh --skip-build          # install env/source/deps only
#   ./install.sh --work-root /path     # store env/build outside the checkout
#
# See environment/INSTALL.md for details and troubleshooting.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ENV_FILE="$PROJECT_ROOT/environment/environment.yml"
PATCH_FILE="$PROJECT_ROOT/environment/patches/chrono-local-crm-sph.patch"

# Conda packages contain symlinks and case-sensitive paths. Filesystems such as
# NTFS/exFAT/FUSE cannot host them, so use a native work directory and symlink
# the expected project paths back into the checkout.
WORK_ROOT_OVERRIDE="${PYCHRONO_WORK_ROOT:-}"
fstype="$(findmnt -no FSTYPE -T "$PROJECT_ROOT" 2>/dev/null || stat -f -c %T "$PROJECT_ROOT" 2>/dev/null || true)"
case "$fstype" in
  ntfs*|*7366746e*|fuseblk*|fuse*|exfat*|vfat*|cifs*|9p*) NEEDS_NATIVE_STORAGE=1 ;;
  *) NEEDS_NATIVE_STORAGE=0 ;;
esac

# Custom Chrono fork with the CRM/SPH fixes used by this project.
TESTED_CHRONO_REF="757a75b3c33af1cd4c42e6e21c30a24a0cecbfe0"
CHRONO_REPO="${CHRONO_REPO:-https://github.com/Rumboogy11/chrono.git}"
CHRONO_REF="${CHRONO_REF:-$TESTED_CHRONO_REF}"
MICROMAMBA_VERSION="2.10.0"
MICROMAMBA_SHA256="9a97976244929bbae28fb1fae504e74a3e7e6d1932870e3a03321f01b7fbd07b"

JOBS="$(nproc)"
CUDA_ARCH=""
SYSTEM_PACKAGES=1
INSTALL_PARAVIEW=0
CLEAN=0
SKIP_BUILD=0
SKIP_PATCH=0

# Chrono's Eigen-heavy translation units need a lot of memory, but the default
# build parallelism is deliberately half the available threads. Users on smaller
# hosts can pick a lower value explicitly with --jobs (for example --jobs 1).
cap_build_jobs() {
  # nproc honours OMP_NUM_THREADS; ignore it so re-running from an already
  # activated environment still sees the machine's real CPU count.
  local total_threads thread_jobs
  total_threads="$(env -u OMP_NUM_THREADS nproc 2>/dev/null || printf '1\n')"
  [[ "$total_threads" =~ ^[0-9]+$ ]] || total_threads=1
  thread_jobs=$(( total_threads / 2 ))
  (( thread_jobs < 1 )) && thread_jobs=1

  if (( build_jobs > thread_jobs )); then
    warn "Reducing Chrono build parallelism from $build_jobs to $thread_jobs (half of $total_threads threads)"
    build_jobs="$thread_jobs"
  fi
}

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }
die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-system-packages) SYSTEM_PACKAGES=0; shift ;;
    --with-paraview) INSTALL_PARAVIEW=1; shift ;;
    --clean) CLEAN=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    --skip-patch) SKIP_PATCH=1; shift ;;
    --jobs)
      JOBS="${2:?--jobs requires a number}"
      shift 2
      ;;
    --cuda-arch)
      CUDA_ARCH="${2:?--cuda-arch requires a value such as 120}"
      [[ "$CUDA_ARCH" =~ ^[0-9]+$ ]] || die "Invalid --cuda-arch value: $CUDA_ARCH"
      shift 2
      ;;
    --chrono-repo)
      CHRONO_REPO="${2:?--chrono-repo requires a URL}"
      shift 2
      ;;
    --chrono-ref)
      CHRONO_REF="${2:?--chrono-ref requires a git ref}"
      shift 2
      ;;
    --work-root)
      WORK_ROOT_OVERRIDE="${2:?--work-root requires a directory}"
      shift 2
      ;;
    -h|--help)
      sed -n '2,16p' "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      die "Unknown argument: $1 (try --help)"
      ;;
  esac
done

[[ -z "$CUDA_ARCH" || "$CUDA_ARCH" =~ ^[0-9]+$ ]] || die "Invalid --cuda-arch value"
[[ "$JOBS" =~ ^[1-9][0-9]*$ ]] || die "Invalid --jobs value"
build_jobs="$JOBS"

command -v git >/dev/null 2>&1 || die "git is required but not found"
command -v curl >/dev/null 2>&1 || die "curl is required but not found"
command -v tar >/dev/null 2>&1 || die "tar is required but not found"
command -v bzip2 >/dev/null 2>&1 || die "bzip2 is required to unpack micromamba but not found"
command -v sha256sum >/dev/null 2>&1 || die "sha256sum is required to verify micromamba but not found"
command -v nproc >/dev/null 2>&1 || die "nproc is required to size the build but not found"
command -v awk >/dev/null 2>&1 || die "awk is required by the installer but not found"

if [[ -n "$WORK_ROOT_OVERRIDE" ]]; then
  WORK_ROOT="$WORK_ROOT_OVERRIDE"
elif [[ "$NEEDS_NATIVE_STORAGE" -eq 1 ]]; then
  project_key="$(printf '%s' "$PROJECT_ROOT" | sha256sum | cut -c1-12)"
  WORK_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/pychrono/$project_key"
  log "Project root is on $fstype; storing build artifacts in $WORK_ROOT"
else
  WORK_ROOT="$PROJECT_ROOT"
fi
mkdir -p "$WORK_ROOT"
WORK_ROOT="$(cd "$WORK_ROOT" && pwd -P)"
work_fstype="$(findmnt -no FSTYPE -T "$WORK_ROOT" 2>/dev/null || stat -f -c %T "$WORK_ROOT" 2>/dev/null || true)"
case "$work_fstype" in
  ntfs*|*7366746e*|fuseblk*|fuse*|exfat*|vfat*|cifs*|9p*)
    warn "Work root $WORK_ROOT is on $work_fstype; conda packages may fail to extract."
    warn "Use --work-root on a native filesystem (for example under \$HOME)."
    ;;
esac
ENV_DIR="$WORK_ROOT/chrono-env"
SRC_DIR="$WORK_ROOT/chrono-src"
BUILD_DIR="$WORK_ROOT/chrono-build"
INSTALL_DIR="$WORK_ROOT/chrono-install"
TOOLS_DIR="$WORK_ROOT/.tools"
MAMBA_ROOT="$WORK_ROOT/.mamba"

link_project_paths() {
  local name target current
  # Nothing to link when the work root is the checkout itself.
  [[ "$WORK_ROOT" != "$PROJECT_ROOT" ]] || return 0

  for name in chrono-env chrono-src chrono-build chrono-install .tools .mamba; do
    target="$PROJECT_ROOT/$name"
    if [[ -L "$target" ]]; then
      current="$(readlink "$target" || true)"
      if [[ "$current" != "$WORK_ROOT/$name" ]]; then
        rm -f "$target"
        ln -s "$WORK_ROOT/$name" "$target"
      fi
      continue
    fi
    if [[ -e "$target" ]]; then
      die "$target already exists as a real path; move or remove it before using --work-root $WORK_ROOT"
    fi
    ln -s "$WORK_ROOT/$name" "$target"
  done
}

os_id=""
os_like=""
if [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  os_id="${ID:-}"
  os_like="${ID_LIKE:-}"
fi

sudo_cmd=()
if [[ "$EUID" -ne 0 ]]; then
  if command -v sudo >/dev/null 2>&1 && sudo -n true 2>/dev/null; then
    sudo_cmd=(sudo -n)
  fi
fi

install_system_packages() {
  if [[ "$SYSTEM_PACKAGES" -eq 0 ]]; then
    log "Skipping system packages (--no-system-packages)"
    return
  fi
  if [[ "$EUID" -ne 0 && "${#sudo_cmd[@]}" -eq 0 ]]; then
    warn "No root/sudo access: skipping system packages."
    warn "Install git, curl, bzip2, python3, python3-tk and basic X11/GL libraries manually,"
    warn "or re-run this script as root. The project-local env will still be created."
    return
  fi

  case " $os_like $os_id " in
    *arch*|*cachyos*)
      log "Installing Arch system prerequisites"
      packages=(git curl bzip2 tar python tk libgl glu libx11 libxext libxrender libxkbcommon glib2 xdg-utils)
      if [[ "$INSTALL_PARAVIEW" -eq 1 ]]; then
        packages+=(paraview)
      fi
      "${sudo_cmd[@]}" pacman -S --needed --noconfirm "${packages[@]}"
      ;;
    *debian*|*ubuntu*)
      log "Installing Debian/Ubuntu system prerequisites"
      "${sudo_cmd[@]}" apt-get update
      local glib_pkg="libglib2.0-0"
      if apt-cache show libglib2.0-0t64 >/dev/null 2>&1 \
        && ! apt-cache show libglib2.0-0 >/dev/null 2>&1; then
        glib_pkg="libglib2.0-0t64"
      fi
      packages=(git curl bzip2 tar python3 python3-tk libgl1 libglu1-mesa libx11-6 libxext6 libxrender1 libxkbcommon0 "$glib_pkg" xdg-utils)
      if [[ "$INSTALL_PARAVIEW" -eq 1 ]]; then
        packages+=(paraview)
      fi
      "${sudo_cmd[@]}" apt-get install -y --no-install-recommends "${packages[@]}"
      ;;
    *)
      warn "Unsupported distribution (${os_id:-unknown}); skipping system packages."
      warn "Required tools: git, curl, tar, bzip2, python3, python3-tk, X11/GL runtime libraries."
      ;;
  esac
}

bootstrap_micromamba() {
  mkdir -p "$TOOLS_DIR/bin"
  local mamba="$TOOLS_DIR/bin/micromamba"
  if [[ ! -x "$mamba" ]]; then
    log "Downloading micromamba $MICROMAMBA_VERSION"
    local archive="$TOOLS_DIR/micromamba-${MICROMAMBA_VERSION}.tar.bz2"
    curl -fsSL "https://micro.mamba.pm/api/micromamba/linux-64/${MICROMAMBA_VERSION}" -o "$archive"
    printf '%s  %s\n' "$MICROMAMBA_SHA256" "$archive" | sha256sum -c -
    tar -xjf "$archive" -C "$TOOLS_DIR" bin/micromamba
    rm -f "$archive"
  fi
  MAMBA_EXE="$mamba"
}

create_environment() {
  local mamba="$1"
  mkdir -p "$MAMBA_ROOT"
  export MAMBA_ROOT_PREFIX="$MAMBA_ROOT"

  if [[ ! -x "$ENV_DIR/bin/python" ]]; then
    log "Creating project environment at $ENV_DIR (this downloads ~2 GB)"
    "$mamba" create -y -p "$ENV_DIR" -f "$ENV_FILE"
  else
    log "Updating project environment at $ENV_DIR"
    "$mamba" install -y -p "$ENV_DIR" -f "$ENV_FILE"
  fi

  # The conda-forge pychrono package puts its own libChrono_*.so files in
  # chrono-env/lib; Python's RPATH loads those instead of the source build.
  # Remove it if an older environment still has it.
  if compgen -G "$ENV_DIR/conda-meta/pychrono-*.json" >/dev/null; then
    log "Removing conda-forge pychrono (conflicts with the local source build)"
    "$mamba" remove -y -p "$ENV_DIR" pychrono
  fi

  local detected=""
  if [[ -z "$CUDA_ARCH" ]] && command -v nvidia-smi >/dev/null 2>&1; then
    detected="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n 1 || true)"
    detected="$(printf '%s' "$detected" | tr -cd '0-9')"
  fi
  if [[ -z "$CUDA_ARCH" ]]; then
    if [[ -n "$detected" ]]; then
      CUDA_ARCH="$detected"
    else
      CUDA_ARCH="120"
      warn "Could not detect GPU compute capability; using sm_120 (RTX 5070 Ti)."
      warn "Pass --cuda-arch NNN if your GPU differs."
    fi
  fi
  [[ "$CUDA_ARCH" =~ ^[0-9]+$ ]] || die "Invalid CUDA architecture: $CUDA_ARCH"
  mkdir -p "$ENV_DIR/etc"
  printf '%s\n' "$CUDA_ARCH" > "$ENV_DIR/etc/chrono-cuda-arch"
  log "Chrono CUDA architecture: sm_$CUDA_ARCH"
}

fetch_chrono_source() {
  if [[ -f "$SRC_DIR/CMakeLists.txt" ]]; then
    log "Using existing Chrono source at $SRC_DIR"
    return
  fi

  log "Cloning custom Chrono fork at $CHRONO_REF"
  rm -rf "$SRC_DIR"
  mkdir -p "$SRC_DIR"
  git -C "$SRC_DIR" init -q
  git -C "$SRC_DIR" remote add origin "$CHRONO_REPO"
  git -C "$SRC_DIR" fetch -q --depth 1 origin "$CHRONO_REF"
  git -C "$SRC_DIR" checkout -q --detach FETCH_HEAD
}

verify_chrono_source() {
  if [[ ! -d "$SRC_DIR/.git" ]]; then
    die "Chrono source at $SRC_DIR is not a git checkout; move it aside or pass --work-root"
  fi

  local head expected
  head="$(git -C "$SRC_DIR" rev-parse HEAD 2>/dev/null || true)"
  [[ -n "$head" ]] || die "Could not determine Chrono source revision at $SRC_DIR"

  # A commit SHA can be resolved locally, but branch/tag names need the remote
  # because the checkout is shallow and has no local ref for them.
  if git -C "$SRC_DIR" rev-parse --verify -q "${CHRONO_REF}^{commit}" >/dev/null 2>&1; then
    expected="$(git -C "$SRC_DIR" rev-parse "${CHRONO_REF}^{commit}")"
  else
    expected="$(git ls-remote "$CHRONO_REPO" "$CHRONO_REF" 2>/dev/null | awk 'NR == 1 { print $1 }')"
    [[ -n "$expected" ]] || die "Could not resolve Chrono ref $CHRONO_REF"
    expected="$(git -C "$SRC_DIR" rev-parse "${expected}^{commit}" 2>/dev/null || printf '%s' "$expected")"
  fi

  if [[ "$head" == "$expected" ]]; then
    return
  fi

  if git -C "$SRC_DIR" merge-base --is-ancestor "$expected" HEAD 2>/dev/null; then
    log "Chrono source is a descendant of $CHRONO_REF"
    return
  fi

  die "Chrono source at $SRC_DIR is at $head, expected $CHRONO_REF ($expected)"
}

apply_chrono_patch() {
  if [[ "$SKIP_PATCH" -eq 1 || ! -f "$PATCH_FILE" ]]; then
    return
  fi
  if [[ ! -d "$SRC_DIR/.git" ]]; then
    warn "Chrono source is not a git checkout; skipping local patch."
    return
  fi
  if git -C "$SRC_DIR" apply --check --reverse "$PATCH_FILE" >/dev/null 2>&1; then
    log "Local Chrono SPH patch already applied"
  elif git -C "$SRC_DIR" apply --check "$PATCH_FILE" >/dev/null 2>&1; then
    log "Applying local Chrono SPH patch"
    git -C "$SRC_DIR" apply "$PATCH_FILE"
  else
    die "Local Chrono SPH patch does not apply to this checkout."
  fi
}

prepare_reference_geometry() {
  # geometry/ is intentionally gitignored, but the smoke test needs the
  # reference drum OBJ. Generate it from the metre-scale STLs shipped in fea/.
  mkdir -p "$PROJECT_ROOT/geometry"
  local pairs=(
    "fea/LunarDrumScrew_m_fea_contact_2000_m.stl:geometry/LunarDrumScrew_m_coarse.obj"
    "fea/LunarDrumScrew_m_fea_contact_3000_m.stl:geometry/LunarDrumScrew_m_medium.obj"
  )
  local pair stl obj generated
  for pair in "${pairs[@]}"; do
    stl="${pair%%:*}"
    obj="${pair##*:}"
    [[ -f "$PROJECT_ROOT/$obj" ]] && continue
    if [[ ! -f "$PROJECT_ROOT/$stl" ]]; then
      warn "Reference mesh $stl not found; the CRM smoke test needs a drum OBJ"
      continue
    fi
    log "Generating $obj from $stl"
    (cd "$PROJECT_ROOT" && "$ENV_DIR/bin/python" stl_to_obj.py "$stl") >/dev/null
    generated="$PROJECT_ROOT/$(dirname "$stl")/$(basename "${stl%.stl}").obj"
    [[ -f "$generated" ]] || die "stl_to_obj.py did not produce $generated"
    mv -f "$generated" "$PROJECT_ROOT/$obj"
  done
}

verify_environment() {
  log "Verifying installation"
  # shellcheck disable=SC1091
  source "$PROJECT_ROOT/environment/activate-chrono-source.sh"

  if [[ "$SKIP_BUILD" -eq 1 ]]; then
    "$ENV_DIR/bin/python" - <<'PY'
import matplotlib
import numpy
import pyvista
import tetgen
import trimesh
import pymeshfix
import vtk
from OCP.BRepMesh import BRepMesh_IncrementalMesh

print("  Python dependencies verified (Chrono build skipped)")
PY
  else
    "$ENV_DIR/bin/python" - <<'PY'
import pychrono.core as core
import pychrono.fea as fea
import pychrono.fsi as fsi
import pychrono.vehicle as veh

import matplotlib
import numpy
import pyvista
import tetgen
import trimesh
import pymeshfix
import vtk
from OCP.BRepMesh import BRepMesh_IncrementalMesh

print("  pychrono: core, fea, fsi, vehicle")
print(f"  numpy {numpy.__version__}, pyvista {pyvista.__version__}, vtk {vtk.__version__}")
print(f"  trimesh {trimesh.__version__}, tetgen, pymeshfix, OCP, matplotlib")
print(f"  core module: {core.__file__}")
print("Installation verified.")
PY
  fi

  command -v nvcc >/dev/null 2>&1 || die "nvcc not found in activated environment"
  command -v swig >/dev/null 2>&1 || die "swig not found in activated environment"
}

link_project_paths
install_system_packages
bootstrap_micromamba
create_environment "$MAMBA_EXE"
prepare_reference_geometry
fetch_chrono_source
verify_chrono_source
apply_chrono_patch

if [[ "$CLEAN" -eq 1 ]]; then
  log "Removing previous Chrono build/install trees"
  rm -rf "$BUILD_DIR" "$INSTALL_DIR"
fi

if [[ "$SKIP_BUILD" -eq 1 ]]; then
  log "Skipping Chrono source build (--skip-build)"
else
  cap_build_jobs
  log "Building and installing custom Chrono with $build_jobs job(s)"
  "$PROJECT_ROOT/environment/build-chrono-source.sh" --jobs "$build_jobs" --cuda-arch "$CUDA_ARCH"
fi

verify_environment

log "Done. Activate the environment with:"
printf '  source %s\n' "$PROJECT_ROOT/environment/activate-chrono-source.sh"
printf '  %s --help\n' "$PROJECT_ROOT/run_study.sh"
