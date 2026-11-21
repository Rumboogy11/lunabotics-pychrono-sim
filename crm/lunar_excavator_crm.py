#!/usr/bin/env python3
"""Lunar bucket-drum excavator -- primary Chrono::CRM design driver.

This is the geometry-optimization model for the project.  It treats lunar
regolith as a continuum SPH soil (Chrono::CRM), not as individually resolved
grains.  That is deliberate: the design target is bulk fill ratio and soil flow
around alternative drum/spike geometries, while real regolith grains are tens
to hundreds of micrometres across.

The soil block in ``crm_soil.py`` defines independent bulk soil parameters.
Geometry and kinematics reuse the shared excavator dimensions from
``lunar_excavator_config``.

Fill-ratio definition
---------------------
At each output time, SPH particles are transformed into the drum's local frame
using its full live pose.  The mesh's local Z axis is the long axis of the drum.
With SPH particle radius ``rp = 0.5 * spacing`` (Chrono ``d0 = spacing``), an
"excavated" particle is above the initial bed surface by the ``rp`` lift
tolerance and either:

* inside the eroded outer drum envelope (``radial <= drum_radius - rp`` and
  ``abs(local_z) <= drum_half_length - rp``, bounds clamped at zero), or, when
  ``--fill-detector-obj`` is supplied, inside the exact watertight negative of
  the drum interior, eroded by ``rp``.

The optional mesh follows the excavator's exact rotation and translation and is
never expanded into a revolved envelope, so it cannot protrude outside the
physical geometry.  Classification is instantaneous: a particle is counted only
while it is inside the current mesh pose and above the initial bed, and loses
the flag when it leaves the mesh.  Fill uses exactly this excavated set::

    retained_mass = n_excavated * particle_mass
    capacity_mass = bulk_density * pi * inner_radius**2 * drum_length
    fill_ratio    = retained_mass / capacity_mass

The cylindrical capacity denominator stays the nominal envelope; the screw
flights displace some of it.  Pass ``--capacity-volume`` if a CAD-derived free
volume is available.

Requirements
------------
This driver needs a PyChrono build with the CRM/FSI modules::

    import pychrono.core
    import pychrono.vehicle   # CRMTerrain
    import pychrono.fsi       # ElasticMaterialProperties, SPHParameters

The PyPI ``pychrono`` wheel does not currently include ``pychrono.fsi`` or
``pychrono.vehicle``.  Use a source build or a conda-forge build with FSI/SPH
and CRM enabled.  ``--help`` works without those modules.

Usage
-----
    ./crm/run.sh --smoke-test
    ./crm/run.sh --duration 20 --fps 20 --output-dir Output_CRM_Baseline
    ./crm/run.sh --mesh-file geometry/spiked_drum.obj \
        --output-dir Output_CRM_Spiked

    # Print the independent soil estimate (no CRM import needed):
    ./crm/run.sh --print-soil
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import multiprocessing
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CRM_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CRM_DIR))

from crm_soil import DEFAULT_SOIL, CrmSoilParams  # noqa: E402
from fill_detector import (  # noqa: E402
    FillDetector,
    FillSurfaceError,
    detector_definition,
    load_fill_detector,
)
from crm_soil import _enum as fsi_enum  # noqa: E402
from lunar_excavator_config import (  # noqa: E402
    BED_PADDING_RADIUS_FRACTION,
    DEFAULT as MOTION_DEFAULT,
    OBJ_COARSE,
    OBJ_MEDIUM,
    bed_half_extents,
)


@dataclass(frozen=True)
class CrmRunParams:
    """Numerics, geometry hooks, and excavation metrics for a CRM run."""

    spacing: float = 0.002               # SPH particle spacing [m] (smaller = finer resolution/more particles)
    dt: float = 4.0e-5                   # CRM/FSI step [s] at the default 2 mm spacing
    duration: float = MOTION_DEFAULT.t_max
    fps: float = 20.0                    # metric output cadence
    loads_fps: float = 20.0              # soil-wrench CSV cadence (one-way FEA interpolates by time)
    particle_fps: float = 5.0            # particle CSV cadence (ParaView particle frames)
    particle_writers: int = 4            # background processes for per-frame particle CSV writes
    settle_time: float = MOTION_DEFAULT.settle_time
    descent_speed: float = MOTION_DEFAULT.lower_speed
    descent_mode: str = "kinematic"          # "kinematic" or "force"
    descent_force_n: float = 1.0             # downward actuator force in force mode
    descent_force_ramp_s: float = 0.0        # linear ramp from 0 to descent_force_n
    gravity: float = MOTION_DEFAULT.gravity   # global Z acceleration for SPH/FEA [m/s^2]
    drum_gravity: bool = False                # rigid CRM drum also follows global gravity
    omega: float = MOTION_DEFAULT.drum_omega
    reverse_spin: bool = False
    bed_height: float = MOTION_DEFAULT.bed_height
    bed_padding: float = BED_PADDING_RADIUS_FRACTION  # soil padding per side, fraction of drum radius
    container_padding: float | None = None  # solid wall gap beyond the soil bed; None = derive from spacing
    spin_axis: str = MOTION_DEFAULT.drum_spin_axis
    drum_radius: float = MOTION_DEFAULT.drum_radius        # outer/crest radius [m]
    drum_half_length: float = MOTION_DEFAULT.drum_half_length
    inner_radius: float = 0.055          # nominal cavity radius [m]
    capacity_volume: float | None = None  # override for CAD free volume [m^3]
    fill_detector_obj: str | None = None  # watertight local-frame negative of the drum interior
    active_box_x: float = 0.631333  # padded bed + 2 drum diameters (reference drum)
    active_box_y: float = 0.687407
    active_box_z: float = 0.866370  # bed + initial centre height + 2 diameters
    max_velocity: float = 1.0            # SPH max velocity for EOS stabilization
    artificial_viscosity: float = 0.5    # artificial bilateral viscosity
    shifting_ppst_push: float = 3.0      # PPST shifting push strength
    d0_multiplier: float = 1.0           # h = d0_multiplier * spacing
    free_surface_threshold: float = 2.4  # divergence threshold for free surface
    proximity_steps: int = 4             # steps between neighbor-list updates

    def __post_init__(self) -> None:
        if self.descent_mode not in {"kinematic", "force"}:
            raise ValueError("descent_mode must be 'kinematic' or 'force'")
        if not math.isfinite(self.gravity):
            raise ValueError("gravity must be finite")
        if self.descent_mode == "force":
            if self.descent_force_n <= 0.0:
                raise ValueError("descent_force_n must be positive")
            if self.descent_force_ramp_s < 0.0:
                raise ValueError("descent_force_ramp_s must be non-negative")
        positive = ["spacing", "dt", "duration", "fps", "loads_fps", "particle_fps",
                    "bed_height", "drum_radius", "drum_half_length", "inner_radius",
                    "max_velocity", "d0_multiplier", "free_surface_threshold"]
        for name in positive:
            if getattr(self, name) <= 0.0:
                raise ValueError(f"{name} must be positive")
        # Zero is valid in kinematic mode (constant height) and in force mode
        # (speed is unused there); only a negative lowering command is invalid.
        if self.descent_speed < 0.0:
            raise ValueError("descent_speed cannot be negative")
        if self.particle_writers < 1:
            raise ValueError("particle_writers must be at least 1")
        if self.omega == 0.0:
            raise ValueError("omega must be nonzero")
        if self.spin_axis.upper() not in {"X", "Y", "Z"}:
            raise ValueError("spin axis must be X, Y, or Z")
        if self.proximity_steps < 1:
            raise ValueError("proximity_steps must be at least 1")
        if self.artificial_viscosity < 0.0 or self.shifting_ppst_push < 0.0:
            raise ValueError("artificial_viscosity and shifting_ppst_push must be non-negative")
        if self.bed_padding < 0.0:
            raise ValueError("bed_padding must be non-negative")
        if self.container_padding is not None and self.container_padding < 0.0:
            raise ValueError("container_padding must be non-negative")
        if self.inner_radius >= self.drum_radius:
            raise ValueError("inner radius must be smaller than the outer drum radius")
        if min(self.active_box_x, self.active_box_y, self.active_box_z) <= 0.0:
            raise ValueError("active domain dimensions must be positive")

    @property
    def active_box(self) -> tuple[float, float, float]:
        return (self.active_box_x, self.active_box_y, self.active_box_z)


METRIC_FIELDS = [
    "frame",
    "time_s",
    "wall_s",
    "drum_z_m",
    "spin_deg",
    "omega_rad_s",
    "sph_particles",
    "inside_drum",
    "lifted",
    "excavated",
    "fill_ratio_count",
    "fill_ratio_mass",
    "peak_fill_ratio_mass",
    "capacity_m3",
    "particle_mass_kg",
    "fluid_torque_x_nm",
    "input_power_w",
    "work_j",
    "specific_energy_j_kg",
]

# Per-step soil wrench on the rigid drum.  This is the interface consumed by
# the one-way CRM -> FEA driver (the FEA run replays these loads on the solid
# mesh instead of coupling SPH directly to flexible BCE markers).
LOAD_FIELDS = [
    "time_s",
    "drum_z_m",
    "force_x_n",
    "force_y_n",
    "force_z_n",
    "torque_x_nm",
    "torque_y_nm",
    "torque_z_nm",
]

# Extra telemetry is written only for explicit force-driven runs, so the
# default CSV schema stays byte-for-byte compatible with earlier studies.
FORCE_METRIC_FIELDS = [
    "descent_mode",
    "descent_state",
    "actuator_force_n",
    "vertical_speed_m_s",
]
FORCE_LOAD_FIELDS = list(FORCE_METRIC_FIELDS)


def metric_fields(run: CrmRunParams) -> list[str]:
    return METRIC_FIELDS + (FORCE_METRIC_FIELDS if run.descent_mode == "force" else [])


def load_fields(run: CrmRunParams) -> list[str]:
    return LOAD_FIELDS + (FORCE_LOAD_FIELDS if run.descent_mode == "force" else [])


def build_parser(*, include_drum_gravity: bool = True) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--duration", type=float, default=None,
                   help="simulated seconds (default: 60)")
    p.add_argument("--dt", type=float, default=None,
                   help="CRM/FSI step size in seconds (default: spacing-dependent, "
                        "4e-5 at 2 mm and 2.4e-4 at 12 mm)")
    p.add_argument("--allow-unsafe-dt", action="store_true",
                   help="allow an explicitly requested --dt above the stable SPH bound "
                        "(normally rejected to prevent GPU boundary blowups)")
    p.add_argument("--fps", type=float, default=None,
                   help="metric output cadence in Hz (default: 20)")
    p.add_argument("--particle-fps", type=float, default=None,
                   help="particle CSV cadence in Hz (default: 5; capped by --fps)")
    p.add_argument("--loads-fps", type=float, default=None,
                   help="soil-wrench CSV cadence in Hz (default: 20; one-way FEA interpolates by time)")
    p.add_argument("--spacing", type=float, default=None,
                   help="SPH particle spacing in metres (default: 0.002; smaller = finer particles/more resolution)")
    p.add_argument("--settle-time", type=float, default=None,
                   help="seconds before spin/descent begins")
    p.add_argument("--descent-speed", type=float, default=None,
                   help="drum descent speed in m/s for kinematic mode (default: 0.0015)")
    p.add_argument("--descent-mode", choices=("kinematic", "force"), default=None,
                   help="descent control mode (default: kinematic)")
    p.add_argument("--descent-force", type=float, default=None,
                   help="downward actuator force in N for --descent-mode force (default: 1.0)")
    p.add_argument("--descent-force-ramp", type=float, default=None,
                   help="linear force ramp duration in s for force descent (default: 0)")
    p.add_argument("--gravity", type=float, default=None,
                   help="global Z gravitational acceleration in m/s^2 (default: -9.81). "
                        "Always applied to CRM soil and FEA drums; use --drum-gravity for the rigid CRM drum")
    if include_drum_gravity:
        p.add_argument("--drum-gravity", action=argparse.BooleanOptionalAction, default=False,
                       help="also apply global gravity to the rigid CRM drum (default: off; "
                            "FEA drums always follow global gravity)")
    p.add_argument("--omega", type=float, default=None,
                   help="drum spin rate magnitude in rad/s (default: shared config)")
    p.add_argument("--reverse-spin", action="store_true",
                   help="reverse the drum spin direction (flip rotation sign)")
    p.add_argument("--bed-height", type=float, default=None,
                   help="initial soil bed height in m (default: 0.06)")
    p.add_argument("--bed-padding", type=float, default=None,
                   help="soil padding around the drum per side, as a fraction of drum radius "
                        f"(default: {BED_PADDING_RADIUS_FRACTION:g})")
    p.add_argument("--container-padding", type=float, default=None,
                    help="gap in metres between the outermost soil particles and the fixed reflective "
                         "container (default: half an SPH spacing)")
    p.add_argument("--spin-axis", choices=("X", "Y", "Z"), default=None,
                   help="global spin axis (default: X)")
    p.add_argument("--drum-radius", type=float, default=None,
                   help="outer/crest radius in m (default: shared config, 0.061)")
    p.add_argument("--drum-half-length", type=float, default=None,
                   help="half of drum axial length in m (default: shared config, 0.033)")

    p.add_argument("--mesh", choices=("coarse", "medium"), default="coarse",
                   help="shipped OBJ: full coarse (default, holes preserved) or medium")
    p.add_argument("--mesh-file", type=Path, default=None,
                   help="custom drum OBJ, e.g. a spiked variant (overrides --mesh)")
    p.add_argument("--inner-radius", type=float, default=None,
                   help="nominal cavity radius for fill ratio in m (default: 0.055)")
    p.add_argument("--capacity-volume", type=float, default=None,
                   help="override nominal cavity volume in m^3 (for CAD-calibrated designs)")
    p.add_argument("--fill-detector-obj", type=Path, default=None,
                   help="watertight OBJ surface in drum-local metres representing the negative "
                        "interior of the excavator; follows the full drum pose and defines "
                        "excavated/fill particles by current containment only")
    p.add_argument("--active-box", type=str, default=None,
                   help="active SPH domain full size X,Y,Z in metres (default: padded bed + 2 drum "
                        "diameters laterally; bed + initial height + 2 diameters + padding vertically)")
    p.add_argument("--max-velocity", type=float, default=None,
                   help="SPH stabilizing max velocity in m/s (default: 1.0)")
    p.add_argument("--artificial-viscosity", type=float, default=None,
                    help="artificial bilateral SPH viscosity (default: 0.5)")
    p.add_argument("--shifting-ppst-push", type=float, default=None,
                    help="PPST shifting push strength (default: 3.0)")
    p.add_argument("--d0-multiplier", type=float, default=None,
                   help="kernel length multiplier h/spacing (default: 1.0)")
    p.add_argument("--free-surface-threshold", type=float, default=None,
                   help="free-surface divergence threshold (default: 2.4)")
    p.add_argument("--proximity-steps", type=int, default=None,
                   help="steps between neighbor-list updates (default: 4)")

    p.add_argument("--density", type=float, default=None, help="bulk soil density in kg/m^3")
    p.add_argument("--youngs-modulus", type=float, default=None, help="soil Young's modulus in Pa")
    p.add_argument("--poisson-ratio", type=float, default=None, help="soil Poisson ratio")
    p.add_argument("--friction", type=float, default=None,
                   help="mu_s; also sets mu_2 unless --mu-2 is given")
    p.add_argument("--mu-2", type=float, default=None, help="high-inertia friction mu_2")
    p.add_argument("--mu-i0", type=float, default=None, help="reference inertial number")
    p.add_argument("--cohesion", type=float, default=None, help="cohesion in Pa")
    p.add_argument("--grain-diameter", type=float, default=None,
                   help="physical grain diameter for mu(I), in m (NOT SPH spacing)")

    p.add_argument("--output-dir", type=Path, default=Path("Output_CRM_LunarExcavator"))
    p.add_argument("--no-output", action="store_true", help="run without writing files")
    p.add_argument("--save-particles", action="store_true",
                   help="write per-frame SPH particle CSV (large)")
    p.add_argument("--particle-writers", type=int, default=None,
                   help="background processes for particle CSV writes (default: 4)")
    p.add_argument("--loads-file", type=Path, default=None,
                   help="write the soil force/torque wrench to this CSV at --loads-fps cadence "
                        "(used by the one-way CRM -> FEA driver)")
    p.add_argument("--print-soil", action="store_true",
                   help="print the independent soil parameter block and exit")
    p.add_argument("--smoke-test", action="store_true",
                   help="short coarse-mesh run with a coarser SPH spacing")
    return p


def parse_box(text: str) -> tuple[float, float, float]:
    try:
        values = tuple(float(x) for x in text.replace(";", ",").split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected three comma-separated numbers") from exc
    if len(values) != 3:
        raise argparse.ArgumentTypeError("expected three comma-separated numbers")
    return values  # type: ignore[return-value]


def make_soil(args: argparse.Namespace) -> CrmSoilParams:
    friction = args.friction if args.friction is not None else DEFAULT_SOIL.friction
    mu_2 = args.mu_2 if args.mu_2 is not None else friction
    values = {
        "density": args.density,
        "youngs_modulus": args.youngs_modulus,
        "poisson_ratio": args.poisson_ratio,
        "friction": args.friction,
        "mu_2": mu_2,
        "mu_I0": args.mu_i0,
        "cohesion": args.cohesion,
        "grain_diameter": args.grain_diameter,
    }
    overrides = {k: v for k, v in values.items() if v is not None}
    return replace(DEFAULT_SOIL, **overrides)  # type: ignore[arg-type]


def suggested_sph_dt(spacing: float, d0_multiplier: float = 1.0) -> float:
    """Conservative default CRM timestep for this SPH setup.

    The value is empirical, based on stability tests with this soil block and
    the coarse production OBJ: 4 mm needed <= 1e-4 s (5e-4 s exploded), while
    12 mm is stable at 2.5e-4 s and has also run at 5e-4 s.
    """
    return min(5.0e-4, 8.0e-5 * (spacing / 0.004) * d0_multiplier)


def max_stable_sph_dt(spacing: float, d0_multiplier: float = 1.0) -> float:
    """Largest explicitly requested timestep accepted without --allow-unsafe-dt."""
    if spacing >= 0.008:
        return min(5.0e-4, 5.0e-4 * d0_multiplier)
    return 1.25e-4 * (spacing / 0.004) * d0_multiplier


def solid_container_padding(run: CrmRunParams) -> float:
    """Gap between the outermost soil particles and the reflective container."""
    if run.container_padding is not None:
        return float(run.container_padding)
    # The bed's own BCE layers are one, two, and three spacings outside the
    # particle grid.  Half a spacing puts the container wall almost against the
    # particles and makes its own BCE layers interleave, not coincide with, the
    # bed BCE.  The wall itself stays strictly outside every SPH particle.
    return 0.5 * run.spacing


def bed_particle_bounds(run: CrmRunParams, motion
                        ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Initial SPH particle AABB produced by ``CRMTerrain.Construct()``.

    Chrono snaps the requested bed box to whole particle spacings.  The first
    particle sits on the negative face; the last is reached by rounding the
    requested full extent to the nearest whole number of spacings.  Using the
    snapped grid rather than the nominal bed box keeps the reflective wall just
    outside the real particles on every side, including the asymmetric faces
    caused by rounding.
    """
    spacing = run.spacing

    def intervals(extent: float) -> int:
        return math.floor(extent / spacing + 0.5)

    x_min = -motion.bed_x_half
    y_min = -motion.bed_y_half
    z_min = motion.floor_z
    return (
        (x_min, y_min, z_min),
        (
            x_min + intervals(2.0 * motion.bed_x_half) * spacing,
            y_min + intervals(2.0 * motion.bed_y_half) * spacing,
            z_min + intervals(motion.bed_height) * spacing,
        ),
    )


def fit_solid_container(run: CrmRunParams, motion
                        ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Interior AABB of the fixed reflective container around the soil bed.

    The side walls and floor sit half a spacing outside the actual initial SPH
    particle grid, so soil cannot fall off the bed before meeting a wall and no
    BCE marker cuts through a particle.  The lid is placed just inside the
    active-box ceiling at the lowest drum position reached during the run, so
    ejecta strike physical BCE walls and bounce back instead of being
    deactivated by Chrono's body-frame active box.  The bed floor already has
    its own BCE layers from ``Construct()``; ``AddBoxContainer()`` adds the four
    side walls and this lid.
    """
    padding = solid_container_padding(run)
    particle_min, particle_max = bed_particle_bounds(run, motion)
    min_x, min_y, min_z = (particle_min[i] - padding for i in range(3))
    max_x, max_y = particle_max[0] + padding, particle_max[1] + padding
    lowest_center = min(motion.drum_center_z0, motion.z_target)
    max_z = lowest_center + 0.5 * run.active_box_z - padding
    return ((min_x, min_y, min_z), (max_x, max_y, max_z))


def fit_computational_domain(run: CrmRunParams, motion,
                             container: tuple[tuple[float, float, float],
                                              tuple[float, float, float]] | None = None
                             ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """World-axis deactivation bounds for the SPH computational domain.

    This is distinct from ``fit_active_box``: Chrono's active box is expressed
    in the FSI body frame and follows the rotating drum, while the computational
    domain is a fixed world AABB used for the neighbor grid and the "zombie"
    boundary that zeroes particle velocity.  Its default is derived from the
    initial marker AABB (bed surface plus three BCE layers), which puts a hard
    ceiling just above the bed and freezes excavated soil in mid-air.

    The fixed reflective container created by :func:`fit_solid_container` is
    the physical boundary; this world box only has to contain every moving
    marker and every BCE layer.  Keep the old aerodynamic headroom as a lower
    bound, then expand for the container so an ejecta particle always meets the
    container BCE before the deactivation boundary.
    """
    half_x, half_y = bed_half_extents(
        run.drum_radius, run.drum_half_length, run.spin_axis, run.bed_padding
    )
    if container is None:
        container = fit_solid_container(run, motion)
    container_min, container_max = container
    # Chrono places three BCE layers outside each container wall, starting one
    # spacing from the interior face.  Keep another full wall margin outside so
    # no boundary marker is clipped by the neighbor-search grid.
    wall_margin = max(6.0 * run.spacing, 0.1 * run.drum_radius)
    bce_margin = 3.0 * run.spacing + wall_margin
    lateral_x = max(half_x + wall_margin,
                    abs(container_min[0]) + bce_margin, abs(container_max[0]) + bce_margin)
    lateral_y = max(half_y + wall_margin,
                    abs(container_min[1]) + bce_margin, abs(container_max[1]) + bce_margin)
    z_min = min(motion.floor_z - wall_margin, container_min[2] - bce_margin)
    crest_z = motion.drum_center_z0 + run.drum_radius
    gravity = abs(run.gravity)
    ballistic_rise = (
        run.max_velocity**2 / (2.0 * gravity)
        if gravity > 0.0
        else run.max_velocity * run.duration
    )
    z_max = max(crest_z + 4.0 * run.drum_radius + ballistic_rise + wall_margin,
                container_max[2] + bce_margin)
    return ((-lateral_x, -lateral_y, z_min), (lateral_x, lateral_y, z_max))


def computational_domain_aabb(chrono, bounds: tuple[tuple[float, float, float], tuple[float, float, float]]):
    (xmin, ymin, zmin), (xmax, ymax, zmax) = bounds
    return chrono.ChAABB(
        chrono.ChVector3d(xmin, ymin, zmin),
        chrono.ChVector3d(xmax, ymax, zmax),
    )


def fit_active_box(run: CrmRunParams) -> tuple[float, float, float]:
    """Size the SPH active box with generous clearance around the excavator.

    Chrono deactivates particles (and zeros their velocity) outside a box
    centred on the FSI body.  The box is a full-size extent (Chrono halves it
    internally), so each returned component is twice the half-distance from the
    drum centre.  Flung particles can clear the reflective BCE walls, so the
    lateral half-extent must include the padded bed *plus* two excavator
    diameters, not just a few particle spacings.  Likewise the vertical
    half-height covers the soil bed, the initial excavator centre height, and
    two more diameters above the crest.  This keeps the deactivation ceiling
    far above any plausible ejected particle trajectory.
    """
    half_x, half_y = bed_half_extents(
        run.drum_radius, run.drum_half_length, run.spin_axis, run.bed_padding
    )
    # A few particle spacings, with a floor of 10% of the drum radius.
    padding = max(4.0 * run.spacing, 0.1 * run.drum_radius)
    excavator_diameter = 2.0 * run.drum_radius
    clearance = 2.0 * excavator_diameter + padding
    initial_height = run.bed_height + MOTION_DEFAULT.drum_rest_clearance + run.drum_radius
    z_half = run.bed_height + initial_height + clearance
    return (
        2.0 * (half_x + clearance),
        2.0 * (half_y + clearance),
        2.0 * z_half,
    )


def make_run_params(args: argparse.Namespace, *, validate_sph_dt: bool = True) -> CrmRunParams:
    overrides: dict[str, object] = {}
    mapping = {
        "duration": args.duration,
        "dt": args.dt,
        "fps": args.fps,
        "loads_fps": getattr(args, "loads_fps", None),
        "particle_fps": getattr(args, "particle_fps", None),
        "particle_writers": getattr(args, "particle_writers", None),
        "spacing": args.spacing,
        "settle_time": args.settle_time,
        "descent_speed": args.descent_speed,
        "descent_mode": getattr(args, "descent_mode", None),
        "descent_force_n": getattr(args, "descent_force", None),
        "descent_force_ramp_s": getattr(args, "descent_force_ramp", None),
        "gravity": getattr(args, "gravity", None),
        "drum_gravity": getattr(args, "drum_gravity", None),
        "omega": args.omega,
        "reverse_spin": getattr(args, "reverse_spin", None),
        "bed_height": args.bed_height,
        "bed_padding": getattr(args, "bed_padding", None),
        "container_padding": getattr(args, "container_padding", None),
        "spin_axis": args.spin_axis,
        "drum_radius": getattr(args, "drum_radius", None),
        "drum_half_length": getattr(args, "drum_half_length", None),
        "inner_radius": args.inner_radius,
        "capacity_volume": args.capacity_volume,
        "fill_detector_obj": getattr(args, "fill_detector_obj", None),
        "max_velocity": args.max_velocity,
        "artificial_viscosity": getattr(args, "artificial_viscosity", None),
        "shifting_ppst_push": getattr(args, "shifting_ppst_push", None),
        "d0_multiplier": args.d0_multiplier,
        "free_surface_threshold": args.free_surface_threshold,
        "proximity_steps": args.proximity_steps,
    }
    overrides.update({k: v for k, v in mapping.items() if v is not None})
    if overrides.get("fill_detector_obj") is not None:
        overrides["fill_detector_obj"] = str(overrides["fill_detector_obj"])
    if args.active_box is not None:
        box = parse_box(args.active_box)
        overrides.update(active_box_x=box[0], active_box_y=box[1], active_box_z=box[2])

    if overrides.get("reverse_spin"):
        omega_val = overrides.get("omega", CrmRunParams.omega)
        if omega_val is not None:
            overrides["omega"] = -abs(float(omega_val)) if float(omega_val) != 0.0 else float(omega_val)

    if args.smoke_test:
        overrides.setdefault("spacing", 0.012)
        overrides.setdefault("dt", 5.0e-4)
        overrides.setdefault("duration", 0.05)
        overrides.setdefault("fps", 100.0)
        overrides.setdefault("settle_time", 0.0)

    spacing = float(overrides.get("spacing", CrmRunParams.spacing))
    d0_multiplier = float(overrides.get("d0_multiplier", CrmRunParams.d0_multiplier))
    if "dt" in overrides:
        dt = float(overrides["dt"])
        limit = max_stable_sph_dt(spacing, d0_multiplier)
        if validate_sph_dt and dt > limit and not getattr(args, "allow_unsafe_dt", False):
            raise SystemExit(
                f"--dt {dt:g} s is too large for SPH spacing {spacing:g} m "
                f"(stable bound ~{limit:g} s). Use --dt {suggested_sph_dt(spacing, d0_multiplier):g} "
                "or omit --dt to get the spacing-dependent default. "
                "Pass --allow-unsafe-dt only if you intentionally want to test instability."
            )
    else:
        overrides["dt"] = suggested_sph_dt(spacing, d0_multiplier)

    run = CrmRunParams(**overrides)  # type: ignore[arg-type]
    if args.active_box is None:
        box = fit_active_box(run)
        run = replace(run, active_box_x=box[0], active_box_y=box[1], active_box_z=box[2])
    return run


def resolve_mesh(args: argparse.Namespace) -> Path:
    if args.mesh_file is not None:
        mesh = args.mesh_file.resolve()
    else:
        mesh = OBJ_COARSE if args.mesh == "coarse" else OBJ_MEDIUM
    if not mesh.exists():
        raise SystemExit(
            f"missing drum mesh {mesh}; run `python3 stl_to_obj.py` first, "
            "or pass --mesh-file with a spiked variant OBJ"
        )
    return mesh


def import_crm_modules():
    """Import CRM modules only when actually running a simulation."""
    try:
        import pychrono.core as chrono
        import pychrono.fsi as fsi
        import pychrono.vehicle as veh
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit(
            "Failed to import PyChrono core/FSI/vehicle modules for the CRM run.\n"
            f"  Original error: {exc}\n"
            "If this mentions an undefined Chrono symbol, check that crm/run.sh is\n"
            "preloading chrono-install/lib ahead of the stale chrono-env copies.\n"
            "A source/conda-forge Chrono build with FSI/SPH and CRM is required;\n"
            "see environment/INSTALL.md."
        ) from exc
    return chrono, fsi, veh


def set_enum_attr(obj, name: str, fsi, family: str, member: str) -> None:
    value = fsi_enum(fsi, family, member)
    if value is not None and hasattr(obj, name):
        setattr(obj, name, value)


def configure_sph(fsi, terrain, run: CrmRunParams) -> None:
    params = fsi.SPHParameters()
    set_enum_attr(params, "integration_scheme", fsi, "IntegrationScheme", "RK2")
    set_enum_attr(params, "eos_type", fsi, "EosType", "ISOTHERMAL")
    set_enum_attr(params, "viscosity_method", fsi, "ViscosityMethod", "ARTIFICIAL_BILATERAL")
    set_enum_attr(params, "boundary_method", fsi, "BoundaryMethod", "ADAMI")
    set_enum_attr(params, "kernel_type", fsi, "KernelType", "CUBIC_SPLINE")
    set_enum_attr(params, "shifting_method", fsi, "ShiftingMethod", "PPST")

    params.initial_spacing = run.spacing
    params.d0_multiplier = run.d0_multiplier
    params.artificial_viscosity = run.artificial_viscosity
    params.free_surface_threshold = run.free_surface_threshold
    params.use_consistent_gradient_discretization = False
    params.use_consistent_laplacian_discretization = False
    if hasattr(params, "max_velocity"):
        params.max_velocity = run.max_velocity
    if hasattr(params, "num_proximity_search_steps"):
        params.num_proximity_search_steps = run.proximity_steps
    if hasattr(params, "shifting_ppst_push"):
        params.shifting_ppst_push = run.shifting_ppst_push
    if hasattr(params, "shifting_ppst_pull"):
        params.shifting_ppst_pull = 1.0
    terrain.SetSPHParameters(params)


def side_flags_open_top(fsi):
    side_all = fsi_enum(fsi, "BoxSide", "ALL")
    side_z_pos = fsi_enum(fsi, "BoxSide", "Z_POS")
    if side_all is None or side_z_pos is None:
        return fsi.BoxSide_ALL & ~fsi.BoxSide_Z_POS
    return side_all & ~side_z_pos


def side_flags_solid_container(fsi):
    """Four vertical walls plus a lid; the soil bed already has a floor BCE."""
    side_all = fsi_enum(fsi, "BoxSide", "ALL")
    side_z_neg = fsi_enum(fsi, "BoxSide", "Z_NEG")
    if side_all is None or side_z_neg is None:
        return fsi.BoxSide_ALL & ~fsi.BoxSide_Z_NEG
    return side_all & ~side_z_neg


def call_or_attr(obj, name: str):
    value = getattr(obj, name)
    return value() if callable(value) else value


def vec_parts(value) -> tuple[float, float, float]:
    try:
        return tuple(float(call_or_attr(value, name)) for name in ("x", "y", "z"))  # type: ignore[return-value]
    except (AttributeError, TypeError, ValueError):
        return (float(value[0]), float(value[1]), float(value[2]))


def quat_parts(value) -> tuple[float, float, float, float]:
    """Return a quaternion in (w, x, y, z) order across PyChrono bindings."""
    for names in (("w", "x", "y", "z"), ("e0", "e1", "e2", "e3")):
        try:
            return tuple(float(call_or_attr(value, name)) for name in names)  # type: ignore[return-value]
        except (AttributeError, TypeError, ValueError):
            continue
    try:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError, IndexError) as exc:
        raise TypeError(f"cannot read quaternion components from {value!r}") from exc


def initial_chrono_quat(chrono, motion):
    """Convert the shared (x, y, z, w) initial pose to Chrono's (w, x, y, z)."""
    x, y, z, w = motion.initial_quaternion_xyzw()
    return chrono.ChQuaterniond(w, x, y, z)


def quat_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quat_conj(q):
    return (q[0], -q[1], -q[2], -q[3])


def quat_rotate(q, v):
    """Rotate vector v by quaternion q (w, x, y, z)."""
    result = quat_mul(quat_mul(q, (0.0, v[0], v[1], v[2])), quat_conj(q))
    return result[1], result[2], result[3]


def get_fluid_system(terrain, fsi):
    fluid = None
    if hasattr(terrain, "GetFluidSystemSPH"):
        fluid = terrain.GetFluidSystemSPH()
    if fluid is None:
        cfd = terrain.GetFsiSystemSPH()
        fluid = cfd.GetFluidSystemSPH()
    if fluid is None:
        raise SystemExit("could not access the SPH fluid system from CRMTerrain")
    return fluid


def particle_positions(fluid, fsi, terrain=None):
    """Return positions of SPH fluid (soil) markers only.

    ``fluid.GetParticlePositions()`` returns all markers (fluid SPH, boundary
    BCE, rigid-body BCE, and flexible BCE).  Excavation/fill metrics must be
    computed on soil particles only, so we prefer an explicit fluid-group query
    and otherwise slice to the fluid-marker prefix/count.
    """
    group = fsi_enum(fsi, "MarkerGroup", "FLUID")
    if group is not None:
        try:
            return fluid.GetParticlePositions(group)
        except TypeError:
            pass
    positions = fluid.GetParticlePositions()
    count = None
    if hasattr(fluid, "GetNumFluidMarkers"):
        count = fluid.GetNumFluidMarkers()
    elif terrain is not None and hasattr(terrain, "GetNumSPHParticles"):
        count = terrain.GetNumSPHParticles()
    if count is not None:
        return positions[:count]
    raise RuntimeError(
        "cannot identify the SPH fluid-marker prefix; this Chrono binding "
        "exposes neither MarkerGroup.FLUID positions nor a fluid-marker count"
    )


def particle_positions_numpy(fluid, fsi, terrain=None) -> np.ndarray:
    """Return fluid-marker positions as an ``(N, 3)`` float array.

    ``GetParticlePositionsNumpy()`` avoids creating hundreds of thousands of
    Python ``ChVector3d`` objects per frame.  Request ``MarkerGroup.FLUID``
    first so boundary/container BCE markers are never copied from the GPU.
    Older bindings that reject the group argument fall back to the all-marker
    array and slice to the fluid prefix exactly as :func:`particle_positions`
    does.
    """
    count = None
    if hasattr(fluid, "GetNumFluidMarkers"):
        try:
            count = int(fluid.GetNumFluidMarkers())
        except (TypeError, ValueError):
            count = None
    if count is None and terrain is not None and hasattr(terrain, "GetNumSPHParticles"):
        count = int(terrain.GetNumSPHParticles())

    if hasattr(fluid, "GetParticlePositionsNumpy"):
        group = fsi_enum(fsi, "MarkerGroup", "FLUID")
        try:
            if group is not None:
                try:
                    positions = np.asarray(
                        fluid.GetParticlePositionsNumpy(group), dtype=np.float64
                    )
                except TypeError:
                    # This Chrono build exposes only the default all-marker form.
                    if count is None:
                        raise
                    positions = np.asarray(
                        fluid.GetParticlePositionsNumpy(), dtype=np.float64
                    )[:count]
            else:
                positions = np.asarray(
                    fluid.GetParticlePositionsNumpy(), dtype=np.float64
                )
                if count is not None:
                    positions = positions[:count]
            if positions.ndim == 1 and positions.size % 3 == 0:
                positions = positions.reshape(-1, 3)
            if positions.ndim != 2 or positions.shape[1] != 3:
                raise ValueError(f"unexpected position array shape {positions.shape}")
            return positions
        except (TypeError, ValueError, RuntimeError):
            pass

    # Fallback for builds without the NumPy binding.
    return np.asarray([vec_parts(p) for p in particle_positions(fluid, fsi, terrain)], dtype=np.float64)


def quat_rotation_matrix(q: tuple[float, float, float, float]) -> np.ndarray:
    """Rotation matrix for a quaternion in (w, x, y, z) order."""
    w, x, y, z = q
    # q * v * conjugate(q), matching the scalar quat_rotate helper.  When q is
    # not exactly unit length the raw numerator preserves its exact behaviour,
    # including the small scale factor, so metrics stay numerically identical.
    return np.array([
        [w * w + x * x - y * y - z * z, 2.0 * (x * y - w * z), 2.0 * (x * z + w * y)],
        [2.0 * (x * y + w * z), w * w - x * x + y * y - z * z, 2.0 * (y * z - w * x)],
        [2.0 * (x * z - w * y), 2.0 * (y * z + w * x), w * w - x * x - y * y + z * z],
    ], dtype=np.float64)


def get_particle_mass(fluid, soil: CrmSoilParams, spacing: float) -> tuple[float, str]:
    if hasattr(fluid, "GetParticleMass"):
        try:
            return float(fluid.GetParticleMass()), "solver"
        except (TypeError, ValueError):
            pass
    return soil.density * spacing**3, "rho*spacing^3"


def axis_vector(chrono, axis: str):
    axis = axis.upper()
    if axis == "X":
        return chrono.ChVector3d(1.0, 0.0, 0.0)
    if axis == "Y":
        return chrono.ChVector3d(0.0, 1.0, 0.0)
    if axis == "Z":
        return chrono.ChVector3d(0.0, 0.0, 1.0)
    raise ValueError(f"unsupported spin axis: {axis!r}")


def build_descent_force_function(chrono, run: CrmRunParams, t_end: float):
    """Downward actuator force sequence: hold, ramp, then constant force.

    Chrono's linear-force motor applies +Z for a positive function value, so a
    downward actuator is negative.  During the settle interval the hold motor
    is active instead, but the sequence still starts at zero for clarity.
    """
    seq = chrono.ChFunctionSequence()
    if hasattr(seq, "SetClamped"):
        seq.SetClamped(True)
    settle = max(0.0, float(run.settle_time))
    ramp = max(0.0, float(run.descent_force_ramp_s))
    force = -abs(float(run.descent_force_n))
    end = max(float(t_end), settle + ramp, 1.0)

    # Each segment is written in absolute force units, so C0 offsets must stay
    # disabled.  In particular, asking Chrono to smooth a zero-force settle
    # segment into a full-force step would offset the command back to zero.
    if settle > 0.0:
        seq.InsertFunct(chrono.ChFunctionConst(0.0), settle, 1.0, False)
    if ramp > 0.0:
        seq.InsertFunct(chrono.ChFunctionRamp(0.0, force / ramp), ramp, 1.0, False)
    remaining = max(0.0, end - settle - ramp)
    if remaining > 0.0:
        seq.InsertFunct(chrono.ChFunctionConst(force), remaining, 1.0, False)
    return seq


def create_force_descent(chrono, system, body, motion, run: CrmRunParams, t_end: float,
                         *, prismatic: bool = False, ground=None, force_function=None) -> dict:
    """Attach a Z force actuator plus a position hold to ``body``.

    The force motor supplies downward load.  A position motor holds the initial
    height until ``settle_time`` and holds ``z_target`` after the descent, so
    the drum cannot drift past its designed final clearance.  ``prismatic=True``
    is used by the nested FEA spindle; the rigid CRM uses a spherical guide so
    its prescribed spin remains free.
    """
    force_guide = (
        chrono.ChLinkMotorLinearForce.GuideConstraint_PRISMATIC if prismatic
        else chrono.ChLinkMotorLinearForce.GuideConstraint_SPHERICAL
    )
    hold_guide = (
        chrono.ChLinkMotorLinearPosition.GuideConstraint_PRISMATIC if prismatic
        else chrono.ChLinkMotorLinearPosition.GuideConstraint_SPHERICAL
    )
    if ground is None:
        ground = chrono.ChBody()
        ground.SetName("descent_guide_ground")
        ground.SetFixed(True)
        ground.SetPos(chrono.ChVector3d(0.0, 0.0, motion.drum_center_z0))
        system.Add(ground)

    frame = chrono.ChFramed(
        chrono.ChVector3d(0.0, 0.0, motion.drum_center_z0),
        chrono.ChQuaterniond(1.0, 0.0, 0.0, 0.0),
    )

    force_motor = chrono.ChLinkMotorLinearForce()
    force_motor.Initialize(body, ground, frame)
    force_motor.SetGuideConstraint(force_guide)
    force_motor.SetForceFunction(
        force_function if force_function is not None
        else build_descent_force_function(chrono, run, t_end)
    )
    force_motor.SetDisabled(True)
    system.Add(force_motor)

    hold_motor = chrono.ChLinkMotorLinearPosition()
    hold_motor.Initialize(body, ground, frame)
    hold_motor.SetGuideConstraint(hold_guide)
    hold_motor.SetMotionFunction(chrono.ChFunctionConst(0.0))
    system.Add(hold_motor)

    return {
        "chrono": chrono,
        "body": body,
        "clamp_bodies": [body],
        "ground": ground,
        "force_motor": force_motor,
        "hold_motor": hold_motor,
        "state": "hold",
    }


def _clamp_force_descent_to_target(drive: dict, motion) -> None:
    """Undo any step overshoot before the position hold takes effect."""
    chrono = drive["chrono"]
    target = float(motion.z_target)
    for body in drive.get("clamp_bodies", []):
        if not hasattr(body, "GetPos") or not hasattr(body, "SetPos"):
            continue
        x, y, _z = vec_parts(body.GetPos())
        body.SetPos(chrono.ChVector3d(x, y, target))
        if hasattr(body, "GetPosDt") and hasattr(body, "SetPosDt"):
            vx, vy, _vz = vec_parts(body.GetPosDt())
            body.SetPosDt(chrono.ChVector3d(vx, vy, 0.0))


def update_force_descent(drive: dict, motion, run: CrmRunParams, t: float, z: float,
                         *, velocity: float | None = None, dt: float | None = None) -> None:
    """Advance the force/hold state machine using the current drum height.

    ``velocity`` and ``dt`` allow the handoff to happen at the *predicted*
    crossing, before the solver takes the step that would pass ``z_target``.
    Otherwise a coarse step can undershoot, then the position motor snaps the
    body upward and the undershoot is written into the load history.
    """
    if drive["state"] == "hold" and t >= max(0.0, run.settle_time):
        drive["force_motor"].SetDisabled(False)
        drive["hold_motor"].SetDisabled(True)
        drive["state"] = "descend"
    if drive["state"] != "descend":
        # Guard against step-to-step drift even after the target hold is active.
        if drive["state"] == "target" and z < motion.z_target:
            _clamp_force_descent_to_target(drive, motion)
        return

    predicted = z
    if velocity is not None and dt is not None and velocity < 0.0:
        predicted = z + velocity * max(0.0, dt)
    if z <= motion.z_target or predicted <= motion.z_target:
        drive["force_motor"].SetDisabled(True)
        # GetMotionFunction() returns the generic ChFunction base, which has no
        # SetConstant binding, so replace the function with a new constant one.
        drive["hold_motor"].SetMotionFunction(
            drive["chrono"].ChFunctionConst(motion.z_target - motion.drum_center_z0)
        )
        drive["hold_motor"].SetDisabled(False)
        drive["state"] = "target"
        if z < motion.z_target:
            _clamp_force_descent_to_target(drive, motion)


def set_drum_state(chrono, drum, motion, t: float, *, impose_translation: bool = True) -> None:
    """Impose the analytic drum spin and, in kinematic mode, its Z descent."""
    omega = motion.drum_omega_at(t)
    axis = axis_vector(chrono, motion.drum_spin_axis)
    q0 = initial_chrono_quat(chrono, motion)
    spin_angle = omega * max(0.0, t - motion.settle_time)
    q_spin = chrono.QuatFromAngleAxis(spin_angle, axis)

    if impose_translation:
        z = motion.drum_z(t)
        drum.SetPos(chrono.ChVector3d(0.0, 0.0, z))
        moving = t >= motion.settle_time and z > motion.z_target
        drum.SetPosDt(chrono.ChVector3d(0.0, 0.0, -motion.lower_speed if moving else 0.0))
    drum.SetRot(q_spin * q0)
    omega_vec = (omega, 0.0, 0.0) if motion.drum_spin_axis.upper() == "X" else (
        (0.0, omega, 0.0) if motion.drum_spin_axis.upper() == "Y" else (0.0, 0.0, omega)
    )
    drum.SetAngVelParent(chrono.ChVector3d(*omega_vec))


def create_drum(chrono, system, terrain, motion, mesh: Path):
    drum = chrono.ChBody()
    drum.SetMass(motion.drum_mass)
    drum.SetInertiaXX(chrono.ChVector3d(*motion.drum_moi))
    drum.SetName("excavator_drum")
    if hasattr(drum, "SetUseSleeping"):
        drum.SetUseSleeping(False)

    geometry = chrono.ChBodyGeometry()
    geometry.coll_meshes.append(
        chrono.TrimeshShape(chrono.VNULL, chrono.QUNIT, str(mesh), chrono.VNULL)
    )

    q0 = initial_chrono_quat(chrono, motion)
    drum.SetPos(chrono.ChVector3d(0.0, 0.0, motion.drum_center_z0))
    drum.SetRot(q0)
    system.Add(drum)

    # Keep the body dynamic: CRMTerrain then reports the FSI reaction torque.
    # Kinematic mode overrides its pose each step; force mode lets the Chrono
    # solver integrate the actuator, gravity, and FSI reaction.
    terrain.AddRigidBody(drum, geometry, False)
    return drum


def compute_metrics(positions: np.ndarray, drum, run: CrmRunParams, motion,
                    soil: CrmSoilParams, particle_mass: float,
                    capacity_volume: float, surface_z: float,
                    with_flags: bool = False):
    pos = np.asarray(positions, dtype=np.float64)
    if pos.ndim == 1 and pos.size % 3 == 0:
        pos = pos.reshape(-1, 3)
    if pos.ndim != 2 or pos.shape[1] != 3:
        raise ValueError(f"unexpected position array shape {pos.shape}")

    origin = np.asarray(vec_parts(drum.GetPos()), dtype=np.float64)
    rotation = quat_rotation_matrix(quat_conj(quat_parts(drum.GetRot())))
    local = (pos - origin) @ rotation.T

    lx = local[:, 0]
    ly = local[:, 1]
    lz = np.abs(local[:, 2])
    radial2 = lx * lx + ly * ly

    # Chrono's SPH initial spacing d0 is the particle diameter, so a particle
    # is only fully contained when its centre is at least rp (0.5*d0) inside
    # the drum envelope.  Erode the outer radius and both axial endcaps by rp
    # (clamped at zero) before classifying particles.
    rp = 0.5 * run.spacing
    r_outer2 = max(0.0, run.drum_radius - rp) ** 2
    half_len_eff = max(0.0, run.drum_half_length - rp)
    lift_tol = rp

    lifted = pos[:, 2] > surface_z + lift_tol
    detector = load_fill_detector(run.fill_detector_obj)
    if detector is not None:
        # Exact containment in the supplied negative-interior mesh, transformed
        # by the drum's live pose.  This is instantaneous: leaving the mesh
        # clears the flag on the next metrics call.
        excavated = detector.contains_with_margin(local, rp) & lifted
    else:
        excavated = (
            (radial2 <= r_outer2)
            & (lz <= half_len_eff)
            & lifted
        )

    # Fill is exactly the excavated set; only its source changes when a mesh
    # detector is supplied.
    inside = excavated

    inside_count = int(np.count_nonzero(inside))
    lifted_count = int(np.count_nonzero(lifted))
    excavated_count = int(np.count_nonzero(excavated))
    flags = (
        np.column_stack((inside.astype(np.uint8), excavated.astype(np.uint8)))
        if with_flags else None
    )

    capacity_mass = soil.density * capacity_volume
    retained_mass = inside_count * particle_mass
    excavated_mass = excavated_count * particle_mass
    metrics = {
        "sph_particles": len(pos),
        "inside_drum": inside_count,
        "lifted": lifted_count,
        "excavated": excavated_count,
        "fill_ratio_count": inside_count / max(1.0, capacity_volume / run.spacing**3),
        "fill_ratio_mass": retained_mass / capacity_mass,
        "excavated_mass_kg": excavated_mass,
        "capacity_m3": capacity_volume,
        "capacity_mass_kg": capacity_mass,
        "particle_mass_kg": particle_mass,
    }
    return metrics, flags


def write_particle_csv(path: Path, positions: np.ndarray, flags: np.ndarray) -> None:
    pos = np.asarray(positions, dtype=np.float64)
    flg = np.asarray(flags, dtype=np.uint8)
    if pos.ndim != 2 or pos.shape[1] != 3:
        raise ValueError(f"unexpected position array shape {pos.shape}")
    if flg.ndim != 2 or flg.shape != (len(pos), 2):
        raise ValueError(f"unexpected flag array shape {flg.shape}")

    data = np.empty((len(pos), 5), dtype=np.float64)
    data[:, :3] = pos
    data[:, 3] = flg[:, 0]
    data[:, 4] = flg[:, 1]
    np.savetxt(
        path,
        data,
        delimiter=",",
        fmt=["%.9g", "%.9g", "%.9g", "%d", "%d"],
        header="X,Y,Z,inside_drum,excavated",
        comments="",
    )


def write_config(path: Path, soil: CrmSoilParams, run: CrmRunParams,
                 mesh: Path, motion, capacity_volume: float,
                 particle_mass: float, particle_mass_source: str,
                 computational_domain: tuple[tuple[float, float, float], tuple[float, float, float]],
                 fill_detector: FillDetector | None = None) -> None:
    container_min, container_max = fit_solid_container(run, motion)
    config = {
        "model": "Chrono::CRM (SPH continuum)",
        "soil": asdict(soil),
        "friction_angle_deg": soil.friction_angle_deg,
        "run": {**asdict(run), "active_box": list(run.active_box)},
        "gravity_m_s2": run.gravity,
        "drum_gravity": run.drum_gravity,
        "computational_domain": {
            "min_m": list(computational_domain[0]),
            "max_m": list(computational_domain[1]),
        },
        "solid_container": {
            "interior_min_m": list(container_min),
            "interior_max_m": list(container_max),
            "padding_m": solid_container_padding(run),
            "sides": "X/Y walls and Z+ lid (bed floor BCE from Construct)",
        },
        "geometry": {
            "mesh": str(mesh),
            "drum_radius_m": motion.drum_radius,
            "drum_half_length_m": motion.drum_half_length,
            "bed_height_m": motion.bed_height,
            "bed_padding_radius_fraction": motion.bed_padding,
            "bed_x_half_m": motion.bed_x_half,
            "bed_y_half_m": motion.bed_y_half,
            "target_bottom_clearance_m": motion.target_bottom_clearance,
            "spin_axis_global": motion.drum_spin_axis,
            "capacity_volume_m3": capacity_volume,
            "capacity_source": "user override" if run.capacity_volume else "pi*r_i^2*L",
            "fill_detector_obj": run.fill_detector_obj,
            "fill_detector_definition": detector_definition(fill_detector),
        },
        "particle_mass_kg": particle_mass,
        "particle_mass_source": particle_mass_source,
        "fill_ratio_definition": "excavated SPH mass / (bulk density * nominal cavity volume)",
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")


def system_gravity_vector(chrono, run: CrmRunParams):
    """Gravity acting on the rigid drum; SPH soil always uses ``run.gravity``."""
    return chrono.ChVector3d(0.0, 0.0, run.gravity if run.drum_gravity else 0.0)


def apply_drum_gravity(chrono, system, run: CrmRunParams) -> None:
    """Apply the drum-gravity toggle to the multibody system."""
    system.SetGravitationalAcceleration(system_gravity_vector(chrono, run))


def configure_gravity(chrono, system, terrain, run: CrmRunParams) -> None:
    """Set SPH gravity and drum gravity in the order Chrono requires.

    ``CRMTerrain.SetGravitationalAcceleration()`` propagates gravity to both the
    SPH fluid and the shared multibody system.  Reapplying the drum setting
    afterwards is what makes ``--drum-gravity`` effective in force mode.
    """
    apply_drum_gravity(chrono, system, run)
    terrain.SetGravitationalAcceleration(chrono.ChVector3d(0.0, 0.0, run.gravity))
    apply_drum_gravity(chrono, system, run)


def main() -> int:
    args = build_parser().parse_args()
    soil = make_soil(args)
    run = make_run_params(args)
    try:
        fill_detector = load_fill_detector(run.fill_detector_obj)
    except FillSurfaceError as exc:
        raise SystemExit(str(exc)) from exc

    if args.print_soil:
        print(soil.summary())
        return 0

    motion = replace(
        MOTION_DEFAULT,
        t_max=run.duration,
        settle_time=run.settle_time,
        lower_speed=run.descent_speed,
        drum_omega=run.omega,
        gravity=run.gravity,
        bed_height=run.bed_height,
        bed_padding=run.bed_padding,
        drum_spin_axis=run.spin_axis,
        drum_radius=run.drum_radius,
        drum_half_length=run.drum_half_length,
    )
    container_bounds = fit_solid_container(run, motion)
    if container_bounds[1][2] <= motion.drum_center_z0 + run.drum_radius + 2.0 * run.spacing:
        raise SystemExit(
            "solid container lid is below the drum crest; increase --active-box Z"
        )
    comp_domain = fit_computational_domain(run, motion, container_bounds)
    capacity_volume = (
        run.capacity_volume
        if run.capacity_volume is not None
        else math.pi * run.inner_radius**2 * (2.0 * motion.drum_half_length)
    )
    if capacity_volume <= 0.0:
        raise SystemExit("capacity volume must be positive")

    mesh = resolve_mesh(args)
    surface_z = motion.floor_z + motion.bed_height
    print(soil.summary(), flush=True)
    print(
        "\n".join(
            [
                f"CRM run: spacing={run.spacing:g} m, dt={run.dt:g} s, "
                f"duration={run.duration:g} s, fps={run.fps:g}",
                f"Output cadence: metrics={run.fps:g} Hz, particles={run.particle_fps:g} Hz, "
                f"loads={run.loads_fps:g} Hz",
                f"drum: mesh={mesh.name}, R={motion.drum_radius:g} m, "
                f"half-length={motion.drum_half_length:g} m, "
                f"spin={motion.drum_spin_axis.upper()}, omega={run.omega:g} rad/s",
                (
                    f"descent: force {run.descent_force_n:g} N downward "
                    f"(ramp={run.descent_force_ramp_s:g} s), z0={motion.drum_center_z0:.4f} m -> "
                    f"hold at {motion.z_target:.4f} m"
                    if run.descent_mode == "force"
                    else f"descent: {run.descent_speed:g} m/s, z0={motion.drum_center_z0:.4f} m -> "
                    f"{motion.z_target:.4f} m"
                ) + f", bed surface z={surface_z:.4f} m",
                f"soil bed: {2.0 * motion.bed_x_half:.4g} x {2.0 * motion.bed_y_half:.4g} m "
                f"(padding {run.bed_padding:g} R), height={motion.bed_height:.4g} m",
                f"active SPH box: {run.active_box} (body frame, follows drum)",
                f"gravity: gz={run.gravity:g} m/s^2 on soil; "
                f"rigid drum gravity={'on' if run.drum_gravity else 'off'}",
                f"SPH tuning: artificial_viscosity={run.artificial_viscosity:g}, "
                f"PPST push={run.shifting_ppst_push:g}, settle_time={run.settle_time:g} s",
                f"solid reflective container: interior min={container_bounds[0]}, "
                f"max={container_bounds[1]}, padding={solid_container_padding(run):g} m",
                f"world computational domain: min={comp_domain[0]}, max={comp_domain[1]}",
                f"capacity={capacity_volume:.6g} m^3 ({capacity_volume * soil.density:.4f} kg)",
                f"fill detector: {detector_definition(fill_detector)}"
                + (f" ({run.fill_detector_obj})" if run.fill_detector_obj else ""),
            ]
        ),
        flush=True,
    )

    chrono, fsi, veh = import_crm_modules()

    out_dir = args.output_dir.resolve()
    if not args.no_output:
        out_dir.mkdir(parents=True, exist_ok=True)
    chrono.SetChronoOutputPath(str(out_dir) + "/")

    system = chrono.ChSystemNSC()
    if hasattr(system, "SetCollisionSystemType") and hasattr(chrono, "ChCollisionSystem"):
        system.SetCollisionSystemType(chrono.ChCollisionSystem.Type_BULLET)
    system.SetTimestepperType(chrono.ChTimestepper.Type_EULER_IMPLICIT_LINEARIZED)
    if hasattr(system, "SetSolverType"):
        system.SetSolverType(chrono.ChSolver.Type_BARZILAIBORWEIN)

    terrain = veh.CRMTerrain(system, run.spacing)
    terrain.SetVerbose(False)
    configure_gravity(chrono, system, terrain, run)
    terrain.SetComputationalDomain(computational_domain_aabb(chrono, comp_domain), fsi.BC_NONE)
    terrain.SetStepSizeCFD(run.dt)
    if hasattr(terrain, "SetStepsizeMBD"):
        terrain.SetStepsizeMBD(run.dt)
    terrain.SetActiveDomain(chrono.ChVector3d(*run.active_box))
    if hasattr(terrain, "SetFreeFlowDuration"):
        terrain.SetFreeFlowDuration(run.settle_time)
    elif hasattr(terrain, "SetActiveDomainDelay"):
        terrain.SetActiveDomainDelay(run.settle_time)

    soil.apply(fsi, terrain)
    configure_sph(fsi, terrain, run)

    drum = create_drum(chrono, system, terrain, motion, mesh)
    force_drive = None
    if run.descent_mode == "force":
        force_drive = create_force_descent(chrono, system, drum, motion, run, run.duration)
        print(
            f"Force descent: {run.descent_force_n:g} N downward, "
            f"ramp={run.descent_force_ramp_s:g} s, hold until t={run.settle_time:g} s",
            flush=True,
        )

    box_size = chrono.ChVector3d(
        2.0 * motion.bed_x_half, 2.0 * motion.bed_y_half, motion.bed_height
    )
    box_center = chrono.ChVector3d(0.0, 0.0, motion.floor_z)
    terrain.Construct(box_size, box_center, side_flags_open_top(fsi))
    container_min, container_max = container_bounds
    container_size = chrono.ChVector3d(
        container_max[0] - container_min[0],
        container_max[1] - container_min[1],
        container_max[2] - container_min[2],
    )
    container_pos = chrono.ChVector3d(0.0, 0.0, container_min[2])
    container_bce = terrain.AddBoxContainer(
        container_size, container_pos, side_flags_solid_container(fsi)
    )
    print(f"Solid container BCE markers: {container_bce}", flush=True)
    terrain.Initialize()
    # Defensive reassert: some Chrono builds synchronize system gravity during
    # FSI initialization.  Soil gravity remains untouched on the terrain.
    apply_drum_gravity(chrono, system, run)

    fluid = get_fluid_system(terrain, fsi)
    actual_domain = fluid.GetComputationalDomain()
    # Record the domain Chrono actually installed, not just the requested one;
    # this catches any future binding/API change that silently ignores it.
    comp_domain = (vec_parts(actual_domain.min), vec_parts(actual_domain.max))
    particle_mass, mass_source = get_particle_mass(fluid, soil, run.spacing)
    print(
        f"SPH particles: {terrain.GetNumSPHParticles()}, "
        f"boundary BCE: {terrain.GetNumBoundaryBCEMarkers()}, "
        f"particle mass: {particle_mass:.6g} kg ({mass_source})",
        flush=True,
    )
    print(
        f"SPH computational domain after Initialize: min={comp_domain[0]}, max={comp_domain[1]}",
        flush=True,
    )

    writer = None
    state_file = None
    if not args.no_output:
        write_config(
            out_dir / "config.json", soil, run, mesh, motion,
            capacity_volume, particle_mass, mass_source, comp_domain,
            fill_detector=fill_detector,
        )
        state_file = (out_dir / "summary.csv").open("w", newline="", encoding="utf-8")
        writer = csv.DictWriter(state_file, fieldnames=metric_fields(run))
        writer.writeheader()

    loads_file = None
    loads_writer = None
    if args.loads_file is not None:
        loads_path = args.loads_file.resolve()
        loads_path.parent.mkdir(parents=True, exist_ok=True)
        loads_file = loads_path.open("w", newline="", encoding="utf-8")
        loads_writer = csv.DictWriter(loads_file, fieldnames=load_fields(run))
        loads_writer.writeheader()
        print(f"Soil wrench: {loads_path} @ {run.loads_fps:g} Hz", flush=True)

    particle_executor = None
    pending_particle_writes: list = []
    max_pending_writes = 2 * run.particle_writers
    if args.save_particles and not args.no_output:
        # CSV formatting is CPU-bound, so use processes rather than threads.
        # Spawn avoids inheriting the live CUDA context into writer children.
        particle_executor = ProcessPoolExecutor(
            max_workers=run.particle_writers,
            mp_context=multiprocessing.get_context("spawn"),
        )

    last_load_time = -1.0

    def descent_status(t: float) -> tuple[str, float, float]:
        z = vec_parts(drum.GetPos())[2]
        if force_drive is None:
            moving = t >= run.settle_time and z > motion.z_target
            return "kinematic", 0.0, -run.descent_speed if moving else 0.0
        state = force_drive["state"]
        # Chrono's motor sign is negative for downward travel; report a
        # positive downward magnitude so CSV consumers need no sign knowledge.
        actuator = (
            abs(float(force_drive["force_motor"].GetMotorForce()))
            if state == "descend" else 0.0
        )
        vertical_speed = vec_parts(drum.GetPosDt())[2] if hasattr(drum, "GetPosDt") else 0.0
        return state, actuator, vertical_speed

    def write_loads(t: float, force=None, torque=None) -> None:
        nonlocal last_load_time
        if loads_writer is None:
            return
        if force is None:
            force = vec_parts(terrain.GetFsiBodyForce(drum)) if hasattr(terrain, "GetFsiBodyForce") else (0.0, 0.0, 0.0)
        if torque is None:
            torque = vec_parts(terrain.GetFsiBodyTorque(drum)) if hasattr(terrain, "GetFsiBodyTorque") else (0.0, 0.0, 0.0)
        z = vec_parts(drum.GetPos())[2]
        descent_state, actuator_force, vertical_speed = descent_status(t)
        row = {
            "time_s": f"{t:.9g}",
            "drum_z_m": f"{z:.9g}",
            "force_x_n": f"{force[0]:.9g}",
            "force_y_n": f"{force[1]:.9g}",
            "force_z_n": f"{force[2]:.9g}",
            "torque_x_nm": f"{torque[0]:.9g}",
            "torque_y_nm": f"{torque[1]:.9g}",
            "torque_z_nm": f"{torque[2]:.9g}",
        }
        if run.descent_mode == "force":
            row.update({
                "descent_mode": run.descent_mode,
                "descent_state": descent_state,
                "actuator_force_n": f"{actuator_force:.9g}",
                "vertical_speed_m_s": f"{vertical_speed:.9g}",
            })
        loads_writer.writerow(row)
        last_load_time = t

    peak_fill = 0.0
    work_j = 0.0
    last_row = {}
    frame = 0
    wall_start = time.perf_counter()
    next_progress = 0.0
    next_particle_output = 0.0

    def record(t: float, fluid_power: float, fluid_torque_x: float) -> None:
        nonlocal frame, peak_fill, last_row, next_particle_output
        positions = particle_positions_numpy(fluid, fsi, terrain)
        particle_due = (
            bool(args.save_particles and not args.no_output)
            and (t >= next_particle_output - 1.0e-12 or t >= run.duration - 1.0e-12)
        )
        with_flags = particle_due
        metrics, flags = compute_metrics(
            positions, drum, run, motion, soil, particle_mass,
            capacity_volume, surface_z, with_flags=with_flags,
        )
        peak_fill = max(peak_fill, metrics["fill_ratio_mass"])
        angle = math.degrees(run.omega * max(0.0, t - run.settle_time))
        row = {
            "frame": frame,
            "time_s": f"{t:.6f}",
            "wall_s": f"{time.perf_counter() - wall_start:.3f}",
            "drum_z_m": f"{vec_parts(drum.GetPos())[2]:.7f}",
            "spin_deg": f"{angle:.3f}",
            "omega_rad_s": f"{run.omega:.6g}",
            "sph_particles": metrics["sph_particles"],
            "inside_drum": metrics["inside_drum"],
            "lifted": metrics["lifted"],
            "excavated": metrics["excavated"],
            "fill_ratio_count": f"{metrics['fill_ratio_count']:.6f}",
            "fill_ratio_mass": f"{metrics['fill_ratio_mass']:.6f}",
            "peak_fill_ratio_mass": f"{peak_fill:.6f}",
            "capacity_m3": f"{capacity_volume:.9g}",
            "particle_mass_kg": f"{particle_mass:.9g}",
            "fluid_torque_x_nm": f"{fluid_torque_x:.6g}",
            "input_power_w": f"{fluid_power:.6g}",
            "work_j": f"{work_j:.6g}",
            "specific_energy_j_kg": f"{work_j / max(metrics['excavated_mass_kg'], 1e-12):.6g}",
        }
        if run.descent_mode == "force":
            state, actuator, _speed = descent_status(t)
            row.update({
                "descent_mode": run.descent_mode,
                "descent_state": state,
                "actuator_force_n": f"{actuator:.6g}",
                "vertical_speed_m_s": f"{vec_parts(drum.GetPosDt())[2] if hasattr(drum, 'GetPosDt') else 0.0:.6g}",
            })
        if writer is not None:
            writer.writerow(row)
            state_file.flush()
        if particle_due and flags is not None:
            target = out_dir / f"particles_{frame:06d}.csv"
            payload = (positions.copy(), flags.copy())
            if particle_executor is not None:
                pending_particle_writes.append(
                    particle_executor.submit(write_particle_csv, target, *payload)
                )
                while len(pending_particle_writes) >= max_pending_writes:
                    pending_particle_writes.pop(0).result()
            else:
                write_particle_csv(target, *payload)
            while next_particle_output <= t + 1.0e-12:
                next_particle_output += 1.0 / run.particle_fps
        last_row = row
        frame += 1
        print(
            f"frame {frame - 1:05d}  t={t:7.3f}s  inside={metrics['inside_drum']:6d}  "
            f"fill={metrics['fill_ratio_mass']:.4f}  peak={peak_fill:.4f}",
            flush=True,
        )

    # Initial state.  Force mode keeps Z free for the solver; only spin is set.
    impose_translation = force_drive is None
    set_drum_state(chrono, drum, motion, 0.0, impose_translation=impose_translation)
    if force_drive is not None:
        update_force_descent(force_drive, motion, run, 0.0, vec_parts(drum.GetPos())[2])
    last_torque_x = 0.0
    last_power = 0.0
    record(0.0, 0.0, 0.0)
    write_loads(0.0)
    next_output = 1.0 / run.fps
    next_load_output = 1.0 / run.loads_fps

    t = 0.0
    try:
        while t < run.duration - 1.0e-12:
            h = min(run.dt, run.duration - t)
            set_drum_state(chrono, drum, motion, t, impose_translation=impose_translation)
            if force_drive is not None:
                z = vec_parts(drum.GetPos())[2]
                vz = vec_parts(drum.GetPosDt())[2] if hasattr(drum, "GetPosDt") else 0.0
                update_force_descent(force_drive, motion, run, t, z, velocity=vz, dt=h)
            terrain.Synchronize(t)
            terrain.Advance(h)
            t += h
            set_drum_state(chrono, drum, motion, t, impose_translation=impose_translation)
            if force_drive is not None:
                update_force_descent(force_drive, motion, run, t, vec_parts(drum.GetPos())[2])

            omega_vec = (
                (run.omega, 0.0, 0.0) if run.spin_axis.upper() == "X"
                else ((0.0, run.omega, 0.0) if run.spin_axis.upper() == "Y"
                      else (0.0, 0.0, run.omega))
            )
            torque = vec_parts(terrain.GetFsiBodyTorque(drum)) if hasattr(terrain, "GetFsiBodyTorque") else (0.0, 0.0, 0.0)
            tx, ty, tz = torque
            fluid_dot = tx * omega_vec[0] + ty * omega_vec[1] + tz * omega_vec[2]
            # FSI torque is the force of soil on the drum; positive input power
            # is the torque applied by the drive against that reaction.
            fluid_power = max(0.0, -fluid_dot)
            work_j += fluid_power * h
            last_torque_x = tx
            last_power = fluid_power

            if t >= next_load_output - 1.0e-12 or t >= run.duration - 1.0e-12:
                force = vec_parts(terrain.GetFsiBodyForce(drum)) if hasattr(terrain, "GetFsiBodyForce") else (0.0, 0.0, 0.0)
                write_loads(t, force, torque)
                next_load_output += 1.0 / run.loads_fps

            if t >= next_output - 1.0e-12 or t >= run.duration - 1.0e-12:
                record(t, fluid_power, last_torque_x)
                next_output += 1.0 / run.fps

            if t >= next_progress:
                print(f"  t={t:7.3f}s / {run.duration:g}s", flush=True)
                next_progress += 1.0

        if loads_writer is not None and last_load_time < t - 1.0e-12:
            force = vec_parts(terrain.GetFsiBodyForce(drum)) if hasattr(terrain, "GetFsiBodyForce") else (0.0, 0.0, 0.0)
            torque = vec_parts(terrain.GetFsiBodyTorque(drum)) if hasattr(terrain, "GetFsiBodyTorque") else (0.0, 0.0, 0.0)
            write_loads(t, force, torque)
    finally:
        if particle_executor is not None:
            for future in pending_particle_writes:
                future.result()
            particle_executor.shutdown(wait=True)
        if state_file is not None:
            state_file.close()
        if loads_file is not None:
            loads_file.close()

    print(
        "\nCRM run complete: "
        f"final_fill={last_row.get('fill_ratio_mass')} "
        f"peak_fill={peak_fill:.6f} "
        f"inside={last_row.get('inside_drum')} "
        f"excavated={last_row.get('excavated')} "
        f"work={work_j:.4g} J "
        f"last_power={last_power:.4g} W",
        flush=True,
    )
    if not args.no_output:
        print(f"Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
