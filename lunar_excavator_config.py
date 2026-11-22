#!/usr/bin/env python3
"""Shared excavator geometry and kinematic defaults.

This module provides the baseline drum dimensions and motion/pose helpers used by
the Chrono::CRM driver and one-way FEA replay. Values are expressed in SI units
(metres, seconds, radians) unless noted otherwise.

Geometry note
-------------
The reference drum used for these defaults is measured as::

    x in [-0.061, 0.061], y in [-0.061, 0.061], z in [-0.033, 0.033]  [m]

so the mesh long axis is local Z. A 90° rotation about global Y maps that local
Z axis onto global X. With the default ``drum_spin_axis="X"``, the prescribed
spin is therefore about the drum's long axis in the world frame.
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
STL_MEDIUM = GEOMETRY_DIR / "BucketDrumExcavator_m_medium.stl"
OBJ_MEDIUM = GEOMETRY_DIR / "BucketDrumExcavator_m_medium.obj"
STL_COARSE = GEOMETRY_DIR / "BucketDrumExcavator_m_coarse.stl"
OBJ_COARSE = GEOMETRY_DIR / "BucketDrumExcavator_m_coarse.obj"

# Extra soil beyond the drum footprint, per bed side, as a fraction of the
# drum radius.  This keeps flung particles away from the BCE container walls.
BED_PADDING_RADIUS_FRACTION = 0.5


def bed_half_extents(radius: float, half_length: float, axis: str = "X",
                     padding_fraction: float = BED_PADDING_RADIUS_FRACTION) -> tuple[float, float]:
    """Soil-bed half-widths with padding around the excavator."""
    axis_span = half_length if axis.upper() == "X" else radius
    padding = padding_fraction * radius
    return (axis_span + padding, radius + padding)


@dataclass(frozen=True)
class ExcavatorParams:
    """Baseline excavator geometry, motion, and derived pose helpers."""

    # ---- drum geometry (reference model) ----
    drum_omega: float = 6.28 * 0.5          # rad/s, spin rate magnitude
    drum_half_length: float = 0.033         # m
    drum_radius: float = 0.061              # m (outer/crest radius)
    stl_file: str = "BucketDrumExcavator_m_medium.stl"
    drum_spin_axis: str = "X"               # global axis after initial orientation

    # ---- bed / motion sequencing ----
    bed_height: float = 0.06                # m
    floor_z: float = 0.0                    # m
    settle_time: float = 0.001              # s
    lower_speed: float = 0.0015             # m/s
    drum_rest_clearance: float = 0.0        # m
    target_bottom_clearance: float = 0.001  # m
    # Soil padding around the drum, as a fraction of drum radius.  More padding
    # gives flung particles room to bounce off the reflective bed walls instead
    # of seeding collisions against the drum.
    bed_padding: float = BED_PADDING_RADIUS_FRACTION

    # ---- simulation defaults ----
    t_max: float = 60.0                     # s
    gravity: float = -9.81                  # m/s^2
    drum_density: float = 1240.0            # kg/m^3, PLA estimate for mass/MOI

    # ---- derived, filled in __post_init__ ----
    bed_x_half: float = field(init=False)
    bed_y_half: float = field(init=False)
    drum_center_z0: float = field(init=False)
    z_target: float = field(init=False)
    mesh_volume: float = field(init=False)
    drum_mass: float = field(init=False)
    drum_moi: tuple[float, float, float] = field(init=False)

    def __post_init__(self) -> None:
        axis = self.drum_spin_axis.upper()
        if axis not in {"X", "Y", "Z"}:
            raise ValueError("drum_spin_axis must be X, Y, or Z")
        if not math.isfinite(self.gravity):
            raise ValueError("gravity must be finite")

        # Bed footprint includes padding around the drum so ejected soil hits
        # the reflective BCE walls before it can hit (or freeze inside) the
        # excavator.  CRM runs may override this via --bed-padding.
        if self.bed_padding < 0.0:
            raise ValueError("bed_padding must be non-negative")
        bed_x_half, bed_y_half = bed_half_extents(
            self.drum_radius, self.drum_half_length, axis, self.bed_padding
        )
        object.__setattr__(self, "bed_x_half", bed_x_half)
        object.__setattr__(self, "bed_y_half", bed_y_half)
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

        # Measured from the reference binary STL with a signed-tetrahedron sum.
        object.__setattr__(self, "mesh_volume", 7.79895e-5)  # m^3
        mass = self.drum_density * self.mesh_volume
        r, half_l = self.drum_radius, self.drum_half_length
        # Approximate principal moments of a solid cylinder (x/y transverse, z axial).
        i_axial = 0.5 * mass * r**2
        i_trans = mass * (3.0 * r**2 + (2.0 * half_l) ** 2) / 12.0
        object.__setattr__(self, "drum_mass", mass)
        object.__setattr__(self, "drum_moi", (i_trans, i_trans, i_axial))

    @property
    def lowering_duration(self) -> float:
        return (self.drum_center_z0 - self.z_target) / self.lower_speed

    def initial_quaternion_xyzw(self) -> tuple[float, float, float, float]:
        """90° about global Y, in (x, y, z, w) convention."""
        s = math.sqrt(0.5)
        return (0.0, s, 0.0, s)

    def global_axis_local(self, axis: str | None = None) -> tuple[float, float, float]:
        """Unit vector of a global axis expressed in the drum's local frame."""
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
        """Kinematic descent of the drum centre, clamped at ``z_target``."""
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
                f"spin: axis={self.drum_spin_axis}, omega={self.drum_omega:.4g} rad/s",
                f"drum centre z: {self.drum_center_z0:.4f} -> {self.z_target:.4f} m "
                f"({self.lowering_duration:.2f} s descent)",
                f"bed height: {self.bed_height:.4f} m, descent speed: {self.lower_speed:.4g} m/s",
            ]
        )


DEFAULT = ExcavatorParams()
