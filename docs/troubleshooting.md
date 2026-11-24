# Troubleshooting

Work from the top: most problems are setup, geometry, or resolution issues rather than solver bugs. For each problem, check the stage log in your output folder and the generated `commands.sh` to see the exact command that ran.

## Installation and startup

### `ModuleNotFoundError: pychrono.fsi` or `pychrono.vehicle`

You are using a PyPI/conda `pychrono` wheel. Those wheels do not contain the modules these drivers need. Use the project-local build:

```bash
source environment/activate-chrono-source.sh
"$CHRONO_ENV/bin/python" -c "import pychrono.core, pychrono.fea, pychrono.fsi, pychrono.pardisomkl, pychrono.vehicle"
```

If that import fails, rerun `./install.sh` (use `--clean` to force a rebuild). See [environment/INSTALL.md](../environment/INSTALL.md).

### CUDA errors or "no compatible GPU"

A CUDA 12.9-compatible NVIDIA driver and GPU are required at runtime. The CUDA toolkit itself lives in `chrono-env`. If `nvidia-smi` is unavailable during installation, pass `--cuda-arch NNN` (for example `86`, `89`, `120`). CUDA 13 is not supported by this Chrono revision.

### The GUI will not start

- On a headless host, use `xvfb-run ./run_study_gui.sh`.
- Confirm Tkinter exists: `python3 -c 'import tkinter'`; the launcher falls back to `chrono-env/bin/python` if it does not.
- The launcher prefers the system Python for display scaling. Set `PYTHON="$PWD/chrono-env/bin/python"` to force the local one.
- Check the terminal output: the GUI also prints errors before the window closes.

### ParaView crashes on startup

The GUI defaults to `QT_QPA_PLATFORM=xcb` because some ParaView builds abort on Wayland. Set `PYCHRONO_PARAVIEW_QT_PLATFORM` to use a different Qt platform if needed. If you launch ParaView manually, set the variable yourself:

```bash
QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=Output_<study>/paraview \
  paraview --script="$PWD/view_study_paraview.py"
```

### The checkout is on NTFS/FUSE and symlinks/builds fail

The installer automatically moves `chrono-env/`, `chrono-src/`, `chrono-build/`, `chrono-install/`, `.tools/`, and `.mamba/` to a native-filesystem work root and symlinks them back. Override with `PYCHRONO_WORK_ROOT` or `install.sh --work-root PATH`.

## Geometry and conversion

### "geometry not found" or empty mesh output

No CAD or mesh files are bundled. Supply your own STEP file (`geometry/` is gitignored) or existing surface OBJ and `.node`/`.ele` files. The GUI preview (`Tools → Preview STEP mesh`) checks a conversion without touching an active study.

### CAD dimensions look wrong in the simulation

- Check **Unit scale (STEP->m)**: `0.001` for millimetre CAD, `1.0` for metre CAD.
- Check **CAD axis**: newer models use `Y`; `auto` picks the smallest bounding-box dimension.
- Keep **Auto-scale from CAD/mesh** on, then click **Apply CAD/mesh defaults**. With auto geometry enabled the wizard measures the loaded CAD/mesh and restores edited outer drum dimensions if they disagree; those corrections are recorded in `study_config.json`.
- Turn auto geometry off only when you deliberately use a custom radius with a matching mesh.

### STEP surface fit fails or TetGen rejects the mesh

On the default wizard/GUI path, the converter raises **Surface target faces** until the measured exterior deviation is below **Exterior fit tolerance** and meshing succeeds. Direct use with `--skip-decimation`, `--repair-after-decimation`, or a zero face target fails instead of retrying. Increase the target or relax the tolerance (set it to `0` to disable the fit check only when you accept the risk). Check `mesh/<name>.json` for requested/used face counts, achieved deviation, and pass status.

### The FEA mesh is huge and slow

Use the recommended **Coarsen hub/interior mesh** (part-aware simplification). It leaves bucket exterior faces unchanged and decimates only interior faces inside **Interior coarsen radius**, just above the rigid hub radius. Increase **Interior reduction** if the reduction is small. Do not combine it with legacy MeshFix order `after`.

### Fill looks wrong or the detector seems misplaced

- Leave **Auto-size fill detector** on until you have compared the cylinder with the drum in the 3D sizer.
- Manual **Detector cylinder length** is the full length, not a half-length.
- The detector follows the live drum pose and erodes one SPH radius from its sides and ends. Soil that leaves the cylinder, or falls back to the bed, immediately loses its flag.
- Geometry auto-correction never overwrites manual detector dimensions. Detector flags are written only by rigid CRM; the FEA replay does not accept them.

## Simulation behaviour

### Fill ratio stays at zero

Common causes, in order:

1. **Duration too short** for the drum to reach the bed. In kinematic mode a full descent takes roughly `bed_height / descent_speed`; the wizard warns if duration is shorter.
2. **Drum starts too high or spin direction is wrong.** Check `drum_z_m` and signed `spin_deg` in `crm/summary.csv`; enable **Reverse spin** if needed.
3. **Detector too small or mis-centred.** Use the live 3D sizer.
4. **Auto geometry changed the dimensions.** Check `geometry_corrections` in `study_config.json`.
5. **Particles were deactivated.** If flung soil freezes in mid-air, enlarge **Active box X,Y,Z**; it is drum-centred and full-size.

### The first bite blasts soil upward

Increase **Artificial viscosity** above 0.5 or lower **PPST shifting push** below 3.0 to damp the pressure transient and outward drift. These are response controls, not timestep fixes.

### "Unsafe dt" or the simulation explodes

CRM timestep stability depends on SPH spacing; the default is computed from spacing and explicitly oversized values are rejected. Leave **CRM dt** as `auto` unless you know what you are doing. `--allow-unsafe-dt` bypasses the check and is not recommended. If a run diverges after changing geometry scale, wall layout, or forcing, reduce `dt` and recheck stability.

### Too many particles / run is extremely slow

Particle count scales roughly with bed volume divided by spacing cubed. Use the live estimate on **Run & Domain**:

- increase **SPH spacing** (2 mm is the standard default; 3–4 mm is much cheaper),
- reduce **bed height** or **bed padding**,
- shorten duration for a smoke test,
- keep **particle FPS** low (5 Hz is usually plenty) and **loads FPS** moderate (20 Hz).

Also check that surface conversion did not produce an unnecessarily fine mesh; **Exterior fit tolerance** and part-aware simplification control this.

### Export or disk writes are the bottleneck

Raise **particle writers** (background CSV writers), lower **particle FPS**, use ParaView/particle strides, and increase **export workers**. CRM particle and excavator frames are regenerated on every export; FEA VTU frames come from the FEA run. Delete stale exports rather than hand-editing generated frames.

### Force descent behaves differently from a previous run

Kinematic descent is the historical default. Force mode requires matching options in **both** stages:

- `--descent-mode force`, `--descent-force`, `--descent-force-ramp`, `--gravity`;
- the rigid-drum gravity toggle (`--drum-gravity`) is emitted only for rigid CRM;
- one-way FEA replays the recorded Z trajectory and rejects a loads file recorded in the other mode.

Remember that `--drum-gravity` is off by default and only changes force-mode dynamics; kinematic descent overrides the drum pose either way.

### "Skip current step" stops the log instead of reaching FEA

The CRM and export drivers spawn worker processes (for example, particle CSV writers) that inherit the runner's stdout pipe. Signalling only the step's parent PID leaves those workers alive, so the runner blocks reading stdout and never advances to the one-way FEA stage. The GUI now starts each step in its own process group and **Skip current step** / **Stop running process** signal the whole group, which closes the pipe and lets the pipeline continue (or abort cleanly). In a concurrent run, the first **Skip current step** press targets only rigid CRM and leaves FEA running; a second press targets FEA. If you must skip CRM very early, `crm/soil_loads.csv` may not yet contain enough samples for FEA; in that case re-run CRM or use **Run one-way FEA only (existing CRM)** against a completed study.

## FEA and viewing

### One-way FEA rejects the loads file

The CSV is missing required telemetry, has too few or unsorted samples, uses a different descent mode, or—in kinematic mode—has a Z trajectory inconsistent with the configured settle time, descent speed, and bed geometry. Re-run rigid CRM with matching motion/gravity options, or use **Run one-way FEA only (existing CRM)** from the GUI with a matching configuration. The replay interpolates loads by time; gravity, spin, and actuator-force settings are not validated from the CSV, so keep those matched manually.

### One-way FEA finishes earlier than requested

A `NOTICE: one-way FEA duration limited by the available CRM load frames` message means the FEA duration was longer than the recorded load history. The replay stops at the last row of `soil_loads.csv` and exits normally; this is not a failure. Run rigid CRM longer (or with a higher `--loads-fps`) and re-run the replay if you need the extra time.

In a concurrent full study, `waiting for CRM load frames` is expected: the replay has reached the next output time before CRM has written it. Frame and time-progress lines are prefixed `CRM` or `FEA`, so interleaved output can be read per stage. The one-way driver keeps polling `crm/soil_loads.csv` while the `crm/crm_running.flag` liveness marker is current, then applies the ordinary cap as soon as CRM exits. If that message never advances, check that CRM is still running and writing load samples; a FEA-only run never waits because no liveness marker is created. Concurrent mode is opt-in; turning **Run CRM + one-way FEA together** off restores the original sequential run.

### FEA results look noisy or implausible

One-way FEA is a screening tool: no soil feedback, PLA material assumptions, and a load radius fraction that limits where forces are applied. Adjust `--load-radius-fraction` (0.95 is cheaper, 0.8 applies loads more widely) and material/damping values only with a rationale. Two-way coupled FEA and `full_model.py` are diagnostics and are not validated.

Chrono 11's stock Python wrapper does not expose `GetEquivalentVonMises()` on stress tensors. The installer applies `environment/patches/chrono-python-von-mises.patch` so the drivers call Chrono's native formula directly, leaving the reported von Mises stress and VTU stress arrays unchanged. If you run the diagnostic two-way driver, a `Chrono 11 binding note: ChMaterialShellKirchhoff.SetDensity is unavailable` message is expected; that path uses explicit contact-skin node masses instead.

### No FEA charts or no 3D stress in ParaView

The two are separate toggles:

- **Show one-way FEA scalar/deformation history charts** (Tools tab) is off by default; command-line equivalent is `--show-fea-charts` or `PYCHRONO_FEA_CHARTS=1`.
- **Load 3D FEA stress/deformation in ParaView** requires an explicitly exported, structurally usable multi-frame `fea/vtk/fea.pvd` (its VTU files live in `fea/vtk/frames/`). Enable **Export full 3D FEA stress animation** or `--fea-export-vtk`. A lone `t=0` frame is ignored, but a usable partial multi-frame series can be displayed.

Check `paraview/export_manifest.json` for `has_fea_vtk`, `fea_valid`, and `fea_status` as export-time provenance. ParaView rechecks the actual PVD and frame files, so a manifest-marked partial multi-frame series may still display.

If the FEA log reports `FEA 3D VTK writer failed` or `FEA 3D VTK writer shutdown failed`, the scalar history is still usable but the 3D PVD may be incomplete. Resolve the reported disk/VTK error and rerun with **Export full 3D FEA stress animation** enabled.

The 3D series opens on the **undeformed** grid with von Mises coloring; `WarpByVector1` is present but hidden by default. Toggle that filter in the Pipeline Browser if you expected a deformed shape. The excavator/drum source is at 40% opacity so it does not hide the stress overlay; raise its opacity in the Properties panel if you prefer a solid drum.

### ParaView's first frame is white or logs a view-up warning

The current `view_study_paraview.py` primes the camera-orbit track before its first render and picks a non-parallel view-up before any camera reset. Time 0 should open already framed, without stepping one frame backwards and forwards, and without the `Resetting view-up since view plane normal is parallel` warning. If either symptom persists, you are likely running an older copy of the viewer: relaunch from the GUI's Tools tab (which uses this checkout's script) or run `pvpython view_study_paraview.py <study>/paraview` directly. `--no-camera-orbit` disables the track entirely as a fallback; use ParaView's Reset Camera button to reframe the scene.

### The Output Preview tab is missing

It is toggleable in **Settings → Show output preview tab**. Turn it back on and click **Apply now** (or **Save settings** to persist). The **Tools → Open lightweight CRM/FEA output preview** button also re-enables it.

## Docs tab issues

- The Docs tab is a built-in Markdown reader with a searchable text pane and a **Refresh** button. If you edit a Markdown file while the GUI is open, click **Refresh** to reload it.
- Search (`Find`) highlights the next match from the cursor on the *current* page, wraps at the end, and highlights one match at a time; select another page in the left tree first.
- Links are clickable: a link to a registered page switches the tree selection and loads that page, a link with a heading anchor scrolls to the heading, a link inside a table cell follows that label, and a web link opens in your browser. If a file is not registered in the Docs tab, the status line reports that instead of opening a separate viewer. Page names are still written out in the prose so links are useful in plain Markdown viewers.
- Tables are embedded widgets with real columns, not padded text. If a table is wider than the Docs pane, use the horizontal scrollbar under that table; the columns stay aligned while you scroll.

## Still stuck?

Include these when asking for help:

1. `study_config.json` and `commands.sh` from the failing study;
2. the failing stage's `.log` file;
3. output of `source environment/activate-chrono-source.sh` followed by the failing command;
4. your GPU/driver, OS, STEP axis/unit scale, and the mesh/spacing you used.

Then cross-check the detailed references: [crm/README.md](../crm/README.md), [crm/FEA_COUPLING.md](../crm/FEA_COUPLING.md), and [environment/INSTALL.md](../environment/INSTALL.md).
