#!/usr/bin/env python3
"""Regression coverage for global gravity and rigid-CRM drum gravity."""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "crm"))

import lunar_excavator_crm as crm  # noqa: E402
import lunar_excavator_crm_fea as fea  # noqa: E402
import lunar_excavator_oneway as oneway  # noqa: E402
import study_wizard as wiz  # noqa: E402
from test_study_geometry_consistency import consistent_cfg  # noqa: E402


class GravityRunConfigTest(unittest.TestCase):
    def test_rigid_crm_gravity_defaults_and_overrides(self) -> None:
        default = crm.make_run_params(crm.build_parser().parse_args(["--omega", "3.14"]))
        self.assertAlmostEqual(default.gravity, -9.81)
        self.assertFalse(default.drum_gravity)

        lunar = crm.make_run_params(crm.build_parser().parse_args([
            "--omega", "3.14", "--gravity", "-1.62", "--drum-gravity",
        ]))
        self.assertAlmostEqual(lunar.gravity, -1.62)
        self.assertTrue(lunar.drum_gravity)

        disabled = crm.make_run_params(crm.build_parser().parse_args([
            "--omega", "3.14", "--no-drum-gravity",
        ]))
        self.assertFalse(disabled.drum_gravity)

    def test_nonfinite_gravity_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "gravity must be finite"):
            replace(crm.CrmRunParams(), gravity=float("nan"))

    def test_fea_parsers_share_gravity_but_not_crm_drum_toggle(self) -> None:
        for module, extra in ((fea, []), (oneway, ["--loads", "soil_loads.csv"])):
            with self.subTest(module=module.__name__):
                args = module.build_parser().parse_args(["--gravity", "-1.62", *extra])
                if not hasattr(args, "smoke_test"):
                    args.smoke_test = False
                run = crm.make_run_params(args, validate_sph_dt=False)
                self.assertAlmostEqual(run.gravity, -1.62)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    module.build_parser().parse_args(["--drum-gravity", *extra])


class GravitySystemSetupTest(unittest.TestCase):
    def test_drum_gravity_survives_terrain_gravity_propagation(self) -> None:
        class FakeSystem:
            def __init__(self) -> None:
                self.gravity = None

            def SetGravitationalAcceleration(self, gravity) -> None:  # noqa: N802
                self.gravity = tuple(gravity)

        class FakeTerrain:
            def __init__(self, system: FakeSystem) -> None:
                self.system = system
                self.gravity = None

            def SetGravitationalAcceleration(self, gravity) -> None:  # noqa: N802
                self.gravity = tuple(gravity)
                # Chrono's FSI system propagates this gravity to the multibody
                # system as well, which used to erase the drum-gravity toggle.
                self.system.SetGravitationalAcceleration(gravity)

        fake_chrono = SimpleNamespace(ChVector3d=lambda x, y, z: (x, y, z))
        for drum_gravity, expected_z in ((False, 0.0), (True, -1.62)):
            with self.subTest(drum_gravity=drum_gravity):
                run = replace(crm.CrmRunParams(), gravity=-1.62,
                              drum_gravity=drum_gravity)
                system = FakeSystem()
                terrain = FakeTerrain(system)
                crm.configure_gravity(fake_chrono, system, terrain, run)
                self.assertEqual(terrain.gravity, (0.0, 0.0, -1.62))
                self.assertEqual(system.gravity, (0.0, 0.0, expected_z))


class GravityCommandGenerationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original = wiz.measured_geometry_for_cfg

    def tearDown(self) -> None:
        wiz.measured_geometry_for_cfg = self._original

    def _commands(self, cfg: dict, output_dir: Path) -> tuple[list[str], list[str]]:
        wiz.measured_geometry_for_cfg = lambda _cfg: None
        steps = wiz.build_commands(cfg)
        crm_cmd = next(step["command"] for step in steps if step["name"] == "rigid-CRM")
        fea_cmd = next(step["command"] for step in steps if step["name"] == "one-way-FEA")
        return crm_cmd, fea_cmd

    def test_global_gravity_is_emitted_for_both_stages(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gravity_cmd_", dir="/tmp/opencode") as tmp:
            out = Path(tmp)
            cfg = consistent_cfg(out)
            crm_cmd, fea_cmd = self._commands(cfg, out)
            for command in (crm_cmd, fea_cmd):
                self.assertEqual(command[command.index("--gravity") + 1], "-9.81")
            self.assertNotIn("--drum-gravity", crm_cmd)
            self.assertNotIn("--drum-gravity", fea_cmd)

    def test_drum_gravity_is_emitted_only_for_rigid_crm(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gravity_drum_", dir="/tmp/opencode") as tmp:
            out = Path(tmp)
            cfg = consistent_cfg(out)
            cfg["gravity"] = -1.62
            cfg["drum_gravity"] = True
            crm_cmd, fea_cmd = self._commands(cfg, out)
            self.assertEqual(crm_cmd[crm_cmd.index("--gravity") + 1], "-1.62")
            self.assertIn("--drum-gravity", crm_cmd)
            self.assertEqual(fea_cmd[fea_cmd.index("--gravity") + 1], "-1.62")
            self.assertNotIn("--drum-gravity", fea_cmd)

    def test_study_config_rejects_nonfinite_gravity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="gravity_bad_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["gravity"] = float("inf")
            with self.assertRaisesRegex(wiz.StudyConfigError, "gravity must be finite"):
                wiz.build_commands(cfg)


if __name__ == "__main__":
    unittest.main()
