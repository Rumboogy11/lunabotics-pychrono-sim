#!/usr/bin/env python3
"""Regression coverage for Chrono 10/11-compatible binding calls in crm/."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
CRM_DIR = ROOT / "crm"
sys.path.insert(0, str(CRM_DIR))

import numpy as np  # noqa: E402

import lunar_excavator_crm as crm  # noqa: E402
import lunar_excavator_crm_fea as fea_driver  # noqa: E402
from crm_soil import CrmSoilParams  # noqa: E402


class StubMaterial:
    """Accepts the same fields on both the Chrono 10 and Chrono 11 API."""

    def __init__(self) -> None:
        self.rheology_model = None


def stub_terrain(setter_name: str):
    calls = []
    return SimpleNamespace(**{setter_name: calls.append}), calls


class SoilApiShimTest(unittest.TestCase):
    """The shim must prefer Chrono 11 names and fall back to Chrono 10 names."""

    def _fsi(self, material_name: str):
        return SimpleNamespace(**{material_name: StubMaterial})

    def test_prefers_chrono11_soil_properties_set_crm_sph(self) -> None:
        fsi = self._fsi("SoilProperties")
        terrain, calls = stub_terrain("SetCrmSPH")

        CrmSoilParams(density=1700.0, youngs_modulus=3e6).apply(fsi, terrain)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].density, 1700.0)
        self.assertEqual(calls[0].Young_modulus, 3e6)

    def test_falls_back_to_chrono10_elastic_setter(self) -> None:
        fsi = self._fsi("ElasticMaterialProperties")
        terrain, calls = stub_terrain("SetElasticSPH")

        CrmSoilParams().apply(fsi, terrain)

        self.assertEqual(len(calls), 1)

    def test_prefers_chrono11_when_both_families_exist(self) -> None:
        fsi = SimpleNamespace(
            SoilProperties=StubMaterial,
            ElasticMaterialProperties=StubMaterial,
        )
        old, old_calls = stub_terrain("SetCrmSPH")
        new, new_calls = stub_terrain("SetElasticSPH")
        terrain = SimpleNamespace(SetCrmSPH=old.SetCrmSPH, SetElasticSPH=new.SetElasticSPH)

        CrmSoilParams().apply(fsi, terrain)

        self.assertEqual(len(old_calls), 1)
        self.assertEqual(len(new_calls), 0)

    def test_raises_when_no_supported_setter_exists(self) -> None:
        fsi = self._fsi("SoilProperties")
        terrain = SimpleNamespace()

        with self.assertRaises(RuntimeError):
            CrmSoilParams().apply(fsi, terrain)


class ParticleGetterTest(unittest.TestCase):
    """SPH fluid-marker getters must work with both binding signatures."""

    def test_chrono11_group_argument_is_used(self) -> None:
        calls = []

        def get_positions(group=None):
            calls.append(group)
            return np.zeros((3, 3))

        fluid = SimpleNamespace(GetParticlePositionsNumpy=get_positions)
        fsi = SimpleNamespace(MarkerGroup_FLUID="FLUID")

        result = crm.particle_positions_numpy(fluid, fsi)

        self.assertEqual(result.shape, (3, 3))
        self.assertEqual(calls, ["FLUID"])

    def test_chrono10_no_argument_falls_back_and_slices(self) -> None:
        all_positions = np.arange(15, dtype=float).reshape(5, 3)

        def get_positions(group=None):
            if group is not None:
                raise TypeError("no group argument in this binding")
            return all_positions

        fluid = SimpleNamespace(
            GetParticlePositionsNumpy=get_positions,
            GetNumFluidMarkers=lambda: 3,
        )
        fsi = SimpleNamespace(MarkerGroup_FLUID="FLUID")

        result = crm.particle_positions_numpy(fluid, fsi)

        np.testing.assert_array_equal(result, all_positions[:3])


class FlexBceGetterTest(unittest.TestCase):
    """The renamed flexible-BCE diagnostic getter must be source-compatible."""

    def test_two_way_driver_supports_renamed_getter(self) -> None:
        source = (ROOT / "crm/lunar_excavator_crm_fea.py").read_text(encoding="utf-8")
        self.assertIn("GetNumFleaMeshMarkers", source)
        self.assertIn("GetNumFlexBodyMarkers", source)


class VonMisesCompatTest(unittest.TestCase):
    """Chrono 11's stock SWIG wrapper omits GetEquivalentVonMises."""

    def test_patched_native_method_is_preferred(self) -> None:
        stress = SimpleNamespace(
            VonMises=lambda: 1.25e6,
            GetEquivalentVonMises=lambda: self.fail("patched method must win"),
        )
        self.assertEqual(fea_driver.von_mises_pa(stress), 1.25e6)

    def test_original_method_name_is_supported(self) -> None:
        stress = SimpleNamespace(GetEquivalentVonMises=lambda: 2.5e6)
        self.assertEqual(fea_driver.von_mises_pa(stress), 2.5e6)

    def test_raises_when_no_native_getter_exists(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "chrono-python-von-mises.patch"):
            fea_driver.von_mises_pa(SimpleNamespace(ComputePrincipalStresses=lambda *a: None))

    def test_installer_ships_the_swig_patch(self) -> None:
        patch = (ROOT / "environment/patches/chrono-python-von-mises.patch").read_text(encoding="utf-8")
        self.assertIn("ChTensors.i", patch)
        self.assertIn("VonMises()", patch)

    def test_fea_drivers_use_the_shared_helper(self) -> None:
        for name in ("crm/lunar_excavator_crm_fea.py", "crm/lunar_excavator_oneway.py"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn(".GetEquivalentVonMises()", source)
            self.assertIn("von_mises_pa(", source)

    def test_shell_material_density_call_is_guarded(self) -> None:
        source = (ROOT / "crm/lunar_excavator_crm_fea.py").read_text(encoding="utf-8")
        self.assertIn('hasattr(shell_material, "SetDensity")', source)


if __name__ == "__main__":
    unittest.main()
