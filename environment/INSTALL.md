# Installation notes

The two ports have different Python/GPU requirements, so use **two separate
environments**. The commands below use [`uv`](https://docs.astral.sh/uv/), but
`python -m venv` + `python -m pip` works too.

## 1. GPU DEME environment (production port)

Requirements:

* Linux x86-64, NVIDIA GPU with a CUDA 12-capable driver (validated on an
  RTX 5070 Ti, driver 620.70.06),
* Python 3.10+ (validated on CPython 3.14),
* about 1–2 GB of disk for the `deme[cuda12]` wheel and its CUDA libraries.

```bash
uv venv --python 3.14 ./deme-env
uv pip install --python ./deme-env/bin/python "deme[cuda12]==3.1.0" matplotlib

# import check
./deme-env/bin/python -c 'import deme; print(deme.__version__)'

# meaningful setup + GPU check (deme/run.sh also selects ./deme-env by default)
PYTHON=./deme-env/bin/python ./deme/run.sh --smoke-test
```

The environment has been localized from `/tmp/opencode/deme-venv` to
`./deme-env`. The old `/tmp` path is only a fallback if the local copy is
missing.

`import deme` alone is not sufficient — the smoke test exercises NVRTC, kernel
resources, and the GPU driver. First-time JIT compilation can take a long time.
`deme/run.sh` sets these cache variables for you:

```bash
export DEME_JIT_CACHE_DIR="$HOME/.cache/deme/kernels"
export DEME_PERSISTENT_JITIFY_CACHE="$HOME/.cache/deme/jitify_header_cache.bin"
```

If compilation complains about CUDA headers, set `CUDA_HOME` to the toolkit that
matches the loaded `libnvrtc` (check with `nvcc --version` and
`ldd $(python -c 'import deme, pathlib; ...')` if needed). See DEME's
`docs/troubleshooting.rst` for details.

## 2. CPU PyChrono environment (demo port)

`pychrono==10.0.0rc1` currently publishes a CPython 3.12 manylinux wheel on
PyPI, so Python 3.12 is the simplest choice:

```bash
uv venv --python 3.12 ./pychrono-env
uv pip install --python ./pychrono-env/bin/python pychrono==10.0.0rc1

PYTHON=./pychrono-env/bin/python ./pychrono_core/run.sh --smoke-test
```

`./pychrono_core/run.sh` prefers the source build from section 3 when present,
then falls back to `./pychrono-env`. The old `/tmp/opencode/pychrono-venv` copy
is no longer needed.

Headless runs need no display. The PyPI wheel used above contains only the
PyChrono core/robot/postprocess modules: it does **not** ship
`pychrono.irrlicht` or `pychrono.vsg3d`. The CPU script's `--vis` flag therefore
needs a different PyChrono build — either the conda-forge / Project Chrono
package, or a source build with `CH_ENABLE_MODULE_IRRLICHT=ON` (or VSG) — plus
an X11/Wayland display. The CPU solver itself runs headless in the pip
environment, as validated by `./pychrono_core/run.sh --smoke-test`.

## 3. Chrono::CRM local source build (primary design model)

The CRM driver is the primary model for fill-ratio/geometry studies. It cannot
run with the PyPI wheel above because that wheel includes only
`pychrono.core`, `pychrono.postprocess`, and `pychrono.robot`. You need a
PyChrono build that provides:

```text
pychrono.core      # ChSystemNSC, ChBodyGeometry, TrimeshShape
pychrono.vehicle   # CRMTerrain
pychrono.fsi       # ElasticMaterialProperties, SPHParameters, SPH fluid system
```

The primary environment is now a **local source build of Chrono 10.0.0**. All
of it lives under the project root:

| Path | Purpose |
| --- | --- |
| `chrono-src/` | Chrono 10.0.0 source checkout |
| `chrono-env/` | Python 3.12, CMake 3.31, Ninja, SWIG, GCC 14.4, CUDA 12.9 |
| `chrono-build/` | CMake/Ninja build tree |
| `chrono-install/` | installed libraries and PyChrono modules |

The CUDA toolkit is **12.9** because the Chrono 10.0.0 FSI-SPH source requires
it; CUDA 13 is not supported. The build targets `sm_120` for the RTX 5070 Ti.
MPI, HDF5/TDPF, Irrlicht/VSG, sensors, demos, and tests are disabled; none are
needed for CRM. Vehicle cosimulation is therefore unavailable, but
`veh.CRMTerrain` and the CRM/FSI Python bindings are built.

```bash
# Activate local toolchain and put chrono-install first on PYTHONPATH.
source environment/activate-chrono-source.sh

# Configure, build, install, and import-check (use --clean for a fresh tree).
./environment/build-chrono-source.sh --clean

# Verify the expected modules.
"$CHRONO_ENV/bin/python" - <<'PY'
import pychrono.core, pychrono.fsi, pychrono.vehicle
print('CRM Python modules available')
print(pychrono.fsi.__file__)
print(pychrono.vehicle.CRMTerrain)
PY

# Run the CRM driver with the local source build.
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh --smoke-test
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh \
  --duration 20 --fps 20 --output-dir Output_CRM_Baseline
```

`crm/run.sh` selects `chrono-env/bin/python` automatically when `PYTHON` is
unset, so `./crm/run.sh --smoke-test` also works after the source build is
installed. Chrono 10 uses `fsi.ElasticMaterialProperties` and
`terrain.SetElasticSPH()`; the driver also falls back to the older
`SoilProperties`/`SetCrmSPH()` API.

The local toolchain was assembled from conda packages with
`environment/copy_tree_two_pass.py` because this project lives on an NTFS mount
that cannot reliably create symlinks during conda linking. The result is a
self-contained, symlink-free environment. The previous prebuilt environment at
`/tmp/opencode/chrono-conda2` remains only as a fallback if the source build is
temporarily unavailable; it is not the primary setup.

`./crm/run.sh --help` and `./crm/run.sh --print-soil` work without the CRM
modules because Chrono imports are deferred. The driver uses the full coarse OBJ
by default; pass `--mesh-file path/to/spiked.obj` to test an alternative
geometry with the same pose/scale. See `crm/README.md` for the soil parameters,
fill-ratio definition, and outputs.

## 4. Visualisation

### Default: Matplotlib/Tk frame viewer

`deme/view_frames.py` reads `spheres_*.csv`, skips frames from a different
particle count, and in `auto` mode uses the actual `drum_*.vtk` pose only when
its spin axis matches the configured axis. Otherwise it warns and uses the
analytic pose. It draws the drum on the full coarse OBJ with the retaining-wall
box and opens a persistent Tk window. It is the default for `deme/view.sh` and
only needs NumPy + Matplotlib installed in the DEME environment above.

```bash
./deme/view.sh --output-dir Output_DEME_LunarExcavator --stride 8
# headless scene check:
./deme/view.sh --output-dir Output_DEME_LunarExcavator --no-render
```

`--stride` subsamples grains (default 8, so ~11k of ~88k points). The drum uses
the full 7,572-face coarse OBJ by default, matching production; `--mesh
{medium,deme}` selects the 12,956-face OBJ or the optional legacy 1200-face
simplification. The pose source is detected automatically from the VTK point
count. `--drum-source
{vtk,analytic}` overrides the default `auto` pose selection, and
`--drum-stride` subsamples faces (default 1). Space pauses/resumes;
Left/Right step one frame. `view.sh` selects `./deme-env/bin/python`
automatically when present (then falls back to `/tmp/opencode/deme-venv`);
override with `PYTHON=/path/to/python`. The viewer
forces `MPLBACKEND=TkAgg`, avoiding the Qt 6 code path that crashes ParaView
here. The solver-independent numeric path remains `deme/postprocess.sh`.

### Optional: ParaView

ParaView is inherited from the original MercuryDPM helper; neither solver needs
it. If a system ParaView installation provides `paraview.simple`:

```bash
./deme/view.sh --paraview --output-dir Output_DEME_LunarExcavator
# equivalently: ./deme/view_paraview.sh --output-dir Output_DEME_LunarExcavator
# headless pipeline check:
./deme/view_paraview.sh --output-dir Output_DEME_LunarExcavator --no-render
```

`view_paraview.sh` uses the ParaView executable so its Qt event loop stays
alive; it passes `LUNAR_OUTPUT_DIR` and `LUNAR_NO_RENDER` because
`paraview --script` does not forward script arguments. On this host ParaView
6.x / Qt 6 segfaults after building the pipeline, so the GUI path is
best-effort. `QT_QPA_PLATFORM=xcb` is selected automatically on Wayland to
avoid ParaView's warning, but does not fix that crash.

## 5. Optional mesh-preprocessing dependencies

The generated OBJ files are committed, so these are only needed if you change
the mesh. `stl_to_obj.py` regenerates the production coarse/medium OBJs; the
simplification script is optional and produces the legacy low-detail mesh, which
is no longer used by default because it closes screw holes.

```bash
python3 stl_to_obj.py
# optional, lower fidelity only:
uv pip install --python ./deme-env/bin/python trimesh fast-simplification
python3 simplify_for_deme.py
```

## 6. Dependency pins

See `requirements.txt`. In short:

```text
deme[cuda12]==3.1.0      # GPU grain-scale validation, Python 3.10+
matplotlib>=3.8          # default DEME frame viewer (install in DEME env)
pychrono==10.0.0rc1      # CPU demo only, CPython 3.12 wheel
trimesh, fast-simplification  # optional preprocessing only
```

Chrono::CRM is not available from the pip pins; it requires the separate
FSI/SPH/vehicle-enabled PyChrono build described in section 3. Do not install
multiple solver wheels into one environment unless you have verified ABI
compatibility; the separate environments above are the tested setup.
