# Chrono::CRM excavator driver

`crm/lunar_excavator_crm.py` is the **primary design model** for excavator geometry studies.
It simulates bulk soil interaction with Chrono::CRM (SPH continuum) to compare fill ratio,
soil response, torque, and specific energy across drum/spike geometries.

## Role and scope

- **Rigid Chrono::CRM (this directory):** production/design driver for geometry sweeps.
- **One-way CRM → FEA (`run_oneway_fea.sh`):** structural screening by replaying rigid CRM soil loads on a solid PLA mesh (no SPH in replay).
- **Two-way FEA (`run_fea.sh`):** diagnostic only, not engineering-validated (see `FEA_COUPLING.md`).

Soil/FEA metrics count **SPH fluid markers only**. Older comparisons that included BCE markers are invalid.

Fill uses the same instantaneous **excavated** set as the specific-energy metric: a particle must be fully inside a detector cylinder eroded by `rp` radially and at both axial endcaps (bounds clamped at zero), where `rp = 0.5 × --spacing` and Chrono uses `d0 = --spacing`. By default that cylinder is sized from the drum radius and full length; `--no-detector-auto-size` with `--detector-radius`/`--detector-length` sizes it independently. The analytical cylinder follows the excavator's full live pose. `inside_drum`, `fill_ratio_count`, `fill_ratio_mass`, and both particle CSV flag columns all use that set. `lifted` is reported separately as a diagnostic (above the initial bed by one SPH radius) but does not gate fill.

Classification is current-state only: a particle loses its flag as soon as it leaves the eroded cylinder or drops back to the bed. The lifted predicate, SPH/BCE bookkeeping, and particle-position writers are unaffected. The manual detector dimensions affect only classification; they are not used for the soil mesh, container, or capacity denominator. Capacity starts from the nominal `inner_radius` cylinder and, for derived runs, subtracts the loaded excavator mesh volume inside that cavity once at startup so hubs, spokes, and flights are not counted as unfillable space.

Generated studies keep `--drum-radius`/`--drum-half-length` tied to the CAD or mesh actually loaded. With auto geometry enabled (the default), an edited outer dimension is restored to the measured value, an impossible cavity radius is reset, and a kinematic duration shorter than the full descent is extended (force mode has no analytic descent time, so that correction is skipped); the corrections are recorded in `study_config.json`. Disable auto geometry only when using a mesh that intentionally matches a custom radius. Geometry auto-correction manages the physical drum only and never overwrites manual detector dimensions. Detector flags are added only to the rigid-CRM command; the one-way and coupled FEA drivers reject them. The fill measure and SPH/BCE counts are not changed by this check.

## Requirements

A standard PyPI `pychrono` wheel is not sufficient (it omits `pychrono.fsi`, `pychrono.vehicle`, and the local PardisoMKL binding).
Use the local Chrono 11 source build with FSI-SPH, CRM, and PardisoMKL enabled (see `../environment/INSTALL.md`).

```bash
source ../environment/activate-chrono-source.sh
PYTHON="$CHRONO_ENV/bin/python" ./run.sh --smoke-test --mesh-file /path/to/your_drum.obj
```

Replace `/path/to/your_drum.obj` with a local CRM surface mesh; no geometry is
bundled with the project.

`./run.sh --help` and `./run.sh --print-soil` work without CRM modules because imports are deferred.

## Soil parameters

Defaults are defined in `crm_soil.py` as independent best-estimate bulk parameters for a loose
lunar-regolith simulant (not calibrated measurements). Keep the soil block fixed across geometry comparisons.

| Parameter | Default | Meaning |
| --- | --- | --- |
| bulk density | 1650 kg/m^3 | continuum density / fill-ratio mass basis |
| Young's modulus | 2.0 MPa | effective continuum stiffness |
| Poisson ratio | 0.30 | elastic response |
| mu_s | 0.75 | static friction (phi ~= 36.9°) |
| mu_2 | 0.75 | high-inertia friction limit |
| mu_I0 | 0.04 | reference inertial number |
| cohesion | 1.2 kPa | bulk cohesion |
| grain diameter | 200 µm | physical size for mu(I) rheology (**not** SPH spacing) |

Override via CLI (e.g. `--density`, `--youngs-modulus`, `--friction`, `--cohesion`, `--grain-diameter`).

## Common workflows

### Rigid CRM (design runs)

```bash
# Show soil block
./run.sh --print-soil

# Quick setup check
PYTHON="$CHRONO_ENV/bin/python" ./run.sh --smoke-test --mesh-file /path/to/your_drum.obj

# Baseline run
PYTHON="$CHRONO_ENV/bin/python" ./run.sh \
  --mesh-file /path/to/your_drum.obj \
  --duration 20 --fps 20 --output-dir Output_CRM_Baseline

# Alternative geometry (same pose/scale)
PYTHON="$CHRONO_ENV/bin/python" ./run.sh \
  --mesh-file /path/to/your_other_drum.obj \
  --output-dir Output_CRM_BucketDrumVariant

# Opt-in force-driven descent (kinematic is the default)
PYTHON="$CHRONO_ENV/bin/python" ./run.sh \
  --descent-mode force --descent-force 1.0 --descent-force-ramp 0.2 \
  --loads-file Output_Force/crm/soil_loads.csv --output-dir Output_Force/crm
```

### One-way CRM → FEA (structural screening)

```bash
# 1) Record rigid soil wrench
./run.sh --duration 0.2 --spacing 0.012 --dt 0.00025 \
  --loads-file Output_OneWay/crm/soil_loads.csv \
  --output-dir Output_OneWay/crm

# 2) Replay loads on solid mesh (local PardisoMKL default, dt=2e-3 s)
./run_oneway_fea.sh --duration 0.2 \
  --loads Output_OneWay/crm/soil_loads.csv \
  --output-dir Output_OneWay/fea
```

Use identical motion options in both stages; the replay checks trajectory/load consistency. The one-way replay stops at the earlier of `--duration` and the last row of `soil_loads.csv`, printing a `NOTICE` when the available CRM frames shorten it; it does not invent load samples by holding the final wrench. For a full study started from `../run_study.sh` or either GUI, the optional `--concurrent-crm-fea` / **Run CRM + one-way FEA together** mode starts both stages at once. The runner then passes `--wait-for-crm --crm-alive-file ../crm/crm_running.flag` to the replay: FEA waits for the first valid load samples, reloads the growing CSV, and defers the ordinary frame cap until CRM exits. A FEA-only run never gets those flags. For a force-driven CRM, pass `--descent-mode force` (and the same `--descent-force`/`--descent-force-ramp`) to **both** drivers. The one-way driver replays the recorded `drum_z_m` trajectory with a position motor and rejects a loads file recorded in the other mode; the two-way driver applies the actuator directly to the slider.

### Two-way FEA (diagnostic only)

```bash
./run_fea.sh --duration 0.05 --spacing 0.012 --dt 0.00025 \
  --fps 200 --no-output --stress-stride 50 \
  --solver-iterations 20 --solver-tolerance 1e-3 --bce-layers 2
```

Do not treat two-way results as design validation.

### Interactive study wizard

```bash
../run_study.sh
../run_study.sh --dry-run --duration 0.5 --spacing 0.012
```

The wizard can prompt for all settings, auto-scale derived geometry/motion/SPH defaults from CAD,
write reproducible `study_config.json`/`commands.sh`, run both one-way stages, and generate
`results_overview.png` with four panels: CRM work, CRM fill ratio, peak von Mises, and peak deformation. It can also open results interactively after completion, with a shared crosshair: a vertical time line and a horizontal line on each panel meeting it at the plotted value.

## Useful options (`run.sh`)

| Option | Meaning |
| --- | --- |
| `--duration T` | simulated seconds (default 60) |
| `--dt S` | CRM/FSI timestep (spacing-dependent default) |
| `--allow-unsafe-dt` | bypass explicit timestep stability check (not recommended) |
| `--spacing D` | SPH particle spacing (default 0.002 m) |
| `--artificial-viscosity V` | artificial bilateral SPH viscosity (default 0.5; raise to damp first-bite ejecta) |
| `--shifting-ppst-push V` | PPST shifting push strength (default 3.0; lower reduces outward particle drift) |
| `--fps F` | metric output cadence (default 20 Hz) |
| `--loads-fps F` | soil-wrench CSV cadence (default 20 Hz; one-way FEA interpolates between samples) |
| `--particle-fps F` | particle CSV cadence for ParaView (default 5 Hz; capped by `--fps`) |
| `--particle-writers N` | background processes for per-frame particle CSV writes (default 4) |
| `--settle-time T` | delay before spin/descent (default 0.001 s) |
| `--descent-mode {kinematic,force}` | descent control mode (default `kinematic`) |
| `--descent-speed V` | drum descent speed in kinematic mode (default 0.0015 m/s) |
| `--descent-force N` | downward actuator force in force mode (default 1.0 N; 0 allows gravity-only descent with `--drum-gravity`) |
| `--descent-force-ramp T` | linear force ramp after settling in force mode (default 0 s) |
| `--gravity G` | signed global Z acceleration for SPH soil and FEA drums (default -9.81 m/s²) |
| `--drum-gravity` / `--no-drum-gravity` | enable/disable gravity on the rigid CRM drum only (default off) |
| `--omega W` | spin rate in rad/s (signed/nonzero supported) |
| `--reverse-spin` | reverse spin direction (helper flag) |
| `--spin-axis {X,Y,Z}` | global spin axis (default X) |
| `--drum-radius R` | outer/crest radius (geometry/pose sizing) |
| `--drum-half-length L` | drum half axial length |
| `--bed-height H` | initial bed height |
| `--bed-padding F` | soil padding per bed side, as a fraction of drum radius (default 0.5) |
| `--container-padding D` | gap between the outermost soil particles and the fixed reflective container (default: half an SPH spacing) |
| `--inner-radius R` | nominal cavity radius for fill ratio |
| `--capacity-volume V` | CAD-derived cavity/free-volume override |
| `--detector-auto-size` / `--no-detector-auto-size` | size the fill cylinder from the drum, or use manual dimensions (default: auto) |
| `--detector-radius R` | manual nominal detector radius in m (requires `--no-detector-auto-size`) |
| `--detector-length L` | manual nominal detector **full** axial length in m; stored as half-length internally (requires `--no-detector-auto-size`) |
| `--active-box X,Y,Z` | active SPH full size (default: padded bed + 2 drum diameters laterally; bed + initial excavator height + 2 diameters + padding vertically) |
| `--mesh {coarse,medium}` | legacy local OBJ name from `lunar_excavator_config.py` (files not bundled; prefer `--mesh-file`) |
| `--mesh-file PATH` | custom OBJ geometry |
| `--loads-file PATH` | write soil wrench for one-way FEA replay at `--loads-fps` cadence |
| `--save-particles` | write per-frame SPH particle CSV (large) |
| `--export-vtk`, `--vtk-dir`, `--vtk-stride` | export ParaView PVD/VTP/VTU outputs |
| `--no-output` | benchmark without writing files |

Notes:
- Default timestep scales with spacing (e.g. ~4e-5 s at 2 mm). Explicitly oversized `--dt` values are rejected unless `--allow-unsafe-dt` is set. The rigid CRM setup also requests three boundary BCE layers explicitly; Chrono warns that fewer can leave insufficient kernel support and leak particles through walls.
- `artificial_viscosity`, `shifting_ppst_push`, and `settle_time` are editable under **Run & Domain** in `study_gui.py` and are recorded in `study_config.json`; the first two are passed only to rigid CRM, while `settle_time` is also passed to one-way FEA so the replay pose stays aligned. Raising viscosity or lowering PPST push can damp the first-contact soil eruption, but neither is a timestep fix.
- Force descent preserves the original kinematic path as the default and leaves the fill measure, capacity denominator, and SPH/BCE counts unchanged. In force mode the rigid CRM drum is held at its initial height through `settle_time`, driven downward by `--descent-force`, then held at `z_target`; soil reaction always acts, and the drum's own weight is added only with `--drum-gravity` (default off). `--descent-force 0` is valid and makes the descent gravity-only when `--drum-gravity` is enabled. Spin remains prescribed. Gravity is a single signed Z value from `--gravity`, always applied to CRM soil and FEA drums. Force runs add `descent_mode`, `descent_state`, `actuator_force_n` (positive magnitude), and `vertical_speed_m_s` to `summary.csv` and `soil_loads.csv`; kinematic CSV schemas are unchanged. The wizard/GUI fields are `descent_mode`, `descent_force_n`, `descent_force_ramp_s`, `gravity`, and `drum_gravity`.
- The soil bed is padded around the drum and the default active SPH box extends two full excavator diameters beyond the padded bed laterally, and from below the bed floor to bed height + initial excavator height + two diameters + padding above the drum centre. Chrono deactivates particles outside this **body-frame** box (zeroing their velocity), so an undersized box makes flung particles freeze in mid-air. The driver also sets a separate fixed **world-frame** computational domain from the padded bed plus six spacings laterally/below the floor and the initial rotor crest plus two rotor diameters and the ballistic rise associated with the SPH EOS reference velocity. Chrono's default world domain is derived from the initial marker AABB (bed surface plus three BCE layers), which puts a hard ceiling just above the bed and freezes excavated soil in a flat layer; the explicit domain is recorded in `config.json` and printed after `Initialize()`. The driver now also calls `AddBoxContainer()` with four side walls and a lid placed just inside the active-box ceiling. The side walls and floor sit half a spacing outside the snapped initial particle grid, so the bed is contained immediately without any BCE marker cutting through soil; these fixed BCE walls reflect ejecta back into the bed before the active box can deactivate it. Chrono uses one shared BCE grid offset, so bed and container side markers coincide and are deduplicated; increasing the gap shifts all boundary markers outward and weakens containment. The bed floor keeps its existing BCE layers, the container interior is recorded as `solid_container` in `config.json`, and the world computational domain is expanded only enough to hold the new BCE layers.
- Use the full-detail OBJ for production; legacy simplified meshes may close holes.
- Summary, soil-wrench, and particle cadences are independent. The one-way FEA driver interpolates the wrench by time, and ParaView can render a lower-rate particle series, so reducing those two cadences speeds up CRM without changing solver physics. Soil-wrench rows are flushed as they are written so a crashed run still leaves a usable load history.
- Particle CSV frames are formatted in background processes; set `--particle-writers` higher if disk writes remain a bottleneck. Starting a run with `--save-particles` removes old `particles_*.csv` frames in `--output-dir` so a shorter rerun cannot mix two particle sets in ParaView; pass `--keep-old-particles` to preserve them.
- Rigid drum mass and inertia are computed from the loaded OBJ's signed volume at the configured PLA density (falling back to the reference mass only if the mesh cannot be parsed). This matters for force descent: keeping the 0.097 kg reference mass for a 3x scaled drum gives ~200 m/s² free acceleration under a 20 N actuator. The value used is printed in the run header and recorded as `geometry.drum_mass_kg` in `config.json`.

## Outputs

Typical rigid CRM run directory (`--output-dir ...`):

- `config.json` — soil, numerics, mesh, capacity provenance (nominal volume, excavator overlap, source), and `fill_detector` settings (size mode, nominal/effective dimensions, and lift tolerance)
- `summary.csv` — per-frame metrics (pose, counts, fill ratios, torque/work/specific energy; force-mode runs also record descent state/actuator telemetry)
- `soil_loads.csv` — time-interpolated soil wrench at `--loads-fps` cadence when `--loads-file` is used (force-mode runs add the same descent telemetry columns)
- `particles_*.csv` — per-frame particle positions/flags when `--save-particles` is used
- `paraview/` — PVD/VTP/VTU exports when `--export-vtk` is used

The fill-ratio denominator starts with the nominal cylindrical cavity (`--inner-radius` × drum length). Its numerator is the excavated set: particles fully inside the detector cylinder eroded by one SPH particle radius (`rp = 0.5 × --spacing`, with Chrono `d0 = --spacing`) radially and at both axial endcaps. For derived capacities, the driver measures how much of the loaded excavator mesh lies inside the nominal cavity and subtracts it once before the run starts. That constant free volume is recorded in `crm/config.json` (`excavator_overlap_m3`, `nominal_capacity_volume_m3`, and `capacity_source`) and used for every frame. Pass `--capacity-volume` when a CAD-derived free volume is already available; the override is used unchanged and is not double-corrected. `lifted` remains a separate diagnostic in `summary.csv`/particle flags context.

## Limitations

- Default soil parameters are estimates; long production sweeps and simulant calibration are not complete.
- Default drum motion is prescribed/kinematic (reaction wrench recorded; drivetrain dynamics not modeled). Force descent is opt-in and adds only a vertical actuator; spin remains prescribed and no full drivetrain/soil feedback controller is modeled. Kinematic descent overrides the drum pose even when `--drum-gravity` is set.
- SPH spacing is a numerical resolution choice; physical grain size enters mu(I) via `average_diam`.
- Cohesive crust fracture is not represented by the default mu(I) continuum model.
- One-way FEA is a structural response to rigid loads (no soil feedback); two-way FEA coupling remains unresolved.

See `FEA_COUPLING.md` for detailed coupling evidence and mesh/conversion notes.
