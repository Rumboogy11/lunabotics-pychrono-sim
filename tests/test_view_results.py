#!/usr/bin/env python3
"""Regression tests for the four-panel interactive result plots."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from view_results import IsotimeCursor, build_summary_figure

CRM_ROWS = [
    {"time_s": "0", "work_j": "0", "fill_ratio_mass": "0.1"},
    {"time_s": "1", "work_j": "5", "fill_ratio_mass": "0.4"},
]
FEA_ROWS = [
    {"time_s": "0", "max_von_mises_pa": "1000000", "max_deformation_m": "0.001"},
    {"time_s": "1", "max_von_mises_pa": "2000000", "max_deformation_m": "0.002"},
]


class SummaryFigureTest(unittest.TestCase):
    def tearDown(self) -> None:
        plt.close("all")

    def test_creates_four_panels_with_split_fill_and_deformation(self) -> None:
        _figure, axes = build_summary_figure(CRM_ROWS, FEA_ROWS)
        self.assertEqual(len(axes), 4)
        self.assertTrue(all(ax.get_visible() for ax in axes))
        self.assertIn("fill ratio", axes[1].get_title())
        self.assertIn("peak deformation", axes[3].get_title())
        labels = [line.get_label() for ax in axes for line in ax.lines]
        self.assertIn("fill ratio", labels)
        self.assertFalse(any("mass" in label for label in labels))

    def test_missing_stage_hides_only_its_two_panels(self) -> None:
        _figure, axes = build_summary_figure(CRM_ROWS, [])
        self.assertEqual(len(axes), 4)
        self.assertTrue(axes[0].get_visible())
        self.assertTrue(axes[1].get_visible())
        self.assertFalse(axes[2].get_visible())
        self.assertFalse(axes[3].get_visible())

    def test_interactive_figure_keeps_cursor_alive(self) -> None:
        figure, _axes = build_summary_figure(CRM_ROWS, FEA_ROWS, interactive=True)
        cursor = getattr(figure, "isotime_cursor", None)
        self.assertIsInstance(cursor, IsotimeCursor)
        self.assertEqual(len(cursor.vlines), 4)
        self.assertEqual(len(cursor.hlines), 4)
        self.assertEqual(len(cursor.markers), 4)


class IsotimeCursorTest(unittest.TestCase):
    def tearDown(self) -> None:
        plt.close("all")

    def test_crosshair_matches_each_curve_value(self) -> None:
        figure, _axes = build_summary_figure(CRM_ROWS, FEA_ROWS, interactive=True)
        figure.canvas.draw()  # triggers background caching for blitting
        cursor = figure.isotime_cursor
        self.assertTrue(cursor._blit_ok)
        self.assertEqual(len(cursor._backgrounds), 4)
        self.assertTrue(all(
            artist.get_animated()
            for artists in cursor._overlay for artist in artists
        ))
        self.assertTrue(all(not line.get_visible() for line in cursor.hlines))

        cursor.update(0.5)
        expected = (2.5, 0.25, 1.5, 1.5)  # FEA values are plotted in MPa and mm
        for vline, hline, marker, label, value in zip(
            cursor.vlines, cursor.hlines, cursor.markers, cursor.labels, expected,
        ):
            self.assertTrue(vline.get_visible())
            self.assertEqual(tuple(vline.get_xdata()), (0.5, 0.5))
            self.assertTrue(hline.get_visible())
            self.assertAlmostEqual(hline.get_ydata()[0], value)
            self.assertTrue(marker.get_visible())
            self.assertAlmostEqual(marker.get_xdata()[0], 0.5)
            self.assertAlmostEqual(marker.get_ydata()[0], value)
            self.assertIn("t = 0.5 s", label.get_text())

        cursor.hide()
        self.assertTrue(all(not line.get_visible() for line in cursor.vlines))
        self.assertTrue(all(not line.get_visible() for line in cursor.hlines))
        self.assertTrue(all(not marker.get_visible() for marker in cursor.markers))

    def test_update_blits_every_visible_axes(self) -> None:
        figure, axes = build_summary_figure(CRM_ROWS, FEA_ROWS, interactive=True)
        figure.canvas.draw()
        cursor = figure.isotime_cursor
        blitted = []
        original_blit = figure.canvas.blit
        figure.canvas.blit = lambda bbox: blitted.append(bbox)
        try:
            cursor.update(0.5)
        finally:
            figure.canvas.blit = original_blit
        self.assertEqual(len(blitted), 4)
        for axis, bbox in zip(axes, blitted):
            self.assertEqual(tuple(bbox.bounds), tuple(axis.bbox.bounds))

    def test_empty_panel_has_no_horizontal_line(self) -> None:
        figure, _axes = build_summary_figure(CRM_ROWS, [], interactive=True)
        cursor = figure.isotime_cursor
        cursor.update(0.5)
        self.assertTrue(cursor.vlines[2].get_visible())
        self.assertFalse(cursor.hlines[2].get_visible())
        self.assertFalse(cursor.markers[2].get_visible())


if __name__ == "__main__":
    unittest.main()
