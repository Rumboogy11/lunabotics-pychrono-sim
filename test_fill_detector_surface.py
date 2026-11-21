#!/usr/bin/env python3
"""Regression coverage for the optional STEP-derived fill detector surface."""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(CRM_DIR))

import fill_detector  # noqa: E402
import lunar_excavator_crm as crm  # noqa: E402
from crm_soil import DEFAULT_SOIL  # noqa: E402
from lunar_excavator_config import DEFAULT as MOTION_DEFAULT  # noqa: E402


def write_box_obj(path: Path, half: float = 1.0) -> None:
    """Write a cube OBJ with duplicated vertices, like stl_to_obj.py."""
    v = [
        (-half, -half, -half), (half, -half, -half), (half, half, -half), (-half, half, -half),
        (-half, -half, half), (half, -half, half), (half, half, half), (-half, half, half),
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
        handle.write("o test_box\n")
        for tri in faces:
            for index in tri:
                handle.write("v %.9g %.9g %.9g\n" % v[index])
        for i in range(len(faces)):
            handle.write("f %d %d %d\n" % (3 * i + 1, 3 * i + 2, 3 * i + 3))


def brute_triangle_distance2(points: np.ndarray, tri: np.ndarray) -> np.ndarray:
    """Independent per-triangle reference for the vectorized BVH distance."""
    result = np.empty(len(points), dtype=float)
    for i, point in enumerate(points):
        best = float("inf")
        for a, b, c in tri:
            ab, ac = b - a, c - a
            normal = np.cross(ab, ac)
            n2 = float(np.dot(normal, normal))
            ap = point - a
            candidates = []
            if n2 > 1.0e-30:
                h = float(np.dot(ap, normal)) / n2
                d00, d01, d11 = np.dot(ab, ab), np.dot(ab, ac), np.dot(ac, ac)
                denom = d00 * d11 - d01 * d01
                if abs(denom) > 1.0e-30:
                    u = (d11 * np.dot(ap, ab) - d01 * np.dot(ap, ac)) / denom
                    v = (d00 * np.dot(ap, ac) - d01 * np.dot(ap, ab)) / denom
                    if u >= -1e-12 and v >= -1e-12 and u + v <= 1.0 + 1e-12:
                        candidates.append(a + u * ab + v * ac)
            for start, end in ((a, b), (a, c), (b, c)):
                edge = end - start
                e2 = float(np.dot(edge, edge))
                t = 0.0 if e2 <= 1.0e-30 else float(np.dot(point - start, edge)) / e2
                candidates.append(start + np.clip(t, 0.0, 1.0) * edge)
            best = min(best, min(float(np.dot(point - q, point - q)) for q in candidates))
        result[i] = best
    return result


class FillDetectorSurfaceTest(unittest.TestCase):
    def test_ray_parity_and_full_particle_margin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fill_obj_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "detector.obj"
            write_box_obj(path)
            detector = fill_detector.load_fill_detector(path)
            self.assertIsNotNone(detector)
            assert detector is not None

            points = np.array([
                [0.0, 0.0, 0.0],      # deep inside
                [0.9, 0.0, 0.0],      # inside centre, but not a whole 0.2 m sphere
                [1.1, 0.0, 0.0],      # outside
                [0.0, 0.0, 0.85],     # centre inside, but too close to the axial wall
            ])
            np.testing.assert_array_equal(
                detector.contains_with_margin(points, 0.0),
                np.array([True, True, False, True]),
            )
            np.testing.assert_array_equal(
                detector.contains_with_margin(points, 0.2),
                np.array([True, False, False, False]),
            )

    def test_bvh_distances_match_brute_force(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fill_dist_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "detector.obj"
            write_box_obj(path, half=0.25)
            detector = fill_detector.load_fill_detector(path)
            assert detector is not None
            points = np.array([
                [0.0, 0.0, 0.0],
                [0.1, -0.2, 0.24],
                [0.26, 0.0, 0.0],
                [0.0, 0.0, 0.1],
                [-0.24, 0.24, -0.24],
            ])
            actual = detector.bvh._point_triangle_dist2(
                points, detector.bvh.tri[:, 0], detector.bvh.tri[:, 1], detector.bvh.tri[:, 2]
            ).min(axis=1)
            expected = brute_triangle_distance2(points, detector.bvh.tri)
            np.testing.assert_allclose(np.sqrt(actual), np.sqrt(expected), rtol=1e-10, atol=1e-12)

    def test_default_analytic_detector_is_unchanged(self) -> None:
        run = crm.CrmRunParams(
            spacing=0.02,
            inner_radius=0.05,
            drum_radius=0.061,
            drum_half_length=0.033,
            capacity_volume=1.0,
        )
        motion = replace(
            MOTION_DEFAULT,
            drum_radius=run.drum_radius,
            drum_half_length=run.drum_half_length,
        )
        positions = np.array([
            [0.0, 0.0, 0.0],
            [0.035, 0.0, 0.0],
            [0.049, 0.0, 0.0],
        ])
        metrics, flags = crm.compute_metrics(
            positions,
            type("Drum", (), {"GetPos": lambda self: (0.0, 0.0, 0.0),
                              "GetRot": lambda self: (1.0, 0.0, 0.0, 0.0)})(),
            run,
            motion,
            DEFAULT_SOIL,
            DEFAULT_SOIL.density * run.spacing**3,
            1.0,
            0.06,
            with_flags=True,
        )
        # Without a detector the original eroded-cylinder rule still applies:
        # rp=0.01 leaves a 0.04 m eroded cavity radius, so 0.049 m is rejected.
        np.testing.assert_array_equal(flags[:, 0], np.array([1, 1, 0]))
        self.assertEqual(metrics["sph_particles"], 3)
        self.assertEqual(metrics["capacity_m3"], 1.0)

    def test_non_watertight_surface_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fill_bad_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "detector.obj"
            path.write_text(
                "v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(fill_detector.FillSurfaceError, "watertight"):
                fill_detector.load_fill_detector(path)

    def test_compute_metrics_uses_detector_without_changing_counts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fill_metrics_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "detector.obj"
            write_box_obj(path, half=0.05)

            run = crm.CrmRunParams(
                spacing=0.02,
                inner_radius=0.05,
                drum_radius=0.061,
                drum_half_length=0.033,
                capacity_volume=1.0,
                fill_detector_obj=str(path),
            )
            motion = replace(
                MOTION_DEFAULT,
                drum_radius=run.drum_radius,
                drum_half_length=run.drum_half_length,
            )
            positions = np.array([[0.0, 0.0, 0.0], [0.049, 0.0, 0.0]])
            metrics, flags = crm.compute_metrics(
                positions,
                type("Drum", (), {"GetPos": lambda self: (0.0, 0.0, 0.0),
                                  "GetRot": lambda self: (1.0, 0.0, 0.0, 0.0)})(),
                run,
                motion,
                DEFAULT_SOIL,
                DEFAULT_SOIL.density * run.spacing**3,
                1.0,
                0.06,
                with_flags=True,
            )
            # The analytic cylinder would accept both centres.  The detector's
            # 0.01 m full-particle margin rejects the one near its wall.
            self.assertEqual(metrics["sph_particles"], 2)
            np.testing.assert_array_equal(flags[:, 0], np.array([1, 0]))
            self.assertEqual(metrics["capacity_m3"], 1.0)


if __name__ == "__main__":
    unittest.main()
