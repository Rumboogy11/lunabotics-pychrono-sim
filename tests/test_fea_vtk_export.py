#!/usr/bin/env python3
"""Regression coverage for opt-in full 3D FEA VTU export."""

from __future__ import annotations

import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "crm"))

import lunar_excavator_crm_fea as fea_driver  # noqa: E402
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


class _FakeConst:
    def __init__(self, value: float) -> None:
        self.value = value


class _FakeRamp:
    def __init__(self, start: float, slope: float) -> None:
        self.start = start
        self.slope = slope


class _FakeSequence:
    def __init__(self) -> None:
        self.segments: list[tuple[object, float, float, bool]] = []

    def InsertFunct(self, function, duration, offset, continuous) -> None:  # noqa: N802
        self.segments.append((function, duration, offset, continuous))


class _FakeChrono:
    ChFunctionSequence = _FakeSequence
    ChFunctionConst = _FakeConst
    ChFunctionRamp = _FakeRamp

    class ChVector3d:
        def __init__(self, *values) -> None:
            self.values = values

    class ChQuaterniond:
        def __init__(self, *values) -> None:
            self.values = values

    class ChFramed:
        def __init__(self, pos, rot) -> None:
            self.pos = pos
            self.rot = rot

    class _Motor:
        GuideConstraint_PRISMATIC = "prismatic"
        SpindleConstraint_REVOLUTE = "revolute"

        def Initialize(self, body1, body2, frame) -> None:  # noqa: N802
            self.bodies = (body1, body2)
            self.frame = frame

        def SetGuideConstraint(self, value) -> None:  # noqa: N802
            self.guide = value

        def SetSpindleConstraint(self, value) -> None:  # noqa: N802
            self.spindle = value

        def SetMotionFunction(self, value) -> None:  # noqa: N802
            self.motion = value

        def SetSpeedFunction(self, value) -> None:  # noqa: N802
            self.speed = value

    ChLinkMotorLinearPosition = _Motor
    ChLinkMotorRotationSpeed = _Motor

    @staticmethod
    def QuatFromAngleAxis(angle, axis):  # noqa: N802
        return _FakeChrono.ChQuaterniond(angle, *axis.values)


class _FakeBody:
    def SetPos(self, value) -> None:  # noqa: N802
        self.pos = value

    def SetPosDt(self, value) -> None:  # noqa: N802
        self.pos_dt = value

    def SetRot(self, value) -> None:  # noqa: N802
        self.rot = value

    def SetAngVelParent(self, value) -> None:  # noqa: N802
        self.ang_vel = value


class _FakeSystem:
    def __init__(self) -> None:
        self.items = []

    def Add(self, item) -> None:  # noqa: N802
        self.items.append(item)


class FeaSpindleDirectionTest(unittest.TestCase):
    def _speed_profile(self, omega: float):
        motion = SimpleNamespace(settle_time=0.0, drum_omega=omega)
        return fea_driver.build_spin_speed_function(
            _FakeChrono(), motion, ramp_time=0.5, t_end=2.0).segments

    def test_reverse_omega_keeps_negative_motor_speed(self) -> None:
        segments = self._speed_profile(-3.14)

        ramp = segments[0][0]
        plateau = segments[1][0]
        self.assertIsInstance(ramp, _FakeRamp)
        self.assertIsInstance(plateau, _FakeConst)
        self.assertAlmostEqual(ramp.slope, -3.14 / 0.5)
        self.assertAlmostEqual(plateau.value, -3.14)

    def test_forward_omega_keeps_positive_motor_speed(self) -> None:
        segments = self._speed_profile(2.5)

        ramp = segments[0][0]
        plateau = segments[1][0]
        self.assertAlmostEqual(ramp.slope, 2.5 / 0.5)
        self.assertAlmostEqual(plateau.value, 2.5)

    def test_motor_is_initialized_spindle_first(self) -> None:
        import math

        motion = SimpleNamespace(
            settle_time=0.001,
            drum_center_z0=0.3,
            z_target=0.1,
            lower_speed=0.001,
            drum_omega=-3.14,
            t_max=1.0,
            drum_spin_axis="X",
            initial_quaternion_xyzw=lambda: (0.0, math.sqrt(0.5), 0.0, math.sqrt(0.5)),
        )
        drive = {
            "ground": _FakeBody(),
            "slider": _FakeBody(),
            "spindle": _FakeBody(),
        }

        fea_driver.configure_spindle_drive(
            _FakeChrono(), _FakeSystem(), motion, drive, 0.0, 1.0)

        self.assertEqual(drive["rotation_motor"].bodies,
                         (drive["spindle"], drive["slider"]))


if __name__ == "__main__":
    unittest.main()
