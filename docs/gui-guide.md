# GUI guide

The desktop application is the easiest way to configure, run, and inspect a study. This page describes every tab and the actions that matter. For a first walkthrough, read [Quick start](quickstart.md) first.

## Launching

```bash
./run_study_gui.sh
```

The launcher chooses a Python with Tkinter for the window (system `python3` if available, otherwise the local `chrono-env`) and adds `chrono-env/bin` to `PATH`. Simulations and geometry tools still run with the project Python. To use a specific interpreter, set `PYTHON` before launching. On a headless machine:

```bash
xvfb-run ./run_study_gui.sh
```

> The **Docs** tab of this application contains this page and all other documentation, so you can read it without leaving the GUI.

## The top-level tabs

| Tab | Purpose |
| --- | --- |
| **Study Wizard** | Configure and run a new study. Contains six sub-pages and the run controls. |
| **Defaults** | Persistent starting values for new studies. |
| **Tools / View Results** | Export and view results for an existing study; preview STEP meshes; adjust the fill detector. |
| **Output Preview** | Lightweight, CSV-only particle cloud and timeline viewer. Shown by default; can be hidden in Settings. |
| **Docs** | This documentation, with a searchable text pane. |
| **Settings** | Fonts, scaling, theme, window size, and preview options. |

## Study Wizard

The wizard occupies the left large area of the window. Six sub-tabs guide you through a study. Use **< Back** and **Next >** at the bottom to move between them; the pages are scrollable.

There is an **Info** button next to most fields. It opens a short explanation that says what the value controls and notes the runtime, disk, or display cost where it matters. The same help text is collected into the **GUI parameter reference** page in the Docs tab.

### Page 1 — Study

- **Study name** — short label. Editing it immediately sets the output directory to `Output_<study name>` under the project folder.
- **Output directory** — the folder that will hold the config, commands, logs, meshes, and result subfolders. It follows the study name until you edit it yourself; browse to a new or empty folder to preserve earlier runs.
- **Geometry source** — `supplied` / `files` use the mesh paths on page 2; `step` converts a CAD file into meshes before the simulation.
- **Auto-scale from CAD/mesh** — measure the chosen geometry and refresh suggested drum, bed, domain, spacing, and mesh values. All suggested values stay editable.

**Apply CAD/mesh defaults** at the bottom repeats that measurement whenever you change geometry. **Load study_config.json** reads a previous study so you can rerun or edit it. **Save config/commands (dry run)** writes `study_config.json` and `commands.sh` without simulating.

### Page 2 — Geometry

Basic inputs:

- **STEP file** — used only for `geometry = step`.
- **CRM surface OBJ** — the rigid contact surface when using existing meshes.
- **FEA .node / .ele** — the tetrahedral solid mesh for the replay.
- **CAD axis (STEP)** — axis of the drum in the original CAD (`Y` for current models; `auto` picks the smallest bounding-box dimension).
- **Unit scale (STEP->m)** — `0.001` when CAD coordinates are millimetres.
- **FEA mesh size (m)** — approximate tetrahedron edge length; smaller is finer and slower.

Surface conversion controls (for STEP studies):

- **Surface target faces** — starting triangle count; raised automatically until the fit check and mesh generation pass.
- **Surface max faces** — retry ceiling.
- **Exterior fit tolerance (m)** — allowed deviation of exterior faces from the original CAD; `0` disables the check.
- **MeshFix order** — `none` uses the current OCC unify + topology-preserving route; `before`/`after` are legacy diagnostic routes.
- **Min solid volume (m^3)** — drop tiny assembly solids (screws, debris).
- **Hub radius (m)** — nodes inside this radius are tied rigidly to the spindle in FEA.
- **Coarsen hub/interior mesh** and its three controls — the recommended part-aware simplification: only interior faces are decimated; bucket exterior faces stay unchanged.

Physical dimensions: **Drum radius (m)**, **Drum half-length (m)**, **Inner/cavity radius (m)**, and **Capacity volume override (m^3)**. When auto geometry is on, outer dimensions are checked against the mesh actually loaded and restored if they disagree.

Fill-detector section:

- **Auto-size fill detector** — on by default; cylinder radius and length follow the measured drum.
- **Detector cylinder radius / length (m)** — manual values, enabled only when auto sizing is off. Length is the *full* axial length.
- **Open live 3D detector sizer** — launches the interactive viewer. Slider changes update these fields live; opening the viewer alone does not turn off auto sizing.

### Page 3 — Run & Domain

This page controls time, motion, SPH resolution, and solver behaviour:

| Group | Fields |
| --- | --- |
| Timing | CRM duration, FEA duration, CRM/FEA summary FPS, soil loads FPS, particle CSV FPS |

The FEA duration is capped by the CRM load history: if `soil_loads.csv` ends before the requested replay length, the one-way driver prints a `NOTICE` and finishes at the last recorded frame instead of failing.
| SPH | spacing, d0 multiplier, artificial viscosity, PPST shifting push, CRM dt (`auto` recommended) |
| Bed/domain | bed height, bed padding (fraction of radius), active box X,Y,Z, settle time |
| Gravity | gravity Z, apply gravity to CRM drum |
| Descent | mode (`kinematic` or `force`), speed, force, force ramp |
| Spin | spin rate magnitude, reverse spin, spin axis |
| FEA solver | FEA dt, solver choice, threads, load node radius fraction, raw stress interval, particle writer processes |

A live line under the bed settings estimates the starting SPH particle count from bed size, padding, and spacing. Use it to keep your first run affordable.

Important distinctions:

- **Gravity Z** always applies to CRM soil and both FEA drums. **Apply gravity to CRM drum** is off by default; it only matters for force descent because kinematic motion overrides the drum pose.
- **Descent speed** is used in kinematic mode. In force mode the actuator force and ramp drive the drum, and the one-way replay follows the recorded Z trajectory.
- **CRM summary FPS**, **FEA summary FPS**, **soil loads FPS**, and **particle CSV FPS** are independent. Lowering the output cadences reduces disk I/O without changing solver physics.

### Page 4 — Soil & Material

Two groups of editable material parameters:

- **Soil (CRM)** — density, Young's modulus, Poisson ratio, static friction, cohesion, physical grain diameter. Defaults are best estimates; keep them fixed across geometry comparisons.
- **FEA drum material (PLA defaults)** — Young's modulus, Poisson ratio, density, Rayleigh damping.

### Page 5 — Output & Export

- **Save CRM particle CSV frames** — needed for 3D particle visualization in ParaView; large but useful.
- **Export full 3D FEA stress animation** — writes per-frame VTU files. Expensive; off by default. Scalar FEA summaries are written regardless.
- **FEA VTU frame stride** — `1` exports every recorded frame; larger values sample frames, and the final frame is always included.
- **Export ParaView datasets after run** — writes PVD/VTK time series into `<study>/paraview`.
- **ParaView export stride** — general frame stride.
- **ParaView particle stride** — `0` follows the general stride; larger values skip particle frames independently.
- **ParaView particle render stride** — draw only every Nth particle in each frame.
- **Open results folder after run** / **Open ParaView after run** — convenience launchers.

The note at the bottom of the page summarizes the ParaView behaviour; the same information is on the Tools tab.

### Page 6 — Review & Run

The top pane shows the generated commands; the lower pane streams the stage log while running. Use **Refresh preview** after changing a field.

Along the bottom of the Study Wizard tab (outside page 6):

- **< Back**, **Next >** — navigate pages.
- **Apply CAD/mesh defaults**, **Load study_config.json**, **Save config/commands (dry run)** — as described above.
- **Run one-way FEA only (existing CRM)** — replays an already recorded `crm/soil_loads.csv` without rerunning CRM. Useful when only FEA settings changed.
- **Run study** — run the whole pipeline.

Inside page 6's lower run bar:

- **Skip current step** — terminate the active command and mark it skipped; remaining queued stages still run. The whole process group is signalled, so worker processes such as the CRM particle writers are stopped too and the runner can advance to the next stage. If no command is currently running, nothing is skipped and the status line says so.
- **Stop running process** — terminate the active stage (including its worker processes) and abort the remaining steps.

The status line reports what is happening (for example, "Viewing <doc>" when you use the Docs tab).

## Defaults tab

Starting values for new studies are kept in `study_gui_defaults.json` in the project root (gitignored). Buttons:

- **Save defaults** — persist the values currently shown on the Defaults tab.
- **Apply to current study** — copy them into the wizard fields.
- **Copy current study values** — pull the wizard's current values into Defaults (press Save to keep them).
- **Reset to shipped defaults** — remove the saved file and restore built-in values; this does not change the current study until you apply them.

Fields are grouped by wizard page. Study name and output directory are intentionally not defaults; they are generated per run.

## Tools / View Results tab

**Study/output directory** starts at the study wizard's output directory and follows edits to that field. Set it to the folder containing `study_config.json` (or an `Output_*` folder) and use **Browse** if you want to inspect a different run. The actions are:

| Button | What it does |
| --- | --- |
| **Export ParaView VTK/PVD (separate particles & excavator)** | Runs `export_study_vtk.py` with the strides and workers set on this tab. Progress streams into the log. |
| **Open ParaView 3D view (pvpython, spheres + FEA stress)** | Launches ParaView with `view_study_paraview.py`, using the checkboxes and orbit settings below. |
| **Open interactive result plots** | Runs `view_results.py` to draw four summary plots and write `results_overview.png`. In the interactive window, a shared crosshair follows the pointer: a vertical time line and a horizontal line on each plot meeting it at the plotted value. |
| **Open lightweight CRM/FEA output preview** | Opens the Output Preview tab for this study. |
| **Preview STEP mesh (full-run mesher + comparison)** | Runs the current Geometry-page settings through the real mesher into `geometry_checks/mesh_preview/` and opens the geometry comparison. Active study outputs are untouched. |
| **View geometry comparison (STEP vs converted)** | Renders the CAD and converted surface side by side. |
| **Live 3D fill-detector size adjuster** | Opens the standalone detector sizer. |
| **Open study folder** | Opens the output folder in your file manager. |

Checkboxes:

- **Load 3D FEA stress/deformation in ParaView (if valid)** — on by default. The viewer rechecks the PVD and its frame files: a structurally usable multi-frame `fea/vtk/fea.pvd` can be loaded, but a lone `t=0` frame from a cancelled run is ignored. It shows the raw grid colored by von Mises stress; the `Warp By Vector` filter starts hidden and can be toggled in the Pipeline Browser. The excavator/drum source is rendered at 40% opacity so it does not hide the overlay.
- **Show one-way FEA scalar/deformation history charts** — off by default. Enables the XY charts and clears any inherited `PYCHRONO_FEA_CHARTS` setting when unchecked.

Export controls: **Quick export stride**, **Particle frame stride** (`0` = same as quick), **Particle render stride**, and **Export workers** for parallel frame writes.

Camera orbit controls: **Auto camera orbit around drum**, orbit speed (revolutions per simulation second), radius (`0` auto-fits the excavator bounds), inclination, start angle, and keyframe count. The viewer follows the vertical Z axis and primes the camera track before the first render, so the first frame opens framed instead of blank; no manual step back/forward is needed.

ParaView is launched with `QT_QPA_PLATFORM=xcb` by default to avoid Wayland crashes in some builds. Set `PYCHRONO_PARAVIEW_QT_PLATFORM` to override that value.

## Output Preview tab

A Tk-only viewer that reads `crm/particles_*.csv`, `crm/summary.csv`, and `fea/summary.csv` directly — no VTK conversion needed. It shows:

- a 3D particle point cloud (orthographic or perspective), drag to orbit and wheel to zoom; a 2D Y-Z cross-section is also available;
- a wireframe drum with an orange index mark that follows the signed `spin_deg` from CRM, so **Reverse spin** is visible here;
- CRM and FEA scalar timelines with a lightweight Play control;
- an editable particles-to-sample count (default 5,000), a particle-frame stride, marker size, and line width.

Every row of the displayed frame is read, so the reported particle count is the true simulated count; the sample limit only bounds how many dots are drawn. The preview starts on the study wizard's output directory and follows edits to that field; you can also change the directory from the Tools tab or from its own directory entry.

## Docs tab

The left pane lists the documentation pages; the right pane shows the selected Markdown file, except for the generated **GUI parameter reference**, which is assembled from the app's own help tables. Headings use distinct colors and sizes, body text is easier to read than the log panes, code appears in a shaded monospace box, and quotes are indented. Tables are embedded widgets with real aligned columns, a highlighted header row, and a horizontal scrollbar when the content is wide, so they no longer depend on monospaced padding. Inline **bold**, *italic*, and `code` are rendered as styles rather than Markdown punctuation.

Click a link to jump to another registered Docs page (or to a heading when the link has an anchor); web links open in your browser. If a linked file is not registered in the Docs tab, the status line says so instead of opening an external viewer. Use **Find** (or Enter in the search box) to highlight the next match from the cursor on the current page; it wraps at the end and highlights one match at a time. Use **Refresh** to reload the selected page after editing a file on disk or changing GUI help text.

The list starts with the documentation hub and beginner pages, then the workflow and in-depth guides, followed by the generated GUI parameter reference and the original project documents.

## Settings tab

| Setting | Meaning |
| --- | --- |
| GUI scale | Widget density and padding (0.75–2.0); `1.0` uses system density. |
| Text size | Point size for labels, entries, buttons, and log panels; applied immediately. |
| Window width / height | Size used the next time the GUI starts. |
| Text color / dark mode | Theme colours; picking a colour opens the system colour dialog. |
| Show output preview tab | Add or hide the Output Preview tab. |
| Preview particles to sample | Drawn point limit (default 5,000). |
| Preview frame stride | Show every Nth particle CSV frame. |
| Preview particle size / line width | Marker and trace sizes in the preview. |

**Apply now** affects this session only (including resizing the live window) and does not save. **Save settings** writes `study_gui_settings.json` for the next launch. **Reset to defaults** restores the shipped values and writes that file immediately. Display limits and marker/line sizes edited directly in the Output Preview tab are saved immediately as well.

## Common GUI workflows

**Run a STEP study:** page 1 choose `step` and the output folder → page 2 pick STEP file, axis, unit scale → **Apply CAD/mesh defaults** → page 3 set duration/spacing → page 5 check export options → page 6 **Run study** → Tools tab → preview or ParaView.

**Compare two geometries:** run the first study, then use **Save config/commands (dry run)** to capture it; change only the geometry fields and output folder, save again, and run. Compare `fill_ratio_mass` and `specific_energy_j_kg` in the two `crm/summary.csv` files. Keep soil and motion settings identical.

**Replay FEA with new settings:** use **Run one-way FEA only (existing CRM)**; it reuses the recorded loads and skips the SPH stage.

**Inspect a conversion before running:** Tools → **Preview STEP mesh**. The result goes to `geometry_checks/mesh_preview/`, never into an active study folder.

**Find a setting:** the **GUI parameter reference** page in the Docs tab contains the exact Info text for every wizard and settings field.
