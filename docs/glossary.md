# Glossary

Plain-language definitions for the terms you will see in the GUI, command output, and CSV files. More precise descriptions live in [How it works](how-it-works.md).

## The simulation stages

**CRM** — Continuous Representation Model. Chrono's meshless SPH soil model used here for the rigid excavator study. Think of it as a fluid-like continuum that represents regolith with many small sample points.

**SPH** — Smoothed Particle Hydrodynamics. The numerical method behind CRM soil: the soil is a cloud of moving "fluid markers" that carry mass, velocity, and stress information. The `--spacing` setting controls how far apart these markers start.

**BCE** — Boundary Condition Enforcing markers. Stationary particles that impose wall or body boundary conditions in SPH, including the ground and container walls. They are *not* soil and are never counted in excavation metrics.

**Rigid CRM** — The production simulation: a rigid drum moves through SPH soil. Used for fill ratio, torque, work, and specific energy.

**One-way FEA** — A two-step structural screening: first record soil forces from rigid CRM, then replay those forces on a flexible solid drum mesh. The soil does not react to the flexing drum.

**Two-way FEA** — A coupled simulation where the flexible drum and soil affect each other on every step. Implemented for diagnostics but not engineering-validated in this project.

**FEA** — Finite Element Analysis. A solid mesh of small tetrahedra used to estimate stress and deformation.

**PLA** — Polylactic acid, the 3D-printable drum material whose stiffness/density are used by the FEA replay.

## Geometry and meshing

**STEP** — A standard CAD exchange format (`.step`/`.stp`). The usual starting point for a new design.

**OCC / OpenCASCADE** — The geometry kernel used to read and tessellate STEP files.

**Surface OBJ / STL** — A triangulated shell of the drum. The rigid CRM stage uses the OBJ as its contact surface.

**TetGen `.node` / `.ele`** — The tetrahedral solid mesh files used by FEA: node coordinates and element connectivity.

**Surface target faces / fit tolerance** — Controls for converting CAD to a surface mesh. The converter raises the face target until the measured exterior deviation is below the tolerance and the solid mesh is accepted.

**Hub / interior coarsening** — The hub is the rigid centre of the drum. Its many tiny CAD triangles are expensive in FEA, so the converter coarsens only interior faces and leaves bucket exterior faces unchanged.

**Part-aware simplification** — The converter mode that performs the interior-only coarsening described above.

**CAD axis / unit scale** — Where the drum axis points in the original CAD, and the multiplier that turns CAD coordinates into metres (usually `0.001` for millimetres).

## Motion and numerics

**Kinematic descent** — The default motion: the drum lowers at a fixed speed (`--descent-speed`) while it spins. The pose is prescribed.

**Force descent** — An opt-in mode: a downward actuator force (`--descent-force`) pushes the drum after settling; soil reaction then affects its motion. Spin is still prescribed.

**Settle time** — A short pause before spin/descent so the SPH bed can settle.

**Omega (`--omega`)** — Angular speed of the drum in radians per second. About 3.14 rad/s is roughly 30 rpm. **Reverse spin** flips its sign.

**Spin axis** — The global axis (X, Y, or Z) that the drum rotates about.

**dt / timestep** — The simulated time advanced per solver step. CRM uses a small explicit timestep that depends on SPH spacing; FEA has its own timestep. Unsafe CRM values are rejected unless explicitly overridden.

**FPS** — Here, output cadence in Hz, not graphics frame rate. CRM summary, soil loads, particle CSV, and FEA summary cadences are independent.

**d0 / smoothing length** — The SPH kernel width, set by `d0_multiplier × spacing`. The default multiplier is 1.0.

**Artificial viscosity / PPST push** — Damping and particle-shifting controls that can reduce the first-bite soil eruption or particle drift. They are not substitutes for a stable timestep.

**Active box** — A drum-centred region where SPH particles remain active. Particles flung outside it are deactivated, so it must be large enough.

**Bed padding** — Extra soil around the drum footprint, as a fraction of drum radius, so the drum does not immediately dig through the bed edges.

## Metrics and classification

**Excavated** — A particle fully inside the detector cylinder eroded by one SPH radius in the drum's live pose. The same set feeds fill ratio and specific energy. `Lifted` is a separate diagnostic (above the initial bed by at least one SPH radius) and does not gate fill.

**Fill detector / detector cylinder** — An analytical cylinder, following the drum's live pose, used to decide whether a particle is inside the drum. It can be auto-sized from the measured drum or set manually.

**inside_drum** — The count of particles currently in the excavated set. A particle loses the flag as soon as it leaves the cylinder or falls back to the bed.

**Fill ratio** — Excavated soil mass (or count) divided by the constant free-capacity mass. `fill_ratio_mass` is the main design metric; `fill_ratio_count` is the simpler count-based version.

**Capacity volume** — The constant denominator volume. By default it starts as the nominal cavity cylinder from the drum radius and length, then subtracts the excavator mesh volume overlapping that cavity once at startup. Hubs, spokes, and flights are therefore not counted as unfillable space. A `--capacity-volume` override is already a free volume and is used unchanged.

**Torque / work / specific energy** — Soil resistance torque about the spin axis, accumulated mechanical work, and work per kilogram of excavated soil. Lower specific energy means more efficient excavation.

**raw vs filtered stress** — The FEA summary reports peak von Mises stress from the tet mesh; `max_von_mises_raw_pa` is the unfiltered value used for diagnostics.

## Outputs and viewing

**PVD** — ParaView Data file: a time-series index pointing to per-frame VTK files.

**VTP / VTU** — VTK PolyData / Unstructured Grid files: particles/drum surfaces and tetrahedral stress results respectively.

**`crm_particles.pvd`, `excavator.pvd`, `crm_animation.pvd`** — The particle series, rigid drum series, and combined non-FEA animation. `combined.pvd` is an older alias.

**`fea.pvd` / `with_fea.pvd`** — Optional 3D stress/deformation time series and its combined view with CRM data. The viewer rechecks the PVD and frame files: a lone `t=0` frame is ignored, but a structurally usable partial multi-frame series can still load.

**FEA history charts** — Optional XY plots of scalar stress/deformation over time. Off by default; enable with the Tools checkbox or `--show-fea-charts`.

**Output Preview tab** — A lightweight Tk-only viewer that reads CSVs directly. It draws at most a bounded sample of particles but reports the true particle count.

**Stride** — Take every Nth frame or every Nth particle. Used to trade visual detail for speed and disk space.
