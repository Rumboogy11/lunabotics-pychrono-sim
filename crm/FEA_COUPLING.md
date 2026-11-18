# FEA coupling for the PLA excavator drum

## Current status

A separate spindle-driven FEA path now exists:

```bash
./crm/run_fea.sh --smoke-test --no-output
```

It uses the local Chrono 10 source build, a solid tetrahedral mesh of the STEP
drum, and the same CRM SPH soil as the production driver. The supported default
mesh is `fea/LunarDrumScrew_m_fea_decim8k.node` /
`fea/LunarDrumScrew_m_fea_decim8k.ele`.

The path is **numerically stable for short runs, but the stress and fill results
are not engineering-validated**. Treat it as a feasibility check only:

- default smoke result: final fill `0.00551`, peak fill `0.00551`, inside `2`,
  excavated `3`, maximum nodal displacement `2.96e-4 m`, maximum von Mises
  stress `9.94e+8 Pa` (`~994 MPa`), spindle torque `~-61.7 N·m`, work `9.72 J`;
- the same case in a repeat run gave `~974 MPa` and `~-60.2 N·m`;
- the production rigid-body CRM case with the same SPH spacing reaches fill
  around `0.20` over `1.0 s`. The FEA fill is therefore grossly under-predicted;
- PLA strength is roughly `50–70 MPa`, while reported FEA stress exceeds that
  within `0.05 s`. Those magnitudes are a modeling artifact, not a design result;
- a default `1.0 s` run does not remain stable. It reaches `2.46 GPa` and
  `1.12 mm` displacement by `0.2 s`, `57.8 GPa` and `17.3 mm` by `0.4 s`, then
  aborts near `0.8 s` with `[calcHashD] ... out of min boundary ...`.

Keep the rigid-body CRM model as the production design driver. Do not report FEA
stress or torque as structural validation until the contact and rigid-rotation
issues below are resolved. The FEA path is currently a short-run feasibility
demo only.

## What Chrono supports

`veh.CRMTerrain` inherits `fsi::sph::ChFsiProblemSPH`, which exposes:

```cpp
void AddFeaMesh(std::shared_ptr<fea::ChMesh> mesh, bool check_embedded);
```

This must be called **before** `terrain.Initialize()`. Internally it:

1. adds 1D beam/cable contact segments if the FEA mesh has any;
2. adds 2D shell or tetrahedral-boundary faces as an FSI surface;
3. generates BCE markers on that surface using the SPH spacing and
   `SetBcePattern2D(...)`;
4. sends FEA node position, velocity, and acceleration to the GPU each step;
5. sends SPH-computed nodal forces back through `ChNodeFEAxyz::SetForce(...)`.

The coupling is two-way at the solver level. The FEA driver deliberately calls
`terrain.AddFeaMesh(mesh, False)` and does **not** call `AddRigidBody` for the same
drum, avoiding duplicate soil obstacles.

The official C++ reference for the BCE pattern is
`chrono-src/src/demos/vehicle/wheeled_tests/demo_VEH_TireFEA.cpp`; the Python
CRM demo is
`chrono-src/src/demos/python/vehicle/demo_VEH_CRMTerrain_WheeledVehicle.py`.

## Implemented FEA model

`crm/lunar_excavator_crm_fea.py` does the following:

- loads the TetGen solid mesh with `fea.ChMeshFileLoader.FromTetGenFile`;
- assigns nodal masses manually from element volume and PLA density because
  `FromTetGenFile` creates `ChNodeFEAxyz` nodes with zero mass;
- selects hub nodes from the loaded mesh itself (radius `<= 0.0015 m`), so a
  mesh converter with an empty `HUB` set is still usable;
- creates a kinematic rigid spindle body that follows the analytic drum pose;
- ties hub nodes to the spindle with `fea.ChLinkNodeFrame` (three constraints
  per node), leaving the rest of the PLA solid deformable;
- reconstructs spindle drive torque from hub-link reaction moments about the
  global spin axis;
- sets `terrain.SetBcePattern2D(fsi.BcePatternMesh2D_INWARD, False)` before
  `AddFeaMesh`, matching the official tire-FEA demo;
- uses `ChSolverGMRES` with diagonal preconditioning, typically `20` iterations
  and `1e-3` tolerance.

`ChSolverBB` and `ChSolverPSOR` were tested and rejected: Chrono refuses them
for systems with stiffness or damping matrices.

Two casts are required for TetGen nodes in Python:

```python
base = fea.CastToChNodeFEAbase(mesh.GetNode(i))
node = fea.CastToChNodeFEAxyz(base)
```

`fea.ChMeshFileLoader.FromAbaqusFile` is not practical from this Python build
because the `std::map<std::string, std::vector<...>>` node-set typemap is not
wrapped. `FromTetGenFile` plus manual hub-node selection is the workable route.

## Mesh generation route

The supported converter is `step_to_chrono_fea.py`:

```bash
./chrono-env/bin/python step_to_chrono_fea.py geometry/LunarDrumScrew_m.step \
    --out-prefix fea/LunarDrumScrew_m_fea_decim8k \
    --unit-scale 0.001 \
    --mesh-size 0.012 \
    --surface-target-faces 8000 \
    --hub-radius 0.0015 \
    --axis Z \
    --tetgen-switches pYQ
```

Route: OpenCASCADE STL triangulation -> MeshFix repair -> face decimation with
`trimesh.remesh.subdivide_to_size` -> TetGen with `pYQ`. Direct Gmsh 3D meshing
was abandoned because the screw/thread surfaces produce segment/facet
intersection failures.

The promoted 8k mesh has:

- 8,721 nodes and 32,321 tetrahedra;
- 220 hub nodes and 8,721 boundary/surface nodes;
- volume error `0.763%` relative to the CAD solid;
- edge lengths min/median/max `6.92e-5 / 4.09e-3 / 1.74e-2 m`.

The original fine mesh (`fea/LunarDrumScrew_m_fea.*`) has 17,658 nodes and
73,423 tets but is not the default. Surface targets below roughly 7,500 faces
either lose the hub/volume or make TetGen report self-intersections. The
converter now warns instead of aborting when decimation leaves no `HUB` nodes,
because the FEA driver selects hub nodes from the loaded mesh.

Production CRM and DEME geometry remains
`geometry/LunarDrumScrew_m_coarse.obj`. The 1,200-face simplification closes the
physical holes and must not be used for production.

## Stable run settings

The FEA-specific defaults are:

| Setting | Default | Why |
|---|---:|---|
| mesh | `fea/LunarDrumScrew_m_fea_decim8k.node/.ele` | short-run stable promoted mesh |
| SPH spacing | `0.012 m` | same smoke resolution as rigid CRM |
| timestep | `2.5e-4 s` | FEA/SPH is unstable at the rigid default `5e-4 s` |
| BCE layers | `1` | more layers trigger domain/boundary instability here |
| BCE pattern | `BcePatternMesh2D_INWARD` | required for a closed solid boundary |
| GMRES | `20 / 1e-3` | tighter solves crashed the SPH domain |
| spin ramp | `0 s` | optional `--spin-ramp-time` did not fix stress drift |

Reproduce the smoke case explicitly:

```bash
./crm/run_fea.sh --duration 0.05 --spacing 0.012 --dt 0.00025 \
    --fps 200 --no-output --stress-stride 50 \
    --solver-iterations 20 --solver-tolerance 1e-3 --bce-layers 1
```

`--bce-layers 1` emits Chrono's warning that fewer than three layers may cause
insufficient kernel support and particle leakage. It is accepted only because
the tested multi-layer configurations are unstable; it is not a validated
contact setup.

A solver test with `100 / 1e-6` crashed after the first output with
`[calcHashD] index ... out of min boundary ...`, so tighter convergence is not a
solution.

### Long-run behavior

A `1.0 s` run from these defaults was attempted with the promoted mesh:

```bash
./crm/run_fea.sh --duration 1.0 --spacing 0.012 --fps 5 \
    --no-output --stress-stride 200
```

Fill stayed at `0.00551` (two SPH particles inside) for the whole run, while
stress, displacement, and torque grew without bound:

| Time | Max von Mises | Max displacement | Spindle torque |
|---:|---:|---:|---:|
| 0.2 s | 2.46 GPa | 1.12 mm | -145 N·m |
| 0.4 s | 57.8 GPa | 17.3 mm | -554 N·m |
| 0.6 s | 117 GPa | 75.0 mm | -2139 N·m |
| 0.8 s | 419 GPa | 139 mm | -555 N·m |

At about `0.8 s` the run aborted with
`[calcHashD] index ... out of min boundary ...` and `SIGABRT`. This is numerical
divergence, not a converged structural result. Do not extend the FEA duration
beyond the short smoke case without first fixing the contact/kinematic model.

## Why the stress is not yet valid

Diagnostics in `--debug-stress` show the hot element is a small tet near the top
of the drum while the largest displacement is a steadily growing shear relative
to the small bore. This points to drive/contact modeling, not a PLA failure:

- only the `~1.5 mm` bore nodes are tied to the spindle; the rest of the solid
  must be spun up and loaded through that small radius;
- increasing the rigid hub to `10 mm` made stress and torque much worse
  (`~44 GPa`, `~-1.4e4 N·m`);
- adding a `0.1 s` spin ramp with the larger hub still reached `~35.8 GPa`;
- the flexible boundary has 8,721 surface nodes/BCE markers against only 576
  SPH particles (`~15x` marker/particle ratio), so contact is over-constrained;
- torque is about `60 N·m` in FEA versus about `0.25 N·m` in the rigid
  production case;
- fill stays near zero while rigid CRM excavates substantially more soil.

An opt-in coarse contact skin (`--use-contact-skin`, 998 shell nodes tied by
`fea.ChLinkNodeNode`) reduced flexible BCE markers to 4,262 but produced NaNs or
SPH particles ejected from the domain in this Python binding. It is retained as
an experiment only and is not a supported validation path. Snapping the skin to
solid nodes, changing carrier masses, and adding the mesh after state setup did
not make it robust.

## Recommended workflow

1. Use rigid-body CRM for fill ratio, torque comparisons, and geometry sweeps.
2. Use DEME/DEM-Engine only for targeted discrete-mechanism checks.
3. Use this FEA driver only to demonstrate that solid CAD-derived drum meshes
   can be coupled and integrated stably in short runs.
4. Before using any stress magnitude for design, fix the rigid-rotation and
   contact-marker problem: obtain full rigid spin of the PLA body without
   bore-driven twist, and match flexible BCE density to SPH resolution.
5. Calibrate PLA properties from coupon tests of the actual printer, infill, and
   build orientation. FDM PLA is anisotropic and layer-sensitive; the current
   isotropic `E=3 GPa`, `nu=0.38`, `rho=1240 kg/m^3` values are estimates.

## Material assumptions

| Property | Value used | Notes |
|---|---:|---|
| Density | 1240 kg/m^3 | shared config `drum_density` |
| Young's modulus | 3.0 GPa | within 2.6–3.4 GPa initial range |
| Poisson ratio | 0.38 | within 0.36–0.40 initial range |
| Rayleigh beta | 0.01 s | numerical damping only |
| Strength | 50–70 MPa | reference only; not a calibrated failure limit |
