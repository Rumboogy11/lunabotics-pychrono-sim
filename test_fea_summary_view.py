#!/usr/bin/env python3
"""Regression coverage for the lightweight ParaView FEA summary path."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import fea_summary  # noqa: E402


RAW_SUMMARY = """frame,time_s,wall_s,max_deformation_m,max_von_mises_pa,applied_force_n,work_j
0,0,0.5,0,0,0,0
1,0.02,1.0,2e-5,4500000,12.5,0.75
2,0.04,1.5,nan,3000000,15.0,1.5
"""


class FeaSummaryTest(unittest.TestCase):
    def test_derived_csv_scales_units_and_skips_nonfinite_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fea_summary_", dir="/tmp/opencode") as tmp:
            root = Path(tmp)
            raw = root / "raw.csv"
            out = root / "fea_summary.csv"
            raw.write_text(RAW_SUMMARY, encoding="utf-8")

            self.assertTrue(fea_summary.write_fea_summary_csv(raw, out))
            header, rows = fea_summary.read_summary_rows(out)
            self.assertIn("max_von_mises_MPa", header)
            self.assertIn("max_deformation_mm", header)
            self.assertNotIn("max_von_mises_pa", header)
            self.assertEqual(len(rows), 3)
            self.assertEqual(float(rows[0]["time_s"]), 0.0)
            self.assertAlmostEqual(float(rows[1]["max_von_mises_MPa"]), 4.5)
            self.assertAlmostEqual(float(rows[1]["max_deformation_mm"]), 0.02)
            self.assertAlmostEqual(float(rows[1]["work_j"]), 0.75)
            self.assertEqual(rows[2]["max_deformation_mm"], "")
            self.assertAlmostEqual(float(rows[2]["max_von_mises_MPa"]), 3.0)
            self.assertAlmostEqual(float(rows[2]["work_j"]), 1.5)
            self.assertEqual([row["time_s"] for row in rows], ["0.000", "0.020", "0.040"])

    def test_derived_csv_rounds_fea_time_to_milliseconds(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fea_time_", dir="/tmp/opencode") as tmp:
            root = Path(tmp)
            raw = root / "raw.csv"
            out = root / "fea_summary.csv"
            raw.write_text(
                RAW_SUMMARY.replace("1,0.02,", "1,0.0206,")
                .replace("2,0.04,", "2,0.0048,"),
                encoding="utf-8",
            )

            self.assertTrue(fea_summary.write_fea_summary_csv(raw, out))
            _header, rows = fea_summary.read_summary_rows(out)
            self.assertEqual([row["time_s"] for row in rows], ["0.000", "0.021", "0.005"])

    def test_ensure_summary_uses_manifest_study_dir(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fea_manifest_", dir="/tmp/opencode") as tmp:
            study = Path(tmp) / "study"
            export = study / "paraview"
            (study / "fea").mkdir(parents=True)
            export.mkdir(parents=True)
            (study / "fea" / "summary.csv").write_text(RAW_SUMMARY, encoding="utf-8")
            manifest = {"study_dir": str(study), "series": {}}

            found = fea_summary.ensure_fea_summary_csv(export, manifest)
            self.assertIsNotNone(found)
            self.assertEqual(found.name, "fea_summary.csv")
            self.assertEqual(found.parent, export)
            self.assertTrue(fea_summary.is_summary_table(found))

    def test_series_selection_default_and_override(self) -> None:
        available = ("time_s", "frame", "max_von_mises_MPa",
                     "max_deformation_mm", "applied_force_n", "work_j")
        default = fea_summary.choose_fea_series(available)
        self.assertEqual(default[0], "max_von_mises_MPa")
        self.assertIn("max_deformation_mm", default)
        self.assertNotIn("frame", default)
        self.assertEqual(fea_summary.choose_fea_series(
            available, "work_j,unknown,applied_force_n"), ["work_j", "applied_force_n"])

    def test_viewer_exposes_fea_series_argument(self) -> None:
        import view_study_paraview as viewer

        args = viewer._parse_args([
            "view_study_paraview.py", "/tmp/opencode/export",
            "--fea-series", "max_von_mises_MPa,work_j",
        ])
        self.assertEqual(args.fea_series, "max_von_mises_MPa,work_j")
        self.assertFalse(args.no_fea)

    def test_fea_history_charts_are_opt_in(self) -> None:
        import view_study_paraview as viewer

        with patch.dict(os.environ, {"PYCHRONO_FEA_CHARTS": ""}):
            args = viewer._parse_args([
                "view_study_paraview.py", "/tmp/opencode/export",
            ])
        self.assertFalse(args.show_fea_charts)

        with patch.dict(os.environ, {"PYCHRONO_FEA_CHARTS": "1"}):
            args = viewer._parse_args([
                "view_study_paraview.py", "/tmp/opencode/export",
            ])
        self.assertTrue(args.show_fea_charts)

    def test_fea_series_still_parses_without_enabling_charts(self) -> None:
        import view_study_paraview as viewer

        with patch.dict(os.environ, {"PYCHRONO_FEA_CHARTS": ""}):
            args = viewer._parse_args([
                "view_study_paraview.py", "/tmp/opencode/export",
                "--no-fea", "--fea-series", "max_von_mises_MPa,work_j",
            ])
        self.assertTrue(args.no_fea)
        self.assertFalse(args.show_fea_charts)
        self.assertEqual(args.fea_series, "max_von_mises_MPa,work_j")

    def test_gui_environment_enables_3d_fea_overlay(self) -> None:
        import view_study_paraview as viewer

        with patch.dict(os.environ, {"PYCHRONO_WITH_FEA": "1"}):
            args = viewer._parse_args([
                "view_study_paraview.py", "/tmp/opencode/export",
            ])
        self.assertTrue(args.with_fea)

    def test_one_frame_t_zero_pvd_is_rejected(self) -> None:
        import view_study_paraview as viewer

        with tempfile.TemporaryDirectory(prefix="fea_pvd_", dir="/tmp/opencode") as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            frame = frames / "frame_000000.vtu"
            frame.write_text("<VTKFile/>", encoding="utf-8")
            pvd = root / "fea.pvd"
            pvd.write_text(
                '<?xml version="1.0"?><VTKFile type="Collection"><Collection>'
                '<DataSet timestep="0" file="frames/frame_000000.vtu"/>'
                '</Collection></VTKFile>',
                encoding="utf-8",
            )
            valid, reason = viewer.validate_fea_pvd_file(pvd)
            self.assertFalse(valid)
            self.assertIn("incomplete", reason)

            # A stale manifest must not rescue a one-frame t=0 PVD.
            found, status = viewer.find_valid_fea_pvd(
                root, {"fea_valid": False, "fea_status": "incomplete FEA export"})
            self.assertIsNone(found)
            self.assertIn("incomplete", status)

            pvd.write_text(
                '<?xml version="1.0"?><VTKFile type="Collection"><Collection>'
                '<DataSet timestep="0" file="frames/frame_000000.vtu"/>'
                '<DataSet timestep="0.1" file="frames/frame_000001.vtu"/>'
                '</Collection></VTKFile>',
                encoding="utf-8",
            )
            (frames / "frame_000001.vtu").write_text("<VTKFile/>", encoding="utf-8")
            valid, reason = viewer.validate_fea_pvd_file(pvd)
            self.assertTrue(valid)
            self.assertIn("2 frames", reason)

            # A stale manifest must also not hide a real multi-frame source.
            found, status = viewer.find_valid_fea_pvd(
                root, {"fea_valid": False, "fea_status": "incomplete FEA export"})
            self.assertEqual(found, pvd.resolve())
            self.assertIn("2 frames", status)
            self.assertIn("manifest marked invalid", status)


if __name__ == "__main__":
    unittest.main()
