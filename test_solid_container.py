#!/usr/bin/env python3
"""Regression coverage for the fixed reflective CRM soil container."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(CRM_DIR))

import lunar_excavator_crm as crm  # noqa: E402
from lunar_excavator_config import DEFAULT as MOTION_DEFAULT  # noqa: E402


def make_run() -> crm.CrmRunParams:
    return crm.CrmRunParams(
        spacing=0.004,
        drum_radius=0.179095,
        drum_half_length=0.153176,
        bed_height=0.2,
        bed_padding=0.2,
        active_box_x=1.96518,
        active_box_y=2.01702,
        active_box_z=2.54256,
    )


def make_motion() -> object:
    return replace(
        MOTION_DEFAULT,
        t_max=60.0,
        settle_time=0.001,
        lower_speed=0.00440397,
        bed_height=0.2,
        bed_padding=0.2,
        drum_radius=0.179095,
        drum_half_length=0.153176,
    )


class SolidContainerTest(unittest.TestCase):
    def test_bed_particle_bounds_use_snapped_grid(self) -> None:
        run = make_run()
        (min_x, min_y, min_z), (max_x, max_y, max_z) = crm.bed_particle_bounds(run, make_motion())
        self.assertAlmostEqual(min_x, -0.188995)
        self.assertAlmostEqual(max_x, -0.188995 + 94 * run.spacing)
        self.assertAlmostEqual(min_y, -0.214914)
        self.assertAlmostEqual(max_y, -0.214914 + 107 * run.spacing)
        self.assertAlmostEqual(min_z, 0.0)
        self.assertAlmostEqual(max_z, 0.2)

    def test_container_gap_clears_bed_bce_layers(self) -> None:
        run = make_run()
        padding = crm.solid_container_padding(run)
        self.assertAlmostEqual(padding, 0.5 * run.spacing)
        self.assertGreater(padding, 0.0)
        self.assertLess(padding, run.spacing)

        motion = make_motion()
        (pmin_x, pmin_y, pmin_z), (pmax_x, pmax_y, pmax_z) = crm.bed_particle_bounds(run, motion)
        (min_x, min_y, min_z), (max_x, max_y, max_z) = crm.fit_solid_container(run, motion)
        self.assertAlmostEqual(min_x, pmin_x - padding)
        self.assertAlmostEqual(max_x, pmax_x + padding)
        self.assertAlmostEqual(min_y, pmin_y - padding)
        self.assertAlmostEqual(max_y, pmax_y + padding)
        self.assertAlmostEqual(min_z, pmin_z - padding)

    def test_lid_sits_below_active_box_ceiling_and_above_drum(self) -> None:
        run = make_run()
        motion = make_motion()
        _, (_, _, lid_z) = crm.fit_solid_container(run, motion)
        lowest_center = min(motion.drum_center_z0, motion.z_target)
        self.assertLess(lid_z, lowest_center + 0.5 * run.active_box_z)
        self.assertGreater(lid_z, motion.drum_center_z0 + run.drum_radius + 2.0 * run.spacing)

    def test_computational_domain_encloses_container_bce(self) -> None:
        run = make_run()
        motion = make_motion()
        container = crm.fit_solid_container(run, motion)
        (cmin_x, cmin_y, cmin_z), (cmax_x, cmax_y, cmax_z) = container
        (min_x, min_y, min_z), (max_x, max_y, max_z) = crm.fit_computational_domain(
            run, motion, container
        )
        bce_reach = 3.0 * run.spacing
        self.assertLessEqual(min_x, cmin_x - bce_reach)
        self.assertLessEqual(min_y, cmin_y - bce_reach)
        self.assertLessEqual(min_z, cmin_z - bce_reach)
        self.assertGreaterEqual(max_x, cmax_x + bce_reach)
        self.assertGreaterEqual(max_y, cmax_y + bce_reach)
        self.assertGreaterEqual(max_z, cmax_z + bce_reach)

    def test_explicit_container_padding_is_used(self) -> None:
        args = crm.build_parser().parse_args(["--container-padding", "0.025"])
        run = crm.make_run_params(args)
        self.assertEqual(run.container_padding, 0.025)
        self.assertAlmostEqual(crm.solid_container_padding(run), 0.025)


if __name__ == "__main__":
    unittest.main()
