# Installation

The supported workflow is a local Chrono 10 source build with CRM/FSI support.
It runs the rigid Chrono::CRM driver, the one-way CRM→FEA replay, and the study wizard.

## 1. Chrono::CRM source build (recommended)

All required components live under the project root:

| Path | Purpose |
| --- | --- |
| `chrono-src/` | Chrono 10.0.0 source checkout |
| `chrono-env/` | Python 3.12, CMake 3.31, Ninja, SWIG, GCC 14.4, CUDA 12.9 |
| `chrono-build/` | CMake/Ninja build tree |
| `chrono-install/` | installed libraries and PyChrono modules |

Notes:
- CUDA **12.9** is required by the Chrono 10.0.0 FSI-SPH source used here (CUDA 13 is not supported).
- The build targets `sm_120` for the RTX 5070 Ti; adjust if building for a different GPU.
- MPI/HDF5, visualization modules, sensors, demos, and tests are not required for this workflow.

```bash
# Activate local toolchain and put chrono-install first on PYTHONPATH.
source environment/activate-chrono-source.sh

# Configure, build, install, and import-check (use --clean for a fresh tree).
./environment/build-chrono-source.sh --clean

# Verify the expected modules.
"$CHRONO_ENV/bin/python" - <<'PY'
import pychrono.core, pychrono.fsi, pychrono.vehicle
print("CRM Python modules available")
PY

# Quick CRM smoke test.
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh --smoke-test
```

`crm/run.sh`, `crm/run_oneway_fea.sh`, and `run_study.sh` select `chrono-env/bin/python`
automatically when `PYTHON` is unset and the local environment is present.

`./crm/run.sh --help` and `./crm/run.sh --print-soil` work without CRM modules because
Chrono imports are deferred until a simulation starts.

## 2. Optional CPU-only PyChrono wheel

A PyPI `pychrono` wheel can be useful for lightweight non-CRM checks, but it does **not**
include `pychrono.fsi` or `pychrono.vehicle` and therefore cannot run the CRM drivers.

```bash
uv venv --python 3.12 ./pychrono-env
uv pip install --python ./pychrono-env/bin/python pychrono==10.0.0rc1
```

## 3. Optional viewer and preprocessing tools

ParaView export/viewing uses VTK/PyVista when available. Geometry/result plotting and mesh
preprocessing may use NumPy, Matplotlib, trimesh, or fast-simplification depending on the
tool you run.

```bash
# Example: install optional utilities into an environment of your choice
python -m pip install matplotlib trimesh fast-simplification
```

- `view_results.py` plots CRM/FEA summaries and writes `results_overview.png`.
- `view_geometry.py` compares STEP CAD vs converted mesh/surface.
- `export_study_vtk.py` / `view_study_paraview.py` export and view studies in ParaView (VTK/PyVista required).
- `stl_to_obj.py` converts STL meshes to OBJ (pure format conversion, no extra deps).

## 4. Notes

- Run commands from the project root unless a script states otherwise.
- Large outputs are written under `Output_*` (ignored by git). Prefer project-local paths over `/tmp`.
- See `README.md` and `crm/README.md` for workflows and options.
