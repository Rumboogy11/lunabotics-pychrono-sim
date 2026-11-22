#!/usr/bin/env python3
"""Regression coverage for keeping user geometry out of installs."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class NoBundledGeometryTest(unittest.TestCase):
    def test_fea_assets_are_not_tracked(self) -> None:
        tracked = subprocess.run(
            ["git", "ls-files", "fea"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(tracked, "")

    def test_installer_does_not_generate_reference_meshes(self) -> None:
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertNotIn("prepare_reference_geometry", installer)
        self.assertNotIn("fea_contact", installer)

    def test_no_built_in_fea_mesh_defaults(self) -> None:
        import study_wizard as wiz

        self.assertIsNone(wiz.DEFAULT_FEA_NODE)
        self.assertIsNone(wiz.DEFAULT_FEA_ELE)


if __name__ == "__main__":
    unittest.main()
