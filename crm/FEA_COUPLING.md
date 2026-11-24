# FEA coupling for the PLA excavator drum

## Current status

Two spindle-driven FEA paths exist: a one-way rigid-CRM wrench replay for
structural screening and a two-way coupled diagnostic. Commands for both are
provided below. The earlier two-way-only state is preserved on the local branch
`two-way-fea-wip` so the coupling diagnostics remain available.

It uses the local Chrono 11 source build, a solid tetrahedral mesh of the STEP
drum, and the same CRM SPH soil as the production driver. Supply the mesh with
`--fea-node` (and optionally `--fea-ele`); no mesh files are bundled.

The two-way path is **not engineering-validated**. Treat it as a feasibility and
diagnostic experiment only. The corrected fill/excavation metrics include SPH
fluid markers only; older values that counted BCE markers are invalid.

## One-way CRM -> FEA structural replay

The supported structural-estimate path is now two-stage. Rigid CRM writes the
per-step soil wrench with `--loads-file`; `crm/lunar_excavator_oneway.py`
(`./crm/run_oneway_fea.sh`) replays it on the same solid mesh with no
CRMTerrain/SPH system. The wrench is transformed into the spindle frame and
distributed over exterior solid nodes as equivalent nodal forces that reproduce
the recorded net force and torque to numerical precision. Spindle descent/spin
remain motor-driven, hub nodes remain tied to the spindle, and the recorded
drum trajectory is checked against the FEA motion. The replay also aborts if
the motor angle or descent position stops tracking its command, which is what
made the earlier GMRES results unusable.

The replay length is the minimum of the requested `--duration` and the time of
the last recorded CRM load sample. When the loads file ends early, the driver
prints a `NOTICE` that the run was limited by the available CRM frames and
completes with the shorter history; it no longer extends the run by holding the
final wrench. This is a normal non-fatal run: the FEA summary and optional VTU
series cover only the time for which real CRM wrench samples exist.

A full study can optionally overlap the two stages (`--concurrent-crm-fea`, or
the GUI's **Run CRM + one-way FEA together** toggle). The orchestrator writes a
liveness marker under `<study>/crm/` while rigid CRM is running and passes
`--wait-for-crm --crm-alive-file` to the replay. The replay waits for at least two
valid CRM samples, reloads the CSV as it grows, and does not stop at a
temporarily short file while the marker is current. When CRM exits, the runner
removes the marker and the replay applies the same duration/frame cap described
above. The load interpolation, motor settings, and outputs are unchanged; only
the timing of the cap is deferred.

This avoids flex-BCE instability and gives stable short-run PLA stress
estimates. It is a structural response to rigid-CRM loads, not two-way FSI:
deformation cannot feed back into the soil, and the rigid drum wrench is only
valid while the real drum can still be approximated as rigid. Peak stress
excludes tetrahedra below `--min-stress-volume` (default `1e-12 m^3`); the raw
peak is retained for transparency because the promoted mesh has a few sliver
elements. Use rigid CRM for soil/design metrics and this replay only for
structural screening until coupled contact is validated. It is also a
single-mesh estimate; no PLA mesh-convergence or coupon-strength calibration has
been performed.

### Force-driven descent

All drivers default to the original constant-speed kinematic descent. Force
descent is opt-in with `--descent-mode force --descent-force N` (optional
`--descent-force-ramp T`). In the rigid CRM, a linear-force motor pushes the
drum downward after `--settle-time`; a position motor holds the initial height
beforehand and `z_target` after the descent, while spin remains prescribed. The
coupled diagnostic FEA path applies the same force/hold drive to the prismatic
slider and keeps the revolute spin motor unchanged. Use `--descent-force 0` with
`--drum-gravity` in the rigid CRM for gravity-only descent; the one-way replay
still follows the recorded height trajectory.

The one-way replay has no SPH soil reaction to balance an actuator force, so it
does **not** apply a force motor. It instead uses a position motor built from
the rigid CRM's recorded `drum_z_m` samples, linearly interpolating between the
`--loads-fps` samples and holding the final height afterward. The driver rejects
a force-mode loads file unless `--descent-mode force` is supplied, and rejects a
kinematic loads file if force mode is requested; set the mode and force options
identically in both stages. Force-mode `soil_loads.csv` and both summary CSVs
add `descent_mode`, `descent_state`, `actuator_force_n` (positive magnitude) and
`vertical_speed_m_s`; one-way FEA additionally records
`recorded_trajectory_error_m`. Kinematic CSV schemas are unchanged.

Force descent does not change fill/capacity classification or SPH/BCE
bookkeeping. Baseline fill capacity is computed once before the CRM run from
the nominal cavity minus the loaded excavator mesh overlap; that constant is
recorded in `crm/config.json` and is independent of the FEA replay or descent
mode. The wizard/GUI store the mode as `descent_mode` (`kinematic`/
`force`), `descent_force_n` (N) and `descent_force_ramp_s` (s) in
`study_config.json`, and emit the CLI flags only for force studies. Because
descent time is load-dependent, wizard duration guidance based on
`--descent-speed` is skipped in force mode; choose `--duration` long enough for
the chosen force.

One-way replay defaults: `dt=2e-3 s`, `pychrono.pardisomkl.ChSolverPardisoMKL`
with 8 threads, zero spin ramp, load-node radius `>= 0.8 R`, min reported stress
element volume `1e-12 m^3`, and locked direct-solver sparsity pattern. If
`pychrono.pardisomkl` is unavailable, the driver warns and falls back to
`ChSolverSparseLU`. GMRES was tested but did not enforce the spindle/hub
constraints accurately and is not usable for results.

Full 0.2 s validation gave the same deformation (`9.48433e-5 m`), filtered peak
stress (`36.15 MPa`), raw peak stress (`57.03 MPa`), and work (`0.3225 J`) for
PardisoMKL and SparseLU at `dt=2e-3 s`. Timings over that history were:

| Solver / setting | Wall time |
| --- | --- |
| SparseLU, `dt=1e-3 s` | 682 s |
| SparseLU, `dt=2e-3 s`, locked pattern | 309 s |
| PardisoMKL, `dt=2e-3 s`, 1 MKL thread | 42 s |
| PardisoMKL, `dt=2e-3 s`, 8 MKL threads | 28 s |
| PardisoMKL, `dt=5e-3 s`, 8 MKL threads | 12 s |

`dt=5e-3 s` completed with Pardiso but changed peak stress by about 3% and is
not the default; SparseLU at that step size segfaulted. `dt=2e-3 s` was chosen
to balance speed and timestep convergence. Individual peak elements remain
mesh-sensitive.

`ChSolverSparseLU` wraps Eigen's SparseLU, whose factorization is single
threaded: `system.SetNumThreads(...)` does not make its LU factorization use
more CPU cores. Pattern locking still helps it by avoiding repeated analysis
overhead, but it is not multithreading. PardisoMKL is genuinely multithreaded:
the `ChSolverPardisoMKL(num_threads)` constructor controls its MKL thread pool;
8 threads were faster than 1 or 16 on this 16-core machine. The pinned Chrono 11
source also contains upstream commit `6101e132` ("Skip the analyze phase in all
implicit integrators when the system structure is unchanged"), so pattern
locking avoids repeated PARDISO analysis rather than only repeated setup calls.

MUMPS is not viable in the current Python workflow. Chrono's source contains a
`ChSolverMumps` module, but this version has no SWIG/Python binding for it, and
the environment has no MUMPS library or CMake config. It would require building
MUMPS (Fortran is available) and adding a custom Python binding. PardisoMKL is
now built into the project-local Chrono 11 install from oneMKL, and the one-way
launcher always uses that local binding for every supported solver.

```bash
./crm/run.sh --duration 0.2 --spacing 0.012 --dt 0.00025 \
    --loads-file /tmp/opencode/oneway_loads_d02.csv \
    --output-dir /tmp/opencode/oneway_crm_d02

./crm/run_oneway_fea.sh --duration 0.2 \
    --loads /tmp/opencode/oneway_loads_d02.csv \
    --output-dir /tmp/opencode/oneway_fea_d02_default
```

For force descent, add the same `--descent-mode force --descent-force 1.0
--descent-force-ramp 0` to both commands. The one-way stage needs the force-mode
loads file written by the matching CRM run.

### Viewing one-way FEA output

The structural driver writes per-frame deformation VTU files only when
**Export full 3D FEA stress animation** is enabled in the GUI (or `--export-vtk`
is passed to the driver). That export was a large fraction of the spiky FEA
runtime, so it is off by default. When enabled, the simulation process only
snapshots copied arrays (reusing the per-frame stress scan) and a separate
writer process builds/writes the VTK files, so disk I/O overlaps later solver
steps. A bounded queue keeps memory usage fixed, and the writer is joined before
the final summary is printed so `fea.pvd` is complete. The durable scalar
history is always `fea/summary.csv` (filtered/raw peak von Mises stress, peak
deformation, loads, work, pose, and timing). The installer patches Chrono 11's
Python wrapper to expose the native `GetEquivalentVonMises()` formula as
`VonMises()`, so the CSV and VTU arrays use Chrono's own stress calculation.
`export_study_vtk.py` derives
`paraview/fea_summary.csv` with MPa/mm units, and `view_study_paraview.py`
loads it as two lightweight XY charts beside the CRM 3D animation:

```bash
./chrono-env/bin/python export_study_vtk.py --study-dir Output_my_study
pvpython view_study_paraview.py Output_my_study/paraview
```

The viewer derives the table on demand for older studies, so re-running the
exporter is optional. One-way FEA history charts are hidden by default: add
`--show-fea-charts` (or `PYCHRONO_FEA_CHARTS=1` with `paraview --script=...`) to
show them, `--fea-series COL1,COL2` to select other columns, or `--no-fea` to
suppress all FEA views. In the GUI, the Tools tab's **Show one-way FEA
scalar/deformation history charts** checkbox is off by default and controls
both the manual ParaView launch and "Open ParaView after run"; unchecking it
clears an inherited `PYCHRONO_FEA_CHARTS` value. The separate "Load 3D FEA
stress/deformation in ParaView" checkbox remains the 3D overlay control only.

For a full 3D animation, enable the UI toggle before running FEA (wizard
equivalents: `--fea-export-vtk` and optional `--vtk-stride N`, default `1`).
The driver writes `fea/vtk/frames/frame_*.vtu` and `fea/vtk/fea.pvd` with
per-element `von_mises_pa`/`von_mises_MPa`, point `Displacement`,
`DisplacementMagnitude`, and `Deformation_mm`. Every exported frame needs a
tetrahedral stress scan, so use a stride above 1 for long runs when a sampled
animation is sufficient; with the default stride the same scan fills the VTU
stress array, and the file write runs in the background writer. The final frame
is always included. Configure ParaView
with the GUI **Tools** toggle "Load 3D FEA stress/deformation in ParaView" or
pass `--with-fea`. ParaView colors the raw undeformed grid by von Mises stress;
the `Warp By Vector` filter is created from `Displacement` but starts hidden, so
toggle it in the Pipeline Browser to see the deformed shape. The excavator
source is rendered at 40% opacity so it does not hide the overlay. The viewer
rechecks the PVD and frame files rather than trusting the manifest: a lone
`t=0` frame is ignored, while a structurally usable partial multi-frame series
can be displayed.

Driver and SPH-coupling fixes applied in the local source/runtime:

- spindle motion is prescribed by Chrono motor constraints with motion functions
  (ground guide -> prismatic linear-position descent motor -> slider -> revolute
  rotation-speed motor -> spindle), instead of per-step state overrides on a
  fixed/free body; FEA hub nodes remain tied to the spindle with
  `ChLinkNodeFrame`. The revolute motor is initialized as `(spindle, slider)`
  because Chrono measures body1 relative to body2; the previous `(slider,
  spindle)` order made the FEA drum rotate opposite to the commanded
  `drum_omega`. This keeps the FEA VTU/Warp-by-Vector drum rotating in the same
  direction as the rigid `excavator.pvd` drum and the signed CRM `spin_deg`;
- default two-way FEA spin-up uses a `0.5 s` linear speed ramp and FEA nodal
  masses are floored by default (`1e-6 kg`) to stabilize sliver nodes; the
  zero-ramp spin segment used by the one-way replay no longer inherits a
  `ChFunctionSequence` continuity offset, which previously kept motor speed at
  zero;
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
  Chrono motor constraints with motion functions. Kinematic mode uses a
  prismatic linear-position descent motor; force mode uses a prismatic
  linear-force motor plus a position hold, and both modes use a revolute
  rotation-speed spin motor with configurable ramp. This avoids per-step body
  state overrides;
- initializes spindle/FEA node kinematics consistently with the prescribed
  motion at `t=0`; in force mode the initial descent velocity is left zero so
  the solver starts from rest;
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

The supported converter is `step_to_chrono_fea.py`. New STEP models use `--axis
Y` as the standard original-space drum axis; the converter rotates the exported
mesh and CRM surface so that axis becomes local +Z for the FEA drivers. Pass
`--axis Z` only for a legacy model whose source axis is Z:

```bash
./chrono-env/bin/python step_to_chrono_fea.py geometry/MyLegacyDrum.step \
    --out-prefix fea/MyLegacyDrum_fea \
    --unit-scale 0.001 \
    --mesh-size 0.012 \
    --surface-target-faces 8000 \
    --hub-radius 0.0015 \
    --axis Z \
    --tetgen-switches pYQ

# New-model standard: original STEP drum axis Y.
./chrono-env/bin/python step_to_chrono_fea.py geometry/NewDrum.step \
    --out-prefix fea/NewDrum_fea --unit-scale 0.001 \
    --axis Y --mesh-size 0.012 --hub-radius 0.035
```

Route: OpenCASCADE Boolean fusion -> `ShapeUpgrade_UnifySameDomain` ->
STL triangulation -> topology-preserving quadric decimation -> optional
part-aware interior decimation -> `trimesh.remesh.subdivide_to_size` -> TetGen
with `pYQ`. Direct Gmsh 3D meshing was abandoned because bucket drum excavator surfaces
produce segment/facet intersection failures. OpenCASCADE's parallel
tessellation mode is enabled by default; `--no-occ-parallel` disables it, and
`--time-stages` reports time in fusion, tessellation, surface processing,
TetGen, and output writing.

For multi-solid STEP assemblies, add:

```bash
    --min-solid-volume 1e-5 \
    --surface-target-faces 8000 \
    --surface-max-faces 256000
```

This drops sub-millimetre fasteners, removes the splitter edges left by Boolean
fusion, and increases the decimation target automatically until the surface is
watertight and TetGen accepts it. MeshFix is intentionally **not** used by the
default route: on fused shells it can seal the wrong side and inflate the bucket drum excavator
volume by several hundred percent. `--repair-after-decimation` remains as a
legacy compatibility option.

### Part-aware interior coarsening

The current wizard/GUI enables `--part-aware-simplify` by default for STEP
models. It classifies the decimated surface by radial distance from the source
drum axis, extracts the patch wholly inside `--part-aware-radius` (default
0.05 m), and applies topology-preserving, boundary-fixed `vtkDecimatePro` only
to that patch. Protected exterior faces are copied unchanged, so the CRM
contact surface and bucket drum excavator exterior resolution are not reduced. The legacy
`--repair-after-decimation` route is incompatible; use MeshFix order `none` or
`before`.

On a bucket drum excavator CAD model with
`--mesh-size 0.02 --surface-target-faces 80000 --surface-max-faces 256000
--min-solid-volume 1e-05 --hub-radius 0.0357669 --axis Y`, the default
`--part-aware-reduction 0.75` produced:

| Mesh | Nodes | Tetrahedra | Hub nodes | Volume error |
|---|---:|---:|---:|---:|
| Without interior coarsening | 81,606 | 352,278 | 24,174 | 0.442% |
| With interior coarsening | 62,939 | 252,638 | 5,569 | 0.259% |

The surface-area totals for every outer radial band (`r > 0.05 m`) were
identical; the area reduction was confined to the rigid hub region. This
primarily cuts the number of constrained hub multipliers and interior DOFs in
the direct Pardiso solve. Revalidate peak stresses close to the hub if those
are design-critical; the outer load-bearing surface is unchanged.

The promoted 8k mesh has:

- 8,721 nodes and 32,321 tetrahedra;
- 220 hub nodes and 8,721 boundary/surface nodes;
- volume error `0.763%` relative to the CAD solid;
- edge lengths min/median/max `6.92e-5 / 4.09e-3 / 1.74e-2 m`.

The original fine reference mesh has 17,658 nodes and 73,423 tets but is not the
default. Surface targets below roughly 7,500 faces
either lose the hub/volume or make TetGen report self-intersections. The
converter now warns instead of aborting when decimation leaves no `HUB` nodes,
because the FEA driver selects hub nodes from the loaded mesh.

Use the CRM surface OBJ generated from your CAD for production work. Avoid
overly simplified meshes because aggressive simplification can close physical
holes and alter contact behavior.

### Verifying a conversion visually

Render the original STEP tessellation, the converted contact surface, and an
overlay of the two (all in metres, in the FEA local frame):

```bash
./chrono-env/bin/python view_geometry.py --study-dir Output_my_study
# or point at explicit files:
./chrono-env/bin/python view_geometry.py \
    --step 'geometry/BucketDrumExcavator.step' \
    --fea-node 'Output_my_study/mesh/BucketDrumExcavator_fea.node'
```

By default it opens an interactive 3D window (drag to rotate, linked panels);
pass `--output PATH` to save a PNG (defaults to
`geometry_checks/<mesh>_geometry_check.png` in the project directory).
The tool also prints the CAD/mesh bounding boxes, volumes,
and volume-error verdict from the `.json` sidecar. It normalises older meshes
whose surface STL was written in raw CAD units (mm), and reads the drum axis
from either the new `source_drum_axis` or legacy `rotation_axis` metadata key.
The exported `_surface.stl` (and the OBJ derived from it) is now in metres,
matching the `.node` mesh; older meshes are handled by the viewer's detection.

## Stable run settings

The FEA-specific defaults are:

| Setting | Default | Why |
|---|---:|---|
| mesh | user-supplied `--fea-node`/`--fea-ele` | promoted tetrahedral mesh; mesh promotion does not validate coupling |
| SPH spacing | `0.012 m` | same smoke resolution as rigid CRM |
| timestep | `2.5e-4 s` | FEA/SPH coupling is unstable at the former rigid production step `5e-4 s` |
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
2. Use the one-way wrench replay for structural screening of the promoted PLA
   mesh; cite it as a response to rigid-CRM loads, not coupled FSI.
3. Use separate, targeted discrete-mechanism checks only when a continuum model cannot represent the mechanism of interest.
4. Use the two-way FEA driver only for short coupling diagnostics; even short
   runs are not established as physically valid.
5. Before treating two-way stress magnitudes as design validation, fix the
   contact-marker problem: match flexible BCE density to SPH resolution and
   verify full rigid spin of the PLA body without bore-driven twist.
6. Calibrate PLA properties from coupon tests of the actual printer, infill, and
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
