#!/usr/bin/env python3
"""Regression coverage for ParaView --script execution without __file__."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


class ParaViewDefaultDisplayTest(unittest.TestCase):
    def test_excavator_opens_at_40_percent_opacity(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertAlmostEqual(viewer.EXCAVATOR_OPACITY, 0.4)
        self.assertIn("disp.Opacity = EXCAVATOR_OPACITY", source)
        self.assertNotIn("disp.Opacity = 0.85", source)
        self.assertLess(source.index("excav = PVDReader"),
                        source.index("disp.Opacity = EXCAVATOR_OPACITY"))

    def test_fea_warp_starts_hidden(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertFalse(viewer.FEA_WARP_VISIBLE)
        self.assertIn("GetRepresentation(warp, view).Visibility = 0", source)
        self.assertLess(source.index("warp = WarpByVector(Input=fea)"),
                        source.index("GetRepresentation(warp, view).Visibility = 0"))


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


class ParaViewFirstFrameTest(unittest.TestCase):
    """Regression coverage for the GUI's first-frame camera and view-up."""

    class FakeCamera:
        def __init__(self, up, normal):
            self.up = tuple(float(v) for v in up)
            self.normal = tuple(float(v) for v in normal)

        def GetViewUp(self):
            return list(self.up)

        def GetViewPlaneNormal(self):
            return list(self.normal)

        def SetViewUp(self, *value):
            self.up = tuple(float(v) for v in value)

    class FakeScene:
        def __init__(self, fail=False, view=None):
            self.calls: list[str] = []
            self.fail = fail
            self.view = view

        def GoToNext(self):
            if self.fail:
                raise RuntimeError("no next frame")
            self.calls.append("next")
            if self.view is not None:
                self.view.suppression_seen.append(self.view.SuppressRendering)

        def GoToFirst(self):
            self.calls.append("first")
            if self.view is not None:
                self.view.suppression_seen.append(self.view.SuppressRendering)

    class FakeView:
        def __init__(self):
            self.SuppressRendering = 0
            self.suppression_seen: list[int] = []

    def test_parallel_view_up_is_replaced_before_reset_camera(self) -> None:
        camera = self.FakeCamera((0.0, 0.0, 1.0), (0.0, 0.0, -1.0))

        up = viewer._set_nonparallel_view_up(camera)

        self.assertLess(abs(viewer._dot(up, camera.normal)), 0.9)
        self.assertEqual(camera.up, up)

    def test_orthogonal_view_up_is_preserved(self) -> None:
        camera = self.FakeCamera((0.0, 1.0, 0.0), (0.0, 0.0, 1.0))

        up = viewer._set_nonparallel_view_up(camera)

        self.assertEqual(up, (0.0, 1.0, 0.0))
        self.assertEqual(camera.up, (0.0, 1.0, 0.0))

    def test_zero_view_up_is_replaced(self) -> None:
        camera = self.FakeCamera((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))

        up = viewer._set_nonparallel_view_up(camera)

        self.assertNotEqual(up, (0.0, 0.0, 0.0))

    def test_animation_scene_is_primed_forward_then_first(self) -> None:
        view = self.FakeView()
        scene = self.FakeScene(view=view)

        self.assertTrue(viewer._prime_animation_scene(scene, view))
        self.assertEqual(scene.calls, ["next", "first"])
        self.assertEqual(view.suppression_seen, [True, True])
        self.assertEqual(view.SuppressRendering, 0)

    def test_priming_failure_is_nonfatal_and_restores_rendering(self) -> None:
        view = self.FakeView()
        view.SuppressRendering = 1

        self.assertFalse(viewer._prime_animation_scene(self.FakeScene(fail=True), view))
        self.assertEqual(view.SuppressRendering, 1)
        self.assertFalse(viewer._prime_animation_scene(None))

    def test_orbit_and_reset_camera_use_the_guards(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("_prime_animation_scene(scene, view)", source)
        safe_up = source.index("_set_nonparallel_view_up(view.GetActiveCamera())")
        self.assertLess(safe_up, source.index("ResetCamera(view)"))


if __name__ == "__main__":
    unittest.main()
