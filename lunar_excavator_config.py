"""Shared parameters for the Lunar bucket-drum excavator DEM setup.

This module is the single source of truth for both solver ports:

* ``deme/lunar_excavator_deme.py``   -- GPU DEM (DEM-Engine / pyDEME), production scale
* ``pychrono_core/lunar_excavator_pychrono.py`` -- CPU PyChrono core, small demo scale

The geometric and contact values in the "MercuryDPM" block are copied from
``LunarBucketDrumExcavator.cpp`` (the driver in MercurySource/Drivers/LunarExcavator).
That driver is now a historical sanity check only: the engineering target is
bulk excavator fill ratio and geometry optimization, for which a calibrated
continuum model is the primary solver and this DEM setup is a validation aid.
The production bed/motion values are deliberate overrides: 0.9 mm grains, a
60 mm bed, and 1.5 mm/s descent.  The global X spin matches the MercuryDPM
driver.  The "mapping" block documents how a MercuryDPM linear-viscoelastic
contact model is translated into the material parameters expected by the two
Chrono solvers.

Important geometry note
-----------------------
The shipped STL is measured as:

    x in [-0.061, 0.061], y in [-0.061, 0.061], z in [-0.033, 0.033]  [m]

so the *mesh's long axis is global Z*, even though the MercuryDPM source comments say
"spin axis is global X".  The C++ driver sets an initial quaternion of
(sqrt(0.5), 0, sqrt(0.5), 0) -- a 90 deg rotation about Y in MercuryDPM's
(w, x, y, z) convention -- which maps the mesh's own short (Z) axis onto the
global X axis -- and then prescribes angular velocity (omega, 0, 0).  That is the
drum's long axis, so this setup defaults to a global **X** spin, matching the
MercuryDPM driver.  DEME stores prescribed angular velocity in the body frame,
so ``global_axis_local()`` converts the requested global axis before it is sent
to the solver.  Set ``drum_spin_axis="Y"`` or ``"Z"`` for other global axes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
GEOMETRY_DIR = ROOT / "geometry"
STL_MEDIUM = GEOMETRY_DIR / "LunarDrumScrew_m_medium.stl"
OBJ_MEDIUM = GEOMETRY_DIR / "LunarDrumScrew_m_medium.obj"
STL_COARSE = GEOMETRY_DIR / "LunarDrumScrew_m_coarse.stl"
OBJ_COARSE = GEOMETRY_DIR / "LunarDrumScrew_m_coarse.obj"
# Production simulation mesh: the full coarse STL-derived OBJ.  It keeps the
# screw holes open; the 1,200-face simplification below closes some of them and
# is retained only as an optional legacy comparison mesh (`--mesh deme`).
OBJ_SIM = OBJ_COARSE
OBJ_DEME = GEOMETRY_DIR / "LunarDrumScrew_m_coarse_deme1200.obj"  # legacy optional mesh


# ---------------------------------------------------------------------------
# Parameters copied verbatim from LunarBucketDrumExcavator.cpp
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExcavatorParams:
    # ---- drum geometry (measured from the STL) ----
    drum_omega: float = 6.28 * 0.5          # rad/s, spin rate
    drum_half_length: float = 0.033         # m
    drum_radius: float = 0.061              # m (outer/crest radius)
    stl_file: str = "LunarDrumScrew_m_medium.stl"
    drum_spin_axis: str = "X"               # global X; drum long axis after initial 90 deg Y rotation

    # ---- particle bed ----
    particle_radius: float = 0.0009         # m (user-selected smaller grains)
    bed_height: float = 0.06                # m (user-selected taller bed)
    bed_margin_x: float = 0.01              # m
    bed_margin_y: float = 0.005             # m

    # ---- motion sequencing ----
    floor_z: float = 0.0                    # m
    settle_time: float = 0.001              # s
    lower_speed: float = 0.0015             # m/s (user-selected faster descent)
    drum_rest_clearance: float = 0.0        # m
    target_bottom_clearance: float = 0.001  # m

    # ---- time stepping / output (main() in the C++ driver) ----
    t_max: float = 60.0                     # s
    fps: float = 120.0                      # VTK/CSV output frames per second
    steps_per_frame: int = 200              # MercuryDPM steps per output frame
    reference_fps: float = 120.0            # fixed cadence used to define dt
    deme_substeps: int = 4                  # extra DEME substeps per MercuryDPM step
    gravity: float = -9.81                  # m/s^2

    # ---- LinearViscoelasticFrictionSpecies ----
    density: float = 1500.0                 # kg/m^3
    stiffness: float = 1.0e3                # N/m   (normal spring stiffness k)
    dissipation: float = 0.02               # kg/s  (normal dashpot c)
    sliding_friction: float = 0.5           # mu_s
    # sliding_stiffness = stiffness * 2/7, sliding_dissipation = dissipation

    # ---- integration / JIT tuning ----
    random_seed: int = 4150                 # replaces std::rand() for reproducible beds
    poisson_ratio: float = 0.30             # only needed for the Hertz -> k mapping
    reference_overlap_ratio: float = 0.01   # delta/R used in the Hertz -> k mapping
    drum_density: float = 1240.0            # kg/m^3, PLA; only used for mass/MOI of the kinematic drum

    # ---- derived, filled in __post_init__ via object.__setattr__ ----
    bed_x_half: float = field(init=False)
    bed_y_half: float = field(init=False)
    drum_center_z0: float = field(init=False)
    z_target: float = field(init=False)
    dt: float = field(init=False)
    mesh_volume: float = field(init=False)
    drum_mass: float = field(init=False)
    drum_moi: tuple[float, float, float] = field(init=False)

    def __post_init__(self) -> None:
        x_span = self.drum_half_length
        if self.drum_spin_axis.upper() in {"Y", "Z"}:
            # A Y/Z spin sweeps the drum's radius through global X; the X-spin
            # default only needs the drum half-length in that direction.
            x_span = self.drum_radius
        object.__setattr__(self, "bed_x_half", x_span + self.bed_margin_x)
        object.__setattr__(self, "bed_y_half", self.drum_radius + self.bed_margin_y)
        object.__setattr__(
            self,
            "drum_center_z0",
            self.floor_z + self.bed_height + self.drum_rest_clearance + self.drum_radius,
        )
        object.__setattr__(
            self,
            "z_target",
            self.floor_z + self.target_bottom_clearance + self.drum_radius,
        )
        # MercuryDPM's dt is tied to its reference output cadence.  Do NOT derive
        # it from `fps`: output cadence is a user-facing setting and changing it
        # must not silently change the integration step.
        object.__setattr__(self, "dt", 1.0 / (self.reference_fps * self.steps_per_frame))

        # DEME's Hertz step limit scales roughly as r^1.5.  Four substeps were
        # validated at r=1 mm; bump automatically for smaller grains so the
        # selected 0.9 mm setup stays comfortably inside the stable range.
        recommended = max(4, math.ceil(4.0 * (0.001 / self.particle_radius) ** 1.5))
        if self.deme_substeps < recommended:
            object.__setattr__(self, "deme_substeps", recommended)

        # Measured from the binary STL with a signed-tetrahedron volume sum.
        object.__setattr__(self, "mesh_volume", 7.79895e-5)  # m^3
        mass = self.drum_density * self.mesh_volume
        r, half_l = self.drum_radius, self.drum_half_length
        # Approximate principal moments of a solid cylinder (x/y transverse, z axial).
        i_axial = 0.5 * mass * r**2
        i_trans = mass * (3.0 * r**2 + (2.0 * half_l) ** 2) / 12.0
        object.__setattr__(self, "drum_mass", mass)
        object.__setattr__(self, "drum_moi", (i_trans, i_trans, i_axial))

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------
    @property
    def domain_bounds(self) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        """World box used by DEM-Engine (top is open / unbounded above)."""
        z_top = self.drum_center_z0 + self.drum_radius + 0.01
        return (
            (-self.bed_x_half, self.bed_x_half),
            (-self.bed_y_half, self.bed_y_half),
            (self.floor_z, z_top),
        )

    @property
    def deme_dt(self) -> float:
        """Integration step actually used by DEME (smaller than MercuryDPM's dt).

        DEME's Hertz force model becomes unstable at the original 4.17e-5 s step
        for millimetre grains.  Four substeps were validated at r=1 mm; the
        config automatically uses more for smaller grains (5 at the selected
        0.9 mm radius), preserving the physical trajectory and output cadence.
        """
        return self.dt / self.deme_substeps

    @property
    def particle_mass(self) -> float:
        return self.density * 4.0 / 3.0 * math.pi * self.particle_radius**3

    @property
    def effective_mass(self) -> float:
        """Two-particle reduced mass for the dashpot -> restitution conversion."""
        return self.particle_mass / 2.0

    @property
    def restitution(self) -> float:
        """Coefficient of restitution implied by the MercuryDPM linear spring/dashpot."""
        zeta = self.dissipation / (2.0 * math.sqrt(self.stiffness * self.effective_mass))
        if zeta >= 1.0:
            return 0.0
        return math.exp(-zeta * math.pi / math.sqrt(1.0 - zeta * zeta))

    @property
    def youngs_modulus(self) -> float:
        """Hertz E giving the same tangent stiffness as MercuryDPM's k at delta = alpha*R.

        For two equal spheres: R* = R/2, k = 2 E* sqrt(R* delta), E* = E/(2(1-nu^2)).
        With delta = alpha*R this reduces to E = (1-nu^2) k / (R sqrt(alpha/2)).
        """
        alpha = self.reference_overlap_ratio
        return (1.0 - self.poisson_ratio**2) * self.stiffness / (
            self.particle_radius * math.sqrt(alpha / 2.0)
        )

    @property
    def lowering_duration(self) -> float:
        return (self.drum_center_z0 - self.z_target) / self.lower_speed

    @property
    def n_particles(self) -> int:
        r = self.particle_radius
        margin = r + self.grain_jitter_amplitude
        spacing = self.grain_spacing
        nx = int((2 * self.bed_x_half - 2 * margin) / spacing) + 1
        ny = int((2 * self.bed_y_half - 2 * margin) / spacing) + 1
        nz = int((self.bed_height - 2 * margin) / spacing) + 1
        return nx * ny * nz

    @property
    def grain_jitter_amplitude(self) -> float:
        """Per-coordinate jitter amplitude (same 8% of r as the C++ driver)."""
        return 0.08 * self.particle_radius

    @property
    def grain_spacing(self) -> float:
        """Lattice spacing large enough that +/- jitter cannot create overlaps.

        MercuryDPM used exactly 2r with jitter on top, which gives up to 16%r
        overlap.  Its soft linear spring tolerates that; DEME's mapped Hertz
        model does not -- the resulting initial forces eject grains through the
        floor.  Adding 2*jitter keeps the same jitter amplitude without initial
        penetration.
        """
        return 2.0 * self.particle_radius + 2.0 * self.grain_jitter_amplitude

    def initial_quaternion_xyzw(self) -> tuple[float, float, float, float]:
        """90 deg about global Y, in the (x, y, z, w) convention used by DEME/Chrono."""
        s = math.sqrt(0.5)
        return (0.0, s, 0.0, s)  # x, y, z, w

    def global_axis_local(self, axis: str | None = None) -> tuple[float, float, float]:
        """Unit vector of ``axis`` (global X/Y/Z) expressed in the drum's local frame.

        DEME's prescribed angular velocity is stored in the body frame.  The
        initial 90 deg Y rotation maps the mesh's local Z axis onto global X,
        so a global-X spin must be requested as local-Z in the solver.
        """
        name = (axis or self.drum_spin_axis).upper()
        world = {
            "X": (1.0, 0.0, 0.0),
            "Y": (0.0, 1.0, 0.0),
            "Z": (0.0, 0.0, 1.0),
        }[name]
        x, y, z, w = self.initial_quaternion_xyzw()
        qx, qy, qz, qw = -x, -y, -z, w  # conjugate: global -> local
        tx = 2.0 * (qy * world[2] - qz * world[1])
        ty = 2.0 * (qz * world[0] - qx * world[2])
        tz = 2.0 * (qx * world[1] - qy * world[0])
        return (
            world[0] + qw * tx + (qy * tz - qz * ty),
            world[1] + qw * ty + (qz * tx - qx * tz),
            world[2] + qw * tz + (qx * ty - qy * tx),
        )

    def drum_z(self, t: float) -> float:
        """Kinematic descent of the drum centre, clamped at z_target."""
        return max(
            self.z_target,
            self.drum_center_z0 - self.lower_speed * max(0.0, t - self.settle_time),
        )

    def drum_omega_at(self, t: float) -> float:
        return self.drum_omega if t >= self.settle_time else 0.0

    def summary(self) -> str:
        return "\n".join(
            [
                f"drum: R={self.drum_radius:.4f} m, half-length={self.drum_half_length:.4f} m",
                f"drum centre z: {self.drum_center_z0:.4f} -> {self.z_target:.4f} m "
                f"({self.lowering_duration:.2f} s of descent)",
                f"bed: x=+/-{self.bed_x_half:.4f} m, y=+/-{self.bed_y_half:.4f} m, "
                f"height={self.bed_height:.4f} m",
                f"grains: r={self.particle_radius:.5f} m, ~{self.n_particles} particles",
                f"dt={self.dt:.3e} s (DEME {self.deme_dt:.3e} s), "
                f"t_max={self.t_max} s, output fps={self.fps:g}",
                f"contact: rho={self.density:g}, k={self.stiffness:g}, c={self.dissipation:g}, "
                f"mu={self.sliding_friction:g}",
                f"mapped: E={self.youngs_modulus:.4g} Pa, e={self.restitution:.4f}",
            ]
        )


DEFAULT = ExcavatorParams()

# Small-scale override used by the CPU PyChrono demo (the header comment in the
# original C++ file recommends 4 mm radius grains for reasonable runtimes).
PYCHRONO_DEMO = ExcavatorParams(
    particle_radius=0.004,
    bed_height=0.025,
    t_max=2.0,
    fps=20.0,
    steps_per_frame=50,
    stl_file="LunarDrumScrew_m_coarse.stl",
)


def grain_positions(p: ExcavatorParams) -> list[tuple[float, float, float]]:
    """Pre-instantiate the whole bed at t=0, as the MercuryDPM driver does.

    Uses a seeded PRNG instead of std::rand() so both ports produce the same
    (deterministic) bed.  The spacing includes the worst-case relative jitter
    (2 * jitter_amp), so no grain starts overlapping another grain or a wall.
    This deliberate deviation from the C++ driver's ``spacing = 2r`` is
    necessary for the nonlinear Hertz model used by DEME; see ``grain_spacing``.
    """
    import random

    rng = random.Random(p.random_seed)
    r = p.particle_radius
    spacing = p.grain_spacing
    jitter_amp = p.grain_jitter_amplitude
    margin = r + jitter_amp  # keeps every possible jittered grain inside walls
    out: list[tuple[float, float, float]] = []

    x = -p.bed_x_half + margin
    while x <= p.bed_x_half - margin:
        y = -p.bed_y_half + margin
        while y <= p.bed_y_half - margin:
            z = p.floor_z + margin
            while z <= p.floor_z + p.bed_height - margin:
                out.append(
                    (
                        x + jitter_amp * (2.0 * rng.random() - 1.0),
                        y + jitter_amp * (2.0 * rng.random() - 1.0),
                        z + jitter_amp * (2.0 * rng.random() - 1.0),
                    )
                )
                z += spacing
            y += spacing
        x += spacing
    return out
