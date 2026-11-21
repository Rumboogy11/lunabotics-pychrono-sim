#!/usr/bin/env python3
"""Regression tests for the lightweight output preview camera."""

from __future__ import annotations

import csv
import inspect
import math
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from study_output_preview import (
    PARTICLE_SAMPLE_DEFAULT,
    OutputPreview,
    _project_point,
    load_particle_sample,
)


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


class ParticleSampleTest(unittest.TestCase):
    def test_default_particle_sample_count_is_25000(self) -> None:
        default = inspect.signature(OutputPreview.__init__).parameters["particle_limit"].default
        self.assertEqual(default, PARTICLE_SAMPLE_DEFAULT)
        self.assertEqual(PARTICLE_SAMPLE_DEFAULT, 25000)

    def test_sample_count_controls_drawn_particles_and_reports_total(self) -> None:
        with tempfile.TemporaryDirectory(prefix="preview_sample_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "particles_000000.csv"
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["X", "Y", "Z", "inside_drum", "excavated"])
                for i in range(5000):
                    writer.writerow([i * 0.001, 0.0, 0.0, 0, 0])

            small, total = load_particle_sample(path, 100)
            large, _ = load_particle_sample(path, 2000)

            self.assertEqual(total, 5000)
            self.assertEqual(len(small), 100)
            self.assertEqual(len(large), 2000)
            self.assertLess(len(small), len(large))

    def test_status_reports_true_simulation_particle_count(self) -> None:
        fake = SimpleNamespace(
            dir_var=SimpleNamespace(get=lambda: "/tmp/study"),
            frames=[object(), object()],
            _all_frame_count=2,
            frame_stride=1,
            particle_limit=25000,
            particle_total=242172,
            crm_rows=[object()],
            fea_rows=[object()],
            status="",
        )
        fake.status_var = SimpleNamespace(
            set=lambda text: setattr(fake, "status", text))

        OutputPreview._update_status(fake)

        self.assertIn("242,172 sim particles", fake.status)
        self.assertIn("sample limit 25,000", fake.status)


if __name__ == "__main__":
    unittest.main()
