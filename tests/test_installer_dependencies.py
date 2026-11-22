#!/usr/bin/env python3
"""Regression coverage for clean-install desktop/visualization dependencies."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
