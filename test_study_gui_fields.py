#!/usr/bin/env python3
"""Regression coverage for the wizard's output-cadence fields."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYCHRONO_GUI_NO_REEXEC", "1")


class WizardCadenceFieldsTest(unittest.TestCase):
    def test_particle_fps_is_editable_next_to_crm_summary_fps(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            run_page = app.pages[2]  # "3. Run & Domain"
            labels = [
                child.cget("text")
                for child in run_page.winfo_children()
                if isinstance(child, (tk.Label, ttk.Label))
            ]
            self.assertIn("CRM summary FPS", labels)
            self.assertIn("Particle CSV FPS", labels)

            app.vars["crm_fps"].set("20")
            app.vars["particle_fps"].set("20")
            cfg = app._collect_cfg()
            self.assertEqual(cfg["crm_fps"], 20.0)
            self.assertEqual(cfg["particle_fps"], 20.0)

            with tempfile.TemporaryDirectory(prefix="cadence_", dir="/tmp/opencode") as tmp:
                cfg["output_dir"] = Path(tmp)
                steps = gui.wiz.build_commands(cfg)
                crm_cmd = next(step["command"] for step in steps if step["name"] == "rigid-CRM")
                self.assertEqual(crm_cmd[crm_cmd.index("--particle-fps") + 1], "20")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
