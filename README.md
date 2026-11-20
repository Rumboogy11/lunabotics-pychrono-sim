# Lunar bucket-drum excavator — Chrono::CRM simulation tools

This repository contains tools for simulating and evaluating lunar bucket-drum excavator designs with Project Chrono. The current supported workflow is:

- **Rigid Chrono::CRM** (`crm/lunar_excavator_crm.py`) — primary design model for bulk fill ratio, soil response, torque, and specific energy.
- **One-way CRM → FEA replay** (`crm/lunar_excavator_oneway.py`) — structural screening by replaying recorded rigid-CRM soil loads on a solid PLA mesh (no SPH in replay).
- **Interactive study wizard** (`study_wizard.py` / `run_study.sh`) — guided end-to-end setup, reproducible config/commands, run, and result viewing.

A two-way coupled FEA driver (`crm/lunar_excavator_crm_fea.py`) and a combined rigid+FEA runner (`full_model.py`) exist but are **diagnostic only and not engineering-validated**. See `crm/FEA_COUPLING.md` and `FULL_MODEL.md`.

> **Metric note:** soil/excavation metrics count SPH fluid markers only. Older comparisons that included BCE markers are invalid.

## Requirements

- Local **Chrono 10** source build with **FSI-SPH** and **vehicle/CRM** modules, Python bindings, and CUDA (see `environment/INSTALL.md`).
- NVIDIA GPU with a CUDA 12.9-capable setup as used by the local build.
- Standard PyPI `pychrono` wheels do not include `pychrono.fsi`/`pychrono.vehicle` and cannot run the CRM drivers.

## Quick start

```bash
# Activate the local Chrono build environment (if installed)
source environment/activate-chrono-source.sh

# Quick checks (no simulation / short setup checks)
./crm/run.sh --print-soil
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh --smoke-test

# Baseline rigid CRM run
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh \
  --duration 20 --fps 20 --output-dir Output_CRM_Baseline

# Interactive one-way study (prompts for settings, runs CRM + FEA)
./run_study.sh

# Inspect generated commands without running
./run_study.sh --dry-run --duration 0.5 --spacing 0.012
```

Runners select `chrono-env/bin/python` automatically when `PYTHON` is unset and the local environment is present.

## Typical workflows

### 1. Rigid CRM geometry comparison (recommended for design)

```bash
./crm/run.sh --duration 20 --fps 20 --output-dir Output_CRM_Baseline
./crm/run.sh --mesh-file path/to/alternative.obj --output-dir Output_CRM_Alt
```

Keep the same soil block and motion schedule across variants. Useful options include `--spacing`, `--dt` (spacing-dependent default), `--omega`/`--reverse-spin`, `--spin-axis`, `--bed-height`, `--drum-radius`, `--drum-half-length`, `--inner-radius`/`--capacity-volume`, `--save-particles`, and `--export-vtk`.

### 2. One-way CRM → FEA (structural screening)

```bash
# Record rigid soil wrench
./crm/run.sh --duration 0.2 --spacing 0.012 --dt 0.00025 \
  --loads-file Output_OneWay/crm/soil_loads.csv \
  --output-dir Output_OneWay/crm

# Replay loads on solid mesh
./crm/run_oneway_fea.sh --duration 0.2 \
  --loads Output_OneWay/crm/soil_loads.csv \
  --output-dir Output_OneWay/fea
```

Use identical motion settings in both stages. The replay reports stable stress/displacement estimates relative to rigid-CRM loads but is **not** two-way FSI.

### 3. STEP-to-study and result viewing

The wizard can convert a STEP model into CRM/FEA meshes, auto-scale derived geometry/motion/SPH defaults (editable), and run both one-way stages:

```bash
./run_study.sh --geometry step --step-file geometry/MyDrum.step --duration 20
```

Each study writes reproducible outputs (e.g. `study_config.json`, `commands.sh`, stage logs/summaries, `crm/soil_loads.csv`) under `Output_<study_name>/`.

View results:

```bash
./chrono-env/bin/python view_results.py --output-dir Output_<study_name> --show
./chrono-env/bin/python view_geometry.py --study-dir Output_<study_name>
./chrono-env/bin/python export_study_vtk.py --study-dir Output_<study_name>
QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=Output_<study_name>/paraview paraview --script="$PWD/view_study_paraview.py"
```

## Repository layout

```text
.
├── crm/
│   ├── lunar_excavator_crm.py      # rigid CRM driver (primary)
│   ├── lunar_excavator_oneway.py   # one-way FEA replay driver
│   ├── lunar_excavator_crm_fea.py  # two-way coupled FEA (diagnostic)
│   ├── crm_soil.py                 # soil parameter definitions
│   ├── run.sh / run_oneway_fea.sh / run_fea.sh
│   ├── README.md
│   └── FEA_COUPLING.md
├── fea/                            # promoted solid meshes and related artifacts
├── geometry/                       # CAD/mesh inputs used by studies
├── environment/                    # build/activation scripts and dependency notes
├── step_to_chrono_fea.py           # STEP -> TetGen FEA mesh + CRM surface
├── study_wizard.py / run_study.sh  # interactive one-way study workflow
├── export_study_vtk.py             # export study outputs to ParaView datasets
├── view_study_paraview.py          # ParaView/PyVista viewer for exported studies
├── view_results.py                 # plot CRM/FEA summaries
├── view_geometry.py                # compare STEP CAD vs converted geometry
├── stl_to_obj.py                   # STL -> OBJ conversion utility
├── full_model.py / run_full_model.sh / FULL_MODEL.md  # combined diagnostic runner/docs
└── lunar_excavator_config.py       # shared geometry/motion defaults and helpers
```

Local build/env directories (`chrono-src/`, `chrono-build/`, `chrono-install/`, `chrono-env/`) and run outputs (`Output_*`) are typically generated locally and ignored by git.

## Key notes

- **Soil model:** defaults in `crm/crm_soil.py` are independent best-estimate parameters and should be calibrated/frozen for comparisons.
- **Timesteps:** CRM defaults are spacing-dependent; explicitly unsafe `--dt` values are rejected unless overridden.
- **Geometry:** use full-detail meshes for production; some simplified meshes may close holes.
- **Two-way FEA:** current flex-BCE coupling results are unstable/inconsistent; do not use for design validation.

## More documentation

- `environment/INSTALL.md` — environment/build and optional tools
- `crm/README.md` — CRM/one-way usage, options, outputs
- `crm/FEA_COUPLING.md` — one-way/two-way coupling status and evidence
- `FULL_MODEL.md` — combined diagnostic runner
