#!/usr/bin/env python3
"""Regression tests for full-particle SPH containment in the fill detector."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace

import numpy as np

import lunar_excavator_crm as crm
from crm_soil import DEFAULT_SOIL
from lunar_excavator_config import DEFAULT as MOTION_DEFAULT


class FakeDrum:
    """Minimal drum exposing the Chrono pose accessors used by compute_metrics."""

    def __init__(self, origin, quat_wxyz):
        self._origin = tuple(float(v) for v in origin)
        self._quat = tuple(float(v) for v in quat_wxyz)

    def GetPos(self):
        return self._origin

    def GetRot(self):
        return self._quat


def local_to_world(local, origin, quat_wxyz):
    """Apply the inverse of compute_metrics' world-to-local pose transform."""
    rotation = crm.quat_rotation_matrix(quat_wxyz)
    return np.asarray(local, dtype=float) @ rotation.T + np.asarray(origin, dtype=float)


class FillDetectorContainmentTest(unittest.TestCase):
    def setUp(self):
        self.run = crm.CrmRunParams(
            spacing=0.02,
            inner_radius=0.05,
            drum_radius=0.061,
            drum_half_length=0.033,
        )
        self.motion = replace(
            MOTION_DEFAULT,
            drum_radius=self.run.drum_radius,
            drum_half_length=self.run.drum_half_length,
        )
        self.rp = 0.5 * self.run.spacing
        self.surface_z = 0.06
        self.origin = (0.005, -0.003, 0.15)
        # 90 degrees about global Y, matching the shipped mesh orientation.
        s = math.sqrt(0.5)
        self.quat = (s, 0.0, s, 0.0)  # (w, x, y, z)
        self.drum = FakeDrum(self.origin, self.quat)
        self.capacity = math.pi * self.run.inner_radius**2 * (2.0 * self.run.drum_half_length)

    def metrics(self, locals_, drum=None):
        active = self.drum if drum is None else drum
        positions = local_to_world(locals_, active.GetPos(), active.GetRot())
        return crm.compute_metrics(
            positions,
            active,
            self.run,
            self.motion,
            DEFAULT_SOIL,
            DEFAULT_SOIL.density * self.run.spacing**3,
            self.capacity,
            self.surface_z,
            with_flags=True,
        )

    def test_partly_inside_boundary_particles_are_rejected(self):
        eff_inner = self.run.inner_radius - self.rp
        eff_outer = self.run.drum_radius - self.rp
        eff_half = self.run.drum_half_length - self.rp
        eps = 1.0e-12

        # Nonzero translation and rotation ensure classification is done in the
        # drum-local frame, via the fake drum's GetPos()/GetRot().
        locals_ = np.array([
            [0.0, 0.0, 0.0],                                    # deep inside
            [self.run.inner_radius - 0.5 * self.rp, 0.0, 0.0],   # centre inside nominal cavity
            [0.0, 0.0, self.run.drum_half_length - 0.5 * self.rp],  # ditto, near endcap
            [eff_inner - eps, 0.0, eff_half - eps],             # fully inside eroded cavity
            [eff_outer - eps, 0.0, 0.0],                        # fully inside eroded outer envelope
            [self.run.drum_radius - 0.25 * self.rp, 0.0, 0.0],   # partly outside outer drum
            [eff_inner + eps, 0.0, 0.0],                        # just outside eroded inner wall
        ])

        metrics, flags = self.metrics(locals_)
        expected_inside = [True, False, False, True, False, False, False]
        expected_excavated = [True, True, False, True, True, False, True]

        for index, (inside, excavated) in enumerate(zip(expected_inside, expected_excavated)):
            with self.subTest(particle=index):
                self.assertEqual(bool(flags[index, 0]), inside)
                self.assertEqual(bool(flags[index, 1]), excavated)
        self.assertEqual(metrics["inside_drum"], sum(expected_inside))
        self.assertEqual(metrics["excavated"], sum(expected_excavated))
        # The capacity denominator is still the nominal, un-eroded cylinder.
        self.assertEqual(metrics["capacity_m3"], self.capacity)

    def test_lifted_tolerance_stays_at_particle_radius(self):
        locals_ = np.array([[0.0, 0.0, 0.0]])
        below = FakeDrum((0.0, 0.0, self.surface_z + self.rp - 1.0e-9), self.quat)
        above = FakeDrum((0.0, 0.0, self.surface_z + self.rp + 1.0e-9), self.quat)

        metrics_below, flags_below = self.metrics(locals_, drum=below)
        metrics_above, flags_above = self.metrics(locals_, drum=above)

        self.assertEqual(metrics_below["lifted"], 0)
        self.assertEqual(metrics_above["lifted"], 1)
        self.assertEqual(int(flags_below[0, 1]), 0)
        self.assertEqual(int(flags_above[0, 1]), 1)


if __name__ == "__main__":
    unittest.main()
