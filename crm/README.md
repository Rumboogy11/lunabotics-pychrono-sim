# Chrono::CRM excavator driver

This is the **primary design model** for excavator geometry development. It uses
Chrono::CRM's SPH continuum soil to compare bulk fill ratio and soil response
for alternative drum/spike geometries. It deliberately does **not** use the old
MercuryDPM particle contact parameters.

## Role in the project

| Model | Role |
| --- | --- |
| **Chrono::CRM** (this directory) | geometry sweeps, fill ratio, bulk torque and specific energy |
| DEM-Engine (`../deme/`) | short targeted checks of discrete mechanisms and CRM calibration |
| PyChrono core (`../pychrono_core/`) | illustrative CPU demo only |
| MercuryDPM outputs | historical sanity check only |

The excavator is PLA plastic. Two FEA paths exist for the promoted solid mesh
`fea/LunarDrumScrew_m_fea_decim8k.node/.ele`:

* **One-way CRM -> FEA (current structural estimate).** Run rigid CRM with
  `--loads-file`, then replay the recorded soil wrench on the FEA drum with
  `./crm/run_oneway_fea.sh`. No SPH terrain is created in the replay, so flex-BCE
  instability is avoided. The result is a structural response to measured rigid
  soil loads, not two-way FSI.
* **Two-way coupled diagnostic.** `./crm/run_fea.sh` couples the solid mesh to
  CRM with `terrain.AddFeaMesh(mesh, False)`. No tested BCE setup is validated:
  one layer crashes, two layers diverge mechanically by 0.5 s, and three layers
  show interaction/retention instability.

Keep the rigid kinematic driver in this directory as the production design
model. See [FEA_COUPLING.md](FEA_COUPLING.md) for the full findings, mesh route,
and unresolved two-way contact/rigid-rotation issues.

Real lunar regolith is typically tens to hundreds of micrometres across, while
the DEME model uses 1.8 mm diameter grains. Refining DEME toward actual grain
size is far too expensive for geometry optimization. In CRM, physical grain size
enters the mu(I) rheology through `average_diam`; the SPH spacing is a numerical
resolution choice and is independent of it.

## Requirements

A standard PyPI `pychrono` wheel is **not sufficient**: it currently omits
`pychrono.fsi` and `pychrono.vehicle`. Running this driver requires a
Chrono build with FSI/SPH and CRM modules enabled and an NVIDIA CUDA GPU. The
primary environment is now the **local Chrono 10.0.0 source build** under the
project root:

```bash
source ../environment/activate-chrono-source.sh
../environment/build-chrono-source.sh --clean   # only if chrono-install/ is missing
PYTHON="$CHRONO_ENV/bin/python" ./run.sh --smoke-test
```

The local toolchain uses `chrono-src/`, `chrono-env/` (Python 3.12, CMake 3.31,
Ninja, SWIG, GCC 14.4, CUDA 12.9), `chrono-build/`, and `chrono-install/`.
CUDA 12.9 is required by the Chrono 10.0.0 FSI-SPH source; CUDA 13 is not
supported. The build targets `sm_120` for the RTX 5070 Ti. `run.sh` defaults to
`../chrono-env/bin/python` when `PYTHON` is unset.

Chrono 10 exposes the soil properties as `fsi.ElasticMaterialProperties` through
`terrain.SetElasticSPH()`; the driver also supports the older
`SoilProperties`/`SetCrmSPH()` API. The previous conda build at
`/tmp/opencode/chrono-conda2` is only a fallback. See
`../environment/INSTALL.md`.

`./crm/run.sh --help` and `./crm/run.sh --print-soil` work without CRM because
the Chrono imports are deferred until a simulation starts.

## Independent soil estimates

Defaults are in `crm_soil.py`. They are best-estimate starting values for a
loose lunar-regolith simulant, not calibrated measurements.

| Parameter | Default | Meaning |
| --- | --- | --- |
| bulk density | 1650 kg/m^3 | continuum density and fill-ratio mass basis |
| Young's modulus | 2.0 MPa | effective continuum stiffness |
| Poisson ratio | 0.30 | elastic response |
| mu_s | 0.75 | static friction; phi ~= 36.9 deg |
| mu_2 | 0.75 | high-inertia friction limit |
| mu_I0 | 0.04 | reference inertial number |
| cohesion | 1.2 kPa | bulk cohesion of fine regolith/simulant |
| grain diameter | 200 um | physical size used by mu(I), **not** SPH spacing |

Override them on the command line (`--density`, `--youngs-modulus`,
`--friction`, `--cohesion`, `--grain-diameter`, etc.). Keep one soil block fixed
across all geometry comparisons.

## Running

```bash
# Print the independent soil block; no CRM build required.
./crm/run.sh --print-soil

# Quick setup check (coarse geometry, 0.05 s, coarse SPH spacing).
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh --smoke-test

# Baseline design run.
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh \
  --duration 20 --fps 20 --output-dir Output_CRM_Baseline

# Geometry variant: pass any full-detail OBJ with the same pose/scale.
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh \
  --mesh-file geometry/LunarDrumScrew_spiked.obj \
  --output-dir Output_CRM_Spiked

# One-way structural estimate: first record the rigid soil wrench.
./crm/run.sh --duration 0.2 --spacing 0.012 --dt 0.00025 \
  --loads-file Output_OneWay/crm/soil_loads.csv --output-dir Output_OneWay/crm

# Then replay it on the FEA drum (PardisoMKL and dt=2e-3 s are the defaults).
./crm/run_oneway_fea.sh --duration 0.2 \
  --loads Output_OneWay/crm/soil_loads.csv \
  --output-dir Output_OneWay/fea

# Separate two-way FEA diagnostic only (not a production design run).
./crm/run_fea.sh --duration 0.05 --spacing 0.012 --dt 0.00025 \
  --fps 200 --no-output --stress-stride 50 \
  --solver-iterations 20 --solver-tolerance 1e-3 --bce-layers 2
```

The interactive wizard automates the complete one-way sequence:

```bash
./run_study.sh                         # prompts for all settings, then runs both stages
./run_study.sh --dry-run --duration 0.5 --spacing 0.012
./run_study.sh --geometry step --step-file geometry/MyDrum.step --duration 20
```

It can use the supplied meshes, convert a new STEP solid into a TetGen mesh plus
CRM surface OBJ, or accept existing `.obj`/`.node`/`.ele` files. When it can
measure the geometry, it scales the bed box, bed height, initial drum centre
height (`bed_height + radius`), descent speed, cavity radius, and SPH spacing
from the CAD radius; a multi-solid assembly also gets assembly-specific mesh
filtering and hub defaults. Every derived value is offered as an editable prompt
default, and `--no-auto-geometry` restores the fixed legacy defaults. Each study
stores `study_config.json`, `commands.sh`, per-stage logs, `crm/soil_loads.csv`,
and the CRM/FEA summaries under `Output_<study_name>/`.

At completion the wizard writes `results_overview.png` and prints the paths to
the CRM/FEA summaries plus copyable commands to open the plots or result folder.
In an interactive terminal it asks whether to open them directly. To reopen the
plots later:

```bash
./chrono-env/bin/python view_results.py --output-dir Output_<study_name> --show
```

Use identical motion options in the two one-way stages; the replay validates the
recorded drum trajectory against the FEA motion. The replay defaults to
`pychrono.pardisomkl` (8 threads); the wrapper uses the conda-forge PyChrono for
that solver and falls back to local Eigen SparseLU if the module is unavailable.
The two-way FEA driver uses
the promoted solid mesh and its own defaults (`dt=2.5e-4 s`, two BCE layers,
inward 2-D BCE pattern, GMRES/diagonal preconditioning). Two layers are the best
tested interacting setup and complete 0.5 s, but show severe mechanical
divergence by the end. One layer crashes in tested interacting runs. See
[FEA_COUPLING.md](FEA_COUPLING.md).

### Validation performed here

The earlier conda CRM build was exercised with a 0.05 s smoke test and a 1.0 s
run at 12 mm SPH spacing. It initialized 576 SPH particles and 2,160 boundary
BCE markers, ran at 5e-4 s steps on the coarse mesh, and wrote `config.json`
and `summary.csv` without errors. The 1.0 s run took 2.4 s wall time. This
confirms the API mapping, mesh hook, soil parameter transfer, and metric/output
path; it is not a physical validation of the soil model. The local source build
now uses the same driver and has passed the same smoke test.

The default mesh is the full 7,572-face coarse OBJ, which preserves the screw
holes. Use `--mesh medium` for the 12,956-face variant or `--mesh-file` for a
custom geometry. Do not use the legacy 1,200-face simplification for production
results: it closes holes.

### Useful options

| Option | Meaning |
| --- | --- |
| `--duration T` | simulated seconds (default 60) |
| `--dt S` | CRM/FSI step (spacing-dependent default: 8e-5 s at 4 mm, 2.4e-4 s at 12 mm) |
| `--allow-unsafe-dt` | bypass the explicit `--dt` stability check (not recommended) |
| `--spacing D` | SPH particle spacing (default 0.004 m) |
| `--fps F` | metric/particle output cadence (default 20 Hz) |
| `--settle-time T` | delay before spin/descent (default 0.001 s) |
| `--descent-speed V` | drum descent speed (default 0.0015 m/s) |
| `--omega W` | spin rate in rad/s (default shared config, ~3.14) |
| `--spin-axis {X,Y,Z}` | global spin axis (default X) |
| `--drum-radius R` | outer/crest radius used for initial height and bed sizing (default 0.061 m) |
| `--drum-half-length L` | half of the drum axial length (default 0.033 m) |
| `--bed-height H` | initial bed height (default 0.06 m) |
| `--inner-radius R` | nominal cavity radius for fill ratio (default 0.055 m) |
| `--capacity-volume V` | CAD-derived free volume override for fill ratio |
| `--active-box X,Y,Z` | active SPH domain dimensions (default 0.244 m each) |
| `--d0-multiplier F` | SPH kernel length multiplier (default 1.0) |
| `--free-surface-threshold F` | free-surface divergence threshold (default 2.4) |
| `--proximity-steps N` | neighbor-list update interval (default 4) |
| `--mesh {coarse,medium}` | shipped full-detail OBJ (default coarse) |
| `--mesh-file PATH` | custom OBJ, e.g. a spiked design |
| `--save-particles` | write per-frame SPH particle CSV (large) |
| `--loads-file PATH` | write the per-step soil force/torque wrench for one-way FEA |
| `--no-output` | benchmark without writing files |

The default timestep now scales with SPH spacing. A 4 mm run uses `8e-5 s`;
passing `--dt 5e-4` at 4 mm is rejected because it produced immediate SPH
boundary blowups (`[calcHashD] ... out of max boundary`). The 12 mm validation
cases remain stable at `2.5e-4 s`. If you intentionally want to test a larger
step, add `--allow-unsafe-dt`.

## Outputs

With the default output enabled, the run directory contains:

* `config.json` — soil, numerics, mesh, capacity definition, and particle mass.
* `summary.csv` — one row per output frame with time, drum pose, particle counts,
  fill ratio, peak fill ratio, torque, work, and specific energy.
* `particles_*.csv` — optional SPH positions with `inside_drum` and `excavated`
  flags when `--save-particles` is used. These can be opened in ParaView or a
  lightweight Python viewer.
* `soil_loads.csv` — optional per-step force/torque wrench on the rigid drum
  when `--loads-file` is used; consumed by `run_oneway_fea.sh`.

### Fill-ratio definition

SPH particles are transformed into the drum's local frame (mesh local Z is the
long axis). A particle is *retained* if it lies inside a nominal cylinder of
radius `--inner-radius` and length equal to the drum length::

    capacity_volume = pi * inner_radius^2 * drum_length
    fill_ratio = n_retained * particle_mass / (bulk_density * capacity_volume)

The default capacity is a cylindrical envelope; screw flights displace part of
it. For design comparisons, this bias is consistent across geometries. If a
spiked design has a different internal free volume, pass `--capacity-volume` so
the comparison is fair.

*Excavated* particles are those inside the outer drum envelope and above the
initial bed surface. Specific energy is cumulative input work divided by the
current excavated mass, so it is most meaningful at the measurement time (after
full descent and a fixed number of revolutions).

## Recommended workflow

1. Calibrate the CRM soil against simulant bulk tests (angle of repose, direct
   shear, cone penetration, small bucket draw test).
2. Freeze the soil block and numerics.
3. Run baseline and geometry variants with the same motion schedule and output
   time.
4. Compare fill ratio, torque, and specific energy — not just fill ratio; spikes
   can improve fill while making the mechanism impractical.
5. Use short DEME runs only for local mechanisms that a continuum cannot
   represent, such as crust fracture or clod breakup.
6. Validate the top one or two geometries with physical simulant tests.

## Current limitations

* Long production sweeps and simulant calibration have not been performed, so
  treat the default soil parameters as estimates. The corrected 12 mm fluid-only
  CRM checks are coarse-resolution comparisons, not soil-model validation.
* The drum is kinematic; its pose is prescribed analytically and the FSI
  reaction torque is recorded, but drivetrain dynamics are not modeled.
* Fill ratio uses a nominal cylindrical cavity unless `--capacity-volume` is
  supplied.
* Cohesive crust fracture is not represented by the default mu(I) model; use a
  short DEME submodel or a cohesive/damage CRM model for that mechanism.
