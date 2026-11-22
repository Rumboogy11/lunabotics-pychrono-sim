#!/usr/bin/env python3
"""Regression coverage for background FEA VTU export and stress reuse."""

from __future__ import annotations

import multiprocessing as mp
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(CRM_DIR))

import lunar_excavator_crm_fea as fea_driver  # noqa: E402
import lunar_excavator_oneway as oneway  # noqa: E402


class FakeExporter:
    """Minimal writer-side exporter used by the process tests."""

    def __init__(self, log_path: Path, fail: bool = False):
        self.log_path = log_path
        self.fail = fail
        self.entries: list[tuple[float, str]] = []

    def write_snapshot(self, frame: oneway.FeaVtkFrameSnapshot) -> Path:
        if self.fail:
            raise RuntimeError("boom from writer")
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"{frame.frame_index}:{frame.time_s:.3f}\n")
        self.entries.append((frame.time_s, f"frame_{frame.frame_index:06d}.vtu"))
        return self.log_path


def make_snapshot(frame_index: int, time_s: float) -> oneway.FeaVtkFrameSnapshot:
    return oneway.FeaVtkFrameSnapshot(
        frame_index=frame_index,
        time_s=time_s,
        points=np.zeros((2, 3)),
        disp=np.zeros((2, 3)),
        disp_mag=np.zeros(2),
        vm=np.zeros(3),
    )


class StressReuseTest(unittest.TestCase):
    def test_worst_stress_info_fills_full_stress_array(self) -> None:
        class Node:
            def __init__(self, pos):
                self.pos = pos

            def GetPos(self):
                return self.pos

        class Element:
            def __init__(self, stress: float, volume: float):
                self.stress = stress
                self.volume = volume

            def GetStress(self):
                return self.stress

            def ComputeVolume(self):
                return self.volume

            def GetTetrahedronNode(self, index: int):
                return Node((float(index), 0.0, 0.0))

        elements = [Element(10.0, 2.0), Element(20.0, 0.5), Element(30.0, 1.5)]

        class Mesh:
            def GetNumElements(self):
                return len(elements)

            def GetElement(self, index):
                return elements[index]

        class Fea:
            def CastToChElementTetraCorot_4(self, element):
                return element

        values = np.zeros(3)
        with mock.patch.object(fea_driver, "von_mises_pa", side_effect=lambda stress: stress), \
             mock.patch.object(fea_driver.rigid, "vec_parts", side_effect=lambda value: value):
            result = fea_driver.worst_stress_info(
                Fea(), Mesh(), 1, 1.0, stress_values=values
            )

        self.assertEqual(result[0], 2)
        self.assertEqual(result[1], 30.0)
        np.testing.assert_array_equal(values, [10.0, 20.0, 30.0])


class BackgroundWriterTest(unittest.TestCase):
    def setUp(self) -> None:
        # Fork is sufficient for these module-level fakes and avoids spawn's
        # re-import of the unittest module on Linux.
        self.fork_ctx = mp.get_context("fork")
        self.ctx_patch = mock.patch.object(oneway.mp, "get_context", return_value=self.fork_ctx)
        self.ctx_patch.start()
        self.addCleanup(self.ctx_patch.stop)

    def test_frames_are_written_in_order_and_joined(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "written.txt"
            writer = oneway.BackgroundVtkWriter(FakeExporter(log_path))
            for index in range(3):
                writer.submit(make_snapshot(index, index * 0.1))
            writer.close()

            self.assertEqual(log_path.read_text(encoding="utf-8").splitlines(),
                             ["0:0.000", "1:0.100", "2:0.200"])
            self.assertEqual(writer.completed, 3)
            self.assertFalse(writer._process.is_alive())

    def test_worker_failure_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            writer = oneway.BackgroundVtkWriter(FakeExporter(Path(tmp) / "unused.txt", fail=True))
            try:
                writer.submit(make_snapshot(0, 0.0))
            except RuntimeError:
                pass
            with self.assertRaisesRegex(RuntimeError, "boom from writer"):
                writer.close()


if __name__ == "__main__":
    unittest.main()
