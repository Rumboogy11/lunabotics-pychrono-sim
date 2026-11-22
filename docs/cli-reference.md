# Command-line reference

This page catalogues every command-line entry point. It is a map, not a replacement for the detailed option tables in [crm/README.md](../crm/README.md), [FULL_MODEL.md](../FULL_MODEL.md), and [environment/INSTALL.md](../environment/INSTALL.md), or the built-in `--help` of each tool.

Run all commands from the project root unless noted. Most shell launchers add `chrono-env/bin` to `PATH` and pick the project Python automatically when `PYTHON` is unset. The GUI launcher is the exception: it prefers a system Python with working Tkinter for the window and routes simulations/tools through `chrono-env`.

## Quick index

| Command | Purpose |
| --- | --- |
| `./install.sh` | Install/build the local Chrono environment. |
| `./run_study_gui.sh` | Launch the desktop GUI. |
| `./run_study.sh` | Interactive or flag-driven study wizard. |
| `./crm/run.sh` | Rigid Chrono::CRM production simulation. |
| `./crm/run_oneway_fea.sh` | Replay recorded CRM loads on the solid FEA drum. |
| `./crm/run_fea.sh` | Diagnostic two-way coupled FEA (not validated). |
| `./run_full_model.sh` | Diagnostic combined CRM + two-way FEA report. |
| `./chrono-env/bin/python step_to_chrono_fea.py` | Convert STEP CAD into FEA meshes and a CRM surface. |
| `./chrono-env/bin/python stl_to_obj.py` | Convert a surface STL to OBJ. |
| `./chrono-env/bin/python detector_size_tool.py` | Interactively size the fill-detector cylinder. |
| `./chrono-env/bin/python export_study_vtk.py` | Export a study as ParaView VTK/PVD time series. |
| `./chrono-env/bin/python view_study_paraview.py` | ParaView viewer script for exported studies. |
| `./chrono-env/bin/python view_results.py` | Plot CRM/FEA summaries to `results_overview.png`. |
| `./chrono-env/bin/python view_geometry.py` | Compare STEP CAD with the converted mesh. |
| `./chrono-env/bin/python -m unittest discover -s tests` | Run the regression tests. |

## Environment and launchers

### `./install.sh`

Installs system packages (unless `--no-system-packages`), creates `chrono-env/`, checks out the custom Chrono fork, applies the local SPH patch, and builds PyChrono with FSI/FSI-SPH/vehicle modules and CUDA 12.9.

Common flags: `-h` / `--help`, `--no-system-packages`, `--with-paraview` / `--without-paraview`, `--cuda-arch NNN`, `--jobs N`, `--clean`, `--skip-build`, `--skip-patch`, `--chrono-repo URL`, `--chrono-ref REF`, `--work-root PATH`. Set `PYCHRONO_WORK_ROOT` instead of passing `--work-root` if you prefer. `CHRONO_REPO` defaults to the project's custom Chrono fork and `CHRONO_REF` to the tested commit unless overridden. See [environment/INSTALL.md](../environment/INSTALL.md).

### `source environment/activate-chrono-source.sh`

Sets project/build paths (`CHRONO_PROJECT_ROOT`, `CHRONO_ENV`, `CHRONO_SRC`, `CHRONO_BUILD`, `CHRONO_INSTALL`), `PATH`, `PYTHONPATH`, library paths, and `OMP_NUM_THREADS`. It also configures CUDA and compiler helpers (`CUDA_HOME`, `CUDAToolkit_ROOT`, `CUDACXX`, `CUDAHOSTCXX`, `CMAKE_PREFIX_PATH`, `CHRONO_CUDA_ARCHITECTURES`, and `CC`/`CXX`), plus `SWIG_LIB` when needed. Optional for the launchers, but useful when calling tools directly.

### `./run_study_gui.sh`

Starts `study_gui.py`. The GUI has no command-line options. It prefers a system Python with Tkinter for display scaling and falls back to the local environment. Set `PYTHON` to override. Use `xvfb-run ./run_study_gui.sh` on headless hosts.

### `./run_study.sh`

Forwards all arguments to `study_wizard.py`. `--help` prints every wizard flag; see the [wizard guide](wizard-guide.md).

## Simulation drivers

### `./crm/run.sh` — rigid CRM (production)

The main design model. Common flags:

| Flag group | Flags |
| --- | --- |
| Time | `--duration T`, `--dt S`, `--fps F`, `--settle-time T`, `--allow-unsafe-dt` |
| Resolution | `--spacing D`, `--d0-multiplier F`, `--artificial-viscosity V`, `--shifting-ppst-push V`, `--max-velocity V`, `--free-surface-threshold F`, `--proximity-steps N` |
| Motion | `--descent-mode {kinematic,force}`, `--descent-speed V`, `--descent-force N`, `--descent-force-ramp T`, `--omega W`, `--reverse-spin`, `--spin-axis {X,Y,Z}` |
| Gravity | `--gravity G`, `--drum-gravity` / `--no-drum-gravity` |
| Domain | `--bed-height H`, `--bed-padding F`, `--container-padding D`, `--active-box X,Y,Z` |
| Geometry | `--mesh-file PATH`, `--drum-radius R`, `--drum-half-length L`, `--inner-radius R`, `--capacity-volume V` |
| Detector | `--detector-auto-size` / `--no-detector-auto-size`, `--detector-radius R`, `--detector-length L` |
| Soil | `--density`, `--youngs-modulus`, `--poisson-ratio`, `--friction`, `--mu-2`, `--mu-i0`, `--cohesion`, `--grain-diameter` |
| Output | `--output-dir DIR`, `--loads-file PATH`, `--save-particles`, `--particle-fps F`, `--loads-fps F`, `--particle-writers N`, `--keep-old-particles`, `--no-output` |
| Checks | `--print-soil`, `--smoke-test --mesh-file PATH` |

`--mesh {coarse,medium}` selects legacy local OBJ names from `lunar_excavator_config.py`; those files are not shipped, so supply `--mesh-file` with your own surface. `crm/run.sh` also pins local Chrono libraries with `LD_PRELOAD` through `crm/chrono_runtime_env.sh`, while `crm/run_fea.sh` injects local `PYTHONPATH`/`LD_LIBRARY_PATH`. Full option meanings are in [crm/README.md](../crm/README.md).

### `./crm/run_oneway_fea.sh` — one-way structural replay

Replays a recorded soil wrench; no SPH contact is simulated in this stage. The most important flags:

| Flag | Purpose |
| --- | --- |
| `--loads PATH` | Required; `soil_loads.csv` recorded by rigid CRM. |
| `--duration T`, `--dt S`, `--fps F` | Replay length, structural timestep, summary cadence. The replay runs for the smaller of `T` and the last recorded CRM load frame. |
| `--fea-node PATH`, `--fea-ele PATH` | Solid tetrahedral mesh. |
| `--solver {pardiso-mkl,sparse-lu,sparse-qr,gmres}`, `--num-threads N` | Linear solver. |
| `--load-radius-fraction F`, `--load-scale F`, `--min-stress-volume V`, `--raw-stress-every N`, `--no-lock-sparsity-pattern` | Load spread/scaling, peak-stress filtering, scan cadence, and solver sparsity-pattern control. |
| `--export-vtk` / `--no-export-vtk`, `--vtk-dir DIR`, `--vtk-stride N` | Full 3D stress animation (expensive; off by default). |
| Motion/gravity | Match the CRM stage: `--descent-mode`, `--descent-force*`, `--gravity`, `--omega`, `--reverse-spin`, `--spin-axis`, bed and buffer options. |
| Material | `--pla-youngs-modulus`, `--pla-poisson-ratio`, `--pla-density`, `--rayleigh-beta`, `--hub-radius`. |

The replay checks required telemetry, sample count/order, descent mode, and (in kinematic mode) the Z trajectory, then interpolates the wrench by time. It runs for the minimum of `--duration` and the time of the last CRM load sample: if the loads file is shorter, it prints a `NOTICE` naming the shortened duration and finishes normally rather than holding the final wrench. Gravity, spin, and actuator-force settings are not validated from the CSV, so keep them matched manually. It rejects detector and SPH-only flags used by rigid CRM. The launcher adds local `PYTHONPATH`/`LD_LIBRARY_PATH` entries only when `--solver gmres`, `sparse-lu`, or `sparse-qr` is explicitly requested; the default PardisoMKL path uses the existing Python bindings.

### `./crm/run_fea.sh` — two-way FEA (diagnostic)

Uses the flexible-BCE coupled driver `lunar_excavator_crm_fea.py`. Adds `--fea-node`, `--fea-ele`, `--bce-layers`, `--stress-stride`, `--solver`, `--solver-iterations`, `--solver-tolerance`, PLA material flags, and more. **Do not use for design validation**; see [crm/FEA_COUPLING.md](../crm/FEA_COUPLING.md).

### `./run_full_model.sh` — combined diagnostic report

Runs rigid CRM plus two-way FEA with shared settings and writes `combined_report.json` / `combined_summary.csv`. Flags include `--output-dir DIR`, `--duration T`, `--smoke-test`, `--crm-spacing D`, `--crm-dt S`, `--crm-fps F`, `--fea-duration T`, `--fea-spacing D`, `--fea-dt S`, `--fea-fps F`, `--fea-stress-stride N`, `--fea-bce-layers N`, `--fea-threads N`, repeatable `--extra-crm "..."` / `--extra-fea "..."`, `--strict-fea`, and `--dry-run`. A failed FEA stage is recorded but does not fail the command unless `--strict-fea` is given. See [FULL_MODEL.md](../FULL_MODEL.md).

## Geometry conversion helpers

### `step_to_chrono_fea.py`

```bash
./chrono-env/bin/python step_to_chrono_fea.py geometry/MyDrum.step \
  --out-prefix Output_MyDrum/mesh/MyDrum --unit-scale 0.001 \
  --mesh-size 0.012 --axis Y --time-stages
```

Writes `<prefix>.node`, `<prefix>.ele`, `<prefix>.inp`, `<prefix>_surface.stl`, and `<prefix>.json` with measurements and conversion metadata. Key options: `--surface-target-faces`, `--surface-max-faces`, `--surface-fit-tolerance`, `--min-solid-volume`, `--hub-radius`, `--part-aware-simplify` (and its radius/reduction/min-faces controls), `--surface-only`, `--max-volume-error`, `--no-occ-parallel`. The study wizard calls this tool automatically for STEP studies.

### `stl_to_obj.py`

Converts a surface STL to OBJ for rigid CRM contact:

```bash
./chrono-env/bin/python stl_to_obj.py Output_MyDrum/mesh/MyDrum_surface.stl
```

With no arguments it tries the legacy medium/coarse STL names from `lunar_excavator_config.py`; those files are not bundled.

### `detector_size_tool.py`

Interactive 3D sizing of the fill-detector cylinder. It can read STEP (`--step`), a converted surface (`--surface`), or FEA nodes (`--fea-node`), and overlays an orange nominal cylinder plus a green eroded classifier cylinder.

```bash
./chrono-env/bin/python detector_size_tool.py --step geometry/MyDrum.step \
  --radius 0.061 --length 0.066 --spacing 0.002 --source-axis Y \
  --unit-scale 0.001 --show --result-json /tmp/detector_size.json
```

Sliders update the JSON while dragging; the GUI reads it into the Geometry page. `--output PNG` saves a still image instead of opening the window.

## Viewers and exporters

### `export_study_vtk.py`

```bash
./chrono-env/bin/python export_study_vtk.py --study-dir Output_MyDrum \
  --workers 4 --stride 1 --particle-stride 0 --particle-render-stride 2
```

Writes `crm_particles.pvd`, `excavator.pvd`, `crm_animation.pvd` (and `combined.pvd`), `fea_summary.csv`, `export_manifest.json`, and optionally a revalidated multi-frame 3D FEA series under `<study>/paraview`. CRM particle and excavator frames are regenerated on every export; FEA VTU frames come from the FEA run and are validated rather than regenerated. Strides trade detail for speed: `--particle-stride 0` follows `--stride`; `--particle-render-stride N` draws every Nth particle per frame. `--particles-only` and `--excavator-only` restrict the export. See [Outputs and visualization](outputs-and-visualization.md).

### `view_study_paraview.py`

Prefer running it inside ParaView:

```bash
QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=Output_MyDrum/paraview \
  paraview --script="$PWD/view_study_paraview.py"
```

or headlessly with `pvpython view_study_paraview.py Output_MyDrum/paraview`. Flags include `--warp-scale`, `--particle-radius`, `--particle-style {spheres,points}`, `--no-particles`, `--no-excavator`, `--no-fea`, `--show-fea-charts`, `--with-fea`, `--fea-only`, `--fea-series COL1,COL2`, and camera-orbit options. `PYCHRONO_FEA_CHARTS=1` and `PYCHRONO_WITH_FEA=1` are the environment equivalents used by the GUI. The excavator source opens at 40% opacity; the FEA raw grid is shown and `WarpByVector1` starts hidden (toggle it in the Pipeline Browser). The camera track is primed during setup, so the first frame opens framed instead of white and the view-up warning that older versions logged at startup is avoided.

### `view_results.py`

```bash
./chrono-env/bin/python view_results.py --output-dir Output_MyDrum --show
```

Plots `summary.csv` from `crm/` and `fea/` in four panels (CRM work, CRM fill ratio, peak von Mises, and peak deformation) and writes `results_overview.png` in the study folder. With `--show`, a shared crosshair follows the pointer: the vertical line marks the time and a horizontal line on every panel meets it at that panel's plotted value.

### `view_geometry.py`

```bash
./chrono-env/bin/python view_geometry.py --study-dir Output_MyDrum
```

Renders the original STEP and the converted surface/FEA mesh in linked views. `--output PNG` saves a screenshot; `--show` forces the interactive window. `--source-axis`, `--deflection`, `--threads`, and `--no-parallel` control CAD tessellation.

### `fea_summary.py`

A library, not a CLI. It derives compact MPa/mm FEA chart tables (see `paraview/fea_summary.csv`) from `fea/summary.csv` for the viewer.

## Tests

```bash
chrono-env/bin/python -m unittest discover -s tests -p 'test_*.py'
```

The suites cover GUI fields, geometry consistency, fill metrics, force descent, gravity, FEA summaries/export timing, detector sizing, output preview, and installer dependencies. No pytest installation is required.

## Environment variables

| Variable | Effect |
| --- | --- |
| `PYTHON` | Overrides the interpreter used by simulation/CLI shell launchers; `run_study_gui.sh` still chooses its own Tk-capable display Python. |
| `CHRONO_ENV`, `CHRONO_SRC`, etc. | Set by `environment/activate-chrono-source.sh`; used by direct tool invocations. |
| `OMP_NUM_THREADS` | CPU thread count for OpenMP work; the activation script defaults it to half the machine's threads but never overrides an explicit value. |
| `PYCHRONO_EXPORT_DIR` / `PYCHRONO_STUDY_DIR` | Tells `view_study_paraview.py` which export folder to open. |
| `PYCHRONO_SCRIPT_DIR` / `PYCHRONO_ROOT` | Path hints passed by the GUI/wizard so a ParaView-launched viewer can find the local `fea_summary.py` helper. |
| `PYCHRONO_CHRONO_PYTHON` | Records the Chrono-capable interpreter when the GUI re-execs into a system Python with Tkinter; GUI-launched helpers use it for simulations. |
| `PYCHRONO_FEA_CHARTS=1` | Enables one-way FEA history charts in the viewer. |
| `PYCHRONO_WITH_FEA=1` | Loads a structurally usable 3D FEA stress PVD in the viewer. |
| `PYCHRONO_PARAVIEW_QT_PLATFORM` | Overrides the GUI launcher's default `QT_QPA_PLATFORM=xcb` for ParaView. |
| `PYCHRONO_ORBIT_*`, `PYCHRONO_CAMERA_ORBIT` | Camera track settings passed by the GUI. |
| `PYCHRONO_PARAVIEW_GUI=1` | Tells the viewer it is running inside the full ParaView GUI. |
| `QT_QPA_PLATFORM=xcb` | Forces X11 for ParaView on Wayland systems. |
| `PYCHRONO_GUI_NO_REEXEC=1` | Internal guard that stops the GUI's display re-exec loop; used by the regression tests, not needed for normal use. |
| `PYCHRONO_EXPORT_STUDY_VTK`, `PYCHRONO_DETECTOR_SIZE_ENV`, `PYCHRONO_VIEW_GEOMETRY_ENV`, `PYCHRONO_WIZARD_REEXEC` | Internal self-launch guards set by the tools themselves; not user options. |

Other `PYCHRONO_*REEXEC` and `PYCHRONO_*ENV` variables are also internal self-launch guards.
