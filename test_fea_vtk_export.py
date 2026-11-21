#!/usr/bin/env python3
"""Regression coverage for opt-in full 3D FEA VTU export."""

from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "crm"))

import lunar_excavator_oneway as oneway  # noqa: E402


class FeaVtkExportTest(unittest.TestCase):
    def test_driver_defaults_to_scalar_only_and_accepts_explicit_toggle(self) -> None:
        parser = oneway.build_parser()
        base = ["--loads", "Loads.csv"]
        default = parser.parse_args(base)
        self.assertFalse(default.export_vtk)
        self.assertEqual(default.vtk_stride, 1)

        enabled = parser.parse_args(base + ["--export-vtk", "--vtk-stride", "4"])
        self.assertTrue(enabled.export_vtk)
        self.assertEqual(enabled.vtk_stride, 4)

        disabled = parser.parse_args(base + ["--export-vtk", "--no-export-vtk"])
        self.assertFalse(disabled.export_vtk)

    def test_read_tetgen_ele_converts_to_zero_based_connectivity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fea_ele_", dir="/tmp/opencode") as tmp:
            ele = Path(tmp) / "mesh.ele"
            ele.write_text(
                "2 3 0 0\n"
                "1 4 5 6 7\n"
                "2 8 9 10 11\n",
                encoding="utf-8",
            )
            conn = oneway.read_tetgen_ele(ele)
            self.assertEqual(conn.shape, (2, 4))
            self.assertEqual(conn[0].tolist(), [3, 4, 5, 6])
            self.assertEqual(conn[1].tolist(), [7, 8, 9, 10])

    def test_exporter_removes_stale_frames_and_writes_relative_pvd(self) -> None:
        import numpy as np

        with tempfile.TemporaryDirectory(prefix="fea_vtk_", dir="/tmp/opencode") as tmp:
            vtk_dir = Path(tmp)
            stale = vtk_dir / "frames" / "frame_000099.vtu"
            stale.parent.mkdir(parents=True)
            stale.write_text("stale", encoding="utf-8")
            (vtk_dir / "fea.pvd").write_text("stale", encoding="utf-8")

            exporter = oneway.FeaVtkExporter(
                vtk_dir,
                np.array([[0, 1, 2, 3]], dtype=np.int64),
                np.zeros((4, 3), dtype=float),
            )
            self.assertFalse(stale.exists())
            exporter.entries.extend([
                (0.0, str(vtk_dir / "frames" / "frame_000000.vtu")),
                (0.2, str(vtk_dir / "frames" / "frame_000001.vtu")),
            ])
            exporter._write_pvd()

            pvd = vtk_dir / "fea.pvd"
            root = ET.parse(pvd).getroot()
            entries = root.find("Collection").findall("DataSet")
            self.assertEqual([entry.attrib["timestep"] for entry in entries], ["0", "0.2"])
            self.assertEqual([entry.attrib["file"] for entry in entries],
                             ["frames/frame_000000.vtu", "frames/frame_000001.vtu"])

    def test_clear_removes_only_generated_vtk_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fea_clear_", dir="/tmp/opencode") as tmp:
            vtk_dir = Path(tmp)
            (vtk_dir / "frame_000000.vtu").write_text("x", encoding="utf-8")
            (vtk_dir / "fea.pvd").write_text("x", encoding="utf-8")
            keep = vtk_dir / "user_notes.txt"
            keep.write_text("keep", encoding="utf-8")

            oneway.clear_fea_vtk_export(vtk_dir)
            self.assertFalse((vtk_dir / "frame_000000.vtu").exists())
            self.assertFalse((vtk_dir / "fea.pvd").exists())
            self.assertTrue(keep.exists())


if __name__ == "__main__":
    unittest.main()
