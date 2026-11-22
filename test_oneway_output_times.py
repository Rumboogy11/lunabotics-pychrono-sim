#!/usr/bin/env python3
"""Regression coverage for 1 ms one-way FEA output timestamps."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import crm.lunar_excavator_oneway as oneway  # noqa: E402


class OneWayOutputTimeTest(unittest.TestCase):
    def test_output_targets_use_one_millisecond_grid(self) -> None:
        targets: list[float] = []
        index = 1
        after = 0.0
        for _ in range(6):
            index, target = oneway.next_output_time(index, 60.0, after)
            targets.append(target)
            after = target

        self.assertEqual(targets, [0.017, 0.033, 0.050, 0.067, 0.083, 0.100])
        self.assertTrue(all(round(t, 3) == t for t in targets))

    def test_stepping_stops_at_output_target_instead_of_overshooting(self) -> None:
        t = 0.0
        for target, expected_dt in (
            (0.017, 0.005), (0.017, 0.005), (0.017, 0.005), (0.017, 0.002),
            (0.033, 0.005), (0.033, 0.005), (0.033, 0.005), (0.033, 0.001),
        ):
            t, h = oneway.step_to_target(t, target, 0.005)
            self.assertAlmostEqual(h, expected_dt, places=12)
            self.assertLessEqual(t, target + 1.0e-12)
        self.assertAlmostEqual(t, 0.033, places=12)

    def test_high_fps_targets_still_advance(self) -> None:
        index, first = oneway.next_output_time(1, 5000.0, 0.0)
        self.assertEqual(first, 0.001)
        next_index, second = oneway.next_output_time(index + 1, 5000.0, first)
        self.assertEqual(second, 0.002)
        self.assertGreater(next_index, index)

    def test_nonpositive_fps_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            oneway.output_target_time(1, 0.0)


class OneWayDurationLimitTest(unittest.TestCase):
    def test_requested_duration_is_used_when_crm_frames_cover_it(self) -> None:
        for requested, last_frame in ((0.5, 0.5), (0.2, 0.5)):
            with self.subTest(requested=requested, last_frame=last_frame):
                duration, notice = oneway.fea_duration_from_crm_frames(
                    requested, last_frame, 5
                )
                self.assertAlmostEqual(duration, requested)
                self.assertIsNone(notice)

    def test_run_is_clamped_to_last_crm_frame_without_failing(self) -> None:
        duration, notice = oneway.fea_duration_from_crm_frames(0.5, 0.2, 5)

        self.assertAlmostEqual(duration, 0.2)
        self.assertIsNotNone(notice)
        self.assertIn("limited", notice)
        self.assertIn("CRM load frames", notice)
        self.assertIn("5 samples", notice)
        self.assertIn("0.2 s", notice)
        self.assertIn("0.5 s", notice)
        self.assertIn("not a failure", notice)

    def test_tiny_float_gap_is_not_treated_as_missing_frames(self) -> None:
        duration, notice = oneway.fea_duration_from_crm_frames(0.2, 0.2 - 1.0e-13, 5)

        self.assertAlmostEqual(duration, 0.2, places=12)
        self.assertIsNone(notice)


if __name__ == "__main__":
    unittest.main()
