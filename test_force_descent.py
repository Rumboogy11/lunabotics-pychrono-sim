#!/usr/bin/env python3
"""Regression coverage for opt-in force-driven descent."""

from __future__ import annotations

import csv
import io
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(CRM_DIR))

import lunar_excavator_crm as crm  # noqa: E402
import lunar_excavator_crm_fea as fea  # noqa: E402
import lunar_excavator_oneway as oneway  # noqa: E402
import study_wizard as wiz  # noqa: E402
from test_study_geometry_consistency import consistent_cfg  # noqa: E402


class FakeConst:
    def __init__(self, value: float) -> None:
        self.value = float(value)

    def GetVal(self, t: float) -> float:  # noqa: N802
        return self.value


class FakeRamp:
    def __init__(self, start: float, slope: float) -> None:
        self.start = float(start)
        self.slope = float(slope)

    def GetVal(self, t: float) -> float:  # noqa: N802
        return self.start + self.slope * t


class FakeSequence:
    """ChFunctionSequence stand-in that emulates C0 continuity offsets."""

    def __init__(self) -> None:
        self.segments: list[tuple[object, float, float]] = []
        self.clamped = False

    def SetClamped(self, value: bool) -> None:  # noqa: N802
        self.clamped = bool(value)

    def InsertFunct(self, function, duration, offset, continuous) -> None:  # noqa: N802
        shift = 0.0
        if continuous and self.segments:
            previous, previous_duration, _ = self.segments[-1]
            shift = previous.GetVal(previous_duration) - function.GetVal(0.0)
        self.segments.append((function, float(duration), shift))

    def GetVal(self, t: float) -> float:  # noqa: N802
        if not self.segments:  # pragma: no cover - defensive
            return 0.0
        t = max(0.0, t) if self.clamped else t
        start = 0.0
        for function, duration, shift in self.segments:
            if t < start + duration:
                return function.GetVal(max(0.0, t - start)) + shift
            start += duration
        last_function, last_duration, last_shift = self.segments[-1]
        return last_function.GetVal(last_duration) + last_shift


class FakeMotor:
    GuideConstraint_PRISMATIC = "prismatic"
    GuideConstraint_SPHERICAL = "spherical"

    def __init__(self) -> None:
        self.disabled = False
        self.guide = None
        self.bodies = None
        self.frame = None
        self.force_function = None
        self.motion_function = None

    def Initialize(self, body1, body2, frame) -> None:  # noqa: N802
        self.bodies = (body1, body2)
        self.frame = frame

    def SetGuideConstraint(self, value) -> None:  # noqa: N802
        self.guide = value

    def SetDisabled(self, value) -> None:  # noqa: N802
        self.disabled = bool(value)

    def SetForceFunction(self, value) -> None:  # noqa: N802
        self.force_function = value

    def SetMotionFunction(self, value) -> None:  # noqa: N802
        self.motion_function = value


class FakeBody:
    def __init__(self) -> None:
        self.name = None
        self.fixed = False
        self.pos = None
        self.vel = None

    def SetName(self, value) -> None:  # noqa: N802
        self.name = value

    def SetFixed(self, value) -> None:  # noqa: N802
        self.fixed = bool(value)

    def SetPos(self, value) -> None:  # noqa: N802
        self.pos = value

    def GetPos(self):  # noqa: N802
        return self.pos

    def SetPosDt(self, value) -> None:  # noqa: N802
        self.vel = value

    def GetPosDt(self):  # noqa: N802
        return self.vel


class FakeSystem:
    def __init__(self) -> None:
        self.items: list[object] = []

    def Add(self, item) -> None:  # noqa: N802
        self.items.append(item)


class FakeChrono:
    ChFunctionSequence = FakeSequence
    ChFunctionConst = FakeConst
    ChFunctionRamp = FakeRamp
    ChLinkMotorLinearForce = FakeMotor
    ChLinkMotorLinearPosition = FakeMotor
    ChBody = FakeBody

    class ChVector3d:
        def __init__(self, x, y, z) -> None:
            self.x, self.y, self.z = x, y, z

    class ChQuaterniond:
        def __init__(self, w, x, y, z) -> None:
            self.w, self.x, self.y, self.z = w, x, y, z

    class ChFramed:
        def __init__(self, pos, rot) -> None:
            self.pos = pos
            self.rot = rot


def force_run(**overrides) -> crm.CrmRunParams:
    values = dict(
        descent_mode="force",
        descent_speed=0.0,
        descent_force_n=2.0,
        descent_force_ramp_s=0.0,
        settle_time=0.2,
    )
    values.update(overrides)
    return crm.CrmRunParams(**values)


def write_loads(path: Path, *, force: bool) -> None:
    fields = [
        "time_s",
        "drum_z_m",
        "force_x_n",
        "force_y_n",
        "force_z_n",
        "torque_x_nm",
        "torque_y_nm",
        "torque_z_nm",
    ]
    if force:
        fields += [
            "descent_mode",
            "descent_state",
            "actuator_force_n",
            "vertical_speed_m_s",
        ]
    rows = [
        [0.0, 0.3, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        [0.5, 0.28, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        [1.0, 0.2, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0],
    ]
    if force:
        rows[0] += ["force", "hold", 0.0, 0.0]
        rows[1] += ["force", "descend", 1.0, -0.1]
        rows[2] += ["force", "descend", 2.0, -0.2]
    path.write_text(
        ",".join(fields) + "\n" + "\n".join(",".join(str(value) for value in row) for row in rows) + "\n",
        encoding="utf-8",
    )


class ForceDescentParamsTest(unittest.TestCase):
    def test_defaults_stay_kinematic(self) -> None:
        run = crm.CrmRunParams()
        self.assertEqual(run.descent_mode, "kinematic")
        self.assertEqual(run.descent_force_n, 1.0)
        self.assertEqual(run.descent_force_ramp_s, 0.0)
        self.assertGreater(run.descent_speed, 0.0)

    def test_zero_analytic_speed_is_allowed(self) -> None:
        kinematic = replace(crm.CrmRunParams(), descent_speed=0.0)
        self.assertEqual(kinematic.descent_mode, "kinematic")
        self.assertEqual(kinematic.descent_speed, 0.0)
        run = force_run()
        self.assertEqual(run.descent_mode, "force")
        self.assertEqual(run.descent_speed, 0.0)

    def test_invalid_values_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "descent_mode"):
            replace(crm.CrmRunParams(), descent_mode="dynamic")
        with self.assertRaisesRegex(ValueError, "descent_force_n"):
            replace(force_run(), descent_force_n=0.0)
        with self.assertRaisesRegex(ValueError, "descent_force_ramp_s"):
            replace(force_run(), descent_force_ramp_s=-0.1)
        with self.assertRaisesRegex(ValueError, "descent_speed cannot be negative"):
            replace(force_run(), descent_speed=-0.1)

    def test_cli_options_flow_into_run_params(self) -> None:
        args = crm.build_parser().parse_args([
            "--descent-mode", "force",
            "--descent-force", "3.5",
            "--descent-force-ramp", "0.4",
            "--descent-speed", "0",
        ])
        run = crm.make_run_params(args, validate_sph_dt=False)
        self.assertEqual(run.descent_mode, "force")
        self.assertAlmostEqual(run.descent_force_n, 3.5)
        self.assertAlmostEqual(run.descent_force_ramp_s, 0.4)
        self.assertEqual(run.descent_speed, 0.0)
        self.assertEqual(crm.load_fields(run), crm.LOAD_FIELDS + crm.FORCE_LOAD_FIELDS)
        self.assertEqual(fea.fea_metric_fields(run), fea.FEA_METRIC_FIELDS + crm.FORCE_METRIC_FIELDS)
        self.assertEqual(oneway.oneway_fields(run), oneway.ONEWAY_FIELDS + oneway.FORCE_ONEWAY_FIELDS)

    def test_default_csv_schemas_are_unchanged(self) -> None:
        run = crm.CrmRunParams()
        self.assertEqual(crm.metric_fields(run), crm.METRIC_FIELDS)
        self.assertEqual(crm.load_fields(run), crm.LOAD_FIELDS)
        self.assertEqual(fea.fea_metric_fields(run), fea.FEA_METRIC_FIELDS)
        self.assertEqual(oneway.oneway_fields(run), oneway.ONEWAY_FIELDS)

    def test_emitted_csv_headers_extend_only_in_force_mode(self) -> None:
        for run, force in ((crm.CrmRunParams(), False), (force_run(), True)):
            with self.subTest(mode=run.descent_mode):
                schemas = {
                    "crm_summary": crm.metric_fields(run),
                    "soil_loads": crm.load_fields(run),
                    "fea_summary": fea.fea_metric_fields(run),
                    "oneway_summary": oneway.oneway_fields(run),
                }
                for name, fields in schemas.items():
                    buffer = io.StringIO()
                    csv.DictWriter(buffer, fieldnames=fields).writeheader()
                    header = next(csv.reader(io.StringIO(buffer.getvalue())))
                    self.assertEqual(header, fields)
                    for column in crm.FORCE_METRIC_FIELDS:
                        self.assertEqual(column in header, force, (name, column))
                    self.assertEqual(
                        "recorded_trajectory_error_m" in header,
                        force and name == "oneway_summary",
                    )


class ForceDescentControlTest(unittest.TestCase):
    def test_force_sequence_uses_absolute_values_without_c0_offsets(self) -> None:
        sequence = crm.build_descent_force_function(
            FakeChrono, force_run(descent_force_n=3.0, descent_force_ramp_s=0.0), 1.0
        )
        self.assertEqual(len(sequence.segments), 2)
        self.assertAlmostEqual(sequence.GetVal(0.1), 0.0)
        self.assertAlmostEqual(sequence.GetVal(0.2), -3.0)
        self.assertAlmostEqual(sequence.GetVal(0.9), -3.0)
        self.assertTrue(all(shift == 0.0 for _, _, shift in sequence.segments))

    def test_force_sequence_ramp_reaches_full_force(self) -> None:
        sequence = crm.build_descent_force_function(
            FakeChrono, force_run(descent_force_n=3.0, descent_force_ramp_s=0.1), 1.0
        )
        self.assertAlmostEqual(sequence.GetVal(0.2), 0.0)
        self.assertAlmostEqual(sequence.GetVal(0.25), -1.5)
        self.assertAlmostEqual(sequence.GetVal(0.3), -3.0)
        self.assertAlmostEqual(sequence.GetVal(0.9), -3.0)

    def test_force_sequence_without_settle_starts_at_full_force(self) -> None:
        run = force_run(settle_time=0.0, descent_force_n=3.0)
        sequence = crm.build_descent_force_function(FakeChrono, run, 1.0)
        self.assertEqual(len(sequence.segments), 1)
        self.assertAlmostEqual(sequence.GetVal(0.0), -3.0)

    def test_create_and_update_force_descent(self) -> None:
        motion = SimpleNamespace(drum_center_z0=0.3, z_target=0.2)
        run = force_run()
        system = FakeSystem()
        body = FakeBody()

        drive = crm.create_force_descent(FakeChrono, system, body, motion, run, 1.0)
        self.assertEqual(drive["state"], "hold")
        self.assertEqual(drive["ground"].name, "descent_guide_ground")
        self.assertTrue(drive["ground"].fixed)
        self.assertAlmostEqual(drive["ground"].pos.z, 0.3)
        self.assertTrue(drive["force_motor"].disabled)
        self.assertFalse(drive["hold_motor"].disabled)
        self.assertEqual(drive["force_motor"].guide, "spherical")
        self.assertAlmostEqual(drive["hold_motor"].motion_function.value, 0.0)
        self.assertEqual(drive["force_motor"].bodies, (body, drive["ground"]))

        crm.update_force_descent(drive, motion, run, 0.1, 0.3)
        self.assertEqual(drive["state"], "hold")
        crm.update_force_descent(drive, motion, run, 0.2, 0.3)
        self.assertEqual(drive["state"], "descend")
        self.assertFalse(drive["force_motor"].disabled)
        self.assertTrue(drive["hold_motor"].disabled)
        crm.update_force_descent(drive, motion, run, 0.4, 0.2)
        self.assertEqual(drive["state"], "target")
        self.assertTrue(drive["force_motor"].disabled)
        self.assertFalse(drive["hold_motor"].disabled)
        self.assertAlmostEqual(drive["hold_motor"].motion_function.value, -0.1)

    def test_predicted_crossing_hands_off_before_the_step(self) -> None:
        motion = SimpleNamespace(drum_center_z0=0.3, z_target=0.2)
        body = FakeBody()
        body.SetPos(FakeChrono.ChVector3d(0.0, 0.0, 0.25))
        drive = crm.create_force_descent(
            FakeChrono, FakeSystem(), body, motion, force_run(), 1.0
        )
        crm.update_force_descent(drive, motion, force_run(), 0.2, 0.25)
        self.assertEqual(drive["state"], "descend")

        crm.update_force_descent(
            drive, motion, force_run(), 0.3, 0.25, velocity=-0.4, dt=0.2
        )
        self.assertEqual(drive["state"], "target")
        self.assertTrue(drive["force_motor"].disabled)
        self.assertFalse(drive["hold_motor"].disabled)
        self.assertAlmostEqual(drive["hold_motor"].motion_function.value, -0.1)
        self.assertAlmostEqual(body.GetPos().z, 0.25)  # not snapped downward early

    def test_target_handoff_clamps_step_overshoot(self) -> None:
        motion = SimpleNamespace(drum_center_z0=0.3, z_target=0.2)
        body = FakeBody()
        body.SetPos(FakeChrono.ChVector3d(0.0, 0.0, 0.19))
        body.SetPosDt(FakeChrono.ChVector3d(0.01, 0.0, -0.3))
        drive = crm.create_force_descent(
            FakeChrono, FakeSystem(), body, motion, force_run(), 1.0
        )
        crm.update_force_descent(drive, motion, force_run(), 0.2, 0.19)
        self.assertEqual(drive["state"], "target")
        self.assertAlmostEqual(body.GetPos().z, 0.2)
        self.assertAlmostEqual(body.GetPosDt().z, 0.0)
        self.assertAlmostEqual(body.GetPos().x, 0.0)
        self.assertAlmostEqual(body.GetPosDt().x, 0.01)

    def test_prismatic_force_drive_reuses_supplied_ground(self) -> None:
        motion = SimpleNamespace(drum_center_z0=0.3, z_target=0.2)
        system = FakeSystem()
        ground = FakeBody()
        drive = crm.create_force_descent(
            FakeChrono, system, FakeBody(), motion, force_run(), 1.0,
            prismatic=True, ground=ground,
        )
        self.assertIs(drive["ground"], ground)
        self.assertEqual(len(system.items), 2)
        self.assertEqual(drive["force_motor"].guide, "prismatic")
        self.assertEqual(drive["hold_motor"].guide, "prismatic")

    def test_recorded_position_function_replays_absolute_heights(self) -> None:
        sequence = fea.build_recorded_position_function(
            FakeChrono, [0.0, 0.5, 1.0], [0.3, 0.25, 0.2], 0.3, 2.0
        )
        self.assertAlmostEqual(sequence.GetVal(0.25), -0.025)
        self.assertAlmostEqual(sequence.GetVal(0.75), -0.075)
        self.assertAlmostEqual(sequence.GetVal(1.5), -0.1)


class ForceLoadHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.motion = SimpleNamespace(
            drum_center_z0=0.3,
            z_target=0.2,
            drum_z=lambda t: 0.3,  # deliberately non-analytic for force mode
        )

    def test_force_metadata_is_parsed_and_interpolated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="force_loads_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "Loads.csv"
            write_loads(path, force=True)
            loads = oneway.LoadHistory(path)
            self.assertEqual(loads.mode, "force")
            loads.validate_motion(self.motion)
            self.assertAlmostEqual(loads.z_at(0.25), 0.29)
            telemetry = loads.telemetry_at(0.75)
            self.assertEqual(telemetry["descent_mode"], "force")
            self.assertEqual(telemetry["descent_state"], "descend")
            self.assertAlmostEqual(float(telemetry["actuator_force_n"]), 1.5)
            self.assertAlmostEqual(float(telemetry["vertical_speed_m_s"]), -0.15)

    def test_mixed_mode_load_history_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mixed_loads_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "Loads.csv"
            write_loads(path, force=True)
            text = path.read_text(encoding="utf-8").replace("force,descend", "kinematic,descend", 1)
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "inconsistent descent_mode"):
                oneway.LoadHistory(path)

    def test_force_history_rejects_target_undershoot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="force_loads_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "Loads.csv"
            write_loads(path, force=True)
            text = path.read_text(encoding="utf-8").replace("0.28", "0.19")
            path.write_text(text, encoding="utf-8")
            loads = oneway.LoadHistory(path)
            with self.assertRaisesRegex(SystemExit, "below the target"):
                loads.validate_motion(self.motion)

    def test_legacy_load_history_keeps_kinematic_behavior(self) -> None:
        with tempfile.TemporaryDirectory(prefix="legacy_loads_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "Loads.csv"
            write_loads(path, force=False)
            text = path.read_text(encoding="utf-8").replace("0.28", "0.25")
            path.write_text(text, encoding="utf-8")
            loads = oneway.LoadHistory(path)
            self.assertEqual(loads.mode, "kinematic")
            self.assertEqual(loads.telemetry_at(0.75), {})
            analytic = SimpleNamespace(
                drum_center_z0=0.3,
                z_target=0.2,
                drum_z=lambda t: 0.3 - 0.1 * t,
            )
            loads.validate_motion(analytic)


class ForceWizardCommandsTest(unittest.TestCase):
    def test_force_flags_are_generated_for_both_simulation_steps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="force_wizard_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            cfg["descent_mode"] = "force"
            cfg["descent_speed"] = 0.0
            cfg["descent_force_n"] = 2.5
            cfg["descent_force_ramp_s"] = 0.1
            with patch.object(wiz, "measured_geometry_for_cfg", return_value=None):
                steps = wiz.build_commands(cfg)
            crm_cmd = next(step["command"] for step in steps if step["name"] == "rigid-CRM")
            fea_cmd = next(step["command"] for step in steps if step["name"] == "one-way-FEA")
            for command in (crm_cmd, fea_cmd):
                self.assertEqual(command[command.index("--descent-mode") + 1], "force")
                self.assertEqual(command[command.index("--descent-force") + 1], "2.5")
                self.assertEqual(command[command.index("--descent-force-ramp") + 1], "0.1")
            self.assertFalse(any(
                "--descent-mode" in step["command"] or "--descent-force" in step["command"]
                for step in steps if step["name"] not in {"rigid-CRM", "one-way-FEA"}
            ))

    def test_kinematic_studies_do_not_generate_force_flags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="kinematic_wizard_", dir="/tmp/opencode") as tmp:
            cfg = consistent_cfg(Path(tmp))
            with patch.object(wiz, "measured_geometry_for_cfg", return_value=None):
                steps = wiz.build_commands(cfg)
            crm_cmd = next(step["command"] for step in steps if step["name"] == "rigid-CRM")
            fea_cmd = next(step["command"] for step in steps if step["name"] == "one-way-FEA")
            self.assertNotIn("--descent-mode", crm_cmd)
            self.assertNotIn("--descent-force", crm_cmd)
            self.assertNotIn("--descent-mode", fea_cmd)
            self.assertNotIn("--descent-force", fea_cmd)

    def test_force_validation_rejects_bad_values(self) -> None:
        cfg = consistent_cfg(Path("/tmp/opencode"))
        cfg["descent_mode"] = "force"
        cfg["descent_force_n"] = 0.0
        with self.assertRaisesRegex(wiz.StudyConfigError, "descent_force_n"):
            wiz.validate_study_geometry(cfg)

    def test_wizard_cli_defaults_to_kinematic_and_accepts_force_overrides(self) -> None:
        with tempfile.TemporaryDirectory(prefix="force_wizard_cli_", dir="/tmp/opencode") as tmp:
            crm_obj = Path(tmp) / "drum.obj"
            node = Path(tmp) / "drum.node"
            ele = Path(tmp) / "drum.ele"
            for path in (crm_obj, node, ele):
                path.write_text("stub for config generation", encoding="utf-8")
            base = [
                "--geometry", "files",
                "--output-dir", tmp,
                "--crm-obj", str(crm_obj),
                "--fea-node", str(node),
                "--fea-ele", str(ele),
                "--no-auto-geometry",
                "--dry-run",
                "--yes",
            ]
            with patch.object(wiz, "INTERACTIVE", False):
                default_cfg = wiz.collect_settings(wiz.build_parser().parse_args(base))
                force_cfg = wiz.collect_settings(wiz.build_parser().parse_args(base + [
                    "--descent-mode", "force",
                    "--descent-force", "2.5",
                    "--descent-force-ramp", "0.1",
                    "--descent-speed", "0",
                ]))
            self.assertEqual(default_cfg["descent_mode"], "kinematic")
            self.assertEqual(default_cfg["descent_force_n"], 1.0)
            self.assertEqual(default_cfg["descent_force_ramp_s"], 0.0)
            self.assertEqual(force_cfg["descent_mode"], "force")
            self.assertAlmostEqual(force_cfg["descent_force_n"], 2.5)
            self.assertAlmostEqual(force_cfg["descent_force_ramp_s"], 0.1)
            self.assertEqual(force_cfg["descent_speed"], 0.0)


if __name__ == "__main__":
    unittest.main()
