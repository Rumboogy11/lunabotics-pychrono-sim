# Chrono::CRM excavator driver

`crm/lunar_excavator_crm.py` is the **primary design model** for excavator geometry studies.
It simulates bulk soil interaction with Chrono::CRM (SPH continuum) to compare fill ratio,
soil response, torque, and specific energy across drum/spike geometries.

## Role and scope

- **Rigid Chrono::CRM (this directory):** production/design driver for geometry sweeps.
- **One-way CRM → FEA (`run_oneway_fea.sh`):** structural screening by replaying rigid CRM soil loads on a solid PLA mesh (no SPH in replay).
- **Two-way FEA (`run_fea.sh`):** diagnostic only, not engineering-validated (see `FEA_COUPLING.md`).

Soil/FEA metrics count **SPH fluid markers only**. Older comparisons that included BCE markers are invalid.

Fill/cavity metrics require **full particle containment**, not just a centre-inside test: the SPH particle radius `rp = 0.5 × --spacing` (Chrono `d0 = --spacing`) is subtracted from the cavity radius, outer drum radius, and axial half-length (clamped at zero) before classifying particles. A particle whose centre lies near any boundary is rejected even if that centre is nominally inside the envelope. The nominal cavity volume used as the capacity denominator is unchanged; only the particle counts change.

For a geometry-specific cavity, pass `--fill-detector-obj path/to/detector.obj` (generated from a second STEP file by `../study_wizard.py --fill-detector-step` or the GUI's **Fill-detector STEP** field). The OBJ must be watertight, metre-scale, and in the same drum-local frame as the CRM contact mesh. Containment then uses exact ray/parity crossings against that closed surface, with the same `rp` boundary margin. It replaces only the analytic retained-particle predicate: capacity, excavated/lifted tests, SPH/BCE bookkeeping, and particle-position writers are untouched.

Generated studies keep `--drum-radius`/`--drum-half-length` tied to the CAD or mesh actually loaded. With auto geometry enabled (the default), an edited outer dimension is restored to the measured value, an impossible cavity radius is reset, and a duration shorter than the full descent is extended; the corrections are recorded in `study_config.json`. Disable auto geometry only when using a mesh that intentionally matches a custom radius. The containment predicates and SPH/BCE counts are not changed by this check.

## Requirements

A standard PyPI `pychrono` wheel is not sufficient (it omits `pychrono.fsi` and `pychrono.vehicle`).
Use the local Chrono 10 source build with FSI-SPH and CRM enabled (see `../environment/INSTALL.md`).

```bash
source ../environment/activate-chrono-source.sh
PYTHON="$CHRONO_ENV/bin/python" ./run.sh --smoke-test
```

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
PYTHON="$CHRONO_ENV/bin/python" ./run.sh --smoke-test

# Baseline run
PYTHON="$CHRONO_ENV/bin/python" ./run.sh \
  --duration 20 --fps 20 --output-dir Output_CRM_Baseline

# Alternative geometry (same pose/scale)
PYTHON="$CHRONO_ENV/bin/python" ./run.sh \
  --mesh-file ../geometry/LunarDrumScrew_spiked.obj \
  --output-dir Output_CRM_Spiked
```

### One-way CRM → FEA (structural screening)

```bash
# 1) Record rigid soil wrench
./run.sh --duration 0.2 --spacing 0.012 --dt 0.00025 \
  --loads-file Output_OneWay/crm/soil_loads.csv \
  --output-dir Output_OneWay/crm

# 2) Replay loads on solid mesh (defaults: PardisoMKL if available, dt=2e-3 s)
./run_oneway_fea.sh --duration 0.2 \
  --loads Output_OneWay/crm/soil_loads.csv \
  --output-dir Output_OneWay/fea
```

Use identical motion options in both stages; the replay checks trajectory/load consistency.

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
`results_overview.png`. It can also open results interactively after completion.

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
| `--descent-speed V` | drum descent speed (default 0.0015 m/s) |
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
| `--active-box X,Y,Z` | active SPH full size (default: padded bed + 2 drum diameters laterally; bed + initial excavator height + 2 diameters + padding vertically) |
| `--mesh {coarse,medium}` | shipped OBJ selection |
| `--mesh-file PATH` | custom OBJ geometry |
| `--loads-file PATH` | write soil wrench for one-way FEA replay at `--loads-fps` cadence |
| `--save-particles` | write per-frame SPH particle CSV (large) |
| `--export-vtk`, `--vtk-dir`, `--vtk-stride` | export ParaView PVD/VTP/VTU outputs |
| `--no-output` | benchmark without writing files |

Notes:
- Default timestep scales with spacing (e.g. ~4e-5 s at 2 mm). Explicitly oversized `--dt` values are rejected unless `--allow-unsafe-dt` is set.
- `artificial_viscosity`, `shifting_ppst_push`, and `settle_time` are editable under **Run & Domain** in `study_gui.py` and are recorded in `study_config.json`; the first two are passed only to rigid CRM, while `settle_time` is also passed to one-way FEA so the replay pose stays aligned. Raising viscosity or lowering PPST push can damp the first-contact soil eruption, but neither is a timestep fix.
- The soil bed is padded around the drum and the default active SPH box extends two full excavator diameters beyond the padded bed laterally, and from below the bed floor to bed height + initial excavator height + two diameters + padding above the drum centre. Chrono deactivates particles outside this **body-frame** box (zeroing their velocity), so an undersized box makes flung particles freeze in mid-air. The driver also sets a separate fixed **world-frame** computational domain from the padded bed plus six spacings laterally/below the floor and the initial rotor crest plus two rotor diameters and the ballistic rise associated with the SPH EOS reference velocity. Chrono's default world domain is derived from the initial marker AABB (bed surface plus three BCE layers), which puts a hard ceiling just above the bed and freezes excavated soil in a flat layer; the explicit domain is recorded in `config.json` and printed after `Initialize()`. The driver now also calls `AddBoxContainer()` with four side walls and a lid placed just inside the active-box ceiling. The side walls and floor sit half a spacing outside the snapped initial particle grid, so the bed is contained immediately without any BCE marker cutting through soil; these fixed BCE walls reflect ejecta back into the bed before the active box can deactivate it. The bed floor keeps its existing BCE layers, the container interior is recorded as `solid_container` in `config.json`, and the world computational domain is expanded only enough to hold the new BCE layers.
- Use the full-detail OBJ for production; legacy simplified meshes may close holes.
- Summary, soil-wrench, and particle cadences are independent. The one-way FEA driver interpolates the wrench by time, and ParaView can render a lower-rate particle series, so reducing those two cadences speeds up CRM without changing solver physics.
- Particle CSV frames are formatted in background processes; set `--particle-writers` higher if disk writes remain a bottleneck.

## Outputs

Typical rigid CRM run directory (`--output-dir ...`):

- `config.json` — soil, numerics, mesh, and capacity settings
- `summary.csv` — per-frame metrics (pose, counts, fill ratios, torque/work/specific energy)
- `soil_loads.csv` — time-interpolated soil wrench at `--loads-fps` cadence when `--loads-file` is used
- `particles_*.csv` — per-frame particle positions/flags when `--save-particles` is used
- `paraview/` — PVD/VTP/VTU exports when `--export-vtk` is used

Fill ratio uses a nominal cylindrical cavity by default (`--inner-radius` × drum length); retained particles must be completely inside that cavity eroded by one SPH particle radius (`0.5 × --spacing`, with Chrono `d0 = --spacing`), and excavated particles must be completely inside the similarly eroded outer drum envelope. The capacity denominator always remains the nominal (un-eroded) volume; pass `--capacity-volume` when a CAD-derived free volume is available for fairer geometry comparisons.

## Limitations

- Default soil parameters are estimates; long production sweeps and simulant calibration are not complete.
- Drum motion is prescribed/kinematic (reaction wrench recorded; drivetrain dynamics not modeled).
- SPH spacing is a numerical resolution choice; physical grain size enters mu(I) via `average_diam`.
- Cohesive crust fracture is not represented by the default mu(I) continuum model.
- One-way FEA is a structural response to rigid loads (no soil feedback); two-way FEA coupling remains unresolved.

See `FEA_COUPLING.md` for detailed coupling evidence and mesh/conversion notes.
