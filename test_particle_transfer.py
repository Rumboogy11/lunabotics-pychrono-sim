#!/usr/bin/env python3
"""Regression coverage for GPU particle-position transfers in the CRM loop."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(CRM_DIR))

import lunar_excavator_crm as crm  # noqa: E402


class FluidOnlyBinding:
    """Binding that can copy only the requested marker group."""

    def __init__(self, fluid_positions: np.ndarray, fluid_group: object) -> None:
        self.fluid_positions = fluid_positions
        self.fluid_group = fluid_group
        self.calls: list[object] = []

    def GetNumFluidMarkers(self) -> int:
        return len(self.fluid_positions)

    def GetParticlePositionsNumpy(self, group) -> np.ndarray:
        self.calls.append(group)
        if group is not self.fluid_group:
            raise AssertionError(f"expected the FLUID marker group, got {group!r}")
        return self.fluid_positions.copy()


class LegacyBinding:
    """Binding whose NumPy accessor only supports the all-marker default."""

    def __init__(self, all_positions: np.ndarray, fluid_count: int) -> None:
        self.all_positions = all_positions
        self.fluid_count = fluid_count
        self.calls: list[object | None] = []

    def GetNumFluidMarkers(self) -> int:
        return self.fluid_count

    def GetParticlePositionsNumpy(self, group=None) -> np.ndarray:
        self.calls.append(group)
        if group is not None:
            raise TypeError("GetParticlePositionsNumpy() takes no marker group")
        return self.all_positions.copy()


class ParticleTransferTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fluid_group = object()
        self.fsi = SimpleNamespace(
            MarkerGroup=SimpleNamespace(FLUID=self.fluid_group)
        )

    def test_numpy_read_requests_fluid_group_only(self) -> None:
        expected = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        fluid = FluidOnlyBinding(expected, self.fluid_group)

        positions = crm.particle_positions_numpy(fluid, self.fsi)

        self.assertEqual(fluid.calls, [self.fluid_group])
        np.testing.assert_array_equal(positions, expected)

    def test_numpy_read_falls_back_to_fluid_prefix(self) -> None:
        fluid_positions = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
        bce_positions = np.array([[2.0, 0.0, 0.0], [3.0, 0.0, 0.0]])
        fluid = LegacyBinding(
            np.vstack((fluid_positions, bce_positions)), len(fluid_positions)
        )

        positions = crm.particle_positions_numpy(fluid, self.fsi)

        self.assertEqual(fluid.calls, [self.fluid_group, None])
        np.testing.assert_array_equal(positions, fluid_positions)


if __name__ == "__main__":
    unittest.main()
