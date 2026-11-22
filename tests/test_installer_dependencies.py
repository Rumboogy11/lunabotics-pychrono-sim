#!/usr/bin/env python3
"""Regression coverage for clean-install desktop/visualization dependencies."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class InstallerPackageListsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        start = cls.installer.index("install_system_packages() {")
        end = cls.installer.index("bootstrap_micromamba() {")
        cls.package_function = cls.installer[start:end]
        cls.arch = cls.package_function[
            cls.package_function.index("*arch*|*cachyos*)"):
            cls.package_function.index("*debian*|*ubuntu*)")
        ]
        cls.debian = cls.package_function[
            cls.package_function.index("*debian*|*ubuntu*)"):
            cls.package_function.index("    *)")
        ]

    def test_arch_installs_gui_and_paraview_packages(self) -> None:
        for package in (
            "paraview",
            "xorg-server-xvfb",
            "libxkbcommon-x11",
            "xcb-util-cursor",
            "libice",
            "libsm",
            "fontconfig",
        ):
            self.assertIn(package, self.arch, package)

    def test_debian_installs_gui_and_paraview_packages(self) -> None:
        for package in (
            "paraview",
            "xvfb",
            "libxkbcommon-x11-0",
            "libxcb-cursor0",
            "libice6",
            "libsm6",
            "libfontconfig1",
        ):
            self.assertIn(package, self.debian, package)

    def test_system_package_install_precedes_command_checks(self) -> None:
        package_call = self.installer.index("install_system_packages\n")
        command_check = self.installer.index('for cmd in git curl tar bzip2')
        self.assertLess(package_call, command_check)

    def test_sudo_is_used_only_for_system_packages(self) -> None:
        self.assertIn("sudo pacman", self.arch)
        self.assertIn("sudo apt-get", self.debian)
        for unprivileged_command in ("sudo pip", "sudo curl", "sudo git", "sudo cmake"):
            self.assertNotIn(unprivileged_command, self.installer)


class Chrono11BuildConfigurationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.env_spec = (ROOT / "environment/environment.yml").read_text(encoding="utf-8")
        cls.build_script = (ROOT / "environment/build-chrono-source.sh").read_text(encoding="utf-8")
        cls.installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        cls.oneway_launcher = (ROOT / "crm/run_oneway_fea.sh").read_text(encoding="utf-8")
        cls.runtime_env = (ROOT / "crm/chrono_runtime_env.sh").read_text(encoding="utf-8")

    def test_environment_spec_pins_supported_toolchain_and_mkl(self) -> None:
        for fragment in (
            "gcc=13.4.*",
            "gxx=13.4.*",
            "cuda-toolkit=12.9.*",
            "mkl-devel=2025.2.0",
        ):
            self.assertIn(fragment, self.env_spec, fragment)

    def test_build_script_enables_pardiso_mkl_and_robot_models(self) -> None:
        for fragment in (
            "-DCH_ENABLE_MODULE_ROBOT_MODELS=ON",
            "-DCH_ENABLE_MODULE_PARDISO_MKL=ON",
            "-DMKL_ROOT=\"$CHRONO_ENV\"",
            "-DMKL_THREADING=gnu_thread",
            "-DMKL_INTERFACE=lp64",
            "import pychrono.pardisomkl as pardiso",
        ):
            self.assertIn(fragment, self.build_script, fragment)

    def test_build_script_recovers_from_parallel_compiler_crash(self) -> None:
        self.assertIn("if ! cmake --build \"$CHRONO_BUILD\" --parallel \"$JOBS\"; then", self.build_script)
        self.assertIn("cmake --build \"$CHRONO_BUILD\" --parallel 1", self.build_script)
        self.assertIn("ulimit -s unlimited 2>/dev/null || true", self.build_script)

    def test_installer_does_not_reintroduce_a_four_job_cap(self) -> None:
        self.assertIn("thread_jobs=$(( total_threads - 2 ))", self.installer)
        self.assertNotIn("(( thread_jobs > 4 )) && thread_jobs=4", self.installer)

    def test_installer_verifies_chrono11_and_pardiso_binding(self) -> None:
        self.assertIn("set(CH_VERSION 0x00110000)", self.installer)
        self.assertIn("import pychrono.pardisomkl as pardiso", self.installer)

    def test_installer_applies_both_chrono_patches(self) -> None:
        self.assertIn('PY_PATCH_FILE="$PROJECT_ROOT/environment/patches/chrono-python-von-mises.patch"', self.installer)
        self.assertIn('for patch in "$PATCH_FILE" "$PY_PATCH_FILE"; do', self.installer)
        self.assertTrue((ROOT / "environment/patches/chrono-python-von-mises.patch").is_file())

    def test_oneway_launcher_always_uses_local_chrono_bindings(self) -> None:
        self.assertIn("chrono-install/share/chrono/python", self.oneway_launcher)
        self.assertIn("chrono-install/lib", self.oneway_launcher)
        self.assertIn("chrono_use_local_runtime", self.oneway_launcher)
        self.assertNotIn("if [[ \"$solver\" == \"gmres\"", self.oneway_launcher)

    def test_runtime_preload_includes_pardiso_mkl(self) -> None:
        self.assertIn("libChrono_pardisomkl.so", self.runtime_env)


class StudyGuiLauncherTest(unittest.TestCase):
    def test_launcher_selects_a_python_with_tkinter(self) -> None:
        launcher = (ROOT / "run_study_gui.sh").read_text(encoding="utf-8")
        self.assertIn("import tkinter", launcher)
        self.assertIn("chrono-env/bin/python", launcher)

    def test_launchers_expose_project_local_tools(self) -> None:
        for name in (
            "run_study.sh",
            "run_study_gui.sh",
            "run_full_model.sh",
            "crm/run.sh",
            "crm/run_fea.sh",
            "crm/run_oneway_fea.sh",
        ):
            launcher = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("chrono-env/bin:$PATH", launcher, name)


class InstallerBuildThreadPolicyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        start = cls.installer.index("cap_build_jobs() {")
        end = cls.installer.index("\n}\n", start) + len("\n}\n")
        cls.cap_function = cls.installer[start:end]
        cls.build_script = (ROOT / "environment/build-chrono-source.sh").read_text(
            encoding="utf-8"
        )
        cls.install_doc = (ROOT / "environment/INSTALL.md").read_text(encoding="utf-8")
        cls.cli_doc = (ROOT / "docs/cli-reference.md").read_text(encoding="utf-8")
        cls.dev_doc = (ROOT / "docs/developer-guide.md").read_text(encoding="utf-8")

    def _capped_jobs(self, total_threads: int, requested: int) -> int:
        with tempfile.TemporaryDirectory() as directory:
            fake_nproc = Path(directory) / "nproc"
            fake_nproc.write_text(f"#!/bin/sh\necho {total_threads}\n", encoding="utf-8")
            fake_nproc.chmod(0o755)
            env = dict(os.environ)
            env["PATH"] = os.pathsep.join([directory, env.get("PATH", "")])
            script = "\n".join(
                (
                    "set -eu",
                    "warn() { :; }",
                    self.cap_function,
                    f"build_jobs={requested}",
                    "cap_build_jobs",
                    'printf "%s\\n" "$build_jobs"',
                )
            )
            result = subprocess.run(
                ["bash", "-c", script],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            return int(result.stdout.strip())

    def test_installer_caps_build_jobs_to_total_threads_minus_two(self) -> None:
        self.assertEqual(self._capped_jobs(16, 16), 14)
        self.assertEqual(self._capped_jobs(16, 999), 14)
        self.assertEqual(self._capped_jobs(8, 4), 4)
        self.assertEqual(self._capped_jobs(2, 8), 1)
        self.assertEqual(self._capped_jobs(1, 8), 1)

    def test_standalone_build_defaults_to_total_threads_minus_two(self) -> None:
        self.assertIn("JOBS=$(( JOBS - 2 ))", self.build_script)
        self.assertIn("(( JOBS < 1 )) && JOBS=1", self.build_script)
        self.assertNotIn("(( JOBS > 4 )) && JOBS=4", self.build_script)

    def test_docs_match_build_thread_policy(self) -> None:
        self.assertIn("all but two", self.install_doc)
        self.assertIn("nproc - 2", self.install_doc)
        self.assertIn("nproc - 2", self.cli_doc)
        self.assertIn("nproc - 2", self.dev_doc)
        self.assertNotIn("four-job default cap", self.dev_doc)
        self.assertNotIn("default/ceiling: half of `nproc`", self.install_doc)


if __name__ == "__main__":
    unittest.main()
