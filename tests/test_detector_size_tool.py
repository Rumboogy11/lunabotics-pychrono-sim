#!/usr/bin/env python3
"""Regression coverage for the live fill-detector size-adjuster helpers."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

import detector_size_tool as tool  # noqa: E402


class DetectorSizeToolHelpersTest(unittest.TestCase):
    def test_parser_accepts_step_surface_and_fea_inputs(self) -> None:
        args = tool.build_parser().parse_args([
            "--step", "model.step",
            "--surface", "surface.stl",
            "--fea-node", "mesh.node",
            "--radius", "0.05",
            "--length", "0.08",
            "--spacing", "0.002",
            "--source-axis", "Z",
            "--unit-scale", "1.0",
            "--result-json", "size.json",
            "--show",
        ])
        self.assertEqual(args.step, Path("model.step"))
        self.assertEqual(args.surface, Path("surface.stl"))
        self.assertEqual(args.fea_node, Path("mesh.node"))
        self.assertEqual(args.radius, 0.05)
        self.assertEqual(args.length, 0.08)
        self.assertEqual(args.source_axis, "Z")
        self.assertTrue(args.show)

    def test_center_parser_accepts_commas_and_semicolons(self) -> None:
        self.assertEqual(tool.parse_center("1, 2;3"), (1.0, 2.0, 3.0))
        with self.assertRaises(SystemExit):
            tool.parse_center("1,2")
        with self.assertRaises(SystemExit):
            tool.parse_center("1,2,x")

    def test_slider_bounds_contain_initial_values(self) -> None:
        (r_min, r_max), (l_min, l_max) = tool.slider_bounds(
            0.061037, 0.066, 0.002)
        self.assertLess(r_min, 0.061037)
        self.assertGreater(r_max, 0.061037)
        self.assertLess(l_min, 0.066)
        self.assertGreater(l_max, 0.066)
        for args in ((0.0, 0.066, 0.002), (0.061, -1.0, 0.002),
                     (0.061, 0.066, 0.0)):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                tool.slider_bounds(*args)

    def test_effective_dimensions_match_fill_classifier_erosion(self) -> None:
        self.assertEqual(
            tool.effective_dimensions(0.061, 0.066, 0.002),
            (0.06, 0.064),
        )
        # Bounds are clamped, so tiny cylinders cannot produce a negative wireframe.
        self.assertEqual(
            tool.effective_dimensions(0.0004, 0.0004, 0.002),
            (0.0, 0.0),
        )

    def test_parser_accepts_spin_direction_flags(self) -> None:
        args = tool.build_parser().parse_args([
            "--spin-axis", "Y", "--reverse-spin",
        ])
        self.assertEqual(args.spin_axis, "Y")
        self.assertTrue(args.reverse_spin)

    def test_rotation_axis_uses_local_frame_and_reverse_flag(self) -> None:
        # Default X spin maps onto the drum-local Z axis; reverse flips it.
        forward = tool.rotation_axis("X")
        reverse = tool.rotation_axis("X", reverse=True)
        self.assertAlmostEqual(abs(forward[2]), 1.0, places=9)
        np.testing.assert_allclose(reverse, [-v for v in forward], atol=1.0e-9)
        self.assertAlmostEqual(abs(tool.rotation_axis("Y")[1]), 1.0, places=9)

    def test_rotation_arrow_is_a_closed_3d_arrow(self) -> None:
        forward, head = tool.rotation_arrow(
            0.06, 0.066, (0.0, 0.0, 0.0), tool.rotation_axis("X"))
        reverse, reverse_head = tool.rotation_arrow(
            0.06, 0.066, (0.0, 0.0, 0.0), tool.rotation_axis("X", True))
        self.assertGreater(forward.n_cells, 0)
        self.assertGreater(head.n_cells, 0)
        self.assertGreater(reverse.n_cells, 0)
        self.assertGreater(reverse_head.n_cells, 0)
        # The cone sits beyond opposite axial ends for the two spin directions.
        self.assertGreater(np.asarray(head.points)[:, 2].mean(), 0.0)
        self.assertLess(np.asarray(reverse_head.points)[:, 2].mean(), 0.0)

    def test_actor_opacity_is_clamped_and_applied(self) -> None:
        class FakeProperty:
            def __init__(self):
                self.value = 1.0

            def SetOpacity(self, value):
                self.value = value

        class FakeActor:
            def __init__(self):
                self.property = FakeProperty()

            def GetProperty(self):
                return self.property

        actor = FakeActor()
        self.assertEqual(tool.set_actor_opacity(actor, 1.4), 1.0)
        self.assertEqual(actor.property.value, 1.0)
        self.assertEqual(tool.set_actor_opacity(actor, -0.2), 0.0)
        self.assertEqual(actor.property.value, 0.0)
        self.assertAlmostEqual(tool.set_actor_opacity(actor, 0.35), 0.35)

    def test_result_file_publishes_live_values_and_dirty_flag(self) -> None:
        with tempfile.TemporaryDirectory(prefix="detector_result_",
                                         dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "nested" / "detector_size.json"
            tool.write_result(path, 0.05, 0.08, 0.002, False)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {
                "radius": 0.05,
                "length": 0.08,
                "spacing": 0.002,
                "dirty": False,
            })
            self.assertFalse(list(path.parent.glob("*.tmp")))

            tool.write_result(path, 0.052, 0.081, 0.002, True)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["radius"], 0.052)
            self.assertEqual(data["length"], 0.081)
            self.assertTrue(data["dirty"])


if __name__ == "__main__":
    unittest.main()
