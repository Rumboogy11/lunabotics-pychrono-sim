#!/usr/bin/env python3
"""Regression tests for the lightweight output preview camera."""

from __future__ import annotations

import csv
import inspect
import math
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from study_output_preview import (
    DRUM_MARKER_COLOR,
    LINE_WIDTH_DEFAULT,
    LINE_WIDTH_MAX,
    LINE_WIDTH_MIN,
    PARTICLE_SAMPLE_DEFAULT,
    PARTICLE_SIZE_DEFAULT,
    PARTICLE_SIZE_MAX,
    PARTICLE_SIZE_MIN,
    OutputPreview,
    _project_point,
    _rotate_about_axis,
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
    def test_default_particle_sample_count_is_5000(self) -> None:
        default = inspect.signature(OutputPreview.__init__).parameters["particle_limit"].default
        self.assertEqual(default, PARTICLE_SAMPLE_DEFAULT)
        self.assertEqual(PARTICLE_SAMPLE_DEFAULT, 5000)

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
            particle_limit=5000,
            particle_total=242172,
            crm_rows=[object()],
            fea_rows=[object()],
            status="",
        )
        fake.status_var = SimpleNamespace(
            set=lambda text: setattr(fake, "status", text))

        OutputPreview._update_status(fake)

        self.assertIn("242,172 sim particles", fake.status)
        self.assertIn("sample limit 5,000", fake.status)


class PreviewStyleTest(unittest.TestCase):
    def test_set_display_style_clamps_and_redraws(self) -> None:
        sizes = []
        widths = []
        draws = []
        fake = SimpleNamespace(
            particle_size=PARTICLE_SIZE_DEFAULT,
            line_width=LINE_WIDTH_DEFAULT,
            particle_size_var=SimpleNamespace(set=sizes.append),
            line_width_var=SimpleNamespace(set=widths.append),
            _draw_all=lambda: draws.append(True),
        )

        self.assertTrue(OutputPreview.set_display_style(fake, 99, 0))
        self.assertEqual(fake.particle_size, PARTICLE_SIZE_MAX)
        self.assertEqual(fake.line_width, LINE_WIDTH_MIN)
        self.assertEqual(sizes[-1], PARTICLE_SIZE_MAX)
        self.assertEqual(widths[-1], LINE_WIDTH_MIN)
        self.assertEqual(len(draws), 1)

        self.assertFalse(OutputPreview.set_display_style(
            fake, PARTICLE_SIZE_MAX, LINE_WIDTH_MIN))
        self.assertEqual(len(draws), 1)

    def test_style_values_reach_canvas_primitives(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_output_preview as preview_module

        root = tk.Tk()
        root.withdraw()
        try:
            parent = ttk.Frame(root)
            parent.pack()
            preview = preview_module.OutputPreview(
                parent, root, "", lambda: {},
                particle_size=6, line_width=3)
            root.update()

            preview.crm_canvas.configure(width=320, height=160)
            preview.particle_canvas.configure(width=320, height=200)
            root.update()

            rows = [
                {"time_s": 0.0, "fill_ratio_mass": 0.1},
                {"time_s": 1.0, "fill_ratio_mass": 0.2},
            ]
            preview.current_time = 0.0
            preview._draw_timeline(preview.crm_canvas, "CRM", rows,
                                   preview_module.CRM_SERIES, True)
            line_widths = [
                float(preview.crm_canvas.itemcget(item, "width"))
                for item in preview.crm_canvas.find_all()
                if preview.crm_canvas.type(item) == "line"
            ]
            self.assertTrue(line_widths)
            self.assertTrue(all(width == 3.0 for width in line_widths))

            preview.particles = [(0.0, 0.0, 0.0, 0, 0)]
            preview._draw_particles_2d(preview.particle_canvas,
                                       preview._palette(), 320, 200, None, 0.0)
            filled = [
                item for item in preview.particle_canvas.find_all()
                if preview.particle_canvas.type(item) == "oval"
                and preview.particle_canvas.itemcget(item, "fill")
            ]
            self.assertTrue(filled)
            coords = preview.particle_canvas.coords(filled[0])
            self.assertAlmostEqual(coords[2] - coords[0], 6.0)
        finally:
            root.destroy()


class DrumSpinPreviewTest(unittest.TestCase):
    def test_rotation_helper_uses_signed_right_hand_rule(self) -> None:
        center = (0.0, 0.0, 0.0)
        axis = (1.0, 0.0, 0.0)
        point = (0.0, -1.0, 0.0)

        forward = _rotate_about_axis(point, center, axis, 90.0)
        reverse = _rotate_about_axis(point, center, axis, -90.0)

        self.assertAlmostEqual(forward[1], 0.0, places=6)
        self.assertLess(forward[2], -0.99)
        self.assertAlmostEqual(reverse[1], 0.0, places=6)
        self.assertGreater(reverse[2], 0.99)

    def test_3d_preview_index_mark_follows_signed_spin(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_output_preview as preview_module

        root = tk.Tk()
        root.withdraw()
        try:
            parent = ttk.Frame(root)
            parent.pack()
            preview = preview_module.OutputPreview(parent, root, "", lambda: {})
            root.update()
            preview.config = {
                "drum_radius": 0.06,
                "inner_radius": 0.03,
                "drum_half_length": 0.03,
                "spin_axis": "X",
            }
            preview.particles = []

            def marker_segments(spin: float) -> list[tuple[float, ...]]:
                preview.particle_canvas.delete("all")
                preview._draw_particles_3d(
                    preview.particle_canvas, preview._palette(), 320, 200,
                    {"spin_deg": spin}, 0.06, False)
                return [tuple(preview.particle_canvas.coords(item))
                        for item in preview.particle_canvas.find_all()
                        if preview.particle_canvas.type(item) == "line"
                        and preview.particle_canvas.itemcget(item, "fill")
                        == DRUM_MARKER_COLOR]

            forward = marker_segments(90.0)
            reverse = marker_segments(-90.0)

            self.assertEqual(len(forward), 2)
            self.assertEqual(len(reverse), 2)
            self.assertNotEqual(forward, reverse)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
