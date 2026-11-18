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

The path is **improved for short runs, but the stress and fill results are not
engineering-validated**. Treat it as a feasibility check only.

Driver and SPH-coupling fixes applied in the local source/runtime:

- spindle motion is prescribed by Chrono motor constraints with motion functions
  (ground guide -> prismatic linear-position descent motor -> slider -> revolute
  rotation-speed motor -> spindle), instead of per-step state overrides on a
  fixed/free body; FEA hub nodes remain tied to the spindle with
  `ChLinkNodeFrame`;
- default FEA spin-up uses a `0.5 s` linear speed ramp and FEA nodal masses are
  floored by default (`1e-6 kg`) to stabilize sliver nodes;
- local `chrono-src` CRM/SPH RHS handling for flex BCE coupling was hardened
  (no constitutive stress-rate evolution written for non-fluid BCE markers,
  no FSI RHS force for BCE markers without fluid-neighbor support, degenerate
  pair guard), and rebuilt FSI artifacts were copy-installed into
  `chrono-install` (`libChrono_fsisph.so`, `libChrono_fsi.so`,
  `share/chrono/python/pychrono/_fsi.so`). `chrono-src`/build/install remain
  local to this workspace.

Short-run validation with the defaults below (12 mm, 2.5e-4 s, 1 BCE layer,
GMRES 20/1e-3, spin ramp 0.5 s):

- `0.05 s`: final/peak fill `0.00551`, inside `2`, excavated `3`, max
  displacement `1.49e-5 m`, max von Mises `6.58 MPa`, spindle torque
  `0.176 N·m`, work `0.105 J` (log: `/tmp/opencode/fea_fixed_005.log`);
- `0.20 s`: final/peak fill `0.00551`, inside `2`, excavated `3`, max
  displacement `1.29e-4 m`, max von Mises `59.3 MPa`, spindle torque
  `1.48 N·m`, work `0.290 J` (log: `/tmp/opencode/fea_fixed_02.log`).
  Stress/displacement trend upward over this interval (near PLA strength by
  0.2 s) and should not be interpreted as converged.

Longer-run behavior remains non-physical:

- `0.50 s` (same settings, 20 fps sampling): stress/displacement grow after the
  early stable phase to `3907 MPa` and `6.21 mm` by `0.50 s` with large torque
  excursions (`156 N·m`, work `14.9 J`, log: `/tmp/opencode/fea_fixed_05.log`);
- rigid CRM comparison at `0.20 s` / 12 mm: final fill `0.165`, peak `0.198`,
  inside `60`, excavated `93`, work `0.326 J` (`./crm/run.sh ...`). The FEA case
  therefore still severely under-predicts fill/excavation and is not suitable
  for torque/energy comparison.

Keep the rigid-body CRM model as the production design driver. Do not report FEA
stress or torque as structural validation. The FEA path is currently a short-run
feasibility/diagnostic tool pending further stabilization (kernel support/BCE
layering, contact density and initial contact, and long-run coupled dynamics).

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
  `FromTetGenFile` creates `ChNodeFEAxyz` nodes with zero mass, and floors
  sliver-node masses by default (`--min-nodal-mass`, default `1e-6 kg`);
- selects hub nodes from the loaded mesh itself (radius `<= 0.0015 m`), so a
  mesh converter with an empty `HUB` set is still usable;
- creates a ground guide, translating slider, and rotating spindle connected by
  Chrono motor constraints with motion functions (prismatic linear-position
  descent and revolute rotation-speed spin with configurable ramp), avoiding
  per-step body state overrides;
- initializes spindle/FEA node kinematics consistently with the prescribed
  motion at `t=0`;
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
| GMRES | `20 / 1e-3` | practical direct/iterative choice for FEA stiffness coupling |
| spin ramp | `0.5 s` | avoids impulsive startup; motion prescribed by motor functions |
| nodal mass floor | `1e-6 kg` | stabilizes sliver nodes from CAD-derived tetrahedral mesh |

Reproduce the short-run check explicitly:

```bash
./crm/run_fea.sh --duration 0.05 --spacing 0.012 --dt 0.00025 \
    --fps 200 --no-output --stress-stride 50 \
    --solver-iterations 20 --solver-tolerance 1e-3 --bce-layers 1
```

`--bce-layers 1` emits Chrono's warning that fewer than three layers may cause
insufficient kernel support and particle leakage. It remains a likely contributor
to long-run instability and under-predicted excavation, but tested multi-layer
configurations were not stable in this setup.

### Long-run behavior

With the motor-driven spindle, nodal-mass flooring, and local SPH RHS fixes,
short runs are stable and physically plausible in magnitude (`0.05 s`: ~6.6 MPa;
`0.20 s`: ~59 MPa, trending upward). However, longer runs still diverge:

| Time | Max von Mises | Max displacement | Spindle torque |
|---:|---:|---:|---:|
| 0.05 s | 6.58 MPa | 14.9 µm | 0.176 N·m |
| 0.20 s | 59.3 MPa | 129 µm | 1.48 N·m |
| 0.30 s | 729 MPa | 1.12 mm | 27.4 N·m |
| 0.50 s | 3907 MPa | 6.21 mm | 156 N·m |

Fill remains ~`0.0055` (2 particles inside) throughout these runs, far below the
rigid CRM baseline (~`0.165–0.198` at 0.2 s). Do not treat results beyond the
short-run feasibility window as converged or physically meaningful.

## Remaining limitations

Motor-driven spindle kinematics and SPH RHS hardening removed the dominant
short-run stress artifacts, but the coupled long-run response is still not
validated:

- the flexible boundary has 8,721 surface nodes/BCE markers against only 576
  SPH particles (`~15x` marker/particle ratio), so contact sampling/support is
  poorly matched at 12 mm spacing and 1 BCE layer;
- fill remains near zero while rigid CRM excavates substantially more soil at
  the same spacing, indicating the FEA/SPH contact and excavation interaction is
  still not representative;
- stress/displacement grow over time after an initially plausible short-run
  phase, so constraint coupling, mass/stiffness distribution, contact forces,
  and/or SPH boundary support likely still introduce accumulating errors;
- earlier diagnostics showed hot spots and shear consistent with drive/contact
  modeling sensitivity; the current setup is improved but not proven mesh- or
  parameter-converged.

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
