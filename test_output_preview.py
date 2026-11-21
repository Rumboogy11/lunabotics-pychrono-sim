#!/usr/bin/env python3
"""Regression tests for the lightweight output preview camera."""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace

from study_output_preview import OutputPreview, _project_point


class OutputPreviewCameraTest(unittest.TestCase):
    def test_world_axes_at_zero_yaw_pitch(self) -> None:
        center = (0.0, 0.0, 0.0)
        front = _project_point((1.0, 0.0, 0.0), center, 0.0, 0.0, 3.0)
        right = _project_point((0.0, 1.0, 0.0), center, 0.0, 0.0, None)
        up = _project_point((0.0, 0.0, 1.0), center, 0.0, 0.0, None)

        self.assertAlmostEqual(front[0], 0.0)
        self.assertAlmostEqual(front[1], 0.0)
        self.assertLess(front[2], 0.0)
        self.assertGreater(front[3], 1.0)
        self.assertGreater(right[0], 0.0)
        self.assertAlmostEqual(right[1], 0.0)
        self.assertGreater(up[1], 0.0)
        self.assertAlmostEqual(up[2], 0.0)

    def test_drag_follows_pointer(self) -> None:
        center = (0.0, 0.0, 0.0)
        front = (1.0, 0.0, 0.0)

        before = _project_point(front, center, 0.0, 0.0, None)
        after_right = _project_point(front, center, math.radians(-5.0), 0.0, None)
        after_down = _project_point(front, center, 0.0, math.radians(5.0), None)

        self.assertGreater(after_right[0], before[0])
        self.assertLess(after_down[1], before[1])

    def test_drag_handler_signs(self) -> None:
        preview = SimpleNamespace(
            yaw_deg=10.0,
            pitch_deg=5.0,
            _drag_origin=(100, 50),
            _draw_particles=lambda: None,
        )
        OutputPreview._on_drag_move(preview, SimpleNamespace(x=120, y=80))
        self.assertAlmostEqual(preview.yaw_deg, 0.0 % 360.0)
        self.assertAlmostEqual(preview.pitch_deg, 20.0)


if __name__ == "__main__":
    unittest.main()
