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

The excavator is PLA plastic. A separate spindle-driven FEA structural check is
available through `./crm/run_fea.sh --smoke-test --no-output`. It couples the
promoted solid tetrahedral mesh `fea/LunarDrumScrew_m_fea_decim8k.node/.ele` to
CRM with `terrain.AddFeaMesh(mesh, False)` at `dt=2.5e-4 s` and one BCE layer.
Short runs are numerically stable, but the FEA fill is near zero and the reported
von Mises stress is far above PLA strength. A default 1.0 s run diverges and
aborts near 0.8 s, so these magnitudes are feasibility artifacts rather than
engineering results. Keep the rigid kinematic driver in
this directory as the production design model. See
[FEA_COUPLING.md](FEA_COUPLING.md) for the full findings, mesh route, and
unresolved contact/rigid-rotation issues.

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

# Separate solid-FEA feasibility check (not a production design run).
./crm/run_fea.sh --smoke-test --no-output --fps 200 --stress-stride 50
```

The FEA driver uses the promoted solid mesh and its own defaults (`dt=2.5e-4 s`,
one BCE layer, inward 2-D BCE pattern, GMRES/diagonal preconditioning). Only the
short smoke case is stable; longer runs diverge and the stress/fill values are
not engineering results. See [FEA_COUPLING.md](FEA_COUPLING.md).

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
| `--dt S` | CRM/FSI step (default 5e-4 s) |
| `--spacing D` | SPH particle spacing (default 0.004 m) |
| `--fps F` | metric/particle output cadence (default 20 Hz) |
| `--settle-time T` | delay before spin/descent (default 0.001 s) |
| `--descent-speed V` | drum descent speed (default 0.0015 m/s) |
| `--omega W` | spin rate in rad/s (default shared config, ~3.14) |
| `--spin-axis {X,Y,Z}` | global spin axis (default X) |
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
| `--no-output` | benchmark without writing files |

## Outputs

With the default output enabled, the run directory contains:

* `config.json` — soil, numerics, mesh, capacity definition, and particle mass.
* `summary.csv` — one row per output frame with time, drum pose, particle counts,
  fill ratio, peak fill ratio, torque, work, and specific energy.
* `particles_*.csv` — optional SPH positions with `inside_drum` and `excavated`
  flags when `--save-particles` is used. These can be opened in ParaView or a
  lightweight Python viewer.

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

* Only the short smoke test has been run here on the conda CRM build. Long
  production sweeps and simulant calibration have not been performed, so treat
  the default soil parameters as estimates.
* The drum is kinematic; its pose is prescribed analytically and the FSI
  reaction torque is recorded, but drivetrain dynamics are not modeled.
* Fill ratio uses a nominal cylindrical cavity unless `--capacity-volume` is
  supplied.
* Cohesive crust fracture is not represented by the default mu(I) model; use a
  short DEME submodel or a cohesive/damage CRM model for that mechanism.
