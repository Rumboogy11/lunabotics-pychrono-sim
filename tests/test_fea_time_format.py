#!/usr/bin/env python3
"""Regression coverage for millisecond FEA summary timestamps."""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import full_model  # noqa: E402
import study_wizard as wiz  # noqa: E402


class FeaTimeFormatTest(unittest.TestCase):
    def test_combined_summary_rounds_fea_final_time_to_milliseconds(self) -> None:
        report = {
            "crm": {"status": "completed", "final_metrics": {"time_s": 0.123456}},
            "fea": {"status": "completed", "final_metrics": {"time_s": 0.0206}},
        }
        with tempfile.TemporaryDirectory(prefix="fea_time_report_", dir="/tmp/opencode") as tmp:
            out_dir = Path(tmp)
            full_model.write_reports(out_dir, report)
            with (out_dir / "combined_summary.csv").open(newline="", encoding="utf-8") as handle:
                rows = {row["model"]: row for row in csv.DictReader(handle)}
            self.assertEqual(rows["crm"]["final_time_s"], "0.123456")
            self.assertEqual(rows["fea"]["final_time_s"], "0.021")

    def test_wizard_result_summary_rounds_fea_time_to_milliseconds(self) -> None:
        crm_row = {
            "time_s": "0.123456", "fill_ratio_mass": "0.5",
            "inside_drum": "1", "excavated": "1", "work_j": "1",
        }
        fea_row = {
            "time_s": "0.0206", "max_deformation_m": "1e-05",
            "max_von_mises_pa": "2000000", "work_j": "1",
        }

        def last_row(path: Path) -> dict[str, str] | None:
            if path.parent.name == "crm":
                return crm_row
            if path.parent.name == "fea":
                return fea_row
            return None

        with tempfile.TemporaryDirectory(prefix="fea_time_wizard_", dir="/tmp/opencode") as tmp:
            cfg = {"output_dir": Path(tmp)}
            stdout = io.StringIO()
            with patch.object(wiz, "last_csv_row", side_effect=last_row), \
                 patch.object(wiz.subprocess, "run", return_value=SimpleNamespace(
                     returncode=1, stdout="", stderr="")), \
                 redirect_stdout(stdout):
                wiz.print_results(cfg, offer_open=False)
            self.assertIn("FEA: t=0.021 s", stdout.getvalue())
            self.assertNotIn("FEA: t=0.0206 s", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
