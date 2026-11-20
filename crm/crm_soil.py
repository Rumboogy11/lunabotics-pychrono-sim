#!/usr/bin/env -S /mnt/P3/Programs/Linux/pychrono/chrono-env/bin/python
"""Independent soil estimates for the Chrono::CRM excavator model.

These values are best-estimate starting points for a loose lunar-regolith
simulant at the scale of this 122 mm drum. They are meant to be calibrated
against bulk tests (angle of repose, direct shear, cone penetration, and a small
bucket draw test), then held fixed while excavator geometries are compared.

Notes on the mapping to Chrono::CRM
-----------------------------------
``density`` is the bulk density used by the CRM continuum and by the fill-ratio
mass calculation.  ``grain_diameter`` is the physical grain size used by the
mu(I) rheology; it is independent of the SPH particle spacing.  Do not set it to
the SPH spacing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def _enum(fsi, family: str, member: str, fallback=None):
    """Resolve a pybind enum exposed either as MODULE_FAMILY_MEMBER or MODULE.FAMILY.MEMBER."""
    direct = getattr(fsi, f"{family}_{member}", None)
    if direct is not None:
        return direct
    enum = getattr(fsi, family, None)
    return getattr(enum, member, fallback) if enum is not None else fallback


@dataclass(frozen=True)
class CrmSoilParams:
    """Bulk soil properties for a Chrono::CRM mu(I) granular continuum."""

    density: float = 1650.0              # kg/m^3, loose bulk density
    youngs_modulus: float = 2.0e6        # Pa, effective continuum stiffness
    poisson_ratio: float = 0.30          # -
    friction: float = 0.75               # mu_s, tan(friction angle)
    mu_2: float = 0.75                   # high-inertia limiting friction
    mu_I0: float = 0.04                  # reference inertial number
    cohesion: float = 1.2e3              # Pa
    grain_diameter: float = 0.0002       # m, physical regolith size for mu(I)

    def __post_init__(self) -> None:
        if self.density <= 0.0:
            raise ValueError("soil density must be positive")
        if self.youngs_modulus <= 0.0:
            raise ValueError("Young's modulus must be positive")
        if not (-1.0 < self.poisson_ratio < 0.5):
            raise ValueError("Poisson ratio must be in (-1, 0.5)")
        if self.friction <= 0.0 or self.mu_2 <= 0.0:
            raise ValueError("friction coefficients must be positive")
        if self.mu_I0 <= 0.0:
            raise ValueError("mu_I0 must be positive")
        if self.cohesion < 0.0:
            raise ValueError("cohesion cannot be negative")
        if self.grain_diameter <= 0.0:
            raise ValueError("grain diameter must be positive")

    @property
    def friction_angle_deg(self) -> float:
        return math.degrees(math.atan(self.friction))

    def summary(self) -> str:
        return "\n".join(
            [
                "CRM soil (independent best-estimate parameters):",
                f"  bulk density      {self.density:,.1f} kg/m^3",
                f"  Young's modulus   {self.youngs_modulus:.3g} Pa",
                f"  Poisson ratio     {self.poisson_ratio:.3f}",
                f"  friction mu_s     {self.friction:.3f} "
                f"(phi ~= {self.friction_angle_deg:.1f} deg)",
                f"  friction mu_2     {self.mu_2:.3f}",
                f"  mu_I0             {self.mu_I0:.4f}",
                f"  cohesion          {self.cohesion:.4g} Pa",
                f"  grain diameter    {self.grain_diameter * 1e6:.0f} um (rheology only)",
            ]
        )

    def apply(self, fsi, terrain) -> None:
        """Copy these properties into a Chrono CRM terrain.

        Chrono 10 exposes the granular continuum properties as
        ``fsi.ElasticMaterialProperties`` via ``terrain.SetElasticSPH()``.
        Older builds used ``fsi.SoilProperties`` and ``terrain.SetCrmSPH()``;
        both are supported here so the driver works with either binding.
        """
        mat_type = getattr(fsi, "ElasticMaterialProperties", None)
        setter = getattr(terrain, "SetElasticSPH", None)
        if mat_type is None or setter is None:
            mat = fsi.SoilProperties()
            old_setter = getattr(terrain, "SetCrmSPH", None)
            if old_setter is None:
                raise RuntimeError(
                    "this PyChrono build exposes neither SetElasticSPH nor SetCrmSPH"
                )
        else:
            mat = mat_type()
            old_setter = setter

        mat.density = self.density
        mat.Young_modulus = self.youngs_modulus
        mat.Poisson_ratio = self.poisson_ratio
        mat.mu_I0 = self.mu_I0
        mat.mu_fric_s = self.friction
        mat.mu_fric_2 = self.mu_2
        mat.average_diam = self.grain_diameter
        mat.cohesion_coeff = self.cohesion

        rheology = _enum(fsi, "RheologyCRM", "MU_OF_I")
        if rheology is not None and hasattr(mat, "rheology_model"):
            mat.rheology_model = rheology

        old_setter(mat)


DEFAULT_SOIL = CrmSoilParams()
