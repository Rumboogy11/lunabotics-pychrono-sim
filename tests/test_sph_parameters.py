#!/usr/bin/env python3
"""Regression coverage for rigid-CRM SPH boundary settings."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(CRM_DIR))

import lunar_excavator_crm as crm  # noqa: E402


class StubParams:
    num_bce_layers = 0
    max_velocity = 0.0
    num_proximity_search_steps = 0
    shifting_ppst_push = 0.0
    shifting_ppst_pull = 0.0


def stub_fsi():
    return SimpleNamespace(
        SPHParameters=StubParams,
        IntegrationScheme=SimpleNamespace(RK2=1),
        EosType=SimpleNamespace(ISOTHERMAL=2),
        ViscosityMethod=SimpleNamespace(ARTIFICIAL_BILATERAL=3),
        BoundaryMethod=SimpleNamespace(ADAMI=4),
        KernelType=SimpleNamespace(CUBIC_SPLINE=5),
        ShiftingMethod=SimpleNamespace(PPST=6),
    )


class SphParametersTest(unittest.TestCase):
    def test_rigid_driver_requests_three_bce_layers(self) -> None:
        run = crm.make_run_params(crm.build_parser().parse_args(["--omega", "3.14"]))
        captured: list[StubParams] = []
        terrain = SimpleNamespace(SetSPHParameters=captured.append)

        crm.configure_sph(stub_fsi(), terrain, run)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].num_bce_layers, 3)
        self.assertEqual(captured[0].initial_spacing, run.spacing)

    def test_too_few_bce_layers_rejected(self) -> None:
        with self.assertRaises(ValueError):
            replace(crm.CrmRunParams(), bce_layers=2)


if __name__ == "__main__":
    unittest.main()
