# Quick start: your first excavation study

This page walks you through a complete study from a fresh checkout to a first set of results. It assumes you are comfortable running commands in a terminal, but it does not assume anything about Chrono, SPH, or FEA. If a term is unfamiliar, check the [Glossary](glossary.md).

The whole workflow has five ideas:

1. **Install** the project's local Chrono environment.
2. **Supply geometry**: a CAD model or pre-made meshes. Nothing is bundled.
3. **Configure a study** in the desktop GUI (or the terminal wizard).
4. **Run** the rigid soil simulation and, optionally, the structural replay.
5. **Look at the outputs** to compare designs.

> Time budget: installation can take a long time (it builds Chrono with CUDA). A short first study takes a few minutes to an hour depending on your GPU, mesh, and particle spacing.

## 1. Install

On Arch/CachyOS, Debian, or Ubuntu:

```bash
git clone https://github.com/Rumboogy11/lunabotics-pychrono-sim.git
cd lunabotics-pychrono-sim
./install.sh
```

The installer creates a project-local `chrono-env/` with the custom PyChrono build, CUDA toolkit, Python packages, and (by default) ParaView. It does not install anything into the system Python. It needs root access for a few system packages unless you pass `--no-system-packages`; see [Environment and build options](../environment/INSTALL.md) for CUDA architecture, ParaView, and no-root options.

Verify the environment:

```bash
source environment/activate-chrono-source.sh
./crm/run.sh --print-soil
```

`--print-soil` prints the soil parameter block and works without a GPU or a mesh. A real simulation needs a compatible NVIDIA GPU and driver.

> PyPI `pychrono` wheels will not work here: they do not include the `pychrono.fsi` and `pychrono.vehicle` modules that these drivers use.

## 2. Supply geometry

The simulation needs a *bucket drum excavator* shape. No geometry ships with the project, so choose one of these inputs:

| Geometry source | What you need | Best for |
| --- | --- | --- |
| `step` | A `.step`/`.stp` CAD assembly | Starting from CAD; the wizard converts it into meshes for you |
| `files` or `supplied` | A surface `.obj` for CRM contact, plus TetGen `.node`/`.ele` files for FEA | Reusing meshes you already made |

Put your files in `geometry/` (it is gitignored) or anywhere you like and point the GUI at them. STEP coordinates are usually millimetres, so the default `unit-scale` is `0.001`; the converter always ends up with metres. Newer models use `cad-axis = Y` for the drum's axis in the original CAD.

If you are starting from STEP, the GUI/wizard will:

1. tessellate and repair the CAD surface,
2. write a TetGen solid mesh (`*_fea.node` / `*_fea.ele`),
3. make a matching triangulated surface for rigid CRM (`*_fea_surface.obj`), and
4. measure the CAD automatically so bed, domain, spacing, and motion defaults make sense for your drum size.

You can preview that conversion before committing to a full run with the Tools tab's **Preview STEP mesh** button.

## 3. Configure a study in the GUI

Launch the application:

```bash
./run_study_gui.sh
```

On a machine without a display, use `xvfb-run ./run_study_gui.sh`. The launcher picks a Python with Tkinter for the window and uses the project environment for simulations.

The GUI opens on the **Study Wizard** tab with six pages. Work left to right; use **Next >** and **< Back** at the bottom.

### Page 1 — Study

- **Study name**: a short label. In the GUI, editing it immediately sets the output directory to `Output_<name>` in the project root.
- **Output directory**: where configs, logs, meshes, and results go. It follows the study name until you change it yourself; use a fresh folder for each study.
- **Geometry source**: `step` if you are converting CAD, otherwise `files`/`supplied`.
- **Auto-scale from CAD/mesh**: leave on. It measures the drum and suggests compatible dimensions.

Click **Apply CAD/mesh defaults** after choosing geometry to refresh the suggested values.

### Page 2 — Geometry

For a STEP study, set the **STEP file**, **CAD axis**, and **Unit scale**. The other mesh controls (target faces, fit tolerance, mesh size, interior coarsening) have good defaults; leave them alone for the first run.

The **Fill-detector cylinder** section controls how "soil inside the drum" is counted. Leave **Auto-size fill detector** checked initially. If you want to tune it visually, click **Open live 3D detector sizer**: drag the radius/length sliders until the orange cylinder matches your drum's inside, then close the viewer.

### Page 3 — Run & Domain

For a first check, use a short run so you can see results quickly:

- **CRM duration (s)**: `2` to `5` for a smoke test, `20` or more for a real run.
- **SPH spacing (m)**: `0.002` (2 mm) is the standard starting point. Smaller means more particles, much more computing, and a smaller timestep.
- **CRM dt (s or 'auto')**: leave `auto`. The GUI computes a spacing-dependent value and rejects unsafe timesteps.
- **Particle CSV FPS**: `5` is enough for visualization; the CRM summary can stay at `20`.
- **Descent mode**: `kinematic` (default) gives a constant lowering speed. `force` uses a downward actuator and is an advanced option.
- **Reverse spin**: tick it if the drum turns the wrong way in the preview.

The live label under the bed settings estimates how many SPH particles will be created. If it is enormous (millions), reduce duration, increase spacing, or shrink the bed/domain before running.

### Page 4 — Soil & Material

Leave the defaults for your first study. They are best-estimate regolith/PLA parameters, not calibrated measurements. Keep them frozen when comparing geometries.

### Page 5 — Output & Export

Recommended for a first study:

- **Save CRM particle CSV frames**: on (needed for the 3D particle view).
- **Export ParaView datasets after run**: on.
- **Export full 3D FEA stress animation**: **off**. It is expensive; scalar FEA summaries are still written.
- **Open ParaView after run**: off unless you want it launched automatically.

### Page 6 — Review & Run

Click **Refresh preview** to see the exact commands that will run. This is also a good moment to click **Save config/commands (dry run)** if you want a reproducible recipe without simulating.

Click **Run study**. Watch the log panel: the runner executes the STEP conversion (if needed), the rigid CRM stage, the one-way FEA replay, and finally the ParaView export. You can **Skip current step** to terminate the active command and continue, or **Stop running process** to abort the remaining stages.

## 4. Find and view your results

Your output folder looks like this:

```text
Output_<study_name>/
├── study_config.json          all settings actually used
├── commands.sh                the exact shell commands, reusable
├── *.log                      one log per stage
├── mesh/                      converted surface + tet mesh (STEP studies)
├── crm/
│   ├── config.json
│   ├── summary.csv            per-frame fill, torque, energy, pose
│   ├── soil_loads.csv         recorded soil wrench for FEA
│   └── particles_*.csv        SPH particle positions/flags
├── fea/
│   ├── config.json
│   └── summary.csv            stress/displacement history
├── paraview/                  VTK/PVD time series (when export is on)
└── results_overview.png       CRM/FEA summary charts (written after a successful run)
```

Fast ways to look at them:

- In the GUI, open **Tools / View Results** → **Open lightweight CRM/FEA output preview**. It reads the CSVs directly, draws a 3D particle cloud, and shows CRM/FEA timelines. Drag to orbit, scroll to zoom.
- In the GUI, **Tools / View Results** → **Open interactive result plots** draws summary charts and writes `results_overview.png`.
- From the shell:

```bash
./chrono-env/bin/python view_results.py --output-dir Output_<study_name> --show
./chrono-env/bin/python view_geometry.py --study-dir Output_<study_name>
```

- For 3D particles and drum motion, open the ParaView view:

```bash
QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=Output_<study_name>/paraview \
  paraview --script="$PWD/view_study_paraview.py"
```

See [Outputs and visualization](outputs-and-visualization.md) for the full tour.

## 5. Read the numbers

The most useful columns in `crm/summary.csv` are:

- `fill_ratio_mass` — mass of soil fully inside the drum divided by nominal drum capacity. This is the main design metric.
- `inside_drum` / `excavated` — counts of particles that currently satisfy the detector test.
- `fluid_torque_x_nm` — soil torque about the spin axis.
- `work_j` and `specific_energy_j_kg` — energy used and energy per kilogram of excavated soil.

Important: the detector test uses the **current** pose of the drum and the **current** particle positions. Soil that was once inside but has fallen out no longer counts. Only SPH fluid markers are counted in metrics; boundary (BCE) markers are not soil.

The one-way FEA stage answers a different question: roughly how much stress and deflection the drum sees under the recorded soil loads. It does **not** feed soil feedback back into the drum, and it is not a validated two-way simulation. Do not use two-way FEA or the combined `full_model.py` runner for design validation; see [How it works](how-it-works.md).

## 6. Next steps

- Rerun with a longer duration once the short study works.
- Copy `commands.sh` to a new folder and edit the mesh path to compare a second geometry. Keep soil, bed, and motion settings identical.
- Use the **Defaults** tab (or `study_gui_defaults.json`) to make your favourite starting values apply to new studies.
- If something looks wrong, start with [Troubleshooting](troubleshooting.md).
- When you want the full picture, read [How it works](how-it-works.md) and the original [CRM solver reference](../crm/README.md).
