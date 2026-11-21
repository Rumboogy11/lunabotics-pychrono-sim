#!/usr/bin/env python3
"""Regression tests for full-particle SPH containment in the fill detector."""

from __future__ import annotations

import math
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

import fill_detector
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


def world_to_local(world, origin, quat_wxyz):
    """Mirror compute_metrics' exact world-to-local pose transform."""
    rotation = crm.quat_rotation_matrix(crm.quat_conj(quat_wxyz))
    return (np.asarray(world, dtype=float) - np.asarray(origin, dtype=float)) @ rotation.T


def write_rect_box_obj(path: Path, half_x: float, half_y: float, half_z: float) -> None:
    """Write a watertight rectangular detector surface with duplicated vertices."""
    v = [
        (-half_x, -half_y, -half_z), (half_x, -half_y, -half_z),
        (half_x, half_y, -half_z), (-half_x, half_y, -half_z),
        (-half_x, -half_y, half_z), (half_x, -half_y, half_z),
        (half_x, half_y, half_z), (-half_x, half_y, half_z),
    ]
    faces = [
        (0, 2, 1), (0, 3, 2),  # bottom
        (4, 5, 6), (4, 6, 7),  # top
        (0, 1, 5), (0, 5, 4),  # -Y
        (1, 2, 6), (1, 6, 5),  # +X
        (2, 3, 7), (2, 7, 6),  # +Y
        (3, 0, 4), (3, 4, 7),  # -X
    ]
    with path.open("w", encoding="utf-8") as handle:
        for tri in faces:
            for index in tri:
                handle.write("v %.9g %.9g %.9g\n" % v[index])
        for i in range(len(faces)):
            handle.write("f %d %d %d\n" % (3 * i + 1, 3 * i + 2, 3 * i + 3))


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

    def metrics(self, locals_, drum=None, run=None):
        active = self.drum if drum is None else drum
        active_run = self.run if run is None else run
        positions = local_to_world(locals_, active.GetPos(), active.GetRot())
        return crm.compute_metrics(
            positions,
            active,
            active_run,
            self.motion,
            DEFAULT_SOIL,
            DEFAULT_SOIL.density * active_run.spacing**3,
            self.capacity,
            self.surface_z,
            with_flags=True,
        )

    def test_fill_follows_excavated_envelope(self):
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
        expected_excavated = [True, True, False, True, True, False, True]

        for index, excavated in enumerate(expected_excavated):
            with self.subTest(particle=index):
                # The fill flag and both fill ratios now use this same set.
                self.assertEqual(bool(flags[index, 0]), excavated)
                self.assertEqual(bool(flags[index, 1]), excavated)
        self.assertEqual(metrics["inside_drum"], sum(expected_excavated))
        self.assertEqual(metrics["excavated"], sum(expected_excavated))
        self.assertAlmostEqual(
            metrics["fill_ratio_mass"],
            sum(expected_excavated) * DEFAULT_SOIL.density * self.run.spacing**3 /
            (DEFAULT_SOIL.density * self.capacity),
        )
        # The capacity denominator is still the nominal, un-eroded cylinder.
        self.assertEqual(metrics["capacity_m3"], self.capacity)

    def test_fill_is_stable_while_the_drum_rotates(self):
        # A fixed soil particle enters and leaves a non-axisymmetric cavity once
        # per revolution.  The raw mesh test must actually alternate; the
        # excavation latch must still keep that particle counted while elevated.
        with tempfile.TemporaryDirectory(prefix="fill_rotation_", dir="/tmp/opencode") as tmp:
            detector_path = Path(tmp) / "rect.obj"
            write_rect_box_obj(detector_path, half_x=0.07, half_y=0.03, half_z=0.03)
            run = replace(self.run, fill_detector_obj=str(detector_path))
            detector = fill_detector.load_fill_detector(detector_path)
            self.assertIsNotNone(detector)
            assert detector is not None
            state = fill_detector.ExcavatedState()

            local0 = np.array([[0.045, 0.0, 0.0]])
            world = local_to_world(local0, self.origin, self.quat)
            expected_raw = [True, False, False, False, True, False]
            angles = (0.0, 45.0, 90.0, 135.0, 180.0, 270.0)
            drums = []
            for deg in angles:
                half = math.radians(deg) * 0.5
                spin = (math.cos(half), 0.0, 0.0, math.sin(half))
                drums.append(FakeDrum(self.origin, crm.quat_mul(self.quat, spin)))

            raw = [
                bool(detector.contains_with_margin(
                    world_to_local(world, drum.GetPos(), drum.GetRot()), self.rp)[0])
                for drum in drums
            ]
            self.assertEqual(raw, expected_raw, "raw mesh containment must rotate")

            for deg, drum, inside_mesh in zip(angles, drums, raw):
                with self.subTest(degrees=deg, inside_mesh=inside_mesh):
                    metrics, flags = crm.compute_metrics(
                        world, drum, run, self.motion, DEFAULT_SOIL,
                        DEFAULT_SOIL.density * run.spacing**3, self.capacity,
                        self.surface_z, with_flags=True, excavated_state=state,
                    )
                    np.testing.assert_array_equal(flags, np.ones((1, 2), dtype=np.uint8))
                    self.assertEqual(metrics["inside_drum"], 1)
                    self.assertEqual(metrics["excavated"], 1)

            # Settling back to the bed clears the latch; a later rotation cannot
            # resurrect a particle that is no longer elevated.
            settled = world.copy()
            settled[0, 2] = self.surface_z + self.rp - 1.0e-9
            metrics, flags = crm.compute_metrics(
                settled, drums[0], run, self.motion, DEFAULT_SOIL,
                DEFAULT_SOIL.density * run.spacing**3, self.capacity,
                self.surface_z, with_flags=True, excavated_state=state,
            )
            np.testing.assert_array_equal(flags, np.zeros((1, 2), dtype=np.uint8))
            self.assertEqual(metrics["inside_drum"], 0)
            self.assertEqual(metrics["excavated"], 0)

    def test_excavated_state_reset_and_shape_change(self):
        state = fill_detector.ExcavatedState()
        np.testing.assert_array_equal(
            state.update(np.array([True, False]), np.array([True, True])),
            np.array([True, False]),
        )
        # First capture is monotonic while elevated, even without mesh contact.
        np.testing.assert_array_equal(
            state.update(np.array([False, False]), np.array([True, True])),
            np.array([True, False]),
        )
        np.testing.assert_array_equal(
            state.update(np.array([True, True]), np.array([True, False])),
            np.array([True, False]),
        )
        state.reset()
        np.testing.assert_array_equal(
            state.update(np.array([True, True]), np.array([True, False])),
            np.array([True, False]),
        )

    def test_lifted_tolerance_stays_at_particle_radius(self):
        locals_ = np.array([[0.0, 0.0, 0.0]])
        below = FakeDrum((0.0, 0.0, self.surface_z + self.rp - 1.0e-9), self.quat)
        above = FakeDrum((0.0, 0.0, self.surface_z + self.rp + 1.0e-9), self.quat)

        metrics_below, flags_below = self.metrics(locals_, drum=below)
        metrics_above, flags_above = self.metrics(locals_, drum=above)

        self.assertEqual(metrics_below["lifted"], 0)
        self.assertEqual(metrics_above["lifted"], 1)
        for flag in (0, 1):
            self.assertEqual(int(flags_below[0, flag]), 0)
            self.assertEqual(int(flags_above[0, flag]), 1)
        self.assertEqual(metrics_below["fill_ratio_mass"], 0.0)
        self.assertGreater(metrics_above["fill_ratio_mass"], 0.0)


if __name__ == "__main__":
    unittest.main()
