#!/usr/bin/env python3
"""Coverage for FEA-only wizard runs and the Review & Run skip control."""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYCHRONO_GUI_NO_REEXEC", "1")

import study_wizard as wiz  # noqa: E402

LOAD_FIELDS = [
    "time_s", "drum_z_m", "force_x_n", "force_y_n", "force_z_n",
    "torque_x_nm", "torque_y_nm", "torque_z_nm",
]
FORCE_FIELDS = ["descent_mode", "descent_state", "actuator_force_n", "vertical_speed_m_s"]


def write_loads(path: Path, *, force: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = LOAD_FIELDS + (FORCE_FIELDS if force else [])
    row = {name: "0" for name in fields}
    row["time_s"] = "0.5"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerow(row)


def fea_cfg(out_dir: Path) -> dict:
    node = out_dir / "mesh" / "drum.node"
    ele = out_dir / "mesh" / "drum.ele"
    node.parent.mkdir(parents=True, exist_ok=True)
    node.write_text("1 0 0 0\n", encoding="utf-8")
    ele.write_text("1 1 2 3 4\n", encoding="utf-8")
    return {
        "output_dir": out_dir,
        "mesh_steps": [],
        "auto_geometry": False,
        "fea_node": node,
        "fea_ele": ele,
        "fea_duration": 0.4,
        "fea_dt": 0.002,
        "fea_fps": 20.0,
        "bed_height": 0.06,
        "bed_padding": 0.15,
        "settle_time": 0.001,
        "gravity": -9.81,
        "descent_mode": "kinematic",
        "descent_speed": 0.0015,
        "descent_force_n": 1.0,
        "descent_force_ramp_s": 0.0,
        "omega": 3.14,
        "reverse_spin": False,
        "spin_axis": "X",
        "drum_radius": 0.061,
        "drum_half_length": 0.033,
        "inner_radius": 0.055,
        "hub_radius": 0.012,
        "solver": "sparse-lu",
        "num_threads": 4,
        "load_radius_fraction": 0.95,
        "raw_stress_every": 5,
        "pla": dict(wiz.PLA_DEFAULTS),
        "export_vtk": True,
        "fea_export_vtk": False,
        "fea_vtk_stride": 1,
    }


class OneWayFeaOnlyTest(unittest.TestCase):
    def test_only_fea_step_is_built_and_repro_files_are_separate(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fea_only_", dir="/tmp/opencode") as tmp:
            out_dir = Path(tmp)
            write_loads(out_dir / "crm" / "soil_loads.csv")
            cfg = fea_cfg(out_dir)

            steps = wiz.build_oneway_fea_only_commands(cfg)

            self.assertEqual([step["name"] for step in steps], ["one-way-FEA"])
            command = steps[0]["command"]
            self.assertTrue(command[0].endswith("run_oneway_fea.sh"))
            self.assertEqual(command[command.index("--loads") + 1],
                             str(out_dir / "crm" / "soil_loads.csv"))
            self.assertEqual(steps[0]["log"], str(out_dir / "one-way-FEA.log"))
            self.assertFalse(cfg["export_vtk"])
            self.assertNotIn("generate-FEA-mesh", cfg["commands"][0]["name"])
            self.assertNotIn("rigid-CRM", cfg["commands"][0]["name"])

            wiz.write_reproducibility_files(
                cfg, steps,
                config_name="oneway_fea_config.json",
                commands_name="oneway_fea_commands.sh",
            )
            self.assertTrue((out_dir / "oneway_fea_config.json").is_file())
            self.assertTrue((out_dir / "oneway_fea_commands.sh").is_file())
            self.assertFalse((out_dir / "study_config.json").exists())
            self.assertFalse((out_dir / "commands.sh").exists())

    def test_missing_crm_data_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fea_only_missing_", dir="/tmp/opencode") as tmp:
            cfg = fea_cfg(Path(tmp))
            with self.assertRaisesRegex(wiz.StudyConfigError, "no CRM load history"):
                wiz.build_oneway_fea_only_commands(cfg)

    def test_crm_config_and_csv_mode_are_honoured(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fea_only_crm_", dir="/tmp/opencode") as tmp:
            out_dir = Path(tmp)
            write_loads(out_dir / "crm" / "soil_loads.csv", force=True)
            (out_dir / "crm" / "config.json").write_text(json.dumps({
                "run": {
                    "duration": 3.0,
                    "gravity": -5.0,
                    "descent_mode": "kinematic",  # CSV header wins below
                    "descent_speed": 0.003,
                    "omega": 2.5,
                    "reverse_spin": True,
                    "bed_height": 0.2,
                    "bed_padding": 0.25,
                    "settle_time": 0.01,
                    "spin_axis": "Z",
                    "drum_radius": 0.123,
                    "drum_half_length": 0.077,
                    "inner_radius": 0.101,
                    "fps": 30.0,
                },
            }), encoding="utf-8")
            cfg = fea_cfg(out_dir)

            command = wiz.build_oneway_fea_only_commands(cfg)[0]["command"]

            # Force-mode soil_loads.csv adds descent columns; the driver rejects
            # a kinematic replay, so the header must override crm/config.json.
            self.assertEqual(command[command.index("--descent-mode") + 1], "force")
            self.assertEqual(command[command.index("--gravity") + 1], "-5")
            self.assertEqual(command[command.index("--bed-height") + 1], "0.2")
            self.assertEqual(command[command.index("--bed-padding") + 1], "0.25")
            self.assertEqual(command[command.index("--spin-axis") + 1], "Z")
            self.assertEqual(command[command.index("--drum-radius") + 1], "0.123")
            self.assertEqual(command[command.index("--drum-half-length") + 1], "0.077")
            self.assertEqual(command[command.index("--inner-radius") + 1], "0.101")
            self.assertEqual(command[command.index("--omega") + 1], "2.5")
            self.assertIn("--reverse-spin", command)

    def test_project_mesh_is_used_instead_of_stale_gui_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="fea_only_mesh_", dir="/tmp/opencode") as tmp:
            out_dir = Path(tmp) / "project"
            out_dir.mkdir()
            other_dir = Path(tmp) / "other"
            other_dir.mkdir()
            write_loads(out_dir / "crm" / "soil_loads.csv")

            project_mesh = fea_cfg(out_dir)  # creates project mesh files
            stale_node = other_dir / "stale.node"
            stale_ele = other_dir / "stale.ele"
            stale_node.write_text("1 0 0 0\n", encoding="utf-8")
            stale_ele.write_text("1 1 2 3 4\n", encoding="utf-8")

            (out_dir / "study_config.json").write_text(json.dumps({
                "fea_node": str(project_mesh["fea_node"]),
                "fea_ele": str(project_mesh["fea_ele"]),
            }), encoding="utf-8")

            cfg = fea_cfg(out_dir)
            cfg["fea_node"] = stale_node
            cfg["fea_ele"] = stale_ele

            command = wiz.build_oneway_fea_only_commands(cfg)[0]["command"]

            self.assertEqual(command[command.index("--fea-node") + 1],
                             str(project_mesh["fea_node"]))
            self.assertEqual(command[command.index("--fea-ele") + 1],
                             str(project_mesh["fea_ele"]))


class GuiSkipControlTest(unittest.TestCase):
    def setUp(self) -> None:
        import tkinter as tk

        import study_gui as gui

        self.gui = gui
        self._tmp = tempfile.TemporaryDirectory(prefix="gui_skip_", dir="/tmp/opencode")
        self._original_defaults = gui.DEFAULT_FILE
        gui.DEFAULT_FILE = Path(self._tmp.name) / "defaults.json"
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.StudyGUI(self.root)

    def tearDown(self) -> None:
        self.root.destroy()
        self.gui.DEFAULT_FILE = self._original_defaults
        self._tmp.cleanup()

    def test_fea_only_button_is_in_bottom_nav_and_skip_is_on_review_page(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        def labels(widget):
            return [
                child.cget("text")
                for child in descendants(widget)
                if isinstance(child, (tk.Label, ttk.Label, ttk.Button))
            ]

        nav_labels = labels(self.app.study_tab)
        self.assertIn("Run study", nav_labels)
        self.assertIn("Run one-way FEA only (existing CRM)", nav_labels)

        review_labels = labels(self.app.pages[-1])
        self.assertNotIn("Run one-way FEA only (existing CRM)", review_labels)
        self.assertIn("Skip current step", review_labels)
        self.assertIn("Stop running process", review_labels)
        self.assertIsInstance(self.app._skip_requested, threading.Event)

    def _collect_events(self):
        events = []
        while not self.app.log_queue.empty():
            events.append(self.app.log_queue.get_nowait())
        return events

    def test_skip_marks_step_skipped_and_continues(self) -> None:
        class FakeProc:
            def __init__(self, command):
                self.command = command
                self.stdout = io.StringIO("running\n")

            def terminate(self) -> None:
                pass

            def poll(self) -> int:
                return 0

            def wait(self) -> int:
                return 0

        calls = {"count": 0}

        def fake_popen(command, **_kwargs):
            calls["count"] += 1
            proc = FakeProc(command)
            if calls["count"] == 1:
                # Simulate the Skip button firing while the first step is live.
                self.app._skip_requested.set()
            return proc

        steps = [
            {"name": "first", "command": ["first"],
             "log": str(Path(self._tmp.name) / "first.log")},
            {"name": "second", "command": ["second"],
             "log": str(Path(self._tmp.name) / "second.log")},
        ]
        self.app._stop_requested.clear()
        self.app._skip_requested.clear()
        with patch.object(self.gui.subprocess, "Popen", side_effect=fake_popen):
            self.app._run_thread({"output_dir": self._tmp.name}, steps)

        events = self._collect_events()
        self.assertIn(("step_skipped", "first"), events)
        self.assertIn(("step_done", "second"), events)
        self.assertEqual(events[-1], ("done", "ok"))
        self.assertEqual(calls["count"], 2)

    @unittest.skipIf(os.name == "nt", "process-group test requires POSIX fork")
    def test_skip_kills_process_group_and_reaches_fea_stage(self) -> None:
        """A skipped CRM step must not hang the run on inherited stdout.

        The CRM driver spawns particle-writer workers that inherit the stdout
        pipe.  Terminating only the driver PID leaves those workers alive, so
        ``_run_thread`` blocks in ``for line in proc.stdout`` forever and the
        one-way FEA stage never starts.  Skip must signal the whole process
        group so the pipe closes and the pipeline advances.
        """
        import subprocess as sp

        marker = Path(self._tmp.name) / "fea_ran.txt"
        # Fork a long-lived child that inherits stdout, mirroring the CRM
        # particle writers.  Without a group kill the reader never sees EOF.
        crm_script = (
            "import os,time\n"
            "pid=os.fork()\n"
            "if pid==0:\n"
            "    time.sleep(15)\n"
            "    os._exit(0)\n"
            "print('crm running',flush=True)\n"
            "time.sleep(15)\n"
        )
        steps = [
            {"name": "rigid-CRM", "command": [sys.executable, "-c", crm_script],
             "log": str(Path(self._tmp.name) / "crm.log")},
            {"name": "one-way-FEA",
             "command": [sys.executable, "-c",
                         f"open({str(marker)!r},'w').write('yes')"],
             "log": str(Path(self._tmp.name) / "fea.log")},
        ]

        done = threading.Event()

        def run() -> None:
            self.app._stop_requested.clear()
            self.app._skip_requested.clear()
            self.app._run_thread({"output_dir": self._tmp.name}, steps)
            done.set()

        worker = threading.Thread(target=run, daemon=True)
        worker.start()

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            proc = self.app.proc
            if isinstance(proc, sp.Popen) and proc.poll() is None:
                break
            time.sleep(0.02)
        else:
            self.fail("CRM step never started")

        with patch.object(self.gui.messagebox, "showinfo"):
            self.app._skip_step()

        self.assertTrue(
            done.wait(8),
            "run thread hung after skip; the one-way FEA stage never started",
        )
        self.assertTrue(marker.exists(), "FEA step did not run after skipping CRM")
        events = self._collect_events()
        self.assertIn(("step_skipped", "rigid-CRM"), events)
        self.assertIn(("step_done", "one-way-FEA"), events)
        self.assertEqual(events[-1], ("done", "ok"))

    def test_stop_aborts_without_starting_later_steps(self) -> None:
        class FakeProc:
            def __init__(self, command):
                self.command = command
                self.stdout = io.StringIO("running\n")

            def terminate(self) -> None:
                pass

            def poll(self) -> int:
                return 0

            def wait(self) -> int:
                return 0

        calls = {"count": 0}

        def fake_popen(command, **_kwargs):
            calls["count"] += 1
            self.app._stop_requested.set()
            return FakeProc(command)

        steps = [
            {"name": "first", "command": ["first"],
             "log": str(Path(self._tmp.name) / "first.log")},
            {"name": "second", "command": ["second"],
             "log": str(Path(self._tmp.name) / "second.log")},
        ]
        self.app._stop_requested.clear()
        self.app._skip_requested.clear()
        with patch.object(self.gui.subprocess, "Popen", side_effect=fake_popen):
            self.app._run_thread({"output_dir": self._tmp.name}, steps)

        events = self._collect_events()
        self.assertIn(("done", "stopped"), events)
        self.assertEqual(calls["count"], 1)


if __name__ == "__main__":
    unittest.main()
