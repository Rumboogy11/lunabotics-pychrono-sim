# Outputs and visualization

A study writes two kinds of information: **numbers** (CSV/JSON, always) and **3D time series** (VTK/PVD, when export is enabled). This page explains both and how to view them.

## The output directory

```text
Output_<study_name>/
├── study_config.json          final settings, command list, geometry corrections
├── commands.sh                exact reproducible shell commands
├── <stage>.log                one log per generated command (mesh, CRM, FEA, export)
├── mesh/                      STEP conversion (STEP studies only)
│   ├── <name>.node/.ele       tetrahedral solid mesh for FEA
│   ├── <name>.inp             Abaqus-style mesh (diagnostic)
│   ├── <name>_surface.stl     repaired surface
│   ├── <name>_surface.obj     CRM contact surface
│   └── <name>.json            measured CAD/mesh metadata and fit results
├── crm/
│   ├── config.json            soil, numerics, mesh, capacity, detector settings
│   ├── summary.csv            per-frame metrics
│   ├── soil_loads.csv         recorded soil wrench for one-way FEA
│   └── particles_*.csv        SPH particle positions/flags (when saved)
├── fea/
│   ├── config.json            FEA settings and material
│   ├── summary.csv            per-frame structural metrics
│   └── vtk/                   3D stress frames (only when explicitly exported)
│       ├── frames/frame_*.vtu per-frame tetrahedral stress results
│       └── fea.pvd            PVD index pointing into frames/
├── paraview/                  exported ParaView datasets
│   ├── crm_particles.pvd
│   ├── excavator.pvd
│   ├── crm_animation.pvd
│   ├── combined.pvd            older alias of crm_animation
│   ├── fea_summary.csv         compact MPa/mm chart table
│   ├── export_manifest.json    export provenance and FEA status at export time
│   └── fea.pvd / with_fea.pvd optional multi-frame 3D FEA series
└── results_overview.png        four-panel summary plots written by view_results.py
```

`study_config.json` and `commands.sh` are the reproducibility pair. If you want to rerun or audit a study, start there. `commands.sh` shows each stage command together with its log destination; the config's `commands` array stores the stage names and command argument lists. For a concurrent study, `commands.sh` starts rigid CRM in a background subshell, writes its PID to `crm/crm_running.flag`, runs one-way FEA in the foreground, and waits for CRM before continuing. The flag is transient and is removed as soon as CRM exits; it is not a result file.

Each stage log also mirrors the GUI progress stream. Frame and coarse time-progress lines start with `CRM`, `FEA`, or `two-way FEA` (diagnostic), so interleaved output can be attributed without relying on the log filename.

## Numeric outputs

### `crm/summary.csv`

One row per CRM output frame (`--fps`). The columns fall into groups:

- **Time/pose:** `frame`, `time_s`, `wall_s`, `drum_z_m`, `spin_deg`, `omega_rad_s`.
- **Particle bookkeeping:** `sph_particles`, `inside_drum`, `lifted`, `excavated`.
- **Excavation metrics:** `fill_ratio_count`, `fill_ratio_mass`, `peak_fill_ratio_mass`, `capacity_m3`, `particle_mass_kg`.
- **Energy:** `fluid_torque_x_nm`, `input_power_w`, `work_j`, `specific_energy_j_kg`.
- **Force-descent telemetry** (only in force mode): `descent_mode`, `descent_state`, `actuator_force_n`, `vertical_speed_m_s`.

`fill_ratio_mass` is the primary design metric. Both fill ratios and the particle flags use the *same* instantaneous `excavated` set: SPH markers fully inside the detector cylinder eroded by one particle radius. Capacity is computed once at startup from the nominal cavity volume minus the excavator mesh volume inside that cavity, so hubs, spokes, and flights do not appear as unfillable volume; `crm/config.json` records `excavator_overlap_m3` and the capacity source. A `--capacity-volume` override is treated as an already-free volume. `lifted` is reported separately as a diagnostic and does not gate fill.

### `crm/soil_loads.csv`

The soil wrench sampled at `--loads-fps`. One-way FEA interpolates between these rows by simulation time, so a moderate cadence (20 Hz) is usually enough. Force-descent runs add the same telemetry columns as `summary.csv`; kinematic runs keep the original schema.

### `crm/particles_*.csv`

One file per particle output frame (`--particle-fps`). Each row is an SPH marker position and its detector flags (`inside_drum`, `excavated`). These files are large; the exporter and preview both sample them rather than drawing every marker when asked.

### `fea/summary.csv`

One row per FEA output frame. Useful columns include `time_s`, `drum_z_m`, `max_deformation_m`, `max_von_mises_pa`, `max_von_mises_raw_pa`, motor position/angle errors, applied force/torque, `input_power_w`, and `work_j`. Force-mode runs add descent telemetry and `recorded_trajectory_error_m`.

### `mesh/<name>.json`

Records the measured CAD drum dimensions, mesh volumes and errors, target/achieved surface face counts, fit tolerance and achieved deviation, hub-node count, and part-aware simplification statistics. Use it to check whether a STEP conversion met the requested exterior fit.

## ParaView exports

`export_study_vtk.py` converts existing CSVs into time series. It **regenerates all CRM particle and excavator frames on every run**, so a cancelled or shorter study cannot leave stale frames in those animations. FEA VTU frames are written by the FEA driver; the exporter validates and references them rather than regenerating them. The one-way driver snapshots each FEA frame into plain arrays and writes it from a separate bounded-queue writer process, so VTK construction and disk I/O overlap later solver steps. It joins that writer before printing the final summary, so `fea.pvd` is complete whenever the run reports success.

| File | Contents |
| --- | --- |
| `crm_particles.pvd` | SPH particle point clouds; point data includes `inside_drum` and `excavated`. |
| `excavator.pvd` | The rigid drum surface transformed to the pose recorded in `crm/summary.csv` (`drum_z_m`, `spin_deg`). |
| `crm_animation.pvd` | Canonical non-FEA view: particles + rigid excavator together. |
| `combined.pvd` | Older alias with the same content. |
| `fea.pvd` / `with_fea.pvd` | Optional multi-frame 3D FEA stress/deformation series, alone and combined with CRM. |
| `fea_summary.csv` | Compact FEA stress/deformation table in MPa/mm for XY charts. |
| `export_manifest.json` | Export settings, series availability, and FEA status recorded at export time. |

The 3D particle/drum series never includes FEA. The fast default writes only FEA scalar summaries; a full 3D stress animation is opt-in from the GUI's Output & Export page or with `--fea-export-vtk` in the wizard. The viewer revalidates the PVD and its frame files: a lone `t=0` frame from a cancelled export is ignored, but a usable **partial multi-frame** series can still be displayed even when the manifest marks it partial.

### Strides

Three independent strides control export cost:

- `--stride N` — every Nth CRM/excavator frame.
- `--particle-stride N` — every Nth particle frame; `0` follows `--stride`.
- `--particle-render-stride N` — every Nth particle inside each exported frame; `1` draws all.

`--workers N` writes frames in parallel. The GUI Tools tab exposes all four values and streams progress. `fea_vtk_stride` samples FEA frames, always including the last one.

## Viewing options

### 1. Output Preview tab (fastest)

In the GUI: **Tools / View Results** → **Open lightweight CRM/FEA output preview**. It reads CSVs directly with Tkinter and `csv`; no VTK files needed. The Tools directory and Output Preview start on the study wizard's output directory and follow edits to it; you can still browse to another run. Drag to orbit, wheel to zoom, and use the frame slider or Play button. The sample limit (default 5,000) bounds drawn dots only; the true particle count is reported. An orange index mark follows the signed `spin_deg` so reverse spin is visible.

### 2. Result plots

```bash
./chrono-env/bin/python view_results.py --output-dir Output_<study_name> --show
```

Draws four CRM/FEA summary plots (CRM work, CRM fill ratio, peak von Mises, and peak deformation) and writes `results_overview.png`. With `--show`, a shared crosshair follows the pointer: a vertical time line plus a horizontal line on each plot that meets it at that plot's value. In the GUI, use **Open interactive result plots**.

### 3. Geometry comparison

```bash
./chrono-env/bin/python view_geometry.py --study-dir Output_<study_name>
```

Shows the original STEP CAD and converted surface/mesh in linked views. Before a full study, the GUI's **Preview STEP mesh** button runs the real converter into `geometry_checks/mesh_preview/` and opens this comparison without touching your study output.

### 4. ParaView 3D view

Export first (from the wizard, the Output & Export page, or the Tools tab), then:

```bash
QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=Output_<study_name>/paraview \
  paraview --script="$PWD/view_study_paraview.py"
```

or:

```bash
pvpython view_study_paraview.py Output_<study_name>/paraview
```

The viewer shows particles as GPU point sprites with a glyph fallback and the excavator as a separate source at 40% opacity so it does not hide the FEA stress overlay. By default, one-way FEA **history charts are hidden**; enable them with `--show-fea-charts` or `PYCHRONO_FEA_CHARTS=1`. The GUI Tools checkbox does the same and clears the environment variable when unchecked.

A structurally usable 3D FEA stress PVD is loaded by default by the GUI-launched viewer (`PYCHRONO_WITH_FEA=1` or `--with-fea`); uncheck **Load 3D FEA stress/deformation** to skip it. The raw undeformed grid is shown with von Mises coloring; the `Warp By Vector` filter exists in the pipeline but starts hidden. Toggle `WarpByVector1` in the Pipeline Browser to display the deformed shape and adjust its **Scale Factor**. `--warp-scale` sets that factor at launch. The viewer checks the actual PVD and frame files rather than trusting an old manifest entry. `--fea-only` isolates the FEA series and `--fea-series COL1,COL2` selects other summary columns for charts.

The viewer can add a camera-orbit track around the vertical Z axis. Orbit speed, radius (`0` auto-fits with clearance), inclination, start angle, and keyframe count are configurable on the GUI Tools tab and are passed through `PYCHRONO_ORBIT_*` variables. The viewer primes the track before the first render, so time 0 is already framed: you do not need to step back one frame before advancing, and ParaView does not log a `Resetting view-up since view plane normal is parallel` warning during setup.

`QT_QPA_PLATFORM=xcb` is forced by the GUI because some ParaView builds abort on Wayland.

## Choosing a viewing workflow

| Question | Best tool |
| --- | --- |
| Did the drum fill at all? | Output Preview tab or `view_results.py` |
| How do fill, torque, and energy evolve? | `view_results.py`, `crm/summary.csv` |
| Where did particles go? | ParaView particles + excavator series |
| Did the exported mesh match the CAD? | `view_geometry.py` |
| What stress did the drum see? | `fea/summary.csv`, FEA history charts, optional 3D FEA PVD |
| What did the exporter claim about FEA? | `paraview/export_manifest.json` (provenance) |
| Can ParaView actually open the 3D FEA series? | The viewer rechecks `fea.pvd` and its frame files |

Remember that one-way FEA is a structural screening of recorded loads, not a coupled soil simulation. Treat stress magnitudes as comparative unless you have calibrated the material and load assumptions; see [crm/FEA_COUPLING.md](../crm/FEA_COUPLING.md).
