#!/usr/bin/env python3
"""Regression coverage for stale CRM particle frame cleanup."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(CRM_DIR))

import lunar_excavator_crm as crm  # noqa: E402


class ParticleOutputCleanupTest(unittest.TestCase):
    def test_removes_only_particle_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            for name in ("particles_000000.csv", "particles_000097.csv", "particles_123.csv"):
                (out_dir / name).write_text("old", encoding="utf-8")
            (out_dir / "summary.csv").write_text("keep", encoding="utf-8")
            (out_dir / "soil_loads.csv").write_text("keep", encoding="utf-8")
            (out_dir / "config.json").write_text("{}", encoding="utf-8")

            removed = crm.remove_stale_particle_frames(out_dir)

            self.assertEqual(removed, 3)
            self.assertEqual(list(out_dir.glob("particles_*.csv")), [])
            self.assertEqual((out_dir / "summary.csv").read_text(encoding="utf-8"), "keep")
            self.assertEqual((out_dir / "soil_loads.csv").read_text(encoding="utf-8"), "keep")
            self.assertEqual((out_dir / "config.json").read_text(encoding="utf-8"), "{}")

    def test_parser_exposes_opt_out(self) -> None:
        args = crm.build_parser().parse_args(["--save-particles", "--keep-old-particles"])
        self.assertTrue(args.save_particles)
        self.assertTrue(args.keep_old_particles)


if __name__ == "__main__":
    unittest.main()
