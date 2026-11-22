#!/usr/bin/env python3
"""Regression coverage for mesh-scaled rigid drum mass properties."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(CRM_DIR))

import lunar_excavator_crm as crm  # noqa: E402
from lunar_excavator_config import DEFAULT as MOTION_DEFAULT  # noqa: E402

CUBE = """v 0 0 0
v 1 0 0
v 1 1 0
v 0 1 0
v 0 0 1
v 1 0 1
v 1 1 1
v 0 1 1
f 1 4 3
f 1 3 2
f 5 6 7
f 5 7 8
f 1 2 6
f 1 6 5
f 4 8 7
f 4 7 3
f 1 5 8
f 1 8 4
f 2 3 7
f 2 7 6
"""


class DrumMassPropertiesTest(unittest.TestCase):
    def test_obj_mesh_volume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cube.obj"
            path.write_text(CUBE, encoding="utf-8")
            self.assertAlmostEqual(crm.obj_mesh_volume(path), 1.0, places=6)

    def test_mass_uses_mesh_volume_and_falls_back_for_missing_mesh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cube.obj"
            path.write_text(CUBE, encoding="utf-8")
            mass, inertia = crm.drum_mass_properties(MOTION_DEFAULT, path)
            self.assertAlmostEqual(mass, MOTION_DEFAULT.drum_density)
            r, half_l = MOTION_DEFAULT.drum_radius, MOTION_DEFAULT.drum_half_length
            expected_axial = 0.5 * mass * r**2
            expected_trans = mass * (3.0 * r**2 + (2.0 * half_l) ** 2) / 12.0
            self.assertAlmostEqual(inertia[0], expected_trans)
            self.assertAlmostEqual(inertia[2], expected_axial)

            fallback_mass, _ = crm.drum_mass_properties(MOTION_DEFAULT, Path(tmp) / "missing.obj")
            self.assertAlmostEqual(fallback_mass, MOTION_DEFAULT.drum_mass)

    def test_scaled_mesh_does_not_keep_reference_mass(self) -> None:
        motion = replace(
            MOTION_DEFAULT,
            drum_radius=0.179095,
            drum_half_length=0.153176,
        )
        generated = ROOT / "Output_study_20261121_172816/mesh/3-channel_fea_surface.obj"
        if not generated.exists():
            self.skipTest("generated study mesh not present")
        mass, _ = crm.drum_mass_properties(motion, generated)
        self.assertGreater(mass, 10.0 * MOTION_DEFAULT.drum_mass)


if __name__ == "__main__":
    unittest.main()
