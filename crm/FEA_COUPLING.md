# FEA coupling for the PLA excavator drum

## Current status

A separate spindle-driven FEA path now exists. A reproducible diagnostic
command is provided below.

It uses the local Chrono 10 source build, a solid tetrahedral mesh of the STEP
drum, and the same CRM SPH soil as the production driver. The default mesh is
`fea/LunarDrumScrew_m_fea_decim8k.node` /
`fea/LunarDrumScrew_m_fea_decim8k.ele`.

The path is **not engineering-validated**. Treat it as a feasibility and
diagnostic experiment only. The corrected fill/excavation metrics include SPH
fluid markers only; older values that counted BCE markers are invalid.

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

### Corrected fluid-only comparison (12 mm, dt=2.5e-4 s)

`fluid.GetParticlePositions()` returns marker groups in the order
`[fluid, boundary, rigid, flex]`; metrics must use only the fluid prefix (576
particles in these cases). The rigid CRM baseline with this corrected basis is:

| Duration | Final/peak fill | Inside | Excavated | Work |
|---:|---:|---:|---:|---:|
| 0.20 s | 0.002755 | 1 | 7 | 0.3256 J |
| 0.50 s | 0.019286 | 7 | 17 | 0.675 J |

Evidence logs: `/tmp/opencode/rigid_fluid_d02.log` and
`/tmp/opencode/rigid_fluid_d05.log`. These are coarse-resolution alignment
checks, not the 4 mm production configuration.

FEA interaction outcomes at the same 12 mm spacing, dt, GMRES 20/1e-3, and
0.5 s spin ramp depend strongly on flex-BCE layering:

| BCE layers | Result |
|---:|---|
| 1 | Crashes near 0.005 s with `[calcHashD]` index outside the minimum boundary. |
| 2 | 576 fluid / 1,152 boundary / 17,442 flex markers; kernel-support warning. At 0.2 s: inside=6, fill=0.016531, excavated=6. At 0.5 s: mechanical divergence (3.356 mm displacement, 5.209 GPa von Mises, 345.7 N·m torque, 3.408 J work). |
| 3 | 576 fluid / 2,160 boundary / 26,163 flex markers. Interacting default-gap run segfaults between 0.05–0.10 s; a 0.01 m initial-gap run reaches 0.2 s but has no meaningful bed interaction or retained soil. |

Logs: `/tmp/opencode/fea_bce2_d01.log`, `fea_bce2_d02.log`,
`fea_bce2_d05.log`, `fea_bce3_d02b.log`, and `fea_bce3_gap001_d02.log` under
`/tmp/opencode/`. These outcomes do not identify an engineering-valid FEA
configuration. In particular, the 2-layer 0.2 s fill is not proof of a valid
response because the case diverges by 0.5 s and retains little soil.

Keep the rigid-body CRM model as the production design driver. Do not report FEA
stress or torque as structural validation. Use the FEA path only for deliberately
short diagnostics while investigating kernel support/BCE layering, contact
density and initial contact, and long-run coupled dynamics.

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
| mesh | `fea/LunarDrumScrew_m_fea_decim8k.node/.ele` | promoted tetrahedral mesh; mesh promotion does not validate coupling |
| SPH spacing | `0.012 m` | same smoke resolution as rigid CRM |
| timestep | `2.5e-4 s` | FEA/SPH is unstable at the rigid default `5e-4 s` |
| BCE layers | `2` | best tested interacting setup; warning remains and the run diverges by 0.5 s |
| BCE pattern | `BcePatternMesh2D_INWARD` | required for a closed solid boundary |
| GMRES | `20 / 1e-3` | practical direct/iterative choice for FEA stiffness coupling |
| CPU threads | `8` | Chrono/Eigen parallel work; SPH kernels remain GPU-driven |
| spin ramp | `0.5 s` | avoids impulsive startup; motion prescribed by motor functions |
| nodal mass floor | `1e-6 kg` | stabilizes sliver nodes from CAD-derived tetrahedral mesh |

For a short diagnostic sample of the two-layer case (not a validated run):

```bash
./crm/run_fea.sh --duration 0.05 --spacing 0.012 --dt 0.00025 \
    --fps 200 --no-output --stress-stride 50 \
    --solver-iterations 20 --solver-tolerance 1e-3 --bce-layers 2
```

Chrono warns that fewer than three BCE layers may cause insufficient kernel
support and particle leakage. Three layers did not solve the problem: they
increased flex-marker count and produced interacting-run crashes or a stable but
non-interacting gap case. BCE support, occlusion, and bed access remain open.

### Long-run behavior

There is no validated FEA duration window. The corrected 2-layer run already
shows non-credible mechanical growth by 0.5 s; 1- and 3-layer cases have separate
crash or interaction failures. Previous one-layer stress/displacement values and
the prior rigid-vs-FEA fill comparison used BCE-contaminated metrics and are not
validation evidence. Do not use any FEA stress, spindle torque, or fill value
for design decisions.

## Remaining limitations

Motor-driven spindle kinematics and SPH RHS hardening removed the dominant
short-run stress artifacts, but the coupled long-run response is still not
validated:

- the flexible boundary has 8,721 surface nodes against only 576 SPH particles;
  BCE layers raise flex-marker counts to 17,442 or 26,163 and affect support,
  occlusion, and soil access;
- corrected rigid CRM shows progressive but low retention at this coarse
  resolution; the FEA 2-layer case's modest 0.2 s retention does not persist as
  a credible response through 0.5 s;
- stress/displacement diverge in the 2-layer case, while 1- and 3-layer cases
  show hash-domain or segmentation failures, indicating coupled stability and
  boundary support remain unresolved;
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
3. Use this FEA driver only for short coupling diagnostics; even short runs are
   not established as physically valid.
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
