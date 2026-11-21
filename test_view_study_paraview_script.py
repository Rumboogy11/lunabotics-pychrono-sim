#!/usr/bin/env python3
"""Regression coverage for ParaView --script execution without __file__."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPT = ROOT / "view_study_paraview.py"
sys.path.insert(0, str(ROOT))

import view_study_paraview as viewer  # noqa: E402


class ParaViewScriptDirectoryTest(unittest.TestCase):
    def test_gui_script_mode_finds_repo_without_file(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        namespace: dict[str, object] = {"__name__": "view_study_paraview_test"}
        original_argv = list(sys.argv)
        original_script_dir = os.environ.get("PYCHRONO_SCRIPT_DIR")
        try:
            # ParaView 6.x runs --script files with exec() and does not define
            # __file__.  The GUI now also exports PYCHRONO_SCRIPT_DIR.
            sys.argv[:] = ["paraview", f"--script={SCRIPT}"]
            os.environ["PYCHRONO_SCRIPT_DIR"] = str(ROOT)
            exec(compile(source, "<paraview-script>", "exec"), namespace)
            self.assertEqual(namespace["_script_directory"](), ROOT)
        finally:
            sys.argv[:] = original_argv
            if original_script_dir is None:
                os.environ.pop("PYCHRONO_SCRIPT_DIR", None)
            else:
                os.environ["PYCHRONO_SCRIPT_DIR"] = original_script_dir


class ParaViewParticleDisplayTest(unittest.TestCase):
    def test_representation_name_handles_string_list_property(self) -> None:
        class StringList:
            Representation = ["Point Gaussian"]

        class QuotedString:
            Representation = "'Points'"

        self.assertEqual(viewer._representation_name(StringList()), "Point Gaussian")
        self.assertEqual(viewer._representation_name(QuotedString()), "Points")

    def test_sphere_particles_prefer_point_sprites_over_glyphs(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        sprite = source.index("disp.Representation = 'Point Gaussian'")
        glyph = source.index("glyph = Glyph(")
        self.assertLess(sprite, glyph)

    def test_one_way_fea_history_charts_are_opt_in(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("PYCHRONO_FEA_CHARTS", source)
        self.assertIn("if include_fea and args.show_fea_charts:", source)
        self.assertEqual(source.count("CreateXYPlotView()"), 1)


class ParaViewCameraOrbitTest(unittest.TestCase):
    def test_auto_fit_radius_uses_five_times_radial_clearance(self) -> None:
        axis, u, v = viewer._orbit_basis()
        bounds = (-1.0, 1.0, -2.0, 2.0, -3.0, 3.0)

        radius = viewer._fit_orbit_radius(bounds, (0.0, 0.0, 0.0), axis, u, v)

        self.assertAlmostEqual(radius, 10.0)

    def test_orbit_center_is_lowered_by_one_third_excavator_radius(self) -> None:
        axis, u, v = viewer._orbit_basis()
        bounds = (-1.0, 1.0, -2.0, 2.0, -3.0, 3.0)

        center, drop = viewer._lowered_orbit_center(
            bounds, (0.0, 0.0, 0.0), axis, u, v)

        self.assertAlmostEqual(drop, 1.0)
        self.assertAlmostEqual(center[0], 0.0)
        self.assertAlmostEqual(center[1], 0.0)
        self.assertAlmostEqual(center[2], -1.0)

    def test_missing_bounds_keep_original_center(self) -> None:
        axis, u, v = viewer._orbit_basis()

        center, drop = viewer._lowered_orbit_center(
            None, (1.0, 2.0, 3.0), axis, u, v)

        self.assertEqual(center, (1.0, 2.0, 3.0))
        self.assertEqual(drop, 0.0)
        self.assertAlmostEqual(
            viewer._fit_orbit_radius(None, center, axis, u, v), 0.6)


if __name__ == "__main__":
    unittest.main()
