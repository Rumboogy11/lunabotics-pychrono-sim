#!/usr/bin/env python3
"""Regression coverage for the wizard's output-cadence fields."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PYCHRONO_GUI_NO_REEXEC", "1")


class IsolatedGuiDefaultsTest(unittest.TestCase):
    """Keep user-saved GUI defaults from leaking into regression tests."""

    def setUp(self) -> None:
        import study_gui as gui

        self._tmp = tempfile.TemporaryDirectory(prefix="gui_defaults_", dir="/tmp/opencode")
        self._original_defaults = gui.DEFAULT_FILE
        gui.DEFAULT_FILE = Path(self._tmp.name) / "defaults.json"

    def tearDown(self) -> None:
        import study_gui as gui

        gui.DEFAULT_FILE = self._original_defaults
        self._tmp.cleanup()


class WizardCadenceFieldsTest(IsolatedGuiDefaultsTest):
    def test_preview_particle_sample_default_is_5000(self) -> None:
        import study_gui as gui

        self.assertEqual(gui.SHIPPED_SETTINGS["preview_particle_limit"], 5000)

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

    def test_part_aware_interior_coarsening_is_editable_and_default_on(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            self.assertTrue(app._gb("part_aware_simplify"))

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            geom_page = app.pages[1]  # "2. Geometry and mesh inputs"
            labels = [child.cget("text") for child in descendants(geom_page)
                      if isinstance(child, (tk.Label, ttk.Label))]
            self.assertIn("Coarsen hub/interior mesh", labels)
            self.assertIn("Interior coarsen radius (m)", labels)
            self.assertIn("Interior reduction", labels)
            self.assertIn("Exterior fit tolerance (m)", labels)

            with tempfile.TemporaryDirectory(prefix="meshflags_", dir="/tmp/opencode") as tmp:
                app.vars["geometry"].set("step")
                app.vars["output_dir"].set(tmp)
                app.vars["step_file"].set(str(ROOT / "geometry" / "spikey" / "Assembly 1.step"))
                app.vars["surface_repair_order"].set("none")
                app.vars["surface_target_faces"].set("8000")
                app.vars["surface_fit_tolerance"].set("0.0015")
                app.vars["part_aware_simplify"].set(True)
                app.vars["part_aware_radius"].set("0.06")
                app.vars["part_aware_reduction"].set("0.8")
                app.vars["part_aware_min_faces"].set("400")
                cfg = app._collect_cfg()
                steps = gui.wiz.build_commands(cfg)
                mesh_cmd = next(s["command"] for s in steps if s["name"] == "generate-FEA-mesh")
                self.assertEqual(cfg["surface_fit_tolerance"], 0.0015)
                self.assertIn("--part-aware-simplify", mesh_cmd)
                self.assertEqual(mesh_cmd[mesh_cmd.index("--surface-target-faces") + 1], "8000")
                self.assertEqual(mesh_cmd[mesh_cmd.index("--surface-fit-tolerance") + 1], "0.0015")
                self.assertEqual(mesh_cmd[mesh_cmd.index("--part-aware-radius") + 1], "0.06")
                self.assertEqual(mesh_cmd[mesh_cmd.index("--part-aware-reduction") + 1], "0.8")
                self.assertEqual(mesh_cmd[mesh_cmd.index("--part-aware-min-faces") + 1], "400")
        finally:
            root.destroy()


class SurfaceFitToleranceFieldTest(unittest.TestCase):
    def test_defaults_use_measured_one_millimetre_fit(self) -> None:
        import study_gui as gui
        import study_wizard as wiz

        self.assertEqual(gui.shipped_defaults()["surface_fit_tolerance"], "0.001")
        self.assertEqual(
            wiz.geometry_defaults(None, auto=False)["surface_fit_tolerance"], 0.001
        )

    def test_wizard_config_and_mesh_command_carry_fit_tolerance(self) -> None:
        import study_wizard as wiz

        step = ROOT / "geometry" / "spikey" / "Assembly 1.step"
        with tempfile.TemporaryDirectory(prefix="surface_fit_", dir="/tmp/opencode") as tmp:
            args = wiz.build_parser().parse_args([
                "--geometry", "step",
                "--step-file", str(step),
                "--output-dir", tmp,
                "--surface-target-faces", "8000",
                "--surface-fit-tolerance", "0.002",
                "--dry-run",
            ])
            cfg = wiz.collect_settings(args)
            self.assertEqual(cfg["surface_fit_tolerance"], 0.002)
            steps = wiz.build_commands(cfg)
            mesh_cmd = next(s["command"] for s in steps if s["name"] == "generate-FEA-mesh")
            self.assertEqual(mesh_cmd[mesh_cmd.index("--surface-target-faces") + 1], "8000")
            self.assertEqual(mesh_cmd[mesh_cmd.index("--surface-fit-tolerance") + 1], "0.002")


class MeshFillDetectorRemovedTest(IsolatedGuiDefaultsTest):
    def test_mesh_detector_options_are_gone(self) -> None:
        import study_gui as gui
        import study_wizard as wiz

        self.assertNotIn("fill_detector_step", gui.shipped_defaults())
        self.assertNotIn(
            "fill_detector_step",
            [key for _, key, _ in gui.DEFAULT_GROUPS[0][1]],
        )
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            wiz.build_parser().parse_args([
                "--fill-detector-step", "ignored.step",
            ])


class Full3dFeaToggleTest(IsolatedGuiDefaultsTest):
    def test_full_3d_fea_export_is_off_by_default(self) -> None:
        import study_gui as gui

        self.assertFalse(gui.shipped_defaults()["fea_export_vtk"])
        self.assertEqual(gui.shipped_defaults()["fea_vtk_stride"], "1")

    def test_export_and_viewer_toggles_are_editable(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            self.assertFalse(app._gb("fea_export_vtk"))
            self.assertTrue(app.tools_show_fea_3d.get())

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            export_page = app.pages[4]  # "5. Output & Export"
            labels = [child.cget("text") for child in descendants(export_page)
                      if isinstance(child, (tk.Label, ttk.Label))]
            self.assertIn("Export full 3D FEA stress animation", labels)
            self.assertIn("FEA VTU frame stride", labels)

            tool_labels = [child.cget("text") for child in descendants(app.tools_tab)
                           if isinstance(child, (tk.Label, ttk.Checkbutton))]
            self.assertIn("Load 3D FEA stress/deformation in ParaView (if valid)", tool_labels)
            self.assertIn("Show one-way FEA scalar/deformation history charts", tool_labels)
            self.assertFalse(app.tools_show_fea_charts.get())

            with tempfile.TemporaryDirectory(prefix="vtk_toggle_", dir="/tmp/opencode") as tmp:
                app.vars["output_dir"].set(tmp)
                app.vars["fea_export_vtk"].set(True)
                app.vars["fea_vtk_stride"].set("3")
                cfg = app._collect_cfg()
                steps = gui.wiz.build_commands(cfg)
                fea_cmd = next(s["command"] for s in steps if s["name"] == "one-way-FEA")
                self.assertIn("--export-vtk", fea_cmd)
                self.assertEqual(fea_cmd[fea_cmd.index("--vtk-stride") + 1], "3")

                app.vars["fea_export_vtk"].set(False)
                cfg = app._collect_cfg()
                steps = gui.wiz.build_commands(cfg)
                fea_cmd = next(s["command"] for s in steps if s["name"] == "one-way-FEA")
                self.assertNotIn("--export-vtk", fea_cmd)
                self.assertNotIn("--vtk-stride", fea_cmd)
        finally:
            root.destroy()


class ParaViewFeaLoadTest(IsolatedGuiDefaultsTest):
    def test_paraview_button_hides_charts_and_clears_inherited_env(self) -> None:
        import tkinter as tk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            self.assertTrue(app.tools_show_fea_3d.get())
            self.assertFalse(app.tools_show_fea_charts.get())
            with tempfile.TemporaryDirectory(prefix="pv_fea_", dir="/tmp/opencode") as tmp:
                app.tools_study_dir.set(tmp)
                calls = []

                class FakeProc:
                    stdout = None

                def fake_popen(cmd, **kwargs):
                    calls.append((cmd, kwargs))
                    return FakeProc()

                with patch.dict(os.environ, {"PYCHRONO_FEA_CHARTS": "1"}), \
                     patch.object(gui.shutil, "which",
                                  side_effect=lambda name: None if name == "paraview" else "/usr/bin/pvpython"), \
                     patch.object(gui.subprocess, "Popen", fake_popen), \
                     patch.object(gui.threading, "Thread"):
                    app._tool_paraview()
                self.assertTrue(calls)
                self.assertEqual(calls[0][1]["env"]["PYCHRONO_WITH_FEA"], "1")
                self.assertNotIn("PYCHRONO_FEA_CHARTS", calls[0][1]["env"])
                self.assertNotIn("--show-fea-charts", calls[0][0])
        finally:
            root.destroy()

    def test_paraview_button_chart_toggle_opts_in(self) -> None:
        import tkinter as tk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            app.tools_show_fea_charts.set(True)
            app.tools_show_fea_3d.set(False)
            with tempfile.TemporaryDirectory(prefix="pv_fea_charts_", dir="/tmp/opencode") as tmp:
                app.tools_study_dir.set(tmp)
                calls = []

                class FakeProc:
                    stdout = None

                def fake_popen(cmd, **kwargs):
                    calls.append((cmd, kwargs))
                    return FakeProc()

                with patch.dict(os.environ, {"PYCHRONO_FEA_CHARTS": ""}), \
                     patch.object(gui.shutil, "which",
                                  side_effect=lambda name: None if name == "paraview" else "/usr/bin/pvpython"), \
                     patch.object(gui.subprocess, "Popen", fake_popen), \
                     patch.object(gui.threading, "Thread"):
                    app._tool_paraview()
                self.assertTrue(calls)
                self.assertEqual(calls[0][1]["env"]["PYCHRONO_FEA_CHARTS"], "1")
                self.assertEqual(calls[0][1]["env"]["PYCHRONO_WITH_FEA"], "0")
                self.assertIn("--show-fea-charts", calls[0][0])
                self.assertNotIn("--with-fea", calls[0][0])
        finally:
            root.destroy()


class WizardParaViewLaunchTest(IsolatedGuiDefaultsTest):
    def test_after_run_launch_requests_fea_and_marks_gui_mode(self) -> None:
        import study_wizard as wiz

        with tempfile.TemporaryDirectory(prefix="wiz_pv_", dir="/tmp/opencode") as tmp:
            study_dir = Path(tmp)
            paraview_dir = study_dir / "paraview"
            paraview_dir.mkdir()
            calls = []

            def fake_popen(cmd, **kwargs):
                calls.append((cmd, kwargs))
                return type("Proc", (), {})()

            with patch.dict(os.environ, {"PYCHRONO_FEA_CHARTS": "1"}), \
                 patch.object(wiz.shutil, "which", return_value="/usr/bin/paraview"), \
                 patch.object(wiz.subprocess, "Popen", fake_popen):
                ok = wiz.launch_paraview_viewer(
                    ROOT / "view_study_paraview.py", paraview_dir, study_dir)
            self.assertTrue(ok)
            self.assertTrue(calls)
            env = calls[0][1]["env"]
            self.assertEqual(env["PYCHRONO_WITH_FEA"], "1")
            self.assertEqual(env["PYCHRONO_FEA_CHARTS"], "1")
            self.assertNotIn("--show-fea-charts", calls[0][0])
            self.assertEqual(env["PYCHRONO_PARAVIEW_GUI"], "1")
            self.assertEqual(env["PYCHRONO_EXPORT_DIR"], str(paraview_dir))
            self.assertEqual(env["PYCHRONO_SCRIPT_DIR"], str(ROOT))

    def test_after_run_launch_chart_state_overrides_inherited_env(self) -> None:
        import study_wizard as wiz

        with tempfile.TemporaryDirectory(prefix="wiz_pv_charts_", dir="/tmp/opencode") as tmp:
            study_dir = Path(tmp)
            paraview_dir = study_dir / "paraview"
            paraview_dir.mkdir()
            calls = []

            def fake_popen(cmd, **kwargs):
                calls.append((cmd, kwargs))
                return type("Proc", (), {})()

            with patch.dict(os.environ, {"PYCHRONO_FEA_CHARTS": "1"}), \
                 patch.object(wiz.shutil, "which", return_value="/usr/bin/paraview"), \
                 patch.object(wiz.subprocess, "Popen", fake_popen):
                self.assertTrue(wiz.launch_paraview_viewer(
                    ROOT / "view_study_paraview.py", paraview_dir, study_dir,
                    show_fea_charts=False))
                self.assertTrue(wiz.launch_paraview_viewer(
                    ROOT / "view_study_paraview.py", paraview_dir, study_dir,
                    show_fea_charts=True))
            self.assertEqual(len(calls), 2)
            self.assertNotIn("PYCHRONO_FEA_CHARTS", calls[0][1]["env"])
            self.assertEqual(calls[1][1]["env"]["PYCHRONO_FEA_CHARTS"], "1")


class GuiAfterRunParaViewLaunchTest(IsolatedGuiDefaultsTest):
    def _launch_once(self, *, show_charts: bool) -> dict:
        import tkinter as tk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            app.tools_show_fea_charts.set(show_charts)
            with tempfile.TemporaryDirectory(prefix="gui_after_pv_", dir="/tmp/opencode") as tmp:
                out_dir = Path(tmp)
                (out_dir / "paraview").mkdir()
                cfg = {"output_dir": out_dir, "open_paraview": True}
                app.log_queue.put(("done", "ok"))
                with patch.object(app, "_collect_cfg", return_value=cfg), \
                     patch.object(app, "_refresh_output_preview"), \
                     patch.object(app.root, "after"), \
                     patch.object(gui.wiz, "launch_paraview_viewer",
                                  return_value=True) as launch:
                    app._drain_log()
                self.assertEqual(launch.call_count, 1)
                args, kwargs = launch.call_args
                self.assertEqual(args[0], ROOT / "view_study_paraview.py")
                self.assertEqual(args[1], out_dir / "paraview")
                self.assertEqual(args[2], out_dir)
                return kwargs
        finally:
            root.destroy()

    def test_after_run_launch_follows_chart_toggle(self) -> None:
        self.assertFalse(self._launch_once(show_charts=False)["show_fea_charts"])
        self.assertTrue(self._launch_once(show_charts=True)["show_fea_charts"])


class MeshPreviewToolTest(IsolatedGuiDefaultsTest):
    def test_preview_uses_the_full_run_mesher_then_geometry_comparison(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            labels = [
                child.cget("text") for child in descendants(app.tools_tab)
                if isinstance(child, (tk.Label, ttk.Label, ttk.Button))
            ]
            self.assertIn("Preview STEP mesh (full-run mesher + comparison)", labels)

            with tempfile.TemporaryDirectory(prefix="mesh_preview_", dir="/tmp/opencode") as tmp:
                step = Path(tmp) / "Assembly 1.step"
                step.write_text("not parsed by this test")
                app.vars["geometry"].set("step")
                app.vars["step_file"].set(str(step))
                app.vars["cad_axis"].set("Y")
                mesh = app._mesh_settings()
                prefix = ROOT / "geometry_checks" / "mesh_preview" / f"{step.stem}_fea"
                expected = app._mesh_command(prefix, step, mesh)

                self.assertEqual(expected[0], str(ROOT / "chrono-env" / "bin" / "python"))
                self.assertEqual(expected[1], str(ROOT / "step_to_chrono_fea.py"))
                self.assertEqual(expected[expected.index("--surface-target-faces") + 1], "8000")
                self.assertEqual(expected[expected.index("--surface-fit-tolerance") + 1], "0.001")
                self.assertIn("--part-aware-simplify", expected)

                captured = {}

                def fake_run(cmd, **kwargs):
                    captured.update(cmd=cmd, **kwargs)

                with patch.object(app, "_run_tool_cmd", fake_run):
                    app._tool_mesh_preview()
                self.assertEqual(captured["cmd"], expected)
                self.assertEqual(captured["label"], "mesh-preview")

                node = Path(app._mesh_preview_node)
                node.parent.mkdir(parents=True, exist_ok=True)
                node.write_text("stub")
                popen_calls = []

                def fake_popen(cmd, **kwargs):
                    popen_calls.append((cmd, kwargs))
                    return type("Proc", (), {})()

                with patch.object(gui.subprocess, "Popen", fake_popen):
                    app._launch_mesh_comparison()
                self.assertTrue(popen_calls)
                compare_cmd = popen_calls[0][0]
                self.assertEqual(compare_cmd[1], str(ROOT / "view_geometry.py"))
                self.assertEqual(compare_cmd[compare_cmd.index("--step") + 1], str(step))
                self.assertEqual(compare_cmd[compare_cmd.index("--fea-node") + 1], str(node))
                self.assertIn("--show", compare_cmd)
        finally:
            root.destroy()


class ScrollFrameWheelTest(unittest.TestCase):
    def test_wheel_over_child_widget_scrolls_page(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            frame = gui.ScrollFrame(root, width=120, height=80)
            frame.pack()
            for i in range(60):
                ttk.Label(frame.inner, text=f"row {i}").grid(row=i, column=0, sticky="w")
            root.update()  # lets after_idle install bindings and lays out scrollregion

            labels = [child for child in frame.inner.winfo_children()
                      if isinstance(child, ttk.Label)]
            label = labels[10]
            self.assertIn(frame._wheel_tag, label.bindtags())

            start = frame.canvas.yview()[0]
            label.event_generate("<Button-5>")  # X11 wheel down
            root.update()
            self.assertGreater(frame.canvas.yview()[0], start)

            label.event_generate("<MouseWheel>", delta=-120)  # Windows/macOS wheel down
            root.update()
            after_two = frame.canvas.yview()[0]
            self.assertGreater(after_two, start)

            label.event_generate("<Button-4>")  # wheel up
            root.update()
            self.assertLess(frame.canvas.yview()[0], after_two)

            # Extra wheel-up events must never drift above the first row.
            for _ in range(30):
                label.event_generate("<Button-4>")
            root.update()
            self.assertEqual(frame.canvas.yview()[0], 0.0)
        finally:
            root.destroy()


class PreviewStyleSettingsTest(unittest.TestCase):
    def test_style_defaults_are_shipped(self) -> None:
        import study_gui as gui

        self.assertEqual(gui.SHIPPED_SETTINGS["preview_particle_size"], 2)
        self.assertEqual(gui.SHIPPED_SETTINGS["preview_line_width"], 1)

    def test_style_settings_round_trip(self) -> None:
        import study_gui as gui

        original = gui.SETTINGS_FILE
        try:
            with tempfile.TemporaryDirectory(prefix="preview_style_", dir="/tmp/opencode") as tmp:
                gui.SETTINGS_FILE = Path(tmp) / "settings.json"
                values = dict(gui.SHIPPED_SETTINGS)
                values["preview_particle_size"] = 7
                values["preview_line_width"] = 4
                gui.write_ui_settings(values)
                loaded = gui.load_ui_settings()
                self.assertEqual(loaded["preview_particle_size"], 7)
                self.assertEqual(loaded["preview_line_width"], 4)
        finally:
            gui.SETTINGS_FILE = original


class ReverseSpinToggleTest(IsolatedGuiDefaultsTest):
    def test_toggle_flips_signed_omega_and_command_flags(self) -> None:
        import tkinter as tk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            app.vars["omega"].set("3.14")
            with tempfile.TemporaryDirectory(prefix="reverse_spin_", dir="/tmp/opencode") as tmp:
                app.vars["output_dir"].set(tmp)

                app.vars["reverse_spin"].set(False)
                forward = app._collect_cfg()
                forward_steps = gui.wiz.build_commands(forward)
                app.vars["reverse_spin"].set(True)
                reverse = app._collect_cfg()
                reverse_steps = gui.wiz.build_commands(reverse)

                self.assertGreater(forward["omega"], 0.0)
                self.assertFalse(forward["reverse_spin"])
                self.assertLess(reverse["omega"], 0.0)
                self.assertTrue(reverse["reverse_spin"])
                self.assertEqual(reverse["omega"], -forward["omega"])

                for step_name in ("rigid-CRM", "one-way-FEA"):
                    fwd_cmd = next(s["command"] for s in forward_steps if s["name"] == step_name)
                    rev_cmd = next(s["command"] for s in reverse_steps if s["name"] == step_name)
                    self.assertNotIn("--reverse-spin", fwd_cmd)
                    self.assertIn("--reverse-spin", rev_cmd)
                    self.assertEqual(
                        fwd_cmd[fwd_cmd.index("--omega") + 1],
                        rev_cmd[rev_cmd.index("--omega") + 1],
                    )
        finally:
            root.destroy()

    def test_crm_driver_applies_reverse_flag_to_omega(self) -> None:
        sys.path.insert(0, str(ROOT / "crm"))
        try:
            import lunar_excavator_crm as crm
        finally:
            sys.path.remove(str(ROOT / "crm"))

        forward = crm.make_run_params(
            crm.build_parser().parse_args(["--omega", "3.14"]))
        reverse = crm.make_run_params(
            crm.build_parser().parse_args(["--omega", "3.14", "--reverse-spin"]))
        self.assertGreater(forward.omega, 0.0)
        self.assertLess(reverse.omega, 0.0)
        self.assertEqual(reverse.omega, -forward.omega)


class ForceDescentGuiFieldsTest(IsolatedGuiDefaultsTest):
    def test_force_fields_default_to_kinematic_and_generate_flags(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_gui as gui

        defaults = gui.shipped_defaults()
        self.assertEqual(defaults["descent_mode"], "kinematic")
        self.assertEqual(defaults["descent_force_n"], "1.0")
        self.assertEqual(defaults["descent_force_ramp_s"], "0.0")
        fields = {row[1] for _, rows in gui.DEFAULT_GROUPS for row in rows}
        self.assertTrue({"descent_mode", "descent_force_n", "descent_force_ramp_s"} <= fields)

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            labels = [
                child.cget("text")
                for child in app.pages[2].winfo_children()
                if isinstance(child, (tk.Label, ttk.Label))
            ]
            for label in ("Descent mode", "Descent force (N; force mode)",
                          "Descent force ramp (s; force mode)"):
                self.assertIn(label, labels)

            with tempfile.TemporaryDirectory(prefix="force_gui_", dir="/tmp/opencode") as tmp:
                app.vars["output_dir"].set(tmp)
                kinematic = app._collect_cfg()
                self.assertEqual(kinematic["descent_mode"], "kinematic")

                app.vars["descent_mode"].set("force")
                app.vars["descent_speed"].set("0")
                app.vars["descent_force_n"].set("2.5")
                app.vars["descent_force_ramp_s"].set("0.1")
                forced = app._collect_cfg()
                self.assertEqual(forced["descent_mode"], "force")
                self.assertAlmostEqual(forced["descent_force_n"], 2.5)
                self.assertAlmostEqual(forced["descent_force_ramp_s"], 0.1)

                with patch.object(gui.wiz, "measured_geometry_for_cfg", return_value=None):
                    kinematic_steps = gui.wiz.build_commands(kinematic)
                    forced_steps = gui.wiz.build_commands(forced)
                for name in ("rigid-CRM", "one-way-FEA"):
                    kin_cmd = next(s["command"] for s in kinematic_steps if s["name"] == name)
                    force_cmd = next(s["command"] for s in forced_steps if s["name"] == name)
                    self.assertNotIn("--descent-mode", kin_cmd)
                    self.assertEqual(force_cmd[force_cmd.index("--descent-mode") + 1], "force")
                    self.assertEqual(force_cmd[force_cmd.index("--descent-force") + 1], "2.5")

                app.vars["descent_mode"].set("kinematic")
                app._apply_config({
                    "descent_mode": "force",
                    "descent_force_n": 3.0,
                    "descent_force_ramp_s": 0.2,
                })
                self.assertEqual(app.vars["descent_mode"].get(), "force")
                self.assertEqual(app.vars["descent_force_n"].get(), "3.0")
                self.assertEqual(app.vars["descent_force_ramp_s"].get(), "0.2")
        finally:
            root.destroy()


class GravityGuiFieldsTest(IsolatedGuiDefaultsTest):
    def test_gravity_fields_are_editable_and_reach_generated_commands(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_gui as gui

        defaults = gui.shipped_defaults()
        self.assertEqual(defaults["gravity"], "-9.81")
        self.assertFalse(defaults["drum_gravity"])
        fields = {row[1] for _, rows in gui.DEFAULT_GROUPS for row in rows}
        self.assertTrue({"gravity", "drum_gravity"} <= fields)

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            labels = [
                child.cget("text")
                for child in app.pages[2].winfo_children()
                if isinstance(child, (tk.Label, ttk.Label))
            ]
            self.assertIn("Gravity Z (m/s^2)", labels)
            self.assertIn("Apply gravity to CRM drum", labels)

            with tempfile.TemporaryDirectory(prefix="gravity_gui_", dir="/tmp/opencode") as tmp:
                app.vars["output_dir"].set(tmp)
                app.vars["gravity"].set("-1.62")
                app.vars["drum_gravity"].set(True)
                cfg = app._collect_cfg()
                self.assertAlmostEqual(cfg["gravity"], -1.62)
                self.assertTrue(cfg["drum_gravity"])

                with patch.object(gui.wiz, "measured_geometry_for_cfg", return_value=None):
                    steps = gui.wiz.build_commands(cfg)
                crm_cmd = next(s["command"] for s in steps if s["name"] == "rigid-CRM")
                fea_cmd = next(s["command"] for s in steps if s["name"] == "one-way-FEA")
                self.assertEqual(crm_cmd[crm_cmd.index("--gravity") + 1], "-1.62")
                self.assertIn("--drum-gravity", crm_cmd)
                self.assertEqual(fea_cmd[fea_cmd.index("--gravity") + 1], "-1.62")
                self.assertNotIn("--drum-gravity", fea_cmd)

                app._apply_config({"gravity": -9.81, "drum_gravity": False})
                self.assertEqual(app.vars["gravity"].get(), "-9.81")
                self.assertFalse(app.vars["drum_gravity"].get())
        finally:
            root.destroy()

    def test_nonfinite_gravity_is_rejected_before_generating_commands(self) -> None:
        import tkinter as tk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            with tempfile.TemporaryDirectory(prefix="gravity_gui_bad_", dir="/tmp/opencode") as tmp:
                app.vars["output_dir"].set(tmp)
                app.vars["gravity"].set("nan")
                with self.assertRaisesRegex(ValueError, "Gravity Z must be finite"):
                    app._collect_cfg()
        finally:
            root.destroy()


class FillDetectorSizeControlsTest(IsolatedGuiDefaultsTest):
    def test_gui_fields_default_to_auto_drum_dimensions(self) -> None:
        import tkinter as tk
        from tkinter import ttk

        import study_gui as gui

        defaults = gui.shipped_defaults()
        self.assertTrue(defaults["detector_auto_size"])
        self.assertEqual(defaults["detector_radius"], "0.061037")
        self.assertEqual(defaults["detector_length"], "0.066")

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            self.assertTrue(app._gb("detector_auto_size"))
            self.assertEqual(str(app.detector_widgets["detector_radius"].cget("state")), "disabled")
            self.assertEqual(str(app.detector_widgets["detector_length"].cget("state")), "disabled")

            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)

            geometry_labels = [
                child.cget("text") for child in descendants(app.pages[1])
                if isinstance(child, (tk.Label, ttk.Label))
            ]
            self.assertIn("Auto-size fill detector", geometry_labels)
            self.assertIn("Detector cylinder radius (m)", geometry_labels)
            self.assertIn("Detector cylinder length (m)", geometry_labels)

            tool_labels = [
                child.cget("text") for child in descendants(app.tools_tab)
                if isinstance(child, (tk.Label, ttk.Button))
            ]
            self.assertIn("Live 3D fill-detector size adjuster", tool_labels)

            app.vars["detector_auto_size"].set(False)
            app._sync_detector_fields()
            self.assertEqual(str(app.detector_widgets["detector_radius"].cget("state")), "normal")
            self.assertEqual(str(app.detector_widgets["detector_length"].cget("state")), "normal")
        finally:
            root.destroy()

    def test_config_round_trip_collects_full_detector_length(self) -> None:
        import tkinter as tk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            app._apply_config({
                "detector_auto_size": False,
                "detector_radius": 0.05,
                "detector_length": 0.08,
            })
            self.assertFalse(app._gb("detector_auto_size"))
            self.assertEqual(app.vars["detector_radius"].get(), "0.05")
            self.assertEqual(app.vars["detector_length"].get(), "0.08")
            self.assertEqual(str(app.detector_widgets["detector_radius"].cget("state")), "normal")

            with tempfile.TemporaryDirectory(prefix="detector_gui_cfg_", dir="/tmp/opencode") as tmp:
                app.vars["output_dir"].set(tmp)
                cfg = app._collect_cfg()
                self.assertFalse(cfg["detector_auto_size"])
                self.assertEqual(cfg["detector_radius"], 0.05)
                self.assertEqual(cfg["detector_length"], 0.08)

                steps = gui.wiz.build_commands(cfg)
                crm_cmd = next(s["command"] for s in steps if s["name"] == "rigid-CRM")
                fea_cmd = next(s["command"] for s in steps if s["name"] == "one-way-FEA")
                self.assertIn("--no-detector-auto-size", crm_cmd)
                self.assertEqual(crm_cmd[crm_cmd.index("--detector-radius") + 1], "0.05")
                self.assertEqual(crm_cmd[crm_cmd.index("--detector-length") + 1], "0.08")
                self.assertNotIn("--detector-radius", fea_cmd)
        finally:
            root.destroy()

    def test_sizer_launch_passes_geometry_and_show_flag(self) -> None:
        import tkinter as tk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            with tempfile.TemporaryDirectory(prefix="detector_launch_", dir="/tmp/opencode") as tmp:
                base = Path(tmp)
                step = base / "drum.step"
                surface = base / "drum.obj"
                node = base / "drum.node"
                for path in (step, surface, node):
                    path.write_text("", encoding="utf-8")
                (base / "scratch").mkdir()
                app.vars["geometry"].set("step")
                app.vars["step_file"].set(str(step))
                app.vars["crm_obj"].set(str(surface))
                app.vars["fea_node"].set(str(node))
                app.vars["cad_axis"].set("Z")
                app.vars["unit_scale"].set("1")
                app.vars["detector_auto_size"].set(False)
                app.vars["detector_radius"].set("0.05")
                app.vars["detector_length"].set("0.08")

                class Proc:
                    def poll(self):
                        return None

                with patch.object(gui.tempfile, "mkdtemp", return_value=str(base / "scratch")), \
                     patch.object(app.root, "after", return_value=None), \
                     patch.object(gui.subprocess, "Popen", return_value=Proc()) as popen:
                    app._tool_detector_sizer()

                cmd = popen.call_args.args[0]
                self.assertTrue(cmd[1].endswith("detector_size_tool.py"))
                self.assertEqual(cmd[cmd.index("--step") + 1], str(step))
                self.assertEqual(cmd[cmd.index("--surface") + 1], str(surface))
                self.assertEqual(cmd[cmd.index("--fea-node") + 1], str(node))
                self.assertEqual(cmd[cmd.index("--radius") + 1], "0.05")
                self.assertEqual(cmd[cmd.index("--length") + 1], "0.08")
                self.assertEqual(cmd[cmd.index("--source-axis") + 1], "Z")
                self.assertIn("--show", cmd)
                self.assertEqual(
                    cmd[cmd.index("--result-json") + 1],
                    str(base / "scratch" / "detector_size.json"),
                )
        finally:
            root.destroy()

    def test_result_polling_applies_only_dirty_slider_values(self) -> None:
        import tkinter as tk

        import study_gui as gui

        root = tk.Tk()
        root.withdraw()
        try:
            app = gui.StudyGUI(root)
            with tempfile.TemporaryDirectory(prefix="detector_poll_", dir="/tmp/opencode") as tmp:
                path = Path(tmp) / "detector_size.json"
                path.write_text(json.dumps({
                    "radius": 0.061037, "length": 0.066, "dirty": False,
                }), encoding="utf-8")
                app._detector_result_json = path
                app._detector_sizer_proc = FinishedProcess()

                app._poll_detector_sizer()
                self.assertTrue(app._gb("detector_auto_size"))
                self.assertEqual(str(app.detector_widgets["detector_radius"].cget("state")), "disabled")

                path.write_text(json.dumps({
                    "radius": 0.0525, "length": 0.074, "dirty": True,
                }), encoding="utf-8")
                app._detector_sizer_proc = FinishedProcess()

                app._poll_detector_sizer()
                self.assertFalse(app._gb("detector_auto_size"))
                self.assertEqual(app.vars["detector_radius"].get(), "0.0525")
                self.assertEqual(app.vars["detector_length"].get(), "0.074")
                self.assertEqual(str(app.detector_widgets["detector_radius"].cget("state")), "normal")
                self.assertIn("Detector cylinder set to", app.status_var.get())
                self.assertIsNone(app._detector_sizer_proc)
        finally:
            root.destroy()

    def test_command_builder_and_result_reader(self) -> None:
        import study_gui as gui

        cmd = gui.build_detector_sizer_command(
            python="/usr/bin/python3",
            root=Path("/work"),
            step_file=None,
            surface_file=None,
            fea_node=None,
            radius=0.05,
            length=0.08,
            spacing=0.002,
            cad_axis="",
            unit_scale=0.001,
            result_json=Path("/tmp/result.json"),
            show=False,
        )
        self.assertEqual(cmd, [
            "/usr/bin/python3", "/work/detector_size_tool.py",
            "--radius", "0.05", "--length", "0.08", "--spacing", "0.002",
            "--source-axis", "Y", "--unit-scale", "0.001",
            "--result-json", "/tmp/result.json",
        ])

        with tempfile.TemporaryDirectory(prefix="detector_read_", dir="/tmp/opencode") as tmp:
            base = Path(tmp)
            step = base / "drum.step"
            step.write_text("", encoding="utf-8")
            cmd = gui.build_detector_sizer_command(
                python="py", root=base, step_file=step, surface_file=None,
                fea_node=None, radius=0.061, length=0.066, spacing=0.002,
                cad_axis="X", unit_scale=0.001,
                result_json=base / "result.json", threads=2, show=True,
            )
            self.assertEqual(cmd[cmd.index("--step") + 1], str(step))
            self.assertEqual(cmd[cmd.index("--threads") + 1], "2")
            self.assertIn("--show", cmd)

            result = base / "result.json"
            result.write_text("{not json", encoding="utf-8")
            self.assertIsNone(gui.read_detector_result(result))
            result.write_text(json.dumps({
                "radius": 0.055, "length": 0.077,
            }), encoding="utf-8")
            self.assertEqual(gui.read_detector_result(result), {
                "radius": 0.055, "length": 0.077, "dirty": True,
            })


class FinishedProcess:
    def poll(self):
        return 0


if __name__ == "__main__":
    unittest.main()
