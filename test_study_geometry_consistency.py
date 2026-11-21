#!/usr/bin/env python3
"""Regression coverage for generated CRM geometry consistency checks."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import study_wizard as wiz  # noqa: E402


def consistent_cfg(output_dir: Path) -> dict:
    return {
        "output_dir": output_dir,
        "geometry": "files",
        "step_file": None,
        "crm_obj": ROOT / "geometry" / "LunarDrumScrew_m_coarse.obj",
        "fea_node": ROOT / "fea" / "LunarDrumScrew_m_fea_decim8k.node",
        "fea_ele": ROOT / "fea" / "LunarDrumScrew_m_fea_decim8k.ele",
        "hub_radius": 0.0015,
        "drum_radius": 0.178835,
        "drum_half_length": 0.153176,
        "inner_radius": 0.160951,
        "spacing": 0.004,
        "active_box": (0.4, 0.4, 0.5),
        "descent_speed": 0.00439757,
        "bed_height": 0.15,
        "duration": 40.0,
        "fea_duration": 40.0,
        "fea_dt": 0.002,
        "fea_fps": 20.0,
        "crm_fps": 20.0,
        "loads_fps": 20.0,
        "particle_fps": 5.0,
        "particle_writers": 4,
        "d0_multiplier": 1.0,
        "artificial_viscosity": 0.5,
        "shifting_ppst_push": 3.0,
        "crm_dt": None,
        "capacity_volume": None,
        "settle_time": 0.001,
        "spin_axis": "X",
        "omega": 3.14,
        "reverse_spin": False,
        "solver": "pardiso-mkl",
        "num_threads": 8,
        "load_radius_fraction": 0.95,
        "raw_stress_every": 5,
        "export_workers": 1,
        "bed_padding": 0.25,
        "save_particles": True,
        "fea_export_vtk": False,
        "fea_vtk_stride": 1,
        "export_vtk": False,
        "paraview_stride": 1,
        "paraview_particle_stride": 0,
        "paraview_particle_render_stride": 1,
        "mesh_steps": [],
        "soil": dict(wiz.SOIL_DEFAULTS),
        "pla": dict(wiz.PLA_DEFAULTS),
    }


class StudyGeometryConsistencyTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original = wiz.measured_geometry_for_cfg

    def tearDown(self) -> None:
        wiz.measured_geometry_for_cfg = self._original

    def test_mixed_radius_is_rejected_without_changing_fill_logic(self) -> None:
        measured = {"drum_radius": 0.178835, "drum_half_length": 0.153176}
        wiz.measured_geometry_for_cfg = lambda cfg: measured
        cfg = consistent_cfg(Path("/tmp/opencode"))
        cfg["drum_radius"] = 0.3
        with self.assertRaisesRegex(wiz.StudyConfigError, "0.178835"):
            wiz.validate_study_geometry(cfg)

    def test_consistent_geometry_has_no_errors_or_warnings(self) -> None:
        measured = {"drum_radius": 0.178835, "drum_half_length": 0.153176}
        wiz.measured_geometry_for_cfg = lambda cfg: measured
        cfg = consistent_cfg(Path("/tmp/opencode"))
        self.assertEqual(wiz.validate_study_geometry(cfg), [])

    def test_short_duration_is_warned(self) -> None:
        wiz.measured_geometry_for_cfg = lambda cfg: None
        cfg = consistent_cfg(Path("/tmp/opencode"))
        cfg["duration"] = 2.0
        warnings = wiz.validate_study_geometry(cfg)
        self.assertEqual(len(warnings), 1)
        self.assertIn("full descent", warnings[0])

    def test_auto_geometry_repairs_a_mixed_radius_and_short_duration(self) -> None:
        measured = {"drum_radius": 0.178835, "drum_half_length": 0.153176}
        wiz.measured_geometry_for_cfg = lambda cfg: measured
        with tempfile.TemporaryDirectory(prefix="geometry_fix_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["auto_geometry"] = True
            cfg["drum_radius"] = 0.3
            cfg["duration"] = 2.0
            wiz.build_commands(cfg)
            self.assertAlmostEqual(cfg["drum_radius"], 0.178835)
            self.assertGreaterEqual(cfg["duration"], 34.0)
            self.assertTrue(any("duration" in item for item in cfg["geometry_corrections"]))

    def test_explicit_geometry_still_rejects_a_bad_radius(self) -> None:
        measured = {"drum_radius": 0.178835, "drum_half_length": 0.153176}
        wiz.measured_geometry_for_cfg = lambda cfg: measured
        with tempfile.TemporaryDirectory(prefix="geometry_check_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["auto_geometry"] = False
            cfg["drum_radius"] = 0.3
            with self.assertRaises(wiz.StudyConfigError):
                wiz.build_commands(cfg)

    def test_detector_config_is_forwarded_to_crm_only_and_capacity_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="detector_cfg_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["auto_geometry"] = False
            cfg["fill_detector_step"] = Path(tmp) / "cavity.step"
            cfg["fill_detector_obj"] = Path(tmp) / "mesh" / "cavity_detector_surface.obj"
            cfg["capacity_volume"] = 0.0123
            steps = wiz.build_commands(cfg)
            crm_cmd = next(s["command"] for s in steps if s["name"] == "rigid-CRM")
            fea_cmd = next(s["command"] for s in steps if s["name"] == "one-way-FEA")

            self.assertEqual(
                crm_cmd[crm_cmd.index("--fill-detector-obj") + 1],
                str(cfg["fill_detector_obj"]),
            )
            self.assertNotIn("--fill-detector-obj", fea_cmd)
            self.assertEqual(crm_cmd[crm_cmd.index("--capacity-volume") + 1], "0.0123")

    def test_detector_step_generates_surface_conversion_steps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="detector_wizard_", dir="/tmp/opencode") as tmp:
            detector_step = Path(tmp) / "cavity.step"
            detector_step.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n", encoding="utf-8")
            crm_obj = Path(tmp) / "drum.obj"
            fea_node = Path(tmp) / "drum.node"
            fea_ele = Path(tmp) / "drum.ele"
            for path in (crm_obj, fea_node, fea_ele):
                path.write_text("stub for command generation", encoding="utf-8")
            args = wiz.build_parser().parse_args([
                "--geometry", "files",
                "--output-dir", tmp,
                "--crm-obj", str(crm_obj),
                "--fea-node", str(fea_node),
                "--fea-ele", str(fea_ele),
                "--fill-detector-step", str(detector_step),
                "--no-auto-geometry",
                "--dry-run",
                "--yes",
            ])
            with patch.object(wiz, "INTERACTIVE", False):
                cfg = wiz.collect_settings(args)
            self.assertEqual(cfg["fill_detector_step"], detector_step.resolve())
            self.assertEqual(
                cfg["fill_detector_obj"],
                Path(tmp) / "mesh" / "cavity_detector_surface.obj",
            )
            steps = cfg["mesh_steps"]
            names = [step["name"] for step in steps]
            self.assertIn("generate-fill-detector-surface", names)
            self.assertIn("make-fill-detector-OBJ", names)
            surface_cmd = next(s["command"] for s in steps
                               if s["name"] == "generate-fill-detector-surface")
            self.assertIn("--surface-only", surface_cmd)
            commands = wiz.build_commands(cfg)
            crm_cmd = next(s["command"] for s in commands if s["name"] == "rigid-CRM")
            fea_cmd = next(s["command"] for s in commands if s["name"] == "one-way-FEA")
            self.assertEqual(
                crm_cmd[crm_cmd.index("--fill-detector-obj") + 1],
                str(cfg["fill_detector_obj"]),
            )
            self.assertNotIn("--fill-detector-obj", fea_cmd)


if __name__ == "__main__":
    unittest.main()
