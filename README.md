# Lunar bucket-drum excavator — Chrono::CRM simulation tools

This repository contains tools for simulating and evaluating lunar bucket-drum excavator designs with Project Chrono. The current supported workflow is:

- **Rigid Chrono::CRM** (`crm/lunar_excavator_crm.py`) — primary design model for bulk fill ratio, soil response, torque, and specific energy.
- **One-way CRM → FEA replay** (`crm/lunar_excavator_oneway.py`) — structural screening by replaying recorded rigid-CRM soil loads on a solid PLA mesh (no SPH in replay).
- **Interactive study wizard** (`study_wizard.py` / `run_study.sh`) — guided end-to-end setup, reproducible config/commands, run, and result viewing.

A two-way coupled FEA driver (`crm/lunar_excavator_crm_fea.py`) and a combined rigid+FEA runner (`full_model.py`) exist but are **diagnostic only and not engineering-validated**. See `crm/FEA_COUPLING.md` and `FULL_MODEL.md`.

> **Metric note:** soil/excavation metrics count SPH fluid markers only. Older comparisons that included BCE markers are invalid.

> **Fill measure:** `inside_drum`, `fill_ratio_count`, and `fill_ratio_mass` use the same instantaneous `excavated` set: SPH particles lifted above the initial bed and fully inside the analytical detector cylinder eroded by one particle radius. By default the cylinder is sized from the measured drum radius and full length; it may instead be sized manually. The cylinder follows the excavator's full live pose, and a particle loses its flag as soon as it leaves the current cylinder or drops back to the bed. Capacity, the nominal `inner_radius` denominator, SPH/BCE counts, and particle CSV columns are unchanged.

## Installation

Run the installer on Arch/CachyOS or Debian/Ubuntu:

```bash
git clone https://github.com/Rumboogy11/lunabotics-pychrono-sim.git
cd lunabotics-pychrono-sim
./install.sh
```

It clones the custom Chrono fork, builds it with FSI-SPH/vehicle/CRM modules and
CUDA 12.9, and creates the project-local `chrono-env`. See
`environment/INSTALL.md` for options (including `--no-system-packages`,
`--cuda-arch`, ParaView, and no-root setups). On NTFS/FUSE checkouts the
installer keeps the environment on a native filesystem and creates symlinks.

Standard PyPI `pychrono` wheels do not include `pychrono.fsi`/`pychrono.vehicle`
and cannot run the CRM drivers. An NVIDIA GPU with a compatible driver is
required for simulation; the CUDA toolkit itself is installed into `chrono-env`.

## Quick start

```bash
# Activate the local Chrono build environment (if installed)
source environment/activate-chrono-source.sh

# Quick checks (the smoke test needs a local CRM surface OBJ; no geometry is shipped)
./crm/run.sh --print-soil
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh --smoke-test --mesh-file /path/to/your_drum.obj

# Baseline rigid CRM run
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh \
  --mesh-file /path/to/your_drum.obj \
  --duration 20 --fps 20 --output-dir Output_CRM_Baseline

# Interactive one-way study (prompts for settings, runs CRM + FEA)
./run_study.sh

# Inspect generated commands without running
./run_study.sh --dry-run --duration 0.5 --spacing 0.012
```

Simulation and CLI runners select `chrono-env/bin/python` automatically when `PYTHON` is unset and the local environment is present. The GUI launcher instead prefers a system Python with Tkinter for display quality and routes its simulations and tools through `chrono-env`.

### Gravity settings

`--gravity` (or the GUI's **Gravity Z (m/s^2)** field) sets one signed global Z acceleration, default `-9.81` m/s². It always drives CRM soil and the drums in both FEA paths. Rigid CRM's drum is a separate choice: **Apply gravity to CRM drum** / `--drum-gravity` defaults off, so older force-descent runs keep their original actuator-plus-soil-response behavior. Enable it when the drum's own weight should contribute to descent.

### Fill-detector sizing

The fill cylinder is automatic by default: its nominal radius and full length follow the measured drum radius and length used by the rigid CRM run. To size it independently, pass:

```bash
./crm/run.sh --mesh-file /path/to/your_drum.obj --no-detector-auto-size \
  --detector-radius 0.05 --detector-length 0.08 --output-dir Output_ManualDetector
```

`--detector-length` is the **full** axial length; the driver stores half-length internally. The wizard/GUI use the config keys `detector_auto_size`, `detector_radius`, and `detector_length`. On the GUI's **Geometry** page these appear as **Auto-size fill detector**, **Detector cylinder radius (m)**, and **Detector cylinder length (m)**; the manual fields are enabled only when auto sizing is unchecked. Geometry auto-correction still manages only the physical drum dimensions and never overwrites manual detector values. Detector flags are emitted only for rigid CRM; the one-way FEA replay does not accept them.

For visual sizing, open **Tools → Live 3D fill-detector size adjuster** (or the **Open live 3D detector sizer** button on the Geometry page). The adjuster shows the STEP model (or converted CRM/FEA surface) at 50% opacity, an orange nominal detector cylinder, and a green wireframe of the classifier cylinder eroded by one SPH particle radius. Dragging the radius/length sliders updates both the 3D view and the GUI fields in real time; merely opening the viewer leaves auto mode unchanged. The standalone equivalent is:

```bash
./chrono-env/bin/python detector_size_tool.py \
  --step geometry/MyDrum.step --radius 0.061037 --length 0.066 \
  --spacing 0.002 --source-axis Y --unit-scale 0.001 \
  --show --result-json /tmp/detector_size.json
```

`--surface`/`--fea-node` can be used when no STEP file is available. The viewer needs the local `chrono-env` Python (PyVista, trimesh, and OCP).

## Typical workflows

### 1. Rigid CRM geometry comparison (recommended for design)

```bash
./crm/run.sh --mesh-file path/to/baseline.obj --duration 20 --fps 20 --output-dir Output_CRM_Baseline
./crm/run.sh --mesh-file path/to/alternative.obj --duration 20 --fps 20 --output-dir Output_CRM_Alt
```

Keep the same soil block and motion schedule across variants. Useful rigid-CRM options include `--spacing`, `--dt` (spacing-dependent default), `--omega`/`--reverse-spin`, `--spin-axis`, `--bed-height`, `--bed-padding` (soil margin around the drum as a fraction of radius), `--drum-radius`, `--drum-half-length`, `--inner-radius`/`--capacity-volume`, and `--save-particles`. Full 3D FEA export and ParaView strides belong to the wizard/exporter: `--fea-export-vtk`, `--paraview-stride`, and `--paraview-particle-render-stride` in `run_study.sh`; `--stride` and `--particle-render-stride` in `export_study_vtk.py`.

### 2. Force-driven descent (opt-in)

All three drivers default to the original constant-speed kinematic descent. Add `--descent-mode force` to let a downward actuator drive the rigid drum or coupled FEA slider:

```bash
./crm/run.sh --mesh-file /path/to/your_drum.obj \
  --descent-mode force --descent-force 1.0 --descent-force-ramp 0.2 \
  --duration 60 --loads-file Output_Force/crm/soil_loads.csv \
  --output-dir Output_Force/crm

./crm/run_oneway_fea.sh --descent-mode force \
  --loads Output_Force/crm/soil_loads.csv --output-dir Output_Force/fea
```

The CRM force motor is active only after `--settle-time`, then the position motor holds the final `z_target` once reached; spin remains prescribed. SPH soil reaction always acts on the drum. `--descent-force 0` is allowed; with `--drum-gravity` it gives gravity-only descent. Global gravity is always applied to CRM soil and FEA drums; the rigid CRM drum only gets it when `--drum-gravity` is passed (default off to preserve earlier force-descent behavior). Kinematic descent overrides the drum pose either way, so this toggle mainly changes force-mode dynamics. `--descent-speed` is ignored in force mode except for wizard/GUI duration guidance (`0` is allowed). Force runs add `descent_mode`, `descent_state`, `actuator_force_n` (positive magnitude), and `vertical_speed_m_s` to `summary.csv`/`soil_loads.csv`; `recorded_trajectory_error_m` is also added to one-way FEA output. Kinematic CSV schemas are unchanged. The one-way path replays the recorded CRM Z trajectory with a position motor because it has no SPH reaction to balance the force; it rejects a loads file recorded with a different mode. The coupled diagnostic FEA path applies the force motor directly to the slider. Use the same force and gravity options in every stage; the wizard/GUI expose `descent_mode`, `descent_force_n`, `descent_force_ramp_s`, `gravity`, and `drum_gravity`, emitting `--drum-gravity` only for rigid CRM.

### 3. One-way CRM → FEA (structural screening)

```bash
# Record rigid soil wrench
./crm/run.sh --mesh-file /path/to/your_drum.obj --duration 0.2 --spacing 0.012 --dt 0.00025 \
  --loads-file Output_OneWay/crm/soil_loads.csv \
  --output-dir Output_OneWay/crm

# Replay loads on solid mesh
./crm/run_oneway_fea.sh --duration 0.2 \
  --loads Output_OneWay/crm/soil_loads.csv \
  --output-dir Output_OneWay/fea
```

Use identical motion settings in both stages. The replay reports stable stress/displacement estimates relative to rigid-CRM loads but is **not** two-way FSI.

### 4. STEP-to-study and result viewing

The wizard can convert a STEP model into CRM/FEA meshes, auto-scale derived geometry/motion/SPH defaults (editable), and run both one-way stages:

```bash
./run_study.sh --geometry step --step-file geometry/MyDrum.step --duration 20
```

Each study writes reproducible outputs (e.g. `study_config.json`, `commands.sh`, stage logs/summaries, `crm/soil_loads.csv`) under `Output_<study_name>/`. CRM summary frames, soil-wrench samples, and particle CSV frames have independent cadences: `crm_fps`, `loads_fps`, and `particle_fps`. Set `particle_fps` equal to `crm_fps` (e.g. 20 Hz) to write one particle file per CRM frame; the default 5 Hz writes every fourth frame at 20 Hz and is usually sufficient for ParaView.

For STEP studies the GUI/wizard start at `surface_target_faces=8000` and measure the bucket drum excavator exterior surface against the raw OCC tessellation. On that default decimation path, `surface_fit_tolerance` (default `0.001` m, exposed as **Exterior fit tolerance (m)** on the Geometry page) doubles the face target until the measured bidirectional p99.9 deviation and TetGen acceptance both pass. Direct converter use with `--skip-decimation`, `--repair-after-decimation`, or a zero face target fails instead of retrying. The mesh JSON records the requested and used targets, tolerance, achieved error, and pass status; use `0` to disable the fit check. Only the radial hub/interior patch is coarsened—bucket drum excavator exterior faces are never simplified by the part-aware step.

View results:

```bash
./chrono-env/bin/python view_results.py --output-dir Output_<study_name> --show
./chrono-env/bin/python view_geometry.py --study-dir Output_<study_name>
./chrono-env/bin/python export_study_vtk.py --study-dir Output_<study_name>
# optional: sample particle frames independently of the excavator series
./chrono-env/bin/python export_study_vtk.py --study-dir Output_<study_name> \
  --stride 1 --particle-stride 4 --particle-render-stride 2
QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=Output_<study_name>/paraview paraview --script="$PWD/view_study_paraview.py"
```

The export writes `crm_animation.pvd` (also `combined.pvd`) with CRM particles and rigid excavator poses only; that 3D series never includes FEA. The fast default writes FEA scalar summaries only. The exporter still derives `paraview/fea_summary.csv` (MPa/mm) from `fea/summary.csv`, but `view_study_paraview.py` does not display the one-way FEA scalar or deformation history charts by default. Pass `--show-fea-charts` (or set `PYCHRONO_FEA_CHARTS=1` with `paraview --script=...`) to opt in; the table is created on demand for existing study directories, so no VTK frames or mesh copy are needed. Combine with `--fea-series COL1,COL2` to chart other summary columns, or use `--no-fea` to suppress all FEA views. In the GUI, the Tools tab's **Show one-way FEA scalar/deformation history charts** checkbox is off by default; checking it opts both the manual ParaView launch and the post-run "Open ParaView after run" launch into those charts, while unchecking it clears any inherited `PYCHRONO_FEA_CHARTS` value. The separate "Load 3D FEA stress/deformation" checkbox controls only the 3D overlay.

Full 3D stress animation is opt-in from the GUI's **Output & Export** page ("Export full 3D FEA stress animation", stride defaults to every frame) or the wizard's `--fea-export-vtk`/`--vtk-stride N`. It writes `fea/vtk/frames/frame_*.vtu` plus `fea/vtk/fea.pvd` with per-element von Mises stress (Pa/MPa), displacement, and deformation magnitude. This path is expensive: each exported frame adds a full tetrahedral stress scan and a VTU write, so the default remains off. Once a usable multi-frame PVD exists, the GUI's ParaView buttons (Tools tab and "Open ParaView after run") load it by default; on the Tools tab the "Load 3D FEA stress/deformation in ParaView" checkbox is checked, and unchecking it skips the 3D series. The CLI equivalent is `--with-fea`. The viewer rechecks the PVD and frame files: a lone `t=0` frame from a cancelled run is ignored, but a usable partial multi-frame run can be loaded even if the manifest marks it partial. All CRM excavator/particle frames are regenerated on every export, so delete stale exports rather than manually editing their frames. The FEA spindle motor is initialized spindle-first so the FEA VTU frames, PVD3 reader, and Warp By Vector output rotate in the same direction as the rigid `excavator.pvd` drum and the signed CRM `spin_deg`.

Both tools are parallel by default when launched from the GUI. `export_study_vtk.py --workers N` writes frames in `N` processes; the GUI's Tools tab exposes the worker count, the general frame stride, an independent particle frame stride, and a particle render stride, and streams export progress instead of blocking the window. `--particle-stride N` (also on the Study Wizard's Output & Export page and in Defaults) exports every Nth CRM particle frame; `0` follows `--stride`, so particles and excavator stay time-aligned by default. A value greater than 1 is useful because particle files are much larger than excavator poses. The separate particle render stride (`1` by default) writes only every Nth particle within each exported frame. The exporter also uses NumPy's columnar CSV loader, writes particle frames as point clouds without redundant vertex-cell connectivity, and sends only scalar poses (not the whole mesh) to excavator workers. These changes reduce CPU time and VTP size while preserving frame regeneration and ParaView rendering; the viewer now uses GPU point sprites for sphere particles (with a glyph fallback) so large particle frames stay cheap and do not trigger picker OBB warnings. `view_geometry.py` enables OpenCASCADE's parallel meshing and an all-at-once Boolean fuse of the STEP assembly (~6× faster than the serial fuse, identical CAD volume/bounding box); `--threads N` limits OCC/OMP workers (the GUI passes its "FEA threads" setting). Use `--no-parallel` on `view_geometry.py` for a slow serial diagnostic path. The GUI's **Tools** tab also has a "Preview STEP mesh (full-run mesher + comparison)" button: it runs the current Geometry-page values through the same `step_to_chrono_fea.py` command used by a full study, writes only to `geometry_checks/mesh_preview/`, and then opens `view_geometry.py` to compare the fresh surface with the original STEP. It never touches an active study output directory.

Launch the consolidated GUI with `./run_study_gui.sh`. Its **Settings** tab adjusts GUI scale, text size, startup window size, text color, dark mode, whether the **Output Preview** tab is shown, and the preview's particle sample count/frame stride, marker size, and line width; changes can be applied to the current session and saved to the workspace-local `study_gui_settings.json` for the next launch. The **Run & Domain** wizard page exposes bed padding as a fraction of drum radius and updates a live approximate initial SPH particle count as bed dimensions, padding, or spacing change; the same estimate appears on the **Review & Run** page. The toggleable Output Preview tab reads `crm/particles_*.csv`, `crm/summary.csv`, and `fea/summary.csv` directly with no VTK conversion. It draws a bounded 3D particle point cloud by default (orthographic or perspective, with drag-to-orbit and wheel zoom; a 2D Y-Z cross-section is also available), an editable particles-to-sample count (default 5,000) and particle-frame stride, particle marker size, line width, CRM/FEA scalar timelines, and a lightweight Play control. The 3D drum wireframe includes an orange index mark that rotates with the signed `spin_deg` recorded by CRM, so the **Reverse spin** checkbox is visible in the preview as well as in the simulation. The preview uses only Tkinter and `csv`. Every row of the displayed frame is read to report the true simulated particle count; the configured sample count then bounds how many points are drawn. The **Tools** tab can open ParaView with an automatically generated camera-orbit track around the vertical Z axis; orbit speed (revolutions per simulation second), radius (`0` auto-fits the excavator bounds with 5x radial clearance and lowers the orbit center by one third of the excavator's bounding radius), inclination, start angle, and keyframe count are configurable there. Its "Load 3D FEA stress/deformation" checkbox is on by default so a structurally usable multi-frame FEA PVD is overlaid on the CRM animation; uncheck it to skip the 3D stress series. Press ParaView's Play button after opening to run the camera and excavation animation together.

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
├── fea/                            # local/generated FEA meshes (gitignored, not shipped)
├── geometry/                       # CAD/mesh inputs used by studies
├── install.sh                      # one-shot installer (Arch/CachyOS, Debian/Ubuntu)
├── environment/                    # conda env spec, Chrono patch, build/activation scripts
├── step_to_chrono_fea.py           # STEP -> TetGen FEA mesh + CRM surface
├── study_wizard.py / run_study.sh  # interactive one-way study workflow
├── study_gui.py / run_study_gui.sh # consolidated Tkinter GUI
├── study_output_preview.py         # CSV-only lightweight GUI preview widget
├── fea_summary.py                  # derive compact MPa/mm FEA chart tables from summary.csv
├── export_study_vtk.py             # export study outputs to ParaView datasets
├── detector_size_tool.py           # live 3D fill-cylinder radius/length adjuster
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
- **Timesteps:** CRM defaults are spacing-dependent; explicitly unsafe `--dt` values are rejected unless overridden. The guard is empirical for this soil/contact setup, so recheck it after changing geometry scale, wall layout, or forcing.
- **Rigid drum mass:** force-descent runs use the loaded mesh volume (PLA density) for mass and inertia, not the small reference mesh's fixed mass. Particle CSV frames from an earlier run are removed at startup unless `--keep-old-particles` is passed.
- **Geometry:** use full-detail meshes for production; some simplified meshes may close holes. Generated studies validate that the editable outer radius/half-length still match the CAD/mesh actually loaded. With auto geometry enabled (the default), an edited radius/length is restored to the measured CAD/mesh value, a cavity too large for an SPH particle is reset, and a kinematic run shorter than the full descent is extended (force mode has no analytic descent time, so that correction is skipped); each change is printed and recorded as `geometry_corrections` in `study_config.json`. Turn auto geometry off only when you deliberately want a custom radius with a matching mesh. The fill measure and SPH/BCE counts are unchanged.
- **Part-aware hub/interior coarsening (recommended, on by default in the wizard/GUI):** `step_to_chrono_fea.py --part-aware-simplify` keeps all bucket drum excavator exterior faces of a given surface bit-for-bit unchanged and decimates only faces wholly inside `--part-aware-radius` (default 0.05 m) with a fixed boundary and topology preservation. On a bucket drum excavator CAD model with the current production surface settings, this changed 81,606 nodes / 352,278 tets / 24,174 hub nodes to 62,939 nodes / 252,638 tets / 5,569 hub nodes (−23% nodes, −28% tets, −77% constraint rows) while leaving the outer radial bands' surface area unchanged and improving volume error from 0.442% to 0.259%. Use `--no-part-aware-simplify` only for comparison; the generated JSON records eligibility, achieved reduction, boundary preservation, and volume error. This path is incompatible with legacy MeshFix order `after`.
- **Two-way FEA:** current flex-BCE coupling results are unstable/inconsistent; do not use for design validation.

## Documentation

New to the project? Start with the [documentation hub](docs/README.md), which links to a [beginner quick start](docs/quickstart.md), a [plain-language glossary](docs/glossary.md), and the [GUI guide](docs/gui-guide.md). All of these pages are also available in the **Docs** tab of `study_gui.py`, with styled headings, inline code, shaded code blocks, real table columns, clickable links, and searchable text.

| Guide | What it covers |
| --- | --- |
| [Quick start](docs/quickstart.md) | First study end to end: install, geometry, GUI, results |
| [Glossary](docs/glossary.md) | Plain-language definitions for CRM, SPH, FEA, and metrics |
| [GUI guide](docs/gui-guide.md) | Every tab, page, button, and setting in the desktop app |
| [Study wizard](docs/wizard-guide.md) | Interactive and non-interactive terminal wizard |
| [Command-line reference](docs/cli-reference.md) | All launchers, tools, and environment variables |
| [Outputs and visualization](docs/outputs-and-visualization.md) | CSV/VTK/PVD outputs and the viewers |
| [Troubleshooting](docs/troubleshooting.md) | Common setup, geometry, solver, and viewing problems |
| [How it works](docs/how-it-works.md) | In-depth pipeline, physics, metrics, and validation status |
| [Developer guide](docs/developer-guide.md) | Code layout, config schema, tests, and adding docs |

Detailed references:

- `environment/INSTALL.md` — environment/build and optional tools
- `crm/README.md` — CRM/one-way usage, options, outputs
- `crm/FEA_COUPLING.md` — one-way/two-way coupling status and evidence
- `FULL_MODEL.md` — combined diagnostic runner
