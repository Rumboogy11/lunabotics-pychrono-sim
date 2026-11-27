#!/usr/bin/env python3
"""Regression coverage for simultaneous rigid-CRM and one-way FEA studies."""

from __future__ import annotations

import csv
import io
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "crm"))
os.environ.setdefault("PYCHRONO_GUI_NO_REEXEC", "1")

import crm.lunar_excavator_crm as crm_driver  # noqa: E402
import crm.lunar_excavator_oneway as oneway  # noqa: E402
import run_status  # noqa: E402
import study_gui as gui  # noqa: E402
import study_wizard as wiz  # noqa: E402


LOAD_FIELDS = [
    "time_s", "drum_z_m", "force_x_n", "force_y_n", "force_z_n",
    "torque_x_nm", "torque_y_nm", "torque_z_nm",
]


def write_loads(path: Path, times: tuple[float, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOAD_FIELDS)
        writer.writeheader()
        for t in times:
            writer.writerow({name: (f"{t:g}" if name == "time_s" else "0")
                             for name in LOAD_FIELDS})


def full_cfg(output_dir: Path) -> dict:
    return {
        "output_dir": output_dir,
        "geometry": "files",
        "step_file": None,
        "crm_obj": output_dir / "mesh" / "surface.obj",
        "fea_node": output_dir / "mesh" / "drum.node",
        "fea_ele": output_dir / "mesh" / "drum.ele",
        "mesh_steps": [],
        "auto_geometry": False,
        "drum_radius": 0.061037,
        "drum_half_length": 0.033,
        "inner_radius": 0.055,
        "spacing": 0.002,
        "d0_multiplier": 1.0,
        "artificial_viscosity": 0.5,
        "shifting_ppst_push": 3.0,
        "crm_dt": None,
        "capacity_volume": None,
        "detector_auto_size": True,
        "duration": 20.0,
        "fea_duration": 20.0,
        "crm_fps": 20.0,
        "fea_fps": 20.0,
        "loads_fps": 20.0,
        "particle_fps": 5.0,
        "particle_writers": 4,
        "fea_dt": 0.002,
        "bed_height": 0.06,
        "bed_padding": 0.25,
        "active_box": (0.631333, 0.687407, 0.866370),
        "settle_time": 0.001,
        "gravity": -9.81,
        "drum_gravity": False,
        "descent_mode": "kinematic",
        "descent_speed": 0.0015,
        "descent_force_n": 1.0,
        "descent_force_ramp_s": 0.0,
        "omega": 3.14,
        "reverse_spin": False,
        "spin_axis": "X",
        "hub_radius": 0.0015,
        "solver": "pardiso-mkl",
        "num_threads": 8,
        "concurrent_crm_fea": False,
        "load_radius_fraction": 0.95,
        "raw_stress_every": 5,
        "export_workers": 1,
        "save_particles": True,
        "fea_export_vtk": False,
        "fea_vtk_stride": 1,
        "export_vtk": False,
        "paraview_stride": 1,
        "paraview_particle_stride": 0,
        "paraview_particle_render_stride": 1,
        "soil": dict(wiz.SOIL_DEFAULTS),
        "pla": dict(wiz.PLA_DEFAULTS),
    }


def build_steps(cfg: dict) -> list[dict]:
    with redirect_stderr(io.StringIO()):
        return wiz.build_commands(cfg)


class RuntimeStatusLabelTest(unittest.TestCase):
    def test_frame_and_progress_lines_name_their_stage(self) -> None:
        self.assertEqual(
            run_status.frame_status("CRM", 3, 1.25, "inside=4 fill=0.1"),
            "[CRM] frame 00003  t=  1.250s  inside=4 fill=0.1",
        )
        self.assertEqual(
            run_status.frame_status("FEA", 0, 0.0, "vm=12MPa"),
            "[FEA] frame 00000  t=  0.000s  vm=12MPa",
        )
        self.assertEqual(
            run_status.frame_status("two-way FEA", 7, 2.0, "tau=1Nm"),
            "[two-way FEA] frame 00007  t=  2.000s  tau=1Nm",
        )
        self.assertEqual(
            run_status.time_progress("CRM", 2.0, 4.0),
            "  [CRM] t=  2.000s / 4s",
        )
        self.assertEqual(
            run_status.time_progress("FEA", 0.0, 0.2),
            "  [FEA] t=  0.000s / 0.2s",
        )

    def test_integer_progress_helper_fires_once_per_whole_second(self) -> None:
        self.assertEqual(
            run_status.integer_progress_times(0.133, 1.0), ([], 1.0),
        )
        self.assertEqual(
            run_status.integer_progress_times(1.0, 1.0), ([1.0], 2.0),
        )
        self.assertEqual(
            run_status.integer_progress_times(2.0, 1.0), ([1.0, 2.0], 3.0),
        )
        self.assertEqual(
            run_status.integer_progress_times(1.0 + 1.0e-13, 1.0), ([1.0], 2.0),
        )

    def test_drivers_route_status_lines_through_the_helper(self) -> None:
        import re

        sources = {
            "crm": (ROOT / "crm" / "lunar_excavator_crm.py").read_text(encoding="utf-8"),
            "oneway": (ROOT / "crm" / "lunar_excavator_oneway.py").read_text(encoding="utf-8"),
            "coupled": (ROOT / "crm" / "lunar_excavator_crm_fea.py").read_text(encoding="utf-8"),
        }
        for source in sources.values():
            self.assertIn("import run_status", source)
        for stage, source in (("CRM", sources["crm"]),
                              ("FEA", sources["oneway"]),
                              ("two-way FEA", sources["coupled"])):
            pattern = rf'run_status\.frame_status\(\s*"{stage}"'
            self.assertRegex(source, pattern)
            self.assertRegex(source, rf'run_status\.time_progress\(\s*"{stage}"')
        self.assertRegex(sources["oneway"], r'run_status\.integer_progress_times\(')
        self.assertNotRegex(
            sources["oneway"], r'run_status\.time_progress\(\s*"FEA",\s*t,',
        )


class CrmTerminationTest(unittest.TestCase):
    def tearDown(self) -> None:
        crm_driver._TERMINATING = False

    @unittest.skipIf(os.name == "nt", "POSIX signal handling test")
    def test_sigterm_is_converted_to_the_cleanup_path(self) -> None:
        with patch.object(crm_driver.signal, "signal") as register:
            crm_driver.install_termination_handler()

        register.assert_called_once()
        self.assertEqual(register.call_args.args[0], signal.SIGTERM)
        handler = register.call_args.args[1]
        with self.assertRaises(KeyboardInterrupt):
            handler(signal.SIGTERM, None)
        self.assertTrue(crm_driver._TERMINATING)

    def test_terminated_shutdown_cancels_pending_writer_work(self) -> None:
        class FakeExecutor:
            def __init__(self) -> None:
                self.kwargs = None

            def shutdown(self, **kwargs) -> None:
                self.kwargs = kwargs

        class FakeFuture:
            def result(self) -> None:
                raise AssertionError("pending writes must not be awaited on stop")

        executor = FakeExecutor()
        crm_driver.shutdown_particle_executor(
            executor, [FakeFuture()], terminating=True,
        )
        self.assertEqual(executor.kwargs, {"wait": True, "cancel_futures": True})

    def test_normal_shutdown_waits_for_pending_writer_work(self) -> None:
        class FakeExecutor:
            def __init__(self) -> None:
                self.kwargs = None

            def shutdown(self, **kwargs) -> None:
                self.kwargs = kwargs

        results: list[str] = []

        class FakeFuture:
            def result(self) -> None:
                results.append("done")

        executor = FakeExecutor()
        crm_driver.shutdown_particle_executor(executor, [FakeFuture()])
        self.assertEqual(results, ["done"])
        self.assertEqual(executor.kwargs, {"wait": True})


class FeaTerminationAndStateTest(unittest.TestCase):
    def tearDown(self) -> None:
        oneway._TERMINATING = False

    @unittest.skipIf(os.name == "nt", "POSIX signal handling test")
    def test_sigterm_only_records_a_deferred_cleanup_request(self) -> None:
        with patch.object(oneway.signal, "signal") as register:
            oneway.install_termination_handler()

        register.assert_called_once()
        self.assertEqual(register.call_args.args[0], signal.SIGTERM)
        handler = register.call_args.args[1]
        # Raising here can unwind through Chrono's native PardisoMKL call.
        handler(signal.SIGTERM, None)
        self.assertTrue(oneway.termination_requested())

    def test_replay_checks_termination_between_solver_steps(self) -> None:
        source = Path(oneway.__file__).read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r"system\.DoStepDynamics\(h\)\s+if termination_requested\(\):",
        )
        self.assertNotRegex(source, r"raise KeyboardInterrupt")

    def test_nonfinite_values_are_named(self) -> None:
        values = {
            "deformation": 1.0e-5,
            "force": np.array([1.0, 2.0, 3.0]),
            "motor": 0.5,
        }
        self.assertEqual(oneway.nonfinite_fea_values(values), [])
        values["force"] = np.array([1.0, float("nan"), 3.0])
        values["motor"] = float("inf")
        self.assertEqual(
            oneway.nonfinite_fea_values(values), ["force", "motor"]
        )

    def test_nonfinite_state_fails_before_a_frame_is_recorded(self) -> None:
        with self.assertRaisesRegex(SystemExit, "t=0.7 s.*deformation"):
            oneway.require_finite_fea_state(
                0.7, {"deformation": float("nan"), "stress": 1.0}
            )

    def test_terminated_vtk_writer_stops_via_event_without_draining(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.alive = True
                self.terminated = False
                self.killed = False
                self.joined = []

            def is_alive(self) -> bool:
                return self.alive

            def terminate(self) -> None:
                self.terminated = True
                self.alive = False

            def kill(self) -> None:
                self.killed = True
                self.alive = False

            def join(self, timeout=None) -> None:
                self.joined.append(timeout)

        class FakeEvent:
            def __init__(self, process: FakeProcess) -> None:
                self.process = process
                self.set_calls = 0

            def set(self) -> None:
                self.set_calls += 1
                self.process.alive = False

        process = FakeProcess()
        writer = oneway.BackgroundVtkWriter.__new__(oneway.BackgroundVtkWriter)
        writer._process = process
        writer._stop = FakeEvent(process)
        writer.closed = False
        with patch.object(writer, "_drain_results") as drain:
            writer.close(terminating=True)

        self.assertTrue(writer.closed)
        self.assertEqual(writer._stop.set_calls, 1)
        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)
        self.assertEqual(process.joined, [30])
        drain.assert_called_once_with()

    @unittest.skipIf(os.name == "nt", "POSIX signal handling test")
    def test_vtk_writer_ignores_the_runner_sigterm(self) -> None:
        class StoppedEvent:
            def is_set(self) -> bool:
                return True

        with patch.object(oneway.signal, "signal") as register:
            oneway._run_vtk_writer(object(), object(), object(), StoppedEvent())

        register.assert_called_once_with(signal.SIGTERM, signal.SIG_IGN)


class WizardConcurrentTest(unittest.TestCase):
    def test_parser_exposes_the_toggle(self) -> None:
        parser = wiz.build_parser()
        enabled = parser.parse_args(["--concurrent-crm-fea"])
        disabled = parser.parse_args(["--no-concurrent-crm-fea"])

        self.assertIs(enabled.concurrent_crm_fea, True)
        self.assertIs(disabled.concurrent_crm_fea, False)
        self.assertIsNone(parser.parse_args([]).concurrent_crm_fea)

    @unittest.skipIf(os.name == "nt", "POSIX process-group test")
    def test_output_pump_detects_parent_exit_with_inherited_stdout(self) -> None:
        # The child holds stdout open after the parent exits.  A plain
        # ``for line in stdout`` loop would wait for that sleeping child.
        script = (
            "import subprocess, sys\n"
            "subprocess.Popen([sys.executable, '-c', "
            "'import time; time.sleep(10)'], stdout=sys.stdout)\n"
            "print('parent output', flush=True)\n"
            "sys.exit(7)\n"
        )
        proc = subprocess.Popen(
            [sys.executable, "-c", script], stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, bufsize=0,
            start_new_session=True,
        )
        lines: list[str] = []
        stopped: list[subprocess.Popen] = []

        def stop_group(target: subprocess.Popen) -> None:
            stopped.append(target)
            try:
                os.killpg(target.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

        started = time.perf_counter()
        wiz.forward_process_output(
            proc, emit=lines.append, on_parent_exit=stop_group
        )
        elapsed = time.perf_counter() - started

        self.assertIn("parent output\n", lines)
        self.assertEqual(proc.wait(), 7)
        self.assertEqual(stopped, [proc])
        self.assertLess(elapsed, 3.0)

    def test_full_study_marks_the_pair_and_passes_live_flags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_wizard_", dir="/tmp/opencode") as tmp:
            out_dir = Path(tmp).resolve()
            cfg = full_cfg(out_dir)
            steps = build_steps(cfg)
            crm = next(s for s in steps if s["name"] == "rigid-CRM")
            fea = next(s for s in steps if s["name"] == "one-way-FEA")
            self.assertNotIn("concurrent_group", crm)
            self.assertNotIn("--wait-for-crm", fea["command"])

            cfg["concurrent_crm_fea"] = True
            steps = build_steps(cfg)
            crm = next(s for s in steps if s["name"] == "rigid-CRM")
            fea = next(s for s in steps if s["name"] == "one-way-FEA")
            marker = wiz.crm_alive_file(cfg)

            self.assertEqual(crm["concurrent_group"], wiz.CONCURRENT_CRM_FEA_GROUP)
            self.assertEqual(fea["concurrent_group"], wiz.CONCURRENT_CRM_FEA_GROUP)
            self.assertEqual(crm["crm_alive_file"], str(marker))
            self.assertIn("--wait-for-crm", fea["command"])
            self.assertEqual(
                fea["command"][fea["command"].index("--crm-alive-file") + 1],
                str(marker),
            )
            self.assertIn(str(out_dir / "crm" / "soil_loads.csv"),
                          crm.get("cleanup_files", []))

    def test_fea_only_never_enables_the_live_mode(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_fea_only_", dir="/tmp/opencode") as tmp:
            out_dir = Path(tmp).resolve()
            write_loads(out_dir / "crm" / "soil_loads.csv", (0.0, 0.1))
            (out_dir / "mesh").mkdir(parents=True, exist_ok=True)
            (out_dir / "mesh" / "drum.node").write_text("1 0 0 0\n", encoding="utf-8")
            (out_dir / "mesh" / "drum.ele").write_text("1 1 2 3 4\n", encoding="utf-8")
            cfg = full_cfg(out_dir)
            cfg["concurrent_crm_fea"] = True
            with redirect_stderr(io.StringIO()):
                steps = wiz.build_oneway_fea_only_commands(cfg)

            self.assertFalse(cfg["concurrent_crm_fea"])
            self.assertNotIn("--wait-for-crm", steps[0]["command"])
            self.assertNotIn("concurrent_group", steps[0])

    def test_commands_script_runs_the_pair_concurrently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_script_", dir="/tmp/opencode") as tmp:
            out_dir = Path(tmp).resolve()
            cfg = full_cfg(out_dir)
            cfg["concurrent_crm_fea"] = True
            steps = build_steps(cfg)
            wiz.write_reproducibility_files(cfg, steps)

            script = (out_dir / "commands.sh").read_text(encoding="utf-8")
            self.assertIn("# rigid-CRM + one-way-FEA (concurrent)", script)
            self.assertIn("crm_alive_marker=", script)
            self.assertIn("crm_pid=$!", script)
            self.assertIn('trap \'rm -f "$crm_alive_marker"\' EXIT', script)
            self.assertIn('wait "$crm_pid"', script)
            self.assertIn("--wait-for-crm", script)
            self.assertNotIn("# rigid-CRM\n", script)

            cfg["concurrent_crm_fea"] = False
            steps = build_steps(cfg)
            wiz.write_reproducibility_files(cfg, steps)
            sequential = (out_dir / "commands.sh").read_text(encoding="utf-8")
            self.assertIn("# rigid-CRM", sequential)
            self.assertNotIn("crm_pid", sequential)
            self.assertNotIn("--wait-for-crm", sequential)

    def test_concurrent_groups_preserve_step_order(self) -> None:
        steps = [
            {"name": "mesh"},
            {"name": "rigid-CRM", "concurrent_group": wiz.CONCURRENT_CRM_FEA_GROUP},
            {"name": "one-way-FEA", "concurrent_group": wiz.CONCURRENT_CRM_FEA_GROUP},
            {"name": "export"},
        ]
        groups = [[s["name"] for s in group] for group in wiz.concurrent_groups(steps)]
        self.assertEqual(groups, [["mesh"], ["rigid-CRM", "one-way-FEA"], ["export"]])


class LiveReplayHelperTest(unittest.TestCase):
    def test_marker_tracks_a_live_pid(self) -> None:
        with tempfile.TemporaryDirectory(prefix="crm_marker_", dir="/tmp/opencode") as tmp:
            marker = Path(tmp) / "crm_running.flag"
            self.assertFalse(oneway.crm_stage_alive(marker))
            marker.write_text(str(os.getpid()), encoding="utf-8")
            self.assertTrue(oneway.crm_stage_alive(marker))
            marker.write_text(f"pid:{os.getpid()}\n", encoding="utf-8")
            self.assertTrue(oneway.crm_stage_alive(marker))
            marker.write_text("99999999", encoding="utf-8")
            self.assertFalse(oneway.crm_stage_alive(marker))
            marker.unlink()
            self.assertFalse(oneway.crm_stage_alive(marker))

    def test_load_history_refresh_picks_up_new_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="live_loads_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "soil_loads.csv"
            write_loads(path, (0.0, 0.01))
            self.assertTrue(oneway.load_samples_ready(path))
            loads = oneway.LoadHistory(path)
            self.assertAlmostEqual(loads.t_end, 0.01)

            time.sleep(0.01)
            write_loads(path, (0.0, 0.01, 0.02, 0.03))
            self.assertTrue(loads.refresh())
            self.assertAlmostEqual(loads.t_end, 0.03)
            self.assertFalse(loads.refresh())

    def test_wait_decision_requires_live_crm_and_missing_frames(self) -> None:
        self.assertTrue(oneway.should_wait_for_load_frame(0.02, 0.01, True))
        self.assertFalse(oneway.should_wait_for_load_frame(0.02, 0.01, False))
        self.assertFalse(oneway.should_wait_for_load_frame(0.01, 0.01, True))

    def test_wait_for_history_reports_a_dead_crm_without_samples(self) -> None:
        with tempfile.TemporaryDirectory(prefix="wait_loads_", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "soil_loads.csv"
            with self.assertRaisesRegex(SystemExit, "CRM finished"):
                oneway.wait_for_load_history(path, alive_check=lambda: False)


class FakeProc:
    def __init__(self, text: str = "", *, pid: int | None = None,
                 wait_hook=None):
        self.stdout = io.StringIO(text)
        self.pid = pid if pid is not None else os.getpid()
        self._wait_hook = wait_hook
        self.waits = 0

    def wait(self) -> int:
        self.waits += 1
        if self.waits == 1 and self._wait_hook is not None:
            self._wait_hook()
        return 0

    def terminate(self) -> None:
        pass


class ConcurrentRunnerTest(unittest.TestCase):
    def _steps(self, tmp: str) -> list[dict]:
        out_dir = Path(tmp)
        marker = out_dir / "crm" / wiz.CRM_ALIVE_NAME
        loads = out_dir / "crm" / "soil_loads.csv"
        return [
            {
                "name": "rigid-CRM", "command": ["crm"],
                "log": str(out_dir / "rigid-CRM.log"),
                "concurrent_group": wiz.CONCURRENT_CRM_FEA_GROUP,
                "crm_alive_file": str(marker),
                "cleanup_files": [str(loads), str(marker)],
            },
            {
                "name": "one-way-FEA", "command": ["fea"],
                "log": str(out_dir / "one-way-FEA.log"),
                "concurrent_group": wiz.CONCURRENT_CRM_FEA_GROUP,
                "crm_alive_file": str(marker),
            },
        ]

    def test_cli_runner_removes_marker_when_crm_exits(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_cli_", dir="/tmp/opencode") as tmp:
            steps = self._steps(tmp)
            marker = Path(steps[0]["crm_alive_file"])
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text("stale", encoding="utf-8")

            class WaitForMarker(io.StringIO):
                def __iter__(self):
                    deadline = time.monotonic() + 5
                    while marker.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    yield "fea advanced\n"

            procs = [FakeProc("crm running\n"), FakeProc()]
            procs[1].stdout = WaitForMarker()
            with patch.object(wiz.subprocess, "Popen", side_effect=procs):
                results = wiz.run_concurrent_steps(steps)

            self.assertEqual(results, {"rigid-CRM": True, "one-way-FEA": True})
            self.assertFalse(marker.exists())
            self.assertIn("fea advanced\n", Path(steps[1]["log"]).read_text(encoding="utf-8"))

    def test_cli_records_each_stage_finish_time(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_cli_timing_", dir="/tmp/opencode") as tmp:
            steps = self._steps(tmp)
            procs = [
                FakeProc(wait_hook=lambda: time.sleep(0.05)),
                FakeProc(wait_hook=lambda: time.sleep(0.30)),
            ]
            elapsed: dict[str, float] = {}
            with patch.object(wiz.subprocess, "Popen", side_effect=procs):
                results = wiz.run_concurrent_steps(steps, elapsed=elapsed)

            self.assertTrue(all(results.values()))
            self.assertLess(elapsed["rigid-CRM"], elapsed["one-way-FEA"])
            self.assertGreater(
                elapsed["one-way-FEA"] - elapsed["rigid-CRM"], 0.1
            )


class ConcurrentGuiFieldTest(unittest.TestCase):
    def setUp(self) -> None:
        import tkinter as tk

        self._gui_module = gui
        self._tmp = tempfile.TemporaryDirectory(prefix="concurrent_gui_", dir="/tmp/opencode")
        self._original_defaults = gui.DEFAULT_FILE
        self._original_settings = gui.SETTINGS_FILE
        gui.DEFAULT_FILE = Path(self._tmp.name) / "defaults.json"
        # Keep workspace appearance settings (including ui_scale) out of these
        # tests; otherwise constructing the GUI can perturb later Tk tests.
        gui.SETTINGS_FILE = Path(self._tmp.name) / "settings.json"
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.StudyGUI(self.root)

    def tearDown(self) -> None:
        self.root.destroy()
        self._gui_module.DEFAULT_FILE = self._original_defaults
        self._gui_module.SETTINGS_FILE = self._original_settings
        self._tmp.cleanup()

    def _events(self):
        events = []
        while not self.app.log_queue.empty():
            events.append(self.app.log_queue.get_nowait())
        return events

    def _steps(self, tmp: str) -> list[dict]:
        out_dir = Path(tmp)
        marker = out_dir / "crm" / wiz.CRM_ALIVE_NAME
        return [
            {
                "name": "rigid-CRM", "command": ["crm"],
                "log": str(out_dir / "rigid-CRM.log"),
                "concurrent_group": wiz.CONCURRENT_CRM_FEA_GROUP,
                "crm_alive_file": str(marker),
                "cleanup_files": [],
            },
            {
                "name": "one-way-FEA", "command": ["fea"],
                "log": str(out_dir / "one-way-FEA.log"),
                "concurrent_group": wiz.CONCURRENT_CRM_FEA_GROUP,
                "crm_alive_file": str(marker),
            },
        ]

    def test_toggle_defaults_off_and_lives_under_fea_threads(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        def descendants(widget):
            for child in widget.winfo_children():
                yield child
                yield from descendants(child)

        labels = [
            child.cget("text") for child in descendants(self.app.pages[2])
            if isinstance(child, (tk.Label, ttk.Label))
        ]
        self.assertIn("FEA threads", labels)
        self.assertIn("Run CRM + one-way FEA together", labels)
        self.assertGreater(labels.index("Run CRM + one-way FEA together"),
                           labels.index("FEA threads"))
        self.assertFalse(self.app.vars["concurrent_crm_fea"].get())
        self.assertIn("concurrent_crm_fea", gui.PARAM_INFO)
        self.assertIn("concurrent_crm_fea", gui.DEFAULT_KEYS)

    def test_toggle_flows_into_generated_commands(self) -> None:
        self.app.vars["auto_geometry"].set(False)
        self.app.vars["concurrent_crm_fea"].set(True)
        cfg = self.app._collect_cfg()
        self.assertTrue(cfg["concurrent_crm_fea"])
        with redirect_stderr(io.StringIO()):
            steps = wiz.build_commands(cfg)
        fea = next(s for s in steps if s["name"] == "one-way-FEA")
        self.assertIn("--wait-for-crm", fea["command"])

        self.app.vars["concurrent_crm_fea"].set(False)
        cfg = self.app._collect_cfg()
        self.assertFalse(cfg["concurrent_crm_fea"])

    def test_config_round_trip_restores_the_toggle(self) -> None:
        self.app._apply_config({"concurrent_crm_fea": True})
        self.assertTrue(self.app.vars["concurrent_crm_fea"].get())
        self.app._apply_config({"concurrent_crm_fea": False})
        self.assertFalse(self.app.vars["concurrent_crm_fea"].get())

    def test_run_thread_starts_both_stages_together(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_gui_run_", dir="/tmp/opencode") as tmp:
            steps = self._steps(tmp)
            tail = {"name": "export-ParaView-VTK", "command": ["export"],
                    "log": str(Path(tmp) / "export.log")}
            calls = []

            def fake_popen(command, **_kwargs):
                calls.append(command)
                return FakeProc(f"{command[0]} running\n")

            self.app._stop_requested.clear()
            self.app._skip_requested.clear()
            with patch.object(gui.subprocess, "Popen", side_effect=fake_popen):
                self.app._run_thread({"output_dir": tmp}, steps + [tail])

            events = self._events()
            self.assertEqual(calls, [["crm"], ["fea"], ["export"]])
            self.assertIn(("step_done", "rigid-CRM"), events)
            self.assertIn(("step_done", "one-way-FEA"), events)
            self.assertIn(("step_done", "export-ParaView-VTK"), events)
            self.assertEqual(events[-1], ("done", "ok"))
            lines = [text for kind, text in events if kind == "line"]
            def seconds(line: str) -> float:
                return float(line.split(": ", 1)[1].removesuffix(" s\n"))

            crm_time = next(line for line in lines if line.startswith("  CRM: "))
            fea_time = next(line for line in lines if line.startswith("  One-way FEA: "))
            self.assertAlmostEqual(seconds(crm_time), seconds(fea_time), places=1)

    def test_gui_records_crm_finish_before_slower_fea(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_gui_timing_", dir="/tmp/opencode") as tmp:
            steps = self._steps(tmp)
            tail = {"name": "export-ParaView-VTK", "command": ["export"],
                    "log": str(Path(tmp) / "export.log")}
            procs = [
                FakeProc(wait_hook=lambda: time.sleep(0.05)),
                FakeProc(wait_hook=lambda: time.sleep(0.30)),
                FakeProc(),
            ]

            self.app._stop_requested.clear()
            self.app._skip_requested.clear()
            with patch.object(gui.subprocess, "Popen", side_effect=procs):
                self.app._run_thread({"output_dir": tmp}, steps + [tail])

            lines = [text for kind, text in self._events() if kind == "line"]
            def seconds(line: str) -> float:
                return float(line.split(": ", 1)[1].removesuffix(" s\n"))

            crm_time = seconds(next(line for line in lines if line.startswith("  CRM: ")))
            fea_time = seconds(next(line for line in lines if line.startswith("  One-way FEA: ")))
            self.assertGreater(fea_time - crm_time, 0.1)

    def test_gui_pumps_with_a_parent_exit_callback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_gui_pump_", dir="/tmp/opencode") as tmp:
            steps = self._steps(tmp)
            with patch.object(gui.subprocess, "Popen",
                              side_effect=[FakeProc(), FakeProc()]), \
                 patch.object(wiz, "forward_process_output") as forward:
                self.app._run_thread({"output_dir": tmp}, steps)

            self.assertEqual(forward.call_count, 2)
            for call in forward.call_args_list:
                self.assertEqual(call.kwargs["on_parent_exit"],
                                 self.app._terminate_step_process)

    def test_skip_group_continues_to_later_steps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_gui_skip_", dir="/tmp/opencode") as tmp:
            steps = self._steps(tmp)
            tail = {"name": "export-ParaView-VTK", "command": ["export"],
                    "log": str(Path(tmp) / "export.log")}

            def fake_popen(command, **_kwargs):
                if command == ["crm"]:
                    self.app._skip_requested.set()
                return FakeProc("running\n")

            self.app._stop_requested.clear()
            self.app._skip_requested.clear()
            with patch.object(gui.subprocess, "Popen", side_effect=fake_popen):
                self.app._run_thread({"output_dir": tmp}, steps + [tail])

            events = self._events()
            self.assertIn(("step_skipped", "rigid-CRM"), events)
            self.assertIn(("step_skipped", "one-way-FEA"), events)
            self.assertIn(("step_done", "export-ParaView-VTK"), events)
            self.assertEqual(events[-1], ("done", "ok"))

    def test_skip_step_stops_crm_before_fea(self) -> None:
        class LiveProc(FakeProc):
            def __init__(self) -> None:
                super().__init__("running\n")
                self.stopped = False

            def poll(self) -> int | None:
                return 0 if self.stopped else None

        crm, fea = LiveProc(), LiveProc()
        self.app._concurrent_by_name = {"rigid-CRM": crm, "one-way-FEA": fea}
        self.app._concurrent_planned_names = ("rigid-CRM", "one-way-FEA")
        self.app._concurrent_procs = [crm, fea]
        self.app._concurrent_skip_targets.clear()
        self.app._skip_requested.clear()
        stopped: list[object] = []

        with patch.object(self.app, "_step_process_alive",
                          side_effect=lambda p: p.poll() is None), \
             patch.object(self.app, "_terminate_step_process",
                          side_effect=lambda p: stopped.append(p)):
            self.app._skip_step()
            self.assertEqual(stopped, [crm])
            self.assertEqual(self.app._concurrent_skip_targets, {"rigid-CRM"})
            self.app._skip_step()
            self.assertEqual(stopped, [crm, fea])
            self.assertEqual(
                self.app._concurrent_skip_targets,
                {"rigid-CRM", "one-way-FEA"},
            )

        self.assertTrue(self.app._skip_requested.is_set())
        lines = [text for kind, text in self._events() if kind == "line"]
        self.assertTrue(any("skip requested for rigid-CRM" in line
                            and "one-way-FEA keeps running" in line for line in lines))
        self.assertTrue(any("skip requested for one-way-FEA" in line
                            for line in lines))

    def test_first_gui_skip_leaves_fea_complete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_gui_first_skip_",
                                         dir="/tmp/opencode") as tmp:
            steps = self._steps(tmp)
            tail = {"name": "export-ParaView-VTK", "command": ["export"],
                    "log": str(Path(tmp) / "export.log")}
            procs: dict[str, FakeProc] = {}
            stopped: list[object] = []

            def fake_popen(command, **_kwargs):
                proc = FakeProc("running\n")
                procs[command[0]] = proc
                if command == ["crm"]:
                    self.app._concurrent_skip_targets.add("rigid-CRM")
                    self.app._skip_requested.set()
                return proc

            self.app._stop_requested.clear()
            self.app._skip_requested.clear()
            self.app._concurrent_by_name.clear()
            self.app._concurrent_skip_targets.clear()
            with patch.object(gui.subprocess, "Popen", side_effect=fake_popen), \
                 patch.object(self.app, "_terminate_step_process",
                              side_effect=lambda p: stopped.append(p)):
                self.app._run_thread({"output_dir": tmp}, steps + [tail])

            self.assertEqual(stopped, [procs["crm"]])
            events = self._events()
            self.assertIn(("step_skipped", "rigid-CRM"), events)
            self.assertIn(("step_done", "one-way-FEA"), events)
            self.assertIn(("step_done", "export-ParaView-VTK"), events)
            self.assertEqual(events[-1], ("done", "ok"))

    def test_queued_fea_skip_applies_when_it_launches(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_gui_queued_skip_",
                                         dir="/tmp/opencode") as tmp:
            steps = self._steps(tmp)
            procs: dict[str, FakeProc] = {}
            stopped: list[object] = []
            original_put = self.app.log_queue.put

            def fake_popen(command, **_kwargs):
                proc = FakeProc("running\n")
                procs[command[0]] = proc
                return proc

            def put(item):
                # Simulate a second skip press arriving after CRM started but
                # before FEA's Popen has been published.
                if item[0] == "line" and "[rigid-CRM]" in str(item[1]):
                    self.app._concurrent_skip_targets = {"one-way-FEA"}
                    self.app._skip_requested.set()
                original_put(item)

            self.app._stop_requested.clear()
            self.app._skip_requested.clear()
            self.app._concurrent_by_name.clear()
            self.app._concurrent_skip_targets.clear()
            with patch.object(gui.subprocess, "Popen", side_effect=fake_popen), \
                 patch.object(self.app, "_terminate_step_process",
                              side_effect=lambda p: stopped.append(p)), \
                 patch.object(self.app.log_queue, "put", side_effect=put):
                self.app._run_thread({"output_dir": tmp}, steps)

            self.assertEqual(stopped, [procs["fea"]])
            events = self._events()
            self.assertIn(("step_done", "rigid-CRM"), events)
            self.assertIn(("step_skipped", "one-way-FEA"), events)
            self.assertEqual(events[-1], ("done", "ok"))

    def test_stop_group_aborts_later_steps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="concurrent_gui_stop_", dir="/tmp/opencode") as tmp:
            steps = self._steps(tmp)
            tail = {"name": "export-ParaView-VTK", "command": ["export"],
                    "log": str(Path(tmp) / "export.log")}

            def fake_popen(command, **_kwargs):
                if command == ["crm"]:
                    self.app._stop_requested.set()
                return FakeProc("running\n")

            self.app._stop_requested.clear()
            self.app._skip_requested.clear()
            with patch.object(gui.subprocess, "Popen", side_effect=fake_popen):
                self.app._run_thread({"output_dir": tmp}, steps + [tail])

            events = self._events()
            self.assertEqual(events[-1], ("done", "stopped"))
            self.assertFalse(any(kind == "step_done" for kind, _ in events))


class ModernGuiInheritsToggleTest(unittest.TestCase):
    def test_modern_gui_reuses_the_classic_toggle(self) -> None:
        import study_gui_modern as modern

        self.assertTrue(issubclass(modern.ModernStudyGUI, gui.StudyGUI))
        self.assertIn("concurrent_crm_fea", gui.shipped_defaults())
        self.assertFalse(gui.shipped_defaults()["concurrent_crm_fea"])


if __name__ == "__main__":
    unittest.main()
