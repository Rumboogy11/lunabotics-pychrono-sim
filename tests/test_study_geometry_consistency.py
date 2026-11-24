#!/usr/bin/env python3
"""Regression coverage for generated CRM geometry consistency checks."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "crm"))

import lunar_excavator_crm_fea as fea  # noqa: E402
import lunar_excavator_oneway as oneway  # noqa: E402
import study_wizard as wiz  # noqa: E402


def consistent_cfg(output_dir: Path) -> dict:
    return {
        "output_dir": output_dir,
        "geometry": "files",
        "step_file": None,
        "crm_obj": output_dir / "mesh" / "surface.obj",
        "fea_node": output_dir / "mesh" / "drum.node",
        "fea_ele": output_dir / "mesh" / "drum.ele",
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

    def test_capacity_volume_is_forwarded_without_mesh_detector_options(self) -> None:
        # This test is about command forwarding, not mesh measurement. Mock the
        # measurement like the surrounding tests so it does not depend on which
        # (gitignored) reference OBJ happens to be present in geometry/.
        wiz.measured_geometry_for_cfg = lambda cfg: {
            "drum_radius": cfg["drum_radius"],
            "drum_half_length": cfg["drum_half_length"],
        }
        with tempfile.TemporaryDirectory(prefix="capacity_cfg_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["auto_geometry"] = False
            cfg["capacity_volume"] = 0.0123
            steps = wiz.build_commands(cfg)
            crm_cmd = next(s["command"] for s in steps if s["name"] == "rigid-CRM")
            fea_cmd = next(s["command"] for s in steps if s["name"] == "one-way-FEA")

            self.assertEqual(crm_cmd[crm_cmd.index("--capacity-volume") + 1], "0.0123")
            self.assertNotIn("--fill-detector-obj", crm_cmd)
            self.assertNotIn("--fill-detector-obj", fea_cmd)
            self.assertNotIn("--fill-detector-step", crm_cmd)
            self.assertNotIn("--fill-detector-step", fea_cmd)

    def test_manual_detector_size_is_forwarded_only_to_rigid_crm(self) -> None:
        with tempfile.TemporaryDirectory(prefix="detector_cfg_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["detector_auto_size"] = False
            cfg["detector_radius"] = 0.05
            cfg["detector_length"] = 0.08
            steps = wiz.build_commands(cfg)
            crm_cmd = next(s["command"] for s in steps if s["name"] == "rigid-CRM")

            self.assertIn("--no-detector-auto-size", crm_cmd)
            self.assertEqual(crm_cmd[crm_cmd.index("--detector-radius") + 1], "0.05")
            self.assertEqual(crm_cmd[crm_cmd.index("--detector-length") + 1], "0.08")
            for step in steps:
                if step["name"] == "rigid-CRM":
                    continue
                self.assertNotIn("--no-detector-auto-size", step["command"])
                self.assertNotIn("--detector-radius", step["command"])
                self.assertNotIn("--detector-length", step["command"])

    def test_auto_detector_size_sends_no_manual_flags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="detector_auto_cfg_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["detector_auto_size"] = True
            steps = wiz.build_commands(cfg)
            crm_cmd = next(s["command"] for s in steps if s["name"] == "rigid-CRM")
            self.assertNotIn("--no-detector-auto-size", crm_cmd)
            self.assertNotIn("--detector-radius", crm_cmd)
            self.assertNotIn("--detector-length", crm_cmd)

    def test_manual_detector_cli_options_are_accepted(self) -> None:
        args = wiz.build_parser().parse_args([
            "--no-detector-auto-size",
            "--detector-radius", "0.05",
            "--detector-length", "0.08",
        ])
        self.assertFalse(args.detector_auto_size)
        self.assertEqual(args.detector_radius, 0.05)
        self.assertEqual(args.detector_length, 0.08)

    def test_auto_detector_config_follows_corrected_drum_dimensions(self) -> None:
        measured = {"drum_radius": 0.178835, "drum_half_length": 0.153176}
        wiz.measured_geometry_for_cfg = lambda cfg: measured
        with tempfile.TemporaryDirectory(prefix="detector_auto_sync_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["auto_geometry"] = True
            cfg["drum_radius"] = 0.3
            cfg["detector_auto_size"] = True
            cfg["detector_radius"] = 0.3
            cfg["detector_length"] = 0.8
            wiz.build_commands(cfg)

            self.assertAlmostEqual(cfg["detector_radius"], measured["drum_radius"])
            self.assertAlmostEqual(
                cfg["detector_length"], 2.0 * measured["drum_half_length"])

    def test_auto_geometry_correction_leaves_manual_detector_alone(self) -> None:
        measured = {"drum_radius": 0.178835, "drum_half_length": 0.153176}
        wiz.measured_geometry_for_cfg = lambda cfg: measured
        with tempfile.TemporaryDirectory(prefix="detector_auto_geom_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["auto_geometry"] = True
            cfg["drum_radius"] = 0.3
            cfg["duration"] = 2.0
            cfg["detector_auto_size"] = False
            cfg["detector_radius"] = 0.05
            cfg["detector_length"] = 0.08
            wiz.build_commands(cfg)

            self.assertAlmostEqual(cfg["drum_radius"], measured["drum_radius"])
            self.assertFalse(cfg["detector_auto_size"])
            self.assertEqual(cfg["detector_radius"], 0.05)
            self.assertEqual(cfg["detector_length"], 0.08)

    def test_fea_drivers_reject_rigid_only_detector_flags(self) -> None:
        removed_flags = {
            "--detector-auto-size", "--no-detector-auto-size",
            "--detector-radius", "--detector-length",
        }
        for module in (fea, oneway):
            parser_flags = {
                flag
                for action in module.build_parser()._actions
                for flag in action.option_strings
            }
            self.assertFalse(removed_flags & parser_flags, module.__name__)

    def test_detector_step_option_is_rejected(self) -> None:
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            wiz.build_parser().parse_args([
                "--fill-detector-step", "ignored.step",
            ])


if __name__ == "__main__":
    unittest.main()
