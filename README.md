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

Keep the same soil block and motion schedule across variants. Useful options include `--spacing`, `--dt` (spacing-dependent default), `--omega`/`--reverse-spin`, `--spin-axis`, `--bed-height`, `--bed-padding` (soil margin around the drum as a fraction of radius), `--drum-radius`, `--drum-half-length`, `--inner-radius`/`--capacity-volume`, `--save-particles`, `--export-vtk`, `--paraview-stride`, and `--paraview-particle-render-stride` (render every Nth particle in each frame).

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

The export writes `crm_animation.pvd` (also `combined.pvd`) with CRM particles and rigid excavator poses only; it is the default ParaView series and never includes FEA. When a one-way FEA VTK export is complete and valid, `fea.pvd` and `with_fea.pvd` are also written; load the stress overlay with `--with-fea` (or `--fea-only`). Incomplete FEA output from cancelled runs is ignored. All excavator/particle frames are regenerated on every export, so delete stale exports rather than manually editing their frames.

Both tools are parallel by default when launched from the GUI. `export_study_vtk.py --workers N` writes frames in `N` processes; the GUI's Tools tab exposes the worker count, the frame stride, and a particle render stride, and streams export progress instead of blocking the window. The particle render stride also appears on the Study Wizard's Output & Export page and in Defaults; `1` writes all particles, while a value greater than 1 writes only every Nth particle in each frame, reducing particle file size and ParaView load without changing frame timing. The exporter also uses NumPy's columnar CSV loader, writes particle frames as point clouds without redundant vertex-cell connectivity, and sends only scalar poses (not the whole mesh) to excavator workers. These changes reduce CPU time and VTP size while preserving frame regeneration and ParaView glyph rendering. `view_geometry.py` enables OpenCASCADE's parallel meshing and an all-at-once Boolean fuse of the STEP assembly (~6× faster than the serial fuse, identical CAD volume/bounding box); `--threads N` limits OCC/OMP workers (the GUI passes its "FEA threads" setting). Use `--no-parallel` on `view_geometry.py` for a slow serial diagnostic path.

Launch the consolidated GUI with `./run_study_gui.sh`. Its **Settings** tab adjusts text size, startup window size, text color, dark mode, and whether the **Output Preview** tab is shown; changes can be applied to the current session and saved to the workspace-local `study_gui_settings.json` for the next launch. The **Run & Domain** wizard page exposes bed padding as a fraction of drum radius and updates a live approximate initial SPH particle count as bed dimensions, padding, or spacing change; the same estimate appears on the **Review & Run** page. The toggleable Output Preview tab reads `crm/particles_*.csv`, `crm/summary.csv`, and `fea/summary.csv` directly with no VTK conversion, draws a bounded subsample of particles as 2D points, and provides CRM/FEA scalar timelines plus a lightweight Play control. The **Tools** tab can open ParaView with an automatically generated camera-orbit track around the vertical Z axis; orbit speed (revolutions per simulation second), radius (`0` auto-fits the excavator bounds), inclination, start angle, and keyframe count are configurable there. Press ParaView's Play button after opening to run the camera and excavation animation together.

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
├── study_gui.py / run_study_gui.sh # consolidated Tkinter GUI
├── study_output_preview.py         # CSV-only lightweight GUI preview widget
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
- **Optional part-aware hub simplification:** `step_to_chrono_fea.py --part-aware-simplify` keeps all exterior/screw-flight faces bit-for-bit unchanged and decimates only faces inside `--part-aware-radius` (default 0.05 m). The generated JSON records eligibility, reduction, boundary preservation, and volume error. This path is off by default and does not change production mesh generation.
- **Two-way FEA:** current flex-BCE coupling results are unstable/inconsistent; do not use for design validation.

## More documentation

- `environment/INSTALL.md` — environment/build and optional tools
- `crm/README.md` — CRM/one-way usage, options, outputs
- `crm/FEA_COUPLING.md` — one-way/two-way coupling status and evidence
- `FULL_MODEL.md` — combined diagnostic runner
