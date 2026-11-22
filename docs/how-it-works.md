# How it works

This is the in-depth guide to the project's mechanics. It explains what the tools actually simulate, how data moves between stages, and which results you can trust. For a gentler introduction, start with [Quick start](quickstart.md); for definitions, see the [Glossary](glossary.md).

## The big picture

```text
STEP CAD (or existing meshes)
        |
        | step_to_chrono_fea.py + stl_to_obj.py
        v
  mesh/ surface OBJ + tet mesh (.node/.ele) + metadata
        |
        v
  rigid CRM  --soil wrench-->  one-way FEA replay
  (SPH soil,                   (solid PLA drum,
   rigid drum)                  prescribed motion)
        |                             |
        +------------ CSVs -----------+
                       |
              export_study_vtk.py
                       v
             ParaView time series / charts
```

Three front ends drive the same pipeline:

- `study_gui.py` — Tkinter GUI; builds command lists using `study_wizard.py` helper functions.
- `study_wizard.py` — interactive/non-interactive CLI; writes `study_config.json` and `commands.sh`.
- Direct drivers (`crm/run.sh`, `crm/run_oneway_fea.sh`, `step_to_chrono_fea.py`, etc.) for advanced use.

`full_model.py` is a separate diagnostic orchestrator for rigid CRM plus two-way coupled FEA. It is not part of the supported study pipeline.

## Stage 1: geometry preparation

A study can start from existing meshes or from STEP CAD.

The STEP path (`step_to_chrono_fea.py`) does the following:

1. Reads the CAD with OpenCASCADE and scales coordinates to metres (`--unit-scale`, typically `0.001`).
2. Rotates the drum axis from CAD space (`--axis`, usually `Y`) onto the local +Z axis used by Chrono.
3. Fuses the assembly and drops solids below `--min-solid-volume` (small fasteners/debris).
4. Tessellates and repairs the exterior surface. On the default wizard/GUI path it starts at `--surface-target-faces` and raises the target until the measured bidirectional p99.9 deviation is within `--surface-fit-tolerance` and the solid mesh is accepted. Direct converter use with `--skip-decimation`, `--repair-after-decimation`, or a zero face target fails instead of retrying.
5. Optionally applies **part-aware simplification**: only faces wholly within `--part-aware-radius` of the axis are decimated; bucket exterior faces are copied unchanged. The hub is rigid in FEA, so its dense CAD triangulation is mostly wasted solver cost. This cut ~23% of nodes and ~28% of tetrahedra on the reference drum while leaving exterior surface area unchanged.
6. Generates the tetrahedral mesh with TetGen (`.node`/`.ele`, plus `.inp`), a repaired surface STL, and a JSON metadata file with CAD measurements, fit results, volume error, and simplification statistics. The next `stl_to_obj.py` stage turns that STL into the CRM contact OBJ without scaling or reorientation.

`stl_to_obj.py` is a pure format conversion from the surface STL to the OBJ used by rigid CRM contact.

Geometry auto-scaling in the wizard/GUI measures the drum radius and length from the CAD or mesh and suggests compatible bed height, active box, SPH spacing, cavity radius, and mesh size. With auto geometry enabled (the default), edited outer drum dimensions are restored to the measured values, an impossible cavity is reset, and a kinematic run shorter than the full descent is extended. Corrections are recorded in `study_config.json`. Manual **detector** values are never overwritten by this process.

## Stage 2: rigid Chrono::CRM (production model)

`crm/lunar_excavator_crm.py` models regolith as a continuum SPH material (Chrono::CRM), not as individually resolved grains. This is deliberate: the design target is bulk soil flow and fill, while real grains are tens to hundreds of micrometres across. The physical grain diameter enters the μ(I) rheology; the SPH `--spacing` is a numerical resolution choice.

Soil defaults live in `crm/crm_soil.py`: bulk density 1650 kg/m³, effective stiffness 2 MPa, Poisson ratio 0.30, friction μ_s 0.75, cohesion 1.2 kPa, and 200 μm grain diameter. They are independent best estimates, not calibrated measurements, and should be frozen across geometry comparisons.

### Motion

The default is **kinematic** descent: the drum lowers at a constant speed while spinning at a prescribed rate about the configured axis. The spin persists; only the vertical motion changes in force mode.

With `--descent-mode force`, a downward actuator is applied after `--settle-time`, then a position motor holds the final target height. Soil reaction always acts on the drum. The drum's own weight is applied only with `--drum-gravity` (off by default to preserve historical force-descent behaviour). Global `--gravity` always acts on CRM soil and on FEA drums; because kinematic motion overrides the pose, the drum-gravity toggle mainly changes force-mode dynamics.

The soil bed is padded around the drum and enclosed by fixed reflective BCE walls and a lid, so ejecta is reflected back instead of escaping. The **active box** is a larger, drum-centred region in which particles stay active; particles outside it are deactivated. The default box extends two full drum diameters beyond the padded bed laterally and well above the crest vertically.

### Timestep and numerics

CRM uses an explicit timestep whose stable value scales with SPH spacing (about 4 × 10⁻⁵ s at 2 mm). Oversized values are rejected unless `--allow-unsafe-dt` is used. The kernel width is `d0_multiplier × spacing`; artificial viscosity and PPST shifting can damp first-contact ejecta or reduce outward drift, but they do not replace a stable timestep.

### Fill classification and metrics

At each output time, particles are transformed into the drum's local frame using the **full live pose**. With SPH radius `rp = 0.5 × spacing`, a particle is *excavated* when:

- it is above the initial bed surface by at least `rp`, and
- it lies inside the detector cylinder eroded by `rp` on the radial side and at both ends (clamped at zero).

The cylinder dimensions normally follow the measured drum radius and full length; they can be set manually. By default the classifier follows the drum exactly and classification is **instantaneous**: leave the cylinder or fall back to the bed and the flag is removed. The same `excavated` set feeds `inside_drum`, `fill_ratio_count`, `fill_ratio_mass`, and the particle CSV flags.

```text
retained mass = excavated particle count × particle mass
capacity mass = bulk density × π × inner_radius² × drum length
fill ratio    = retained mass / capacity mass
```

The denominator is the nominal cylinder; bucket flights displace some of it. Supply `--capacity-volume` for a CAD-derived free volume when comparing designs fairly. The summary reports the X torque component as `fluid_torque_x_nm`; `input_power_w` and `work_j` use the full soil wrench projected onto the configured spin axis, and `specific_energy_j_kg` divides work by excavated mass. Full force/torque components are in `soil_loads.csv`.

**Only SPH fluid markers are counted.** Boundary (BCE) markers represent walls and the floor; older comparisons that included them are invalid.

## Stage 3: one-way CRM → FEA (structural screening)

Two-way SPH/flexible-BCE coupling proved unstable in this project, so the supported structural path records the rigid soil wrench and replays it later:

1. Rigid CRM writes the soil force/torque on the drum to `soil_loads.csv` at `--loads-fps`. The FEA driver interpolates by time.
2. The replay creates **no SPH/terrain system**. It uses the same motor-driven spindle for descent and spin, ties hub nodes rigidly to the spindle, transforms the recorded wrench into the drum frame, and distributes it over exterior solid nodes as equivalent nodal forces (minimum-norm, mass-weighted) that reproduce the net force and torque.
3. The drum is a solid PLA mesh with configurable modulus, Poisson ratio, density, and Rayleigh damping. Hub nodes inside `--hub-radius` are rigidly connected to the spindle.
4. `--load-radius-fraction` limits forces to exterior nodes beyond a fraction of drum radius; `0.95` is cheaper, `0.8` spreads the load more widely. Peak-von-Mises scanning can be sampled with `--raw-stress-every`.

Keep motion and gravity settings matched manually. The replay checks the CSV schema and descent mode, needs at least two sorted samples, and in kinematic mode compares the Z trajectory with the expected settle/descent geometry. It does not validate gravity, spin, or actuator force settings. In force mode it follows the recorded Z trajectory with a position motor because there is no soil reaction to balance the actuator.

The fast default writes scalar summaries only. Full 3D stress/deformation (`fea/vtk/frames/frame_*.vtu` plus `fea/vtk/fea.pvd`) is opt-in because each frame requires a tetrahedral stress scan and a VTU write. FEA output times are snapped to a 1 ms grid so coarse structural timesteps do not leak into printed times.

This path is a defensible *comparative* structural estimate under recorded rigid loads. It is **not** two-way FSI: the flexing drum cannot change the soil flow.

## Stage 4: commands, configuration, and reproducibility

The wizard/GUI do not hide the simulation from you: they generate a list of stage commands:

1. `generate-FEA-mesh` (STEP studies) — `step_to_chrono_fea.py`.
2. `make-CRM-surface-OBJ` — `stl_to_obj.py`.
3. `rigid-CRM` — `crm/run.sh` with `--loads-file` and optional particle export.
4. `one-way-FEA` — `crm/run_oneway_fea.sh` with the recorded loads.
5. `export-ParaView-VTK` — `export_study_vtk.py` (when enabled).

These are written to `commands.sh` (each stage piped to its own log). The `commands` array in `study_config.json` stores name/command pairs for the same stages, alongside all resolved settings, measured defaults, geometry corrections, and soil/PLA parameter blocks. The GUI can terminate the active step or replay only the FEA stage; the CLI provides the same primitives directly.

## Stage 5: output data flow

- `crm/summary.csv` — time series of pose, counts, fill, torque, work, specific energy.
- `crm/soil_loads.csv` — time-sampled wrench consumed by FEA.
- `crm/particles_*.csv` — marker positions and flags for visualization.
- `fea/summary.csv` — structural time series (deformation, von Mises stress, motor errors).
- `export_study_vtk.py` — rebuilds particle and excavator PVD/VTP series from CRM data every time; derives `fea_summary.csv` in MPa/mm; optionally revalidates and copies a structurally usable 3D FEA PVD.

The 3D `crm_animation.pvd` (and `combined.pvd`) contains **CRM particles and rigid poses only**, never FEA. One-way FEA history charts are opt-in in the viewer; the 3D FEA overlay requires a structurally usable multi-frame PVD. A lone `t=0` frame from a cancelled run is rejected, but a usable partial multi-frame series can load even if an older manifest marked it invalid. See [Outputs and visualization](outputs-and-visualization.md).

## What is validated and what is not

| Path | Status | Use it for |
| --- | --- | --- |
| Rigid CRM | Production/design driver | Fill ratio, bulk soil flow, torque, specific energy, geometry comparisons |
| One-way CRM → FEA | Supported structural screening | Comparative PLA stress/deformation under recorded loads |
| Two-way flex-BCE FEA | Diagnostic, unstable/inconsistent | Method investigation only |
| `full_model.py` combined runner | Diagnostic | Orchestration checks and reports, not design validation |

Soil parameters are estimates, drivetrain dynamics are not modelled, spin is prescribed even in force mode, cohesive crust fracture is absent from the default μ(I) model, and no full simulant calibration has been completed. Treat all absolute numbers with those limits in mind; keep settings fixed when comparing designs.

For the evidence behind the FEA status, read [crm/FEA_COUPLING.md](../crm/FEA_COUPLING.md). For every CLI option, read [crm/README.md](../crm/README.md).
