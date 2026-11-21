#!/usr/bin/env python3
"""Regression coverage for measured STEP exterior-fit tolerance."""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_PY = ROOT / "chrono-env" / "bin" / "python"


@unittest.skipUnless(ENV_PY.is_file(), "chrono-env Python is not installed")
class SurfaceFitMeasurementTest(unittest.TestCase):
    def test_parser_default_and_bidirectional_error(self) -> None:
        code = r"""
import json
import numpy as np
import step_to_chrono_fea as mesher

parser = mesher.build_parser()
args = parser.parse_args(["model.step"])
assert args.surface_fit_tolerance == 0.001

raw_points = np.array([[2, 0, 0], [3, 0, 0], [3, 1, 0], [2, 1, 0]], dtype=float)
raw_faces = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.int32)
candidate_points = raw_points.copy()
candidate_points[1, 2] = 0.002
raw_mesh = mesher.polydata_from_triangles(raw_points, raw_faces)

result = mesher.measure_exterior_fit(
    raw_points, raw_faces, raw_mesh, candidate_points, raw_faces,
    axis=2, exterior_radius_cad=0.1, unit_scale=1.0, tolerance_m=0.001,
)
print(json.dumps({
    "default": args.surface_fit_tolerance,
    "error": result["error_m"],
    "passed": result["passed"],
    "c2b": result["candidate_to_raw"]["max_m"],
    "b2c": result["raw_to_candidate"]["max_m"],
}))
"""
        run = subprocess.run(
            [str(ENV_PY), "-c", code], cwd=ROOT, capture_output=True,
            text=True, timeout=120,
        )
        self.assertEqual(run.returncode, 0, run.stderr)
        data = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(data["default"], 0.001)
        self.assertFalse(data["passed"])
        self.assertGreater(data["error"], 0.0015)
        self.assertGreater(data["c2b"], 0.0015)
        self.assertGreater(data["b2c"], 0.0015)


if __name__ == "__main__":
    unittest.main()
