# Command-line study wizard

`study_wizard.py` is the terminal counterpart of the GUI. It asks a series of questions, writes a reproducible configuration, and then runs the same stages the GUI runs: optional STEP conversion → rigid CRM → one-way FEA → optional ParaView export. Most prompts also have command-line flags, so the wizard works well non-interactively in scripts; a few final post-run choices are controlled by dedicated flags rather than a matching prompt.

For a first run, the [GUI quick start](quickstart.md) is easier. Use this page when you want automation, remote runs, or exact control over every setting.

## Start the wizard

```bash
./run_study.sh
```

The launcher adds `chrono-env/bin` to `PATH` and uses `chrono-env/bin/python` unless `PYTHON` is set. You can run `study_wizard.py` directly with the project Python if you prefer.

You will see:

```text
Lunar excavator CRM -> FEA study setup
Press Enter to accept values in brackets.
```

At each prompt, press Enter to accept the default, or type a new value. Yes/no questions accept `y`, `yes`, `true`, or `1`; anything else is treated as no. Paths are checked for existence. A number that cannot be parsed is asked again; a number outside the allowed range or a malformed active box aborts with an error.

## What it asks, in order

1. **Study name and output directory.** The name defaults to a timestamp. The output folder defaults to `Output_<name>`. An interactive run that finds an existing `study_config.json` asks before overwriting/continuing; `--dry-run` and `--yes` skip that prompt.
2. **Geometry source.** `supplied`, `step`, or `files`. STEP mode asks for the CAD file, drum axis, unit scale, and (in an interactive run with default settings) whether to auto-scale dimensions from the CAD. Existing-mesh mode asks for the CRM surface OBJ and FEA `.node`/`.ele` paths.
3. **STEP conversion settings** (STEP mode only): mesh size, surface face target/maximum, exterior fit tolerance, minimum solid volume, MeshFix order, and part-aware interior coarsening settings. The defaults are suitable for most designs.
4. **Drum dimensions:** outer radius, half-length, cavity radius, optional capacity volume, and fill-detector sizing (automatic by default).
5. **Time and numerics:** CRM/FEA duration, SPH spacing, d0 multiplier, artificial viscosity, PPST push, output cadences, timesteps, and particle writer count. The wizard offers a spacing-dependent safe CRM timestep and lets you override it.
6. **Domain:** bed height, bed padding, active box, settle time.
7. **Gravity and motion:** global gravity Z, rigid-drum gravity toggle, descent mode/speed/force/ramp, spin rate, reverse spin, and spin axis.
8. **Soil parameters:** accept the defaults or edit density, stiffness, Poisson ratio, friction, cohesion, and grain diameter. Changes should be frozen across comparison runs.
9. **FEA settings:** structural timestep, PLA material, solver, thread count, load radius fraction, and stress-evaluation stride.
10. **Outputs:** save CRM particles, export ParaView datasets, optionally export full 3D FEA stress frames, export strides, and whether to open results or ParaView afterwards.
11. **Dry run confirmation:** choose whether to write configuration only or actually run the commands.

## Useful command-line flags

Run `./run_study.sh --help` for the authoritative complete list. The tables below group the flags that correspond to common prompts plus a few useful overrides; not every prompt has an identically named flag.

### Selecting geometry

| Flag | Purpose |
| --- | --- |
| `--study-name NAME`, `--output-dir PATH` | Name and destination of the study. |
| `--geometry {supplied,step,files}` | Which geometry source to use. |
| `--step-file PATH` | CAD input for `step` mode. |
| `--crm-obj PATH`, `--fea-node PATH`, `--fea-ele PATH` | Existing mesh files for `files`/`supplied` mode. |
| `--no-auto-geometry` | Keep fixed legacy defaults instead of scaling from CAD size. |
| `--cad-axis {auto,X,Y,Z}`, `--unit-scale F` | Drum axis in CAD space and coordinate scale to metres. |
| `--drum-radius R`, `--drum-half-length L`, `--inner-radius R`, `--capacity-volume V` | Physical drum dimensions and nominal capacity. |
| `--detector-auto-size` / `--no-detector-auto-size`, `--detector-radius R`, `--detector-length L` | Fill-detector sizing; length is the full axial length. |
| `--mesh-size D`, `--surface-target-faces N`, `--surface-max-faces N`, `--surface-fit-tolerance D` | STEP meshing and exterior fit controls. |
| `--surface-repair-order {none,before,after}`, `--min-solid-volume V`, `--hub-radius R` | Legacy repair order, tiny-solid filter, and rigid hub radius. |
| `--part-aware-simplify` / `--no-part-aware-simplify`, `--part-aware-radius R`, `--part-aware-reduction F`, `--part-aware-min-faces N` | Interior-only coarsening controls. |

### Time, motion, and domain

| Flag | Purpose |
| --- | --- |
| `--duration T`, `--fea-duration T` | CRM duration and replay length. The wizard rejects an FEA duration above the CRM duration, and the replay additionally stops at the last recorded load sample if the loads file is shorter (with a notice, not an error). |
| `--spacing D`, `--crm-dt S` | SPH spacing and timestep; `--crm-dt auto` picks the suggested value. |
| `--d0-multiplier F`, `--artificial-viscosity V`, `--shifting-ppst-push V` | SPH kernel and damping/particle-shift controls. |
| `--fps F`, `--crm-fps F`, `--fea-fps F`, `--loads-fps F`, `--particle-fps F`, `--particle-writers N` | Independent output cadences and background particle writers. |
| `--bed-height H`, `--bed-padding F`, `--active-box X,Y,Z`, `--settle-time T` | Soil bed, SPH domain, and pre-spin settling. |
| `--gravity G`, `--drum-gravity` / `--no-drum-gravity` | Signed global gravity and the rigid-drum-only toggle. |
| `--descent-mode {kinematic,force}`, `--descent-speed V`, `--descent-force N`, `--descent-force-ramp T` | Drum motion. |
| `--omega W`, `--reverse-spin` / `--no-reverse-spin`, `--spin-axis {X,Y,Z}` | Drum spin. |

### Materials and FEA

| Flag | Purpose |
| --- | --- |
| `--density`, `--youngs-modulus`, `--poisson-ratio`, `--friction`, `--cohesion`, `--grain-diameter` | Soil parameters; freeze them across geometry comparisons. |
| `--fea-dt S`, `--solver {pardiso-mkl,sparse-lu,sparse-qr,gmres}`, `--num-threads N` | Structural timestep and linear solver. |
| `--pla-youngs-modulus`, `--pla-poisson-ratio`, `--pla-density`, `--rayleigh-beta` | PLA drum material and damping. |
| `--load-radius-fraction F`, `--raw-stress-every N` | FEA load-spread and stress-scan cost controls. |

### Output and control

| Flag | Purpose |
| --- | --- |
| `--save-particles` / `--no-particles` | Write CRM particle CSV frames. |
| `--export-vtk` / `--no-export-vtk` | Write ParaView datasets after the run. |
| `--fea-export-vtk` / `--no-fea-export-vtk`, `--vtk-stride N` | Full 3D FEA stress animation (expensive, off by default). |
| `--paraview-stride N`, `--paraview-particle-stride N`, `--paraview-particle-render-stride N`, `--export-workers N` | Export/render strides and parallel frame writers. |
| `--dry-run` | Write configuration and commands, but do not simulate. |
| `--yes` | Accept defaults without confirmation prompts. |
| `--open-results`, `--open-paraview`, `--no-open` | Post-run conveniences. |

## Common invocations

```bash
# Fully interactive
./run_study.sh

# Inspect settings and generated commands without simulating
./run_study.sh --dry-run --duration 0.5 --spacing 0.012

# Non-interactive STEP study (all prompts answered by flags, accept the rest)
./run_study.sh --yes --geometry step \
  --step-file geometry/MyDrum.step --cad-axis Y --unit-scale 0.001 \
  --duration 20 --output-dir Output_MyDrum

# Existing meshes, short structural-screening study
./run_study.sh --yes --geometry files \
  --crm-obj mesh/drum.obj --fea-node mesh/drum.node --fea-ele mesh/drum.ele \
  --duration 2 --spacing 0.002 --save-particles --export-vtk

# Force-descent variant (use matching options in both stages)
./run_study.sh --yes --geometry step --step-file geometry/MyDrum.step \
  --descent-mode force --descent-force 1.0 --descent-force-ramp 0.2 \
  --drum-gravity --duration 30
```

## What the wizard writes

Every study, including a dry run, produces a reproducible recipe in the output directory:

```text
Output_<study_name>/
├── study_config.json      every setting actually used, plus generated stage commands
├── commands.sh            executable shell script with one command per stage and its log
├── results_overview.png   CRM/FEA summary charts written after a successful run
└── (stage outputs after a real run)
```

`commands.sh` changes to the project root, then runs each stage with output piped to a matching `.log` file. You can rerun it directly (`bash Output_<name>/commands.sh`) or copy and edit it for a parameter sweep.

The `commands` array in `study_config.json` contains the same commands as structured argument lists, which is handy for automation and debugging. The GUI's **Run one-way FEA only (existing CRM)** button writes `oneway_fea_config.json` and `oneway_fea_commands.sh` instead, so a FEA-only replay does not overwrite the full study recipe.

## Wizard versus GUI

| Task | Wizard | GUI |
| --- | --- | --- |
| Configure a study | Sequential text prompts | Six scrollable pages with Info buttons |
| Reuse settings | CLI flags or saved `study_config.json` | **Load study_config.json**, Defaults tab |
| Visit steps out of order | Not applicable | Click any wizard sub-tab |
| Skip a stage | Not offered | **Skip current step** |
| Replay FEA only | Use `./crm/run_oneway_fea.sh` directly (see [CLI reference](cli-reference.md)) | **Run one-way FEA only (existing CRM)** button |
| Tune detector visually | Standalone `detector_size_tool.py` | Live sizer wired to the Geometry page |
| Inspect outputs | `view_*.py` commands | Tools tab buttons and Output Preview tab |

Both paths call the same `study_wizard.py` functions to build commands, so a study configured in one can be inspected or rerun from the other.

## Automation tips

- Use `--dry-run --yes` in CI to validate a configuration cheaply; the generated `commands.sh` shows exactly what would run.
- Keep soil, gravity, descent, and motion settings identical when comparing two geometries. Change only the geometry and output folder.
- For long runs, keep `--loads-fps` and `--particle-fps` lower than `--fps` to reduce disk I/O; one-way FEA interpolates the loads by time.
- The wizard re-execs itself under the project Python once. `PYCHRONO_WIZARD_REEXEC=1` is an internal guard, not a user option.
- After a successful run, the wizard can open the results folder, the result plots, or ParaView; disable all of these with `--no-open` for headless automation.

If a run fails or produces zero fill, see [Troubleshooting](troubleshooting.md).
