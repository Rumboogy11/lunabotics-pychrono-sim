# Lunar bucket-drum excavator — Chrono-family simulation tools

This directory is a from-scratch reimplementation of MercuryDPM's
`LunarBucketDrumExcavator` DEM simulation using Project Chrono tools:

| Port | Solver | Role | Main script |
| --- | --- | --- | --- |
| Primary | **Chrono::CRM** (SPH continuum) | geometry optimization, fill ratio, bulk soil response | `crm/lunar_excavator_crm.py` |
| GPU | **DEM-Engine 3.1.0 / pyDEME** (`deme[cuda12]`) | targeted grain-scale validation/calibration (0.9 mm grains, ~88,440 particles) | `deme/lunar_excavator_deme.py` |
| CPU | **PyChrono core** (`ChSystemNSC`) | mechanism illustration only (4 mm grains, ~270 particles) | `pychrono_core/lunar_excavator_pychrono.py` |

Everything under this directory is derived from the original
`MercurySource/Drivers/LunarExcavator/LunarBucketDrumExcavator.cpp` build tree.
`lunar_excavator_config.py` is the source of truth for excavator geometry and
motion and for the MercuryDPM → Chrono contact-model translation used by the
DEME/PyChrono validation ports. The primary CRM model has its own independent
soil block in `crm/crm_soil.py`.

## Design goal and model hierarchy

The engineering goal is **excavator geometry development**: improve the fill
ratio of a lunar regolith excavator by changing the bucket/drum/spike geometry.
The quantity of interest is bulk soil flow and retention, not individual grain
trajectories. The previous MercuryDPM setup was a preliminary rough test and is
now only a historical sanity check, not a primary reference.

Recommended hierarchy going forward:

1. **Chrono::CRM as the primary design model.** It resolves bulk shear, dilation,
   and failure of the soil continuum at a cost compatible with geometry sweeps.
2. **DEME as a secondary validation/calibration model.** Use short, targeted
   runs to check local mechanisms (spike–soil interaction, arching, segregation)
   and to calibrate bulk CRM parameters. Do not use it for large sweeps or try
   to brute-force actual lunar grain sizes.
3. **PyChrono core as an illustrative CPU demo**, not a predictive model.
4. **MercuryDPM outputs as historical reference only.**

The excavator is made of PLA plastic. A separate spindle-driven FEA path couples
a solid tetrahedral mesh of the STEP drum to CRM through
`./crm/run_fea.sh --smoke-test --no-output`. It is numerically stable for short
runs on the promoted 8k mesh, but its stress and fill results are **not**
engineering-validated: a default 1.0 s run diverges and aborts near 0.8 s.
Keep the rigid kinematic CRM model as the production design driver. See
[`crm/FEA_COUPLING.md`](crm/FEA_COUPLING.md) for the stable settings,
mesh-generation route, and unresolved contact/rigid-rotation issues.

Actual lunar regolith is mostly tens to hundreds of micrometres in size. The
current 1.8 mm DEM grains are roughly 10–40× too large relative to the 61 mm
drum radius, so their absolute grain size is not physically representative.
Bulk excavation metrics should therefore come from a calibrated continuum model.

---

## 1. What is mirrored

The C++ driver does the following:

1. one `LinearViscoelasticFrictionSpecies` shared by particles and walls,
2. five `InfiniteWall` retaining planes (floor + four sides, open top),
3. a CAD drum+screw imported triangle-by-triangle with `readTriangleWall()`,
4. a pre-instantiated, jittered spherical bed at `t = 0`,
5. settle → spin-up about global **X** (the drum's long axis) → slow kinematic descent → spin in place,
6. `t_max = 60 s`, output at `120 fps`, `dt = 1/(120·200) = 4.1667e-5 s`.

The existing DEME and PyChrono ports reproduce that sequence for reference. The
DEME port uses 0.9 mm grains, a 60 mm bed, and 1.5 mm/s descent; the PyChrono
port uses 4 mm grains and a 25 mm bed for a practical CPU runtime. These are
validation cases, not the primary fill-ratio design model described above.

---

## 2. Directory layout

```
.
├── lunar_excavator_config.py          # all parameters + contact-model mapping
├── stl_to_obj.py                      # binary/ASCII STL -> Wavefront OBJ
├── simplify_for_deme.py               # regenerate optional legacy 1200-face mesh (not default)
├── crm/
│   ├── lunar_excavator_crm.py         # primary CRM design driver
│   ├── crm_soil.py                    # independent soil estimates (no MercuryDPM mapping)
│   ├── run.sh                         # launcher (selects local source build)
│   └── README.md                      # CRM model, fill ratio, and workflow
├── geometry/
│   ├── LunarDrumScrew_m_{medium,coarse,fine}.stl   # original CAD STLs
│   ├── LunarDrumScrew_m_{medium,coarse}.obj       # full meshes for PyChrono/ParaView
│   └── LunarDrumScrew_m_coarse_deme1200.obj       # optional legacy simplified mesh
├── deme/
│   ├── lunar_excavator_deme.py        # GPU grain-scale validation port
│   ├── run.sh                         # launcher (selects ./deme-env)
│   ├── postprocess.py / postprocess.sh
│   ├── view.sh                        # default viewer entry point
│   ├── view_frames.py                 # Matplotlib/Tk playback (default viewer)
│   ├── view_paraview.sh               # optional ParaView GUI launcher
│   ├── visualise.py                   # optional ParaView pipeline
│   └── README.md
├── pychrono_core/
│   ├── lunar_excavator_pychrono.py    # CPU core demo
│   ├── run.sh                         # launcher (source build, then ./pychrono-env)
│   └── README.md                     # CPU port + --vis requirements
├── chrono-src/                        # Chrono 10.0.0 source checkout
├── chrono-env/                        # local Python/CMake/CUDA/SWIG toolchain
├── chrono-build/                      # CMake/Ninja build tree
├── chrono-install/                    # installed PyChrono + Chrono libraries
├── deme-env/                          # local DEM-Engine validation environment
├── pychrono-env/                      # local PyPI PyChrono CPU-demo environment
└── environment/
    ├── INSTALL.md
    ├── requirements.txt
    ├── activate-chrono-source.sh      # local source-build environment
    ├── build-chrono-source.sh         # configure/build/install/verify
    └── copy_tree_two_pass.py          # NTFS-safe environment copier
```

---

## 3. Quick start

All important environments are local. The launchers select them automatically;
`PYTHON` still overrides. Activate the source-build environment once per shell:

```bash
source environment/activate-chrono-source.sh
# If chrono-install/ is missing, build it first:
# ./environment/build-chrono-source.sh --clean

# ---- Chrono::CRM primary design model ----
./crm/run.sh --print-soil                 # independent soil block, no CRM needed
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh --smoke-test
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh \
  --duration 20 --fps 20 --output-dir Output_CRM_Baseline

# Spiked/alternative geometry: pass any full-detail OBJ with the same pose/scale.
PYTHON="$CHRONO_ENV/bin/python" ./crm/run.sh \
  --mesh-file geometry/LunarDrumScrew_spiked.obj \
  --output-dir Output_CRM_Spiked

# ---- GPU / DEME smoke test (810 coarse grains, 0.05 s) ----
PYTHON=./deme-env/bin/python \
  ./deme/run.sh --smoke-test --output-dir Output_DEME_Smoke

# ---- GPU / DEME grain-scale validation settings ----
PYTHON=./deme-env/bin/python \
  ./deme/run.sh --duration 60 --output-dir Output_DEME_LunarExcavator

# NOTE: Output_DEME_LunarExcavator/ now contains a fresh 1 s global-X-spin run
# with the current 0.9 mm / 60 mm settings and the full coarse drum geometry.
# Superseded mixed, Y-spin, and simplified-mesh runs were removed.

# frames -> summary.csv (numeric analysis, no GUI dependency)
PYTHON=./deme-env/bin/python ./deme/postprocess.sh \
  --output-dir Output_DEME_LunarExcavator --fps 120

# default interactive viewer: Matplotlib/Tk, no ParaView or PyChrono GUI needed
./deme/view.sh --output-dir Output_DEME_LunarExcavator --stride 8
# headless scene check: ./deme/view.sh --output-dir ... --no-render

# optional ParaView GUI (not required; may crash on Qt6/Wayland desktops)
./deme/view.sh --paraview --output-dir Output_DEME_LunarExcavator

# ---- CPU / PyChrono demo ----
PYTHON="$CHRONO_ENV/bin/python" ./pychrono_core/run.sh --smoke-test
PYTHON="$CHRONO_ENV/bin/python" ./pychrono_core/run.sh --no-output
# Fallback wheel env: PYTHON=./pychrono-env/bin/python ./pychrono_core/run.sh --smoke-test
# --vis needs pychrono.irrlicht; neither the pip wheel nor the current source
# build enables an Irrlicht/VSG visualization module. See environment/INSTALL.md.
```

The old `/tmp/opencode/chrono-conda2`, `/tmp/opencode/deme-venv`, and
`/tmp/opencode/pychrono-venv` paths remain only as fallbacks if a local copy is
missing.

---

## 4. Parameter mapping (MercuryDPM → Chrono)

### 4.1 Geometry and motion

| MercuryDPM | C++ source value | Selected shared config | DEME equivalent | PyChrono equivalent |
| --- | --- | --- | --- | --- |
| `drumOmega` | `6.28*0.5 = 3.14 rad/s` about global X | `drum_omega`, `drum_spin_axis="X"` | `SetFamilyPrescribedAngVel(2, 0, 0, omega)` in drum-local coords | `SetAngVelParent(...)` + analytic `SetRot()` |
| `drumHalfLength` | 0.033 m | `drum_half_length` | mesh bbox; X bed half-width uses half-length for X spin | same |
| `drumRadius` | 0.061 m | `drum_radius` | mesh crest radius | mesh crest radius |
| `stlFile` | `LunarDrumScrew_m_medium.stl` | `stl_file` | full coarse OBJ by default (holes preserved) | full coarse OBJ by default |
| `particleRadius` | 0.001 m | `particle_radius = 0.0009 m` | `LoadSphereType(m, r, mat)` | `ChBodyEasySphere(r, rho, ...)` |
| `bedHeight`, margins | 0.03, 0.01, 0.005 m | `bed_height = 0.06 m`, margins unchanged | seeded cubic bed (~88,440 grains) | same `grain_positions()` |
| `floorZ` | 0 m | `floor_z` | `AddBCPlane([0,0,0],[0,0,1])` | fixed thin box |
| `drumRestClearance` | 0 m | `drum_rest_clearance` | `drum_center_z0 = 0.121` | same |
| `targetBottomClearance` | 0.001 m | `target_bottom_clearance` | `z_target = 0.062` | same |
| `lowerSpeed` | 0.0007 m/s | `lower_speed = 0.0015 m/s` | JIT position expression | analytic `drum_z(t)` |
| `settleTime` | 0.001 s | `settle_time` | expression gate `t > settleTime` | `t >= settleTime` gate |
| initial `Quaternion(√.5,0,√.5,0)` | 90° about Y | `initial_quaternion_xyzw()` | `(x,y,z,w)=(0,√.5,0,√.5)` | `(w,x,y,z)=(√.5,0,√.5,0)` |
| five `InfiniteWall`s | — | `domain_bounds` | five `AddBCPlane` (normals into free space) | five fixed thin boxes |
| `readTriangleWall()` | one wall per triangle | `OBJ_*` | one `DEMMesh` body | one `ChTriangleMeshShape` collision shape |
| `setNumberOfDomains({2,4,1})` | 8 MPI ranks | — | one CUDA device (`--device`) | single CPU process |

> **Geometry / frame caveat.** The shipped STL measures
> `x,y ∈ [-0.061, 0.061]`, `z ∈ [-0.033, 0.033]` m, so its own drum axis is Z.
> The initial 90° quaternion about Y maps that local Z axis onto **global X**.
> The C++ driver then spins about global X, which is the drum's long axis; this
> port does the same. DEME stores prescribed angular velocity in the drum's
> **local** frame, so a global-X spin is requested as local-Z via
> `global_axis_local()`; a global-Y or global-Z spin can be selected with
> `--spin-axis Y|Z`. The retaining box X half-width is `drum_half_length +
> bed_margin_x` for the default X spin.

### 4.2 Contact model

MercuryDPM uses one linear spring/dashpot with Coulomb sliding:

```
k  = 1000 N/m
c  = 0.02 kg/s
mu = 0.5
k_sliding = 2k/7, c_sliding = c
rho = 1500 kg/m^3
```

The DEME and PyChrono core ports use the following contact mappings, derived in
`lunar_excavator_config.py`. **Chrono::CRM does not use any of these values.** Its
soil is defined independently in `crm/crm_soil.py` as bulk continuum properties
(density, Young's modulus, friction, cohesion, mu(I) grain diameter).

| Quantity | Formula / mapping | DEME | PyChrono NSC |
| --- | --- | --- | --- |
| grain mass | `rho·4πr³/3` | `LoadSphereType` | `ChBodyEasySphere` density |
| reduced mass | `m_eff = m/2` | used below | used below |
| damping ratio | `ζ = c / (2√(k·m_eff))` | — | — |
| restitution | `e = exp(-πζ/√(1-ζ²))` | `CoR` | `SetRestitution(e)` |
| Hertz modulus | `E = (1-ν²)k / (R√(α/2))`, `ν=0.3`, `α=δ/R=0.01` | `E` | — |
| normal compliance | `1/k = 0.001 m/N` | — | `SetCompliance(1/k)` |
| friction | direct | `mu` | `SetFriction(mu)` |
| rolling friction | not in MercuryDPM | `Crr = 0` | `SetRollingFriction(0)` |
| sliding stiffness/dissipation | `2k/7`, `c` | implicit in DEME's tangential model | implicit in Chrono NSC |

Because DEME's Hertz model is nonlinear, the original `dt = 4.1667e-5 s` can be
too large for millimetre grains. The config keeps that as the *MercuryDPM* step
and automatically increases the substep count for smaller grains. Four substeps
were validated at 1 mm; the selected 0.9 mm setup uses `deme_substeps = 5`, so
`solver_dt = 8.333e-6 s` while output timing is unchanged. `--fps` never changes
the integration step.

### 4.3 Time stepping and output

| MercuryDPM | DEME | PyChrono |
| --- | --- | --- |
| `setTimeStep(1/(fps·stepsPerFrame))` | `SetTimeStepSize(p.deme_dt)` | `DoStepDynamics(p.deme_dt)` |
| `setSaveCount(stepsPerFrame)` | one write per `1/fps` simulated seconds | same |
| `particleHandler.setWriteVTK(true)` | `spheres_%06d.csv` (`X,Y,Z,r,family`) | `spheres_%06d.csv` (same columns) |
| `wallHandler.setWriteVTK(true)` | `walls_%06d.vtk`, `drum_%06d.vtk` | `drum_state.csv`, `summary.csv` |

---

## 5. Known differences and limitations

* The initial bed lattice uses `spacing = 2r + 2·jitter` rather than the C++
  driver's `2r`, so the same ±8% jitter cannot create initial overlaps. This is
  required for DEME's nonlinear Hertz model; MercuryDPM's softer linear spring
  tolerated the original overlaps.
* The contact laws are **equivalent mappings**, not byte-for-byte identical
  constitutive models. MercuryDPM's linear spring/dashpot becomes DEME Hertz and
  Chrono NSC compliance; bulk behaviour should be comparable, but forces will
  not match triangle-for-triangle.
* DEME uses **family numbers** (`0` grains, `2` drum) purely as a solver
  bookkeeping detail; MercuryDPM has no equivalent concept. The analytical
  planes are left in DEME's default family because `SetFamily()` on a plane
  silently removes its grain contact in DEME 3.1.0.
* Both ports default to the **full 7,572-face coarse OBJ**. The 1,200-face
  quadric simplification closed some screw holes and is no longer used for
  production; it remains available as `--mesh deme` for lower-fidelity tests.
  The 12,956-face medium OBJ is also available on the GPU port via
  `--mesh medium`. The original STLs are untouched.
* **ParaView is optional**, inherited only from MercuryDPM's original helper.
  The default interactive viewer is the Matplotlib/Tk `view_frames.py`. It skips
  frames from another particle count and uses the recorded `drum_*.vtk` pose
  only when its spin axis matches the configured global X axis; otherwise it
  warns and falls back to the analytic pose. It uses `TkAgg`, avoiding the Qt 6
  code path that crashes the host's ParaView build. `--paraview` remains
  available as a best-effort alternative. Numeric analysis uses
  `postprocess.sh`.
* The PyChrono drum is **kinematic by state overwrite**: position, orientation
  and velocities are imposed before each step rather than being solved as a
  dynamic rigid body. This matches MercuryDPM, which also sets wall position and
  angular velocity directly.
* The Chrono::CRM driver is implemented and now uses the local Chrono 10.0.0
  source build in `chrono-src/` → `chrono-build/` → `chrono-install/`, with
  CUDA 12.9, FSI-SPH, vehicle, and Python modules enabled. The earlier conda
  build at `/tmp/opencode/chrono-conda2` remains only a fallback. Production
  sweeps and simulant calibration have not yet been run. The separate PyPI
  wheel still lacks `pychrono.fsi` and `pychrono.vehicle`; see
  `environment/INSTALL.md`.
* `pychrono==10.0.0rc1` currently ships a CPython 3.12 manylinux wheel; use a
  separate 3.12 environment for the CPU port. DEME 3.1.0 works on newer Python
  (validated on 3.14 here). See `environment/INSTALL.md`.

### 5.1 Solver choice for geometry optimization

**Use Chrono::CRM as the primary solver.** Fill ratio is a bulk outcome
controlled by soil shear, dilation, compaction, and failure around the tool.
Those are continuum quantities; individual grain trajectories are not needed to
compare bucket or spike geometries.

**Use DEME selectively.** It remains useful when a mechanism is intrinsically
discrete — spike-induced breakup of clods or crust, arching in the drum throat,
size segregation, or checking whether a continuum result is grain-size
artifact. Those runs should be short and local, not full 60 s design sweeps.

Do not refine DEME toward actual lunar grain sizes. At 0.45 mm radius the grain
count grows about 8× and the substep count about 3×, making the current coarse
mesh cost roughly 20× more per simulated second. That budget is better spent on
CRM resolution, geometry variants, and simulant validation tests.

---

## 6. Validation performed

On this machine (NVIDIA RTX 5070 Ti, driver 620.70.06, Python 3.14 for DEME,
Python 3.12 for PyChrono):

Historical checks at the original 1 mm / 30 mm settings:

```text
DEME  --particle-radius 0.001 --duration 0.5
                                    30,927 grains          bed settles with
                                                           min_z ~= 0.001 m
DEME  --particle-radius 0.001 --duration 2.0
                                    30,927 grains          223 s wall time
PyChrono --smoke-test                   60 grains, 0.02 s    <1 s
PyChrono --no-output (default demo)    270 grains, 2 s       ~4 s
```

Selected DEME validation settings (global X spin, 0.9 mm grains, 60 mm bed,
1.5 mm/s descent):

```text
DEME  --duration 0.2 (legacy simplified mesh)
                                     88,440 grains         0.2 s in 49.3 s
DEME  --duration 1.0 (coarse mesh)    88,440 grains         1.0 s in 631.0 s
postprocess on 1 s coarse frames      last t=1.000 s         min_z ~= 0.00089 m
drum VTK pose at t=0 -> 1.0 s          spin axis (1,0,0)      z=0.1210 -> 0.1205 m
```

The CRM driver passed static validation (`py_compile`, `bash -n`, `--help`,
`--print-soil`) and real runs on the earlier conda PyChrono 10.0.0 build: a
0.05 s smoke test and a 1.0 s run at 12 mm SPH spacing. Both used the coarse
mesh, 576 SPH particles, 2,160 boundary BCE markers, and 5e-4 s steps;
`config.json` and `summary.csv` were written successfully. The 1.0 s run took
2.4 s wall time. These checks validate setup and metrics plumbing, not physical
soil calibration. The local source build now runs the same driver; its smoke
test result is recorded in `crm/README.md`.

All completed DEME runs finished without contact-count or non-finite-force
errors. The full 60 s DEME run has **not** been rerun at the selected settings. Based on
the coarse-mesh 1 s validation it would take roughly **10.5 hours** and write
about **25 GB** of sphere CSV plus **6 GB** of drum VTK (~31 GB total), so use
`--no-output` for benchmark runs. The default viewer was validated on the new
coarse X-spin output and detects the pose-source mesh from the VTK point count;
the optional ParaView pipeline was validated
headlessly with `./deme/view_paraview.sh --no-render`, but its GUI launch remains
best-effort because the host ParaView build segfaults after pipeline setup.

---

## 7. Regenerating geometry

```bash
python3 stl_to_obj.py                 # full coarse + medium OBJ from the STLs
python3 simplify_for_deme.py          # optional legacy 1200-face OBJ (needs trimesh)
```

The STL files are already in metres and centred on the drum axis; no scaling or
reorientation is applied by either converter.
