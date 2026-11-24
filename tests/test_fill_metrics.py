#!/usr/bin/env python3
"""Regression tests for the excavator-tied analytic fill detector."""

from __future__ import annotations

import io
import json
import math
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(CRM_DIR))

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


class FillMetricTest(unittest.TestCase):
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
            [eff_inner + eps, 0.0, 0.0],                        # outside inner cavity, inside outer envelope
        ])

        metrics, flags = self.metrics(locals_)
        expected_excavated = [True, True, False, True, True, False, True]

        for index, excavated in enumerate(expected_excavated):
            with self.subTest(particle=index):
                # Fill and both flags use the same excavated predicate.
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

    def test_fill_envelope_follows_excavator_pose(self):
        # A point close to both the radial and axial eroded bounds is inside at
        # the reference pose but leaves when the drum tilts or translates away.
        local0 = np.array([[0.050, 0.0, 0.022]])
        origin = (0.0, 0.0, 0.15)
        world = local_to_world(local0, origin, (1.0, 0.0, 0.0, 0.0))
        s = math.sqrt(0.5)
        tilt_90x = (s, s, 0.0, 0.0)
        drums = (
            FakeDrum(origin, (1.0, 0.0, 0.0, 0.0)),
            FakeDrum(origin, tilt_90x),
            FakeDrum((0.2, 0.0, 0.15), (1.0, 0.0, 0.0, 0.0)),
        )
        expected = (1, 0, 0)

        for drum, inside in zip(drums, expected):
            with self.subTest(origin=drum.GetPos(), rot=drum.GetRot()):
                metrics, flags = crm.compute_metrics(
                    world, drum, self.run, self.motion, DEFAULT_SOIL,
                    DEFAULT_SOIL.density * self.run.spacing**3, self.capacity,
                    self.surface_z, with_flags=True,
                )
                np.testing.assert_array_equal(
                    flags, np.array([[inside, inside]], dtype=np.uint8))
                self.assertEqual(metrics["inside_drum"], inside)
                self.assertEqual(metrics["excavated"], inside)

    def test_fill_is_not_cleared_below_bed_surface(self):
        # Soil carried inside the detector should remain counted even when the
        # drum passes below the original bed surface. Lifted is still reported
        # as a diagnostic, but it no longer gates fill/excavated.
        locals_ = np.array([[0.0, 0.0, 0.0]])
        below = FakeDrum((0.0, 0.0, self.surface_z + self.rp - 1.0e-9), self.quat)
        above = FakeDrum((0.0, 0.0, self.surface_z + self.rp + 1.0e-9), self.quat)

        metrics_below, flags_below = self.metrics(locals_, drum=below)
        metrics_above, flags_above = self.metrics(locals_, drum=above)

        self.assertEqual(metrics_below["lifted"], 0)
        self.assertEqual(metrics_above["lifted"], 1)
        for flag in (0, 1):
            self.assertEqual(int(flags_below[0, flag]), 1)
            self.assertEqual(int(flags_above[0, flag]), 1)
        self.assertGreater(metrics_below["fill_ratio_mass"], 0.0)
        self.assertGreater(metrics_above["fill_ratio_mass"], 0.0)

    def test_auto_detector_follows_drum_dimensions(self):
        run = replace(
            self.run,
            detector_auto_size=True,
            detector_radius=0.02,
            detector_half_length=0.01,
        )
        self.assertEqual(
            run.detector_cylinder(),
            (self.run.drum_radius, self.run.drum_half_length),
        )

    def test_manual_detector_is_independent_of_drum_dimensions(self):
        run = replace(
            self.run,
            detector_auto_size=False,
            detector_radius=0.05,
            detector_half_length=0.035,
        )
        self.assertEqual(run.detector_cylinder(), (0.05, 0.035))

        # 0.048 m radial is inside the auto drum envelope but outside the
        # manual one; 0.024 m axial is outside the auto drum but inside the
        # longer manual detector.
        locals_ = np.array([
            [0.048, 0.0, 0.0],
            [0.035, 0.0, 0.0],
            [0.0, 0.0, 0.015],
            [0.0, 0.0, 0.024],
        ])
        _auto_metrics, auto_flags = self.metrics(locals_, run=self.run)
        _manual_metrics, manual_flags = self.metrics(locals_, run=run)
        np.testing.assert_array_equal(auto_flags[:, 0], [1, 1, 1, 0])
        np.testing.assert_array_equal(manual_flags[:, 0], [0, 1, 1, 1])
        np.testing.assert_array_equal(manual_flags[:, 1], manual_flags[:, 0])
        self.assertEqual(_manual_metrics["inside_drum"], 3)
        self.assertEqual(_manual_metrics["excavated"], 3)

    def test_detector_cli_auto_and_full_length_conversion(self):
        parser = crm.build_parser()
        auto = crm.make_run_params(parser.parse_args(["--omega", "3.14"]))
        self.assertEqual(
            auto.detector_cylinder(), (auto.drum_radius, auto.drum_half_length))

        manual = crm.make_run_params(parser.parse_args([
            "--omega", "3.14",
            "--no-detector-auto-size",
            "--detector-radius", "0.05",
            "--detector-length", "0.08",
        ]))
        self.assertEqual(manual.detector_radius, 0.05)
        self.assertEqual(manual.detector_half_length, 0.04)
        self.assertEqual(manual.detector_cylinder(), (0.05, 0.04))

    def test_manual_detector_cli_requires_manual_mode(self):
        args = crm.build_parser().parse_args([
            "--omega", "3.14", "--detector-radius", "0.05",
        ])
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            crm.make_run_params(args)

    def test_manual_detector_rejects_nonpositive_radius(self):
        args = crm.build_parser().parse_args([
            "--omega", "3.14", "--no-detector-auto-size",
            "--detector-radius", "-0.05",
        ])
        with self.assertRaisesRegex(ValueError, "detector_radius must be positive"):
            crm.make_run_params(args)

    def test_mesh_detector_cli_option_is_removed(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            crm.build_parser().parse_args([
                "--fill-detector-obj", "detector.obj",
            ])


def write_cube_obj(path: Path, half: float) -> None:
    """Write a closed, outward-wound cube centred on the drum-local origin."""
    vertices = [
        (-half, -half, -half), (half, -half, -half),
        (-half, half, -half), (half, half, -half),
        (-half, -half, half), (half, -half, half),
        (-half, half, half), (half, half, half),
    ]
    faces = [
        (0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4),
        (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5),
    ]
    lines = ["v {:.9f} {:.9f} {:.9f}".format(*v) for v in vertices]
    lines += ["f " + " ".join(str(i + 1) for i in face) for face in faces]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class CapacityOverlapTest(unittest.TestCase):
    def setUp(self):
        try:
            import pyvista  # noqa: F401
        except ImportError as exc:  # pragma: no cover - optional in minimal envs
            self.skipTest(f"pyvista unavailable: {exc}")
        self._tmp = tempfile.TemporaryDirectory(prefix="capacity_overlap_", dir="/tmp/opencode")
        self.mesh = Path(self._tmp.name) / "cube.obj"
        self.run = crm.CrmRunParams(
            spacing=0.002,
            inner_radius=0.5,
            drum_radius=0.6,
            drum_half_length=0.5,
        )
        self.motion = replace(
            MOTION_DEFAULT,
            drum_radius=self.run.drum_radius,
            drum_half_length=self.run.drum_half_length,
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_mesh_fully_inside_cavity_uses_mesh_volume(self):
        write_cube_obj(self.mesh, 0.1)
        volume = crm.excavator_overlap_volume(self.mesh, 0.5, 0.5)
        self.assertIsNotNone(volume)
        self.assertAlmostEqual(volume, 0.2**3, places=7)

    def test_cavity_fully_inside_mesh_uses_cylinder_volume(self):
        write_cube_obj(self.mesh, 2.0)
        volume = crm.excavator_overlap_volume(self.mesh, 0.5, 0.5)
        self.assertIsNotNone(volume)
        expected = math.pi * 0.5**2 * 1.0
        self.assertAlmostEqual(volume, expected, delta=0.01)

    def test_derived_capacity_subtracts_measured_overlap(self):
        write_cube_obj(self.mesh, 0.1)
        volume, details = crm.capacity_volume_from_mesh(self.run, self.motion, self.mesh)
        nominal = math.pi * self.run.inner_radius**2 * (2.0 * self.run.drum_half_length)
        self.assertAlmostEqual(details["excavator_overlap_m3"], 0.2**3, places=7)
        self.assertAlmostEqual(volume, nominal - 0.2**3, places=7)
        self.assertIn("excavator overlap", details["source"])

    def test_capacity_override_is_not_modified(self):
        write_cube_obj(self.mesh, 0.1)
        run = replace(self.run, capacity_volume=0.0123)
        volume, details = crm.capacity_volume_from_mesh(run, self.motion, self.mesh)
        self.assertEqual(volume, 0.0123)
        self.assertEqual(details["excavator_overlap_m3"], 0.0)
        self.assertIn("free volume", details["source"])

    def test_missing_mesh_keeps_nominal_capacity(self):
        volume, details = crm.capacity_volume_from_mesh(
            self.run, self.motion, self.mesh.parent / "missing.obj")
        nominal = math.pi * self.run.inner_radius**2 * (2.0 * self.run.drum_half_length)
        self.assertEqual(volume, nominal)
        self.assertIn("unavailable", details["source"])

    def test_config_records_capacity_provenance(self):
        write_cube_obj(self.mesh, 0.1)
        volume, details = crm.capacity_volume_from_mesh(self.run, self.motion, self.mesh)
        config_path = Path(self._tmp.name) / "config.json"
        crm.write_config(
            config_path, crm.DEFAULT_SOIL, self.run, self.mesh, self.motion,
            volume, 1.0, "test", ((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0)),
            capacity_details=details,
        )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        geometry = config["geometry"]
        self.assertAlmostEqual(geometry["capacity_volume_m3"], volume)
        self.assertAlmostEqual(geometry["excavator_overlap_m3"], 0.2**3, places=7)
        self.assertAlmostEqual(geometry["nominal_capacity_volume_m3"],
                               details["nominal_volume"])
        self.assertIn("excavator overlap", geometry["capacity_source"])


if __name__ == "__main__":
    unittest.main()
