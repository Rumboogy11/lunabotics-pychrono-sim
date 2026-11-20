#!/usr/bin/env python3
"""Tkinter GUI consolidating PyChrono excavator study scripts.

Provides an editable, multi-step study builder for the CRM -> one-way FEA
workflow (can go back and change parameters), plus quick tools for exporting
and viewing existing study outputs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def _maybe_reexec_for_display():
    """Prefer system Python for the Tk UI when launched from chrono-env.

    chrono-env Python/Tk can appear small or blurry on some Wayland/XWayland setups,
    while system Python/Tk may be properly scaled. Simulation tools, Chrono, and OCP
    are still executed via chrono-env/bin/python by helper methods/subprocesses.
    Set PYCHRONO_GUI_NO_REEXEC=1 to disable.
    """
    if os.environ.get("PYCHRONO_GUI_NO_REEXEC") == "1":
        return
    root = Path(__file__).resolve().parent
    chrono_py = (root / "chrono-env" / "bin" / "python").resolve()
    try:
        cur = Path(sys.executable).resolve()
    except Exception:
        return
    if cur != chrono_py:
        return

    candidates: list[str] = []
    for p in ("/usr/bin/python3", "/usr/local/bin/python3", shutil.which("python3") or ""):
        if not p:
            continue
        try:
            rp = str(Path(p).resolve())
        except Exception:
            rp = p
        if rp and rp != str(chrono_py) and rp not in candidates:
            candidates.append(rp)

    for cand in candidates:
        try:
            r = subprocess.run(
                [cand, "-c", "import tkinter"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if r.returncode != 0:
                continue
        except Exception:
            continue
        env = os.environ.copy()
        env["PYCHRONO_GUI_NO_REEXEC"] = "1"
        env.setdefault("PYCHRONO_CHRONO_PYTHON", str(chrono_py))
        os.execve(cand, [cand] + sys.argv, env)


_maybe_reexec_for_display()

import json
import queue
import re
import shlex
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import BOTH, END, HORIZONTAL, LEFT, RIGHT, VERTICAL, W, X, Y
from tkinter import BooleanVar, StringVar, Tk, colorchooser, filedialog, messagebox
from tkinter import font as tkfont
from tkinter import ttk

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "crm") not in sys.path:
    sys.path.insert(0, str(ROOT / "crm"))

import study_wizard as wiz  # noqa: E402


PARAM_INFO: dict[str, str] = {
    # Study
    "study_name": "Short label for this run; used in the default output folder name.",
    "output_dir": "Folder for meshes, logs, configs, and results. Created if needed; use a new folder to preserve prior runs.",
    "geometry": "supplied = built-in meshes; step = convert a STEP CAD file; files = provide existing CRM OBJ and FEA .node/.ele meshes.",
    "auto_geometry": "Measure the selected CAD/mesh and refresh suggested drum, bed, domain, spacing, and mesh values. All values remain editable.",
    # Geometry
    "step_file": "CAD input (.step/.stp) used only when Geometry source is 'step'. New models use rotation axis Y in STEP space.",
    "crm_obj": "Triangulated drum surface for rigid Chrono::CRM contact. For production use the promoted coarse OBJ, not a full-detail mesh.",
    "fea_node": "TetGen node file for the solid drum mesh used in one-way FEA.",
    "fea_ele": "TetGen element file paired with the FEA .node mesh.",
    "cad_axis": "Drum axis in original STEP coordinates. New models default to Y; the converter rotates this axis to local +Z for Chrono.",
    "unit_scale": "Multiply STEP coordinates by this factor (0.001 when CAD coordinates are millimetres). Final mesh units must be metres.",
    "mesh_size": "Approximate tetrahedral edge length. Smaller gives a finer, slower FEA mesh.",
    "surface_target_faces": "Minimum contact-surface triangle count from STEP. The converter raises it automatically until decimation stays watertight and preserves volume (complex screws often need 50k-80k).",
    "surface_max_faces": "Upper bound for the watertight decimation retry used by STEP mesh generation.",
    "surface_repair_order": "'none' unifies fused CAD faces and retries decimation until watertight. 'before'/'after' are legacy MeshFix routes that can inflate volume.",
    "min_solid_volume": "Drop STEP assembly solids smaller than this volume (m^3) before meshing (fasteners/debris).",
    "hub_radius": "Nodes within this radius of the drum axis are tied rigidly to the spindle in FEA.",
    "drum_radius": "Outer crest radius; sets initial drum height (bed + radius) and bed sizing.",
    "drum_half_length": "Half the drum length along its axis; used for bed box and CRM placement.",
    "inner_radius": "Nominal cavity radius used to estimate fill ratio and capacity volume.",
    "capacity_volume": "Known internal cavity volume; leave blank to derive capacity from drum radii/length.",
    # Run & domain
    "duration": "Total rigid-CRM simulation time. For a full excavation use about bed_height / descent_speed.",
    "fea_duration": "One-way FEA replay length; cannot exceed the CRM load-history duration. FEA replays rigid CRM soil-wrench CSVs, not SPH contact.",
    "spacing": "Initial SPH particle spacing. Smaller = more particles, higher resolution, slower simulation; ~0.002 m is the default (2x finer than the old 4 mm setting).",
    "d0_multiplier": "Smoothing-length factor h = multiplier * spacing. 1.0 is the standard choice.",
    "artificial_viscosity": "Artificial bilateral SPH viscosity. Raise above 0.5 to damp pressure-wave soil ejection at the first drum bite; lower values preserve more dynamic response.",
    "shifting_ppst_push": "PPST particle-shifting push strength. Lower than 3.0 reduces outward particle drift; 0 disables the push term.",
    "crm_dt": "Explicit CRM timestep, or 'auto' for a spacing-dependent stable value. Unsafe explicit dt is rejected unless explicitly allowed.",
    "crm_fps": "How often CRM summary frames are written. Particle and soil-wrench files use their own cadences.",
    "fea_fps": "Independent FEA summary/export cadence. Lower values speed up replay; FEA dynamics still follow FEA dt.",
    "loads_fps": "Soil-wrench CSV sampling rate for one-way FEA. The FEA driver interpolates by time, so 20 Hz is usually enough; lower values make the CRM solver faster and soil_loads.csv much smaller.",
    "particle_fps": "How often particle CSV frames are written for ParaView. Capped by CRM output FPS. Lower values sharply reduce CRM disk I/O; 5 Hz is usually enough for visualization.",
    "particle_writers": "Background processes that write per-frame particle CSV files while the CRM solver continues. Increase only if disk writes remain a bottleneck.",
    "fea_dt": "Structural integrator timestep for one-way FEA, independent of CRM dt. Default is 0.002 s.",
    "bed_height": "Depth of the soil bed. The drum starts at bed_height + radius above the ground.",
    "active_box": "Drum-centred active SPH domain X,Y,Z in metres (full sizes; X along drum axis, Y lateral, Z vertical). "
                  "Defaults to the padded bed plus 2 excavator diameters laterally, and bed height + initial "
                  "excavator height + 2 diameters + padding vertically, so flung particles are not deactivated.",
    "settle_time": "Time to let the bed settle before excavation motion begins. Longer settling can reduce the first-bite pressure transient that flings soil upward.",
    "descent_speed": "Vertical lowering speed of the drum into the bed. 0 keeps it at constant height.",
    "omega": "Drum angular speed magnitude about the spin axis (~3.14 rad/s = 30 rpm). Sign is controlled by Reverse spin.",
    "reverse_spin": "Flip rotation direction if the CAD excavator spins the wrong way. Signed omega is written as negative.",
    "spin_axis": "Global axis the drum spins about during excavation (usually X for the supplied geometry).",
    "solver": "One-way FEA linear solver: pardiso-mkl is default; sparse-lu/qr are fallbacks; gmres is diagnostic only.",
    "num_threads": "Thread count for the FEA solver (pardiso-mkl) and the geometry comparison tool's OCC tessellation/fuse.",
    "load_radius_fraction": "Apply soil loads only to exterior nodes within this fraction of drum radius; 0.95 cuts per-step load work sharply.",
    "raw_stress_every": "Scan tetrahedra for peak stress every Nth recorded frame; higher is cheaper.",
    "export_workers": "Number of processes used to write ParaView frame files in parallel.",
    # Output & export
    "save_particles": "Write CRM particle positions as CSV at the particle output FPS. Needed for ParaView particle spheres; uses disk space.",
    "fea_export_vtk": "Write fea/vtk/frame_*.vtu and fea.pvd so ParaView can show von Mises stress and deformation. Required for 3D stress/deformation views.",
    "fea_vtk_stride": "Export every Nth recorded FEA frame to VTU. Use >1 to reduce disk/time for long runs.",
    "export_vtk": "Create separate ParaView series (crm_particles.pvd, excavator.pvd, optional fea.pvd) plus combined.pvd and a manifest after the run.",
    "paraview_stride": "Export every Nth CRM frame. Use 1 for all frames; all series stay time-aligned.",
    "paraview_particle_render_stride": "Render only every Nth particle in each exported frame. Use 1 for all particles; >1 reduces particle file size and ParaView load without skipping frames.",
    "open_results": "Open the output folder when the study finishes.",
    "open_paraview": "Open the preconfigured 3D view in the full ParaView GUI after a successful run (pvpython render window is the fallback).",
    # Tools tab
    "tools_study_dir": "Study/output directory to inspect. Use the folder containing study_config.json or a previous Output_* run.",
    "tools_stride": "Export every Nth CRM frame when using the Export ParaView VTK/PVD tool. Use 1 for all frames. All series stay time-aligned.",
    "tools_particle_render_stride": "Render only every Nth particle in each exported frame in the Export ParaView VTK/PVD tool. Use 1 for all particles; >1 reduces particle file size and ParaView load without skipping frames.",
    "tools_workers": "Number of worker processes used by the Export ParaView VTK/PVD tool. Match it to available CPU cores; 4 is a good starting point.",
}

PARAM_INFO.update({
    f"soil_{key}": text for key, text in {
        "density": "Regolith bulk density (kg/m^3).",
        "youngs_modulus": "Soil elastic stiffness (Pa) for CRM contact response.",
        "poisson_ratio": "Soil lateral/axial strain ratio.",
        "friction": "Static friction coefficient (mu_s) at particle contacts.",
        "cohesion": "Cohesive shear strength (Pa).",
        "grain_diameter": "Physical grain diameter (m) used by the rheology model.",
    }.items()
})
PARAM_INFO.update({
    f"pla_{key}": text for key, text in {
        "youngs_modulus": "Drum material stiffness (Pa), e.g. PLA ~3 GPa.",
        "poisson_ratio": "Drum material lateral/axial strain ratio.",
        "density": "Drum material density (kg/m^3).",
        "rayleigh_beta": "Rayleigh stiffness-proportional damping (s) to stabilize vibration.",
    }.items()
})

DEFAULT_FILE = ROOT / "study_gui_defaults.json"

# Appearance/startup preferences live in their own file so they never get
# mixed into the study-parameter defaults written by the Defaults tab.
SETTINGS_FILE = ROOT / "study_gui_settings.json"
HEADER_FONT = "StudyHeaderFont"
SECTION_FONT = "StudySectionFont"
LIGHT_TEXT = "#000000"
DARK_TEXT = "#F2F2F2"

SHIPPED_SETTINGS: dict[str, object] = {
    "font_size": 10,
    "window_width": 1100,
    "window_height": 1080,
    "text_color": LIGHT_TEXT,
    "dark_mode": False,
}

SETTINGS_INFO: dict[str, str] = {
    "font_size": "Base text size (7-24). Applies to labels, entries, buttons, and log panels immediately.",
    "window_width": "Window width in pixels used the next time the GUI starts (640-3840).",
    "window_height": "Window height in pixels used the next time the GUI starts (480-2160).",
    "text_color": "Foreground text color as #RRGGBB. Pick... opens the system color dialog.",
    "dark_mode": "Use a dark interface palette. If the text color is still the default, it switches to a light shade automatically.",
}


def _valid_color(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value.strip()) is not None


def _setting_int(value: object, label: str, low: int, high: int, default: int, strict: bool = False) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        if strict:
            raise ValueError(f"{label} must be a whole number.")
        return default
    if number < low or number > high:
        if strict:
            raise ValueError(f"{label} must be between {low} and {high}.")
        return max(low, min(high, number))
    return number


def load_ui_settings() -> dict[str, object]:
    """Load appearance preferences, falling back to shipped values per key."""
    values = dict(SHIPPED_SETTINGS)
    if not SETTINGS_FILE.is_file():
        return values
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return values
    if not isinstance(data, dict):
        return values
    values["font_size"] = _setting_int(
        data.get("font_size"), "Text size", 7, 24, int(SHIPPED_SETTINGS["font_size"]))
    values["window_width"] = _setting_int(
        data.get("window_width"), "Window width", 640, 3840, int(SHIPPED_SETTINGS["window_width"]))
    values["window_height"] = _setting_int(
        data.get("window_height"), "Window height", 480, 2160, int(SHIPPED_SETTINGS["window_height"]))
    if _valid_color(data.get("text_color")):
        values["text_color"] = str(data["text_color"]).strip()
    values["dark_mode"] = bool(data.get("dark_mode", False))
    return values


def write_ui_settings(values: dict[str, object]) -> None:
    """Persist only validated appearance preferences."""
    clean = {
        "font_size": _setting_int(values.get("font_size"), "Text size", 7, 24, int(SHIPPED_SETTINGS["font_size"])),
        "window_width": _setting_int(values.get("window_width"), "Window width", 640, 3840, int(SHIPPED_SETTINGS["window_width"])),
        "window_height": _setting_int(values.get("window_height"), "Window height", 480, 2160, int(SHIPPED_SETTINGS["window_height"])),
        "text_color": (str(values["text_color"]).strip() if _valid_color(values.get("text_color"))
                       else str(SHIPPED_SETTINGS["text_color"])),
        "dark_mode": bool(values.get("dark_mode", False)),
    }
    SETTINGS_FILE.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def shipped_defaults() -> dict[str, object]:
    """Factory defaults for the GUI; per-run identity fields are excluded."""
    defaults: dict[str, object] = {
        "geometry": "supplied",
        "step_file": str(wiz.DEFAULT_STEP_FILE),
        "crm_obj": str(wiz.DEFAULT_CRM_OBJ),
        "fea_node": str(wiz.DEFAULT_FEA_NODE),
        "fea_ele": str(wiz.DEFAULT_FEA_ELE),
        "cad_axis": "Y",
        "unit_scale": "0.001",
        "mesh_size": "0.012",
        "surface_target_faces": "8000",
        "surface_max_faces": "256000",
        "surface_repair_order": "none",
        "min_solid_volume": "0.0",
        "hub_radius": "0.0015",
        "drum_radius": "0.061037",
        "drum_half_length": "0.033",
        "inner_radius": "0.055",
        "capacity_volume": "",
        "duration": "20",
        "fea_duration": "20",
        "spacing": "0.002",
        "d0_multiplier": "1.0",
        "artificial_viscosity": "0.5",
        "shifting_ppst_push": "3.0",
        "crm_dt": "auto",
        "crm_fps": "20",
        "fea_fps": "20",
        "loads_fps": "20",
        "particle_fps": "5",
        "particle_writers": "4",
        "fea_dt": "0.002",
        "bed_height": "0.06",
        "active_box": "0.631333,0.687407,0.86637",
        "settle_time": "0.001",
        "descent_speed": "0.0015",
        "omega": "3.14",
        "reverse_spin": False,
        "spin_axis": "X",
        "solver": "pardiso-mkl",
        "num_threads": "8",
        "load_radius_fraction": "0.95",
        "raw_stress_every": "5",
        "export_workers": "4",
        "save_particles": True,
        "fea_export_vtk": True,
        "fea_vtk_stride": "1",
        "export_vtk": True,
        "paraview_stride": "1",
        "paraview_particle_render_stride": "1",
        "open_paraview": False,
        "open_results": False,
        "auto_geometry": True,
    }
    defaults.update({f"soil_{k}": f"{v:g}" for k, v in wiz.SOIL_DEFAULTS.items()})
    defaults.update({f"pla_{k}": f"{v:g}" for k, v in wiz.PLA_DEFAULTS.items()})
    return defaults


DEFAULT_KEYS = set(shipped_defaults())

DEFAULT_GROUPS: list[tuple[str, list[tuple[str, str, dict]]]] = [
    ("Geometry", [
        ("Geometry source", "geometry", {"combo": ["supplied", "step", "files"]}),
        ("Auto-scale from CAD/mesh", "auto_geometry", {"check": True}),
        ("STEP file", "step_file", {"width": 60, "browse": "file"}),
        ("CRM surface OBJ", "crm_obj", {"width": 60, "browse": "file"}),
        ("FEA .node", "fea_node", {"width": 60, "browse": "file"}),
        ("FEA .ele", "fea_ele", {"width": 60, "browse": "file"}),
        ("CAD axis (STEP)", "cad_axis", {"combo": ["auto", "X", "Y", "Z"]}),
        ("Unit scale (STEP->m)", "unit_scale", {}),
        ("FEA mesh size (m)", "mesh_size", {}),
        ("Surface target faces", "surface_target_faces", {}),
        ("Surface max faces", "surface_max_faces", {}),
        ("MeshFix order", "surface_repair_order", {"combo": ["none", "before", "after"]}),
        ("Min solid volume (m^3)", "min_solid_volume", {}),
        ("Hub radius (m)", "hub_radius", {}),
        ("Drum radius (m)", "drum_radius", {}),
        ("Drum half-length (m)", "drum_half_length", {}),
        ("Inner/cavity radius (m)", "inner_radius", {}),
        ("Capacity volume override (m^3)", "capacity_volume", {}),
    ]),
    ("Run & Domain", [
        ("CRM duration (s)", "duration", {}),
        ("FEA duration (s)", "fea_duration", {}),
        ("SPH spacing (m)", "spacing", {}),
        ("SPH d0 multiplier", "d0_multiplier", {}),
        ("Artificial viscosity", "artificial_viscosity", {}),
        ("PPST shifting push", "shifting_ppst_push", {}),
        ("CRM dt (s or 'auto')", "crm_dt", {}),
        ("CRM output FPS", "crm_fps", {}),
        ("FEA output FPS", "fea_fps", {}),
        ("Soil loads FPS", "loads_fps", {}),
        ("Particle output FPS", "particle_fps", {}),
        ("Particle writer processes", "particle_writers", {}),
        ("FEA dt (s)", "fea_dt", {}),
        ("Bed height (m)", "bed_height", {}),
        ("Active box X,Y,Z (m)", "active_box", {}),
        ("Settle time (s)", "settle_time", {}),
        ("Descent speed (m/s)", "descent_speed", {}),
        ("Spin rate |omega| (rad/s)", "omega", {}),
        ("Reverse spin", "reverse_spin", {"check": True}),
        ("Spin axis", "spin_axis", {"combo": ["X", "Y", "Z"]}),
        ("FEA solver", "solver", {"combo": ["pardiso-mkl", "sparse-lu", "sparse-qr", "gmres"]}),
        ("FEA threads", "num_threads", {}),
        ("Load node radius fraction", "load_radius_fraction", {}),
        ("Raw stress interval (frames)", "raw_stress_every", {}),
    ]),
    ("Soil (CRM)", [
        (label, f"soil_{key}", {}) for key, (label, _lim) in wiz.SOIL_FIELDS.items()
    ]),
    ("FEA drum material (PLA)", [
        (label, f"pla_{key}", {}) for key, (label, _lim) in wiz.PLA_FIELDS.items()
    ]),
    ("Output & Export", [
        ("Save CRM particle CSV frames", "save_particles", {"check": True}),
        ("Export FEA stress/deformation VTU", "fea_export_vtk", {"check": True}),
        ("FEA VTK stride (frames)", "fea_vtk_stride", {}),
        ("Export ParaView datasets after run", "export_vtk", {"check": True}),
        ("ParaView export stride (frames)", "paraview_stride", {}),
        ("ParaView particle render stride", "paraview_particle_render_stride", {}),
        ("ParaView export workers", "export_workers", {}),
        ("Open results folder after run", "open_results", {"check": True}),
        ("Open ParaView after run", "open_paraview", {"check": True}),
    ]),
]


def coerce_default_value(key: str, value, template=None) -> object:
    """Normalize a saved/edited default to the type used by the GUI vars."""
    if template is None:
        template = shipped_defaults().get(key)
    if isinstance(template, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
    return "" if value is None else str(value)


def load_saved_defaults() -> dict[str, object]:
    if not DEFAULT_FILE.is_file():
        return {}
    try:
        data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    templates = shipped_defaults()
    return {
        key: coerce_default_value(key, value, templates.get(key))
        for key, value in data.items()
        if key in DEFAULT_KEYS
    }


def write_saved_defaults(values: dict[str, object]) -> None:
    templates = shipped_defaults()
    clean = {
        key: coerce_default_value(key, value, templates.get(key))
        for key, value in values.items()
        if key in DEFAULT_KEYS
    }
    DEFAULT_FILE.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def py_exec() -> str:
    env_py = os.environ.get("PYCHRONO_CHRONO_PYTHON")
    if env_py and Path(env_py).is_file():
        return env_py
    cand = ROOT / "chrono-env" / "bin" / "python"
    return str(cand) if cand.is_file() else sys.executable


class ScrollFrame(ttk.Frame):
    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        canvas = tk.Canvas(self, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient=VERTICAL, command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        win = canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        vsb.pack(side=RIGHT, fill=Y)


class StudyGUI:
    def __init__(self, root: Tk):
        self.root = root
        root.title("PyChrono Excavator Study GUI")
        self.settings = load_ui_settings()
        self._base_theme = ttk.Style().theme_use()
        self.settings_vars: dict[str, StringVar | BooleanVar] = {}
        self.text_widgets: list[tk.Text] = []
        self._apply_ui_settings(self.settings)
        self.proc: subprocess.Popen | None = None
        self.log_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.tools_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.vars: dict[str, StringVar | BooleanVar] = {}
        self.default_vars: dict[str, StringVar | BooleanVar] = {}
        self._build_vars()
        self._build_ui()
        self.root.after(100, self._drain_log)
        self.root.after(100, self._drain_tools_log)

    def _v(self, key: str, value, is_bool: bool = False, target=None):
        store = target if target is not None else self.vars
        if is_bool:
            store[key] = BooleanVar(value=bool(value))
        else:
            store[key] = StringVar(value="" if value is None else str(value))
        return store[key]

    def _build_vars(self):
        name = datetime.now().strftime("study_%Y%m%d_%H%M%S")
        self._v("study_name", name)
        self._v("output_dir", str(ROOT / f"Output_{name}"))
        defaults = shipped_defaults()
        defaults.update(load_saved_defaults())
        for key, value in defaults.items():
            self._v(key, value, is_bool=isinstance(value, bool), target=self.default_vars)
        for key, var in self.default_vars.items():
            self._v(key, var.get(), is_bool=isinstance(var, BooleanVar))

    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill=BOTH, expand=True)
        self.study_tab = ttk.Frame(nb)
        self.defaults_tab = ttk.Frame(nb)
        self.tools_tab = ttk.Frame(nb)
        self.settings_tab = ttk.Frame(nb)
        nb.add(self.study_tab, text="Study Wizard")
        nb.add(self.defaults_tab, text="Defaults")
        nb.add(self.tools_tab, text="Tools / View Results")
        nb.add(self.settings_tab, text="Settings")
        self._build_study_tab()
        self._build_defaults_tab()
        self._build_tools_tab()
        self._build_settings_tab()
        self._apply_ui_settings(self.settings)

    def _build_study_tab(self):
        top = ttk.Frame(self.study_tab)
        top.pack(fill=BOTH, expand=True)
        self.step_nb = ttk.Notebook(top)
        self.step_nb.pack(fill=BOTH, expand=True, padx=6, pady=6)
        self.pages = []
        self._page_study()
        self._page_geometry()
        self._page_run()
        self._page_materials()
        self._page_export()
        self._page_review()
        nav = ttk.Frame(self.study_tab)
        nav.pack(fill=X, padx=8, pady=4)
        ttk.Button(nav, text="< Back", command=self._back).pack(side=LEFT)
        ttk.Button(nav, text="Next >", command=self._next).pack(side=LEFT, padx=4)
        ttk.Button(nav, text="Apply CAD/mesh defaults", command=self._apply_geo_defaults).pack(side=LEFT, padx=12)
        ttk.Button(nav, text="Load study_config.json", command=self._load_config).pack(side=LEFT)
        ttk.Button(nav, text="Save config/commands (dry run)", command=self._save_only).pack(side=RIGHT)
        ttk.Button(nav, text="Run study", command=self._run_study).pack(side=RIGHT, padx=6)

    def _add_page(self, title: str):
        frame = ScrollFrame(self.step_nb)
        self.step_nb.add(frame, text=title)
        self.pages.append(frame.inner)
        return frame.inner

    def _show_info(self, title: str, text: str):
        messagebox.showinfo(title=f"{title} - info", message=text)

    def _row(self, parent, r, label, key, width=40, browse=None, combo=None, check=False,
             help_text=None, variables=None):
        fields = variables if variables is not None else self.vars
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky=W, padx=6, pady=3)
        if check:
            w = ttk.Checkbutton(parent, variable=fields[key])
            w.grid(row=r, column=1, sticky=W, padx=6, pady=3)
        elif combo:
            w = ttk.Combobox(parent, textvariable=fields[key], values=combo, width=max(12, width-2), state="readonly")
            w.grid(row=r, column=1, sticky=W, padx=6, pady=3)
        else:
            w = ttk.Entry(parent, textvariable=fields[key], width=width)
            w.grid(row=r, column=1, sticky=W, padx=6, pady=3)
            if browse:
                def cmd(bt=browse, k=key, f=fields):
                    if bt == "dir":
                        p = filedialog.askdirectory(initialdir=f[k].get() or str(ROOT))
                    else:
                        p = filedialog.askopenfilename(initialdir=str(ROOT))
                    if p:
                        f[k].set(p)
                ttk.Button(parent, text="Browse", command=cmd).grid(row=r, column=2, sticky=W)
        info_text = PARAM_INFO.get(key) or help_text or "No additional information is available for this setting."
        ttk.Button(
            parent, text="Info", width=6,
            command=lambda t=label, i=info_text: self._show_info(t, i),
        ).grid(row=r, column=3, sticky=W, padx=6, pady=3)
        return r + 1

    def _page_study(self):
        p = self._add_page("1. Study")
        r = 0
        ttk.Label(p, text="Study identity and output location. You can navigate back and edit any step before running.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        r = self._row(p, r, "Study name", "study_name", help_text="Used for default output folder naming.")
        r = self._row(p, r, "Output directory", "output_dir", width=60, browse="dir", help_text="All configs, logs, meshes, and results go here.")
        r = self._row(p, r, "Geometry source", "geometry", combo=["supplied", "step", "files"], help_text="supplied=built-in meshes, step=convert CAD, files=existing meshes.")
        r = self._row(p, r, "Auto-scale from CAD/mesh", "auto_geometry", check=True, help_text="Derive bed/spacing/box defaults from drum size when possible.")
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        ttk.Label(p, text="Tip: use 'Apply CAD/mesh defaults' after choosing geometry to refresh suggested dimensions."
                  ).grid(row=r, column=0, columnspan=4, sticky=W, padx=6)

    def _page_geometry(self):
        p = self._add_page("2. Geometry")
        r = 0
        ttk.Label(p, text="Geometry and mesh inputs (used according to geometry source).",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        r = self._row(p, r, "STEP file", "step_file", width=60, browse="file", help_text="CAD input for geometry=step.")
        r = self._row(p, r, "CRM surface OBJ", "crm_obj", width=60, browse="file", help_text="Rigid CRM contact surface (geometry=files/supplied).")
        r = self._row(p, r, "FEA .node", "fea_node", width=60, browse="file")
        r = self._row(p, r, "FEA .ele", "fea_ele", width=60, browse="file")
        r = self._row(p, r, "CAD axis (STEP)", "cad_axis", combo=["auto", "X", "Y", "Z"])
        r = self._row(p, r, "Unit scale (STEP->m)", "unit_scale", help_text="0.001 for mm CAD.")
        r = self._row(p, r, "FEA mesh size (m)", "mesh_size")
        r = self._row(p, r, "Surface target faces", "surface_target_faces")
        r = self._row(p, r, "Surface max faces", "surface_max_faces")
        r = self._row(p, r, "MeshFix order", "surface_repair_order", combo=["none", "before", "after"])
        r = self._row(p, r, "Min solid volume (m^3)", "min_solid_volume")
        r = self._row(p, r, "Hub radius (m)", "hub_radius")
        r = self._row(p, r, "Drum radius (m)", "drum_radius")
        r = self._row(p, r, "Drum half-length (m)", "drum_half_length")
        r = self._row(p, r, "Inner/cavity radius (m)", "inner_radius")
        r = self._row(p, r, "Capacity volume override (m^3)", "capacity_volume", help_text="Blank = derive from geometry.")

    def _page_run(self):
        p = self._add_page("3. Run & Domain")
        r = 0
        ttk.Label(p, text="Simulation timing, motion, SPH/domain, and solver settings.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        r = self._row(p, r, "CRM duration (s)", "duration")
        r = self._row(p, r, "FEA duration (s)", "fea_duration")
        r = self._row(p, r, "SPH spacing (m)", "spacing", help_text="Smaller = higher resolution/more particles.")
        r = self._row(p, r, "SPH d0 multiplier", "d0_multiplier")
        r = self._row(p, r, "Artificial viscosity", "artificial_viscosity")
        r = self._row(p, r, "PPST shifting push", "shifting_ppst_push")
        r = self._row(p, r, "CRM dt (s or 'auto')", "crm_dt")
        r = self._row(p, r, "CRM output FPS", "crm_fps")
        r = self._row(p, r, "FEA output FPS", "fea_fps")
        r = self._row(p, r, "FEA dt (s)", "fea_dt")
        r = self._row(p, r, "Bed height (m)", "bed_height")
        r = self._row(p, r, "Active box X,Y,Z (m)", "active_box")
        r = self._row(p, r, "Settle time (s)", "settle_time")
        r = self._row(p, r, "Descent speed (m/s)", "descent_speed")
        r = self._row(p, r, "Spin rate |omega| (rad/s)", "omega")
        r = self._row(p, r, "Reverse spin", "reverse_spin", check=True)
        r = self._row(p, r, "Spin axis", "spin_axis", combo=["X", "Y", "Z"])
        r = self._row(p, r, "FEA solver", "solver", combo=["pardiso-mkl", "sparse-lu", "sparse-qr", "gmres"])
        r = self._row(p, r, "FEA threads", "num_threads")

    def _page_materials(self):
        p = self._add_page("4. Soil & Material")
        r = 0
        ttk.Label(p, text="Soil and PLA drum material parameters.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        ttk.Label(p, text="Soil (CRM)", font=SECTION_FONT).grid(row=r, column=0, sticky=W, padx=6, pady=4); r += 1
        for k, (label, _lim) in wiz.SOIL_FIELDS.items():
            r = self._row(p, r, label, f"soil_{k}")
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        ttk.Label(p, text="FEA drum material (PLA defaults)", font=SECTION_FONT).grid(row=r, column=0, sticky=W, padx=6, pady=4); r += 1
        for k, (label, _lim) in wiz.PLA_FIELDS.items():
            r = self._row(p, r, label, f"pla_{k}")

    def _page_export(self):
        p = self._add_page("5. Output & Export")
        r = 0
        ttk.Label(p, text="Saved outputs and ParaView/VTK export (added as final wizard step when enabled).",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        r = self._row(p, r, "Save CRM particle CSV frames", "save_particles", check=True)
        r = self._row(p, r, "Export FEA stress/deformation VTU", "fea_export_vtk", check=True,
                      help_text="Required to visualize von Mises stresses in ParaView.")
        r = self._row(p, r, "FEA VTK stride (frames)", "fea_vtk_stride")
        r = self._row(p, r, "Export ParaView datasets after run", "export_vtk", check=True,
                      help_text="Creates separate particle and excavator series + combined PVD.")
        r = self._row(p, r, "ParaView export stride (frames)", "paraview_stride")
        r = self._row(p, r, "ParaView particle render stride", "paraview_particle_render_stride")
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        r = self._row(p, r, "Open results folder after run", "open_results", check=True)
        r = self._row(p, r, "Open ParaView after run", "open_paraview", check=True)
        ttk.Label(p, text="ParaView note: run 'pvpython view_study_paraview.py <study>/paraview' for a preconfigured 3D view\n"
                          "(particles as spheres, separate excavator, FEA colored by von Mises stress).",
                  justify=LEFT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8)

    def _page_review(self):
        p = ttk.Frame(self.step_nb)
        self.step_nb.add(p, text="6. Review & Run")
        self.pages.append(p)
        top = ttk.Frame(p)
        top.pack(fill=X, padx=6, pady=6)
        ttk.Label(top, text="Review generated configuration/commands. Go back to any step to change parameters.",
                  font=HEADER_FONT).pack(side=LEFT)
        ttk.Button(top, text="Refresh preview", command=self._refresh_preview).pack(side=RIGHT)
        paned = ttk.Panedwindow(p, orient=VERTICAL)
        paned.pack(fill=BOTH, expand=True, padx=6, pady=4)
        self.preview = ttk.Frame(paned)
        self.log_frame = ttk.Frame(paned)
        paned.add(self.preview, weight=1)
        paned.add(self.log_frame, weight=2)
        self.cmd_text = self._text_widget(self.preview, height=16)
        self.log_text = self._text_widget(self.log_frame, height=18)
        runbar = ttk.Frame(p)
        runbar.pack(fill=X, padx=6, pady=4)
        ttk.Button(runbar, text="Stop running process", command=self._stop).pack(side=LEFT)
        self.status_var = StringVar(value="Ready.")
        ttk.Label(runbar, textvariable=self.status_var).pack(side=LEFT, padx=12)

    def _text_widget(self, parent, height=12):
        f = ttk.Frame(parent)
        f.pack(fill=BOTH, expand=True)
        txt = tk.Text(f, height=height, wrap="none")
        self.text_widgets.append(txt)
        vs = ttk.Scrollbar(f, orient=VERTICAL, command=txt.yview)
        hs = ttk.Scrollbar(f, orient=HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        txt.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        return txt

    def _back(self):
        i = self.step_nb.index("current")
        if i > 0:
            self.step_nb.select(i - 1)

    def _next(self):
        i = self.step_nb.index("current")
        if i < self.step_nb.index("end") - 1:
            self.step_nb.select(i + 1)
            if self.step_nb.index("current") == self.step_nb.index("end") - 1:
                self._refresh_preview()

    def _build_tools_tab(self):
        outer = ScrollFrame(self.tools_tab)
        outer.pack(fill=BOTH, expand=True)
        p = outer.inner
        r = 0
        ttk.Label(p, text="Run/view tools for an existing study directory.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=8, pady=10); r += 1
        self.tools_study_dir = StringVar(value=str(ROOT))
        ttk.Label(p, text="Study/output directory").grid(row=r, column=0, sticky=W, padx=8, pady=4)
        ttk.Entry(p, textvariable=self.tools_study_dir, width=70).grid(row=r, column=1, sticky=W, padx=4)
        ttk.Button(p, text="Browse", command=self._browse_tools_dir).grid(row=r, column=2, sticky=W)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Study/output directory", PARAM_INFO["tools_study_dir"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1

        actions = [
            ("Export ParaView VTK/PVD (separate particles & excavator)", self._tool_export),
            ("Open ParaView 3D view (pvpython, spheres + FEA stress)", self._tool_paraview),
            ("Open interactive result plots", self._tool_results),
            ("View geometry comparison (STEP vs converted)", self._tool_geometry),
            ("Open study folder", self._tool_open_folder),
        ]
        for text, cmd in actions:
            ttk.Button(p, text=text, command=cmd, width=54).grid(row=r, column=0, columnspan=2, sticky=W, padx=8, pady=4); r += 1

        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        ttk.Label(p, text="ParaView notes:", font=SECTION_FONT).grid(row=r, column=0, sticky=W, padx=8); r += 1
        notes = (
            "• The 'Open ParaView 3D view' button launches the full ParaView GUI and keeps it open.\n"
            "• It forces QT_QPA_PLATFORM=xcb (X11/XWayland) because ParaView 6.2 can abort on Wayland.\n"
            "• Command line: QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=<study>/paraview paraview --script=view_study_paraview.py\n"
            "  (passing args after '--' may not work with some ParaView builds).\n"
            "• pvpython fallback: pvpython view_study_paraview.py <study>/paraview stays open until its render window is closed.\n"
            "• Particles are rendered as 3D spheres (glyphs sized from export). Excavator is a separate source.\n"
            "• FEA stress/deformation requires FEA VTK export; viewer colors by von Mises and warps by displacement by default."
        )
        ttk.Label(p, text=notes, justify=LEFT).grid(row=r, column=0, columnspan=4, sticky=W, padx=8, pady=4); r += 1

        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        ttk.Label(p, text="Quick export stride (every N CRM frames)").grid(row=r, column=0, sticky=W, padx=8, pady=4)
        self.tools_stride = StringVar(value="1")
        ttk.Entry(p, textvariable=self.tools_stride, width=8).grid(row=r, column=1, sticky=W)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Quick export stride", PARAM_INFO["tools_stride"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        ttk.Label(p, text="Particle render stride (every N particles/frame)").grid(row=r, column=0, sticky=W, padx=8, pady=4)
        self.tools_particle_render_stride = StringVar(value="1")
        ttk.Entry(p, textvariable=self.tools_particle_render_stride, width=8).grid(row=r, column=1, sticky=W)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Particle render stride", PARAM_INFO["tools_particle_render_stride"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        ttk.Label(p, text="Export workers (parallel frame writes)").grid(row=r, column=0, sticky=W, padx=8, pady=4)
        self.tools_workers = StringVar(value=self._g("export_workers") or "4")
        ttk.Entry(p, textvariable=self.tools_workers, width=8).grid(row=r, column=1, sticky=W)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Export workers", PARAM_INFO["tools_workers"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        logf = ttk.Frame(p)
        logf.grid(row=r, column=0, columnspan=4, sticky="nsew", padx=8, pady=8)
        p.rowconfigure(r, weight=1)
        p.columnconfigure(1, weight=1)
        self.tools_log = tk.Text(logf, height=14, wrap="none")
        self.text_widgets.append(self.tools_log)
        vs = ttk.Scrollbar(logf, orient=VERTICAL, command=self.tools_log.yview)
        hs = ttk.Scrollbar(logf, orient=HORIZONTAL, command=self.tools_log.xview)
        self.tools_log.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tools_log.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)

    def _browse_tools_dir(self):
        p = filedialog.askdirectory(initialdir=self.tools_study_dir.get() or str(ROOT))
        if p:
            self.tools_study_dir.set(p)

    # ---- persistent defaults ----
    def _build_defaults_tab(self):
        outer = ScrollFrame(self.defaults_tab)
        outer.pack(fill=BOTH, expand=True)
        p = outer.inner
        r = 0
        ttk.Label(p, text="Persistent starting values for new studies.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=8, pady=8)
        r += 1
        ttk.Label(p, text=f"Saved in {DEFAULT_FILE}. Study name and output directory are generated for each run."
                  ).grid(row=r, column=0, columnspan=4, sticky=W, padx=8)
        r += 1
        bar = ttk.Frame(p)
        bar.grid(row=r, column=0, columnspan=4, sticky=W, padx=8, pady=8)
        for text, cmd in (
            ("Save defaults", self._save_defaults),
            ("Apply to current study", self._apply_defaults_to_study),
            ("Copy current study values", self._copy_study_to_defaults),
            ("Reset to shipped defaults", self._reset_defaults),
        ):
            ttk.Button(bar, text=text, command=cmd).pack(side=LEFT, padx=(0, 6))
        r += 1
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=4)
        r += 1
        for section, fields in DEFAULT_GROUPS:
            ttk.Label(p, text=section, font=SECTION_FONT).grid(
                row=r, column=0, columnspan=4, sticky=W, padx=8, pady=(8, 4))
            r += 1
            for label, key, kwargs in fields:
                r = self._row(p, r, label, key, variables=self.default_vars, **kwargs)
            ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=6)
            r += 1

    def _collect_default_values(self) -> dict[str, object]:
        templates = shipped_defaults()
        return {
            key: coerce_default_value(key, var.get(), templates.get(key))
            for key, var in self.default_vars.items()
        }

    def _save_defaults(self):
        try:
            write_saved_defaults(self._collect_default_values())
            self.status_var.set(f"Saved GUI defaults to {DEFAULT_FILE}")
            messagebox.showinfo("Defaults", f"Saved GUI defaults to:\n{DEFAULT_FILE}")
        except Exception as e:
            messagebox.showerror("Defaults", str(e))

    def _apply_defaults_to_study(self):
        try:
            templates = shipped_defaults()
            for key, var in self.default_vars.items():
                target = self.vars.get(key)
                if target is None:
                    continue
                value = coerce_default_value(key, var.get(), templates.get(key))
                if isinstance(target, BooleanVar):
                    target.set(bool(value))
                else:
                    target.set(str(value))
            if self.step_nb.index("current") == len(self.pages) - 1:
                self._refresh_preview()
            self.status_var.set("Applied default values to the current study fields.")
        except Exception as e:
            messagebox.showerror("Defaults", str(e))

    def _copy_study_to_defaults(self):
        try:
            templates = shipped_defaults()
            for key, target in self.default_vars.items():
                source = self.vars.get(key)
                if source is None:
                    continue
                value = coerce_default_value(key, source.get(), templates.get(key))
                if isinstance(target, BooleanVar):
                    target.set(bool(value))
                else:
                    target.set(str(value))
            self.status_var.set("Copied current study values into Defaults (click Save defaults to keep them).")
        except Exception as e:
            messagebox.showerror("Defaults", str(e))

    def _reset_defaults(self):
        if not messagebox.askyesno(
            "Defaults",
            "Reset all saved defaults to shipped values?\n\n"
            f"This removes {DEFAULT_FILE} and restores the Defaults tab, but does not change "
            "the current study fields until you click 'Apply to current study'.",
        ):
            return
        try:
            if DEFAULT_FILE.exists():
                DEFAULT_FILE.unlink()
            for key, value in shipped_defaults().items():
                var = self.default_vars.get(key)
                if var is None:
                    continue
                if isinstance(var, BooleanVar):
                    var.set(bool(value))
                else:
                    var.set(str(value))
            self.status_var.set("Defaults reset to shipped values. Use 'Apply to current study' if desired.")
        except Exception as e:
            messagebox.showerror("Defaults", str(e))

    # ---- appearance settings ----
    def _build_settings_tab(self):
        outer = ScrollFrame(self.settings_tab)
        outer.pack(fill=BOTH, expand=True)
        p = outer.inner
        r = 0
        ttk.Label(p, text="Appearance and startup preferences (kept separate from study defaults).",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=5, sticky=W, padx=8, pady=8); r += 1
        ttk.Label(p, text=f"Saved in {SETTINGS_FILE}.",
                  font=SECTION_FONT).grid(row=r, column=0, columnspan=5, sticky=W, padx=8); r += 1

        values = self.settings
        self.settings_vars = {
            "font_size": StringVar(value=str(values["font_size"])),
            "window_width": StringVar(value=str(values["window_width"])),
            "window_height": StringVar(value=str(values["window_height"])),
            "text_color": StringVar(value=str(values["text_color"])),
            "dark_mode": BooleanVar(value=bool(values["dark_mode"])),
        }
        r = self._settings_row(p, r, "Text size", "font_size", kind="size")
        r = self._settings_row(p, r, "Window width (px)", "window_width")
        r = self._settings_row(p, r, "Window height (px)", "window_height")
        r = self._settings_row(p, r, "Text color", "text_color", kind="color")
        r = self._settings_row(p, r, "Dark mode", "dark_mode", kind="check")
        ttk.Separator(p).grid(row=r, column=0, columnspan=5, sticky="ew", pady=8); r += 1
        bar = ttk.Frame(p)
        bar.grid(row=r, column=0, columnspan=5, sticky=W, padx=8, pady=6); r += 1
        for text, cmd in (
            ("Apply now", self._apply_settings_now),
            ("Save settings", self._save_settings),
            ("Reset to defaults", self._reset_settings),
        ):
            ttk.Button(bar, text=text, command=cmd).pack(side=LEFT, padx=(0, 6))
        ttk.Label(
            p,
            text="Apply now changes this session only; Save settings also keeps the values for the next launch. "
                 "Text size affects the whole GUI, including the log panels.",
            justify=LEFT,
        ).grid(row=r, column=0, columnspan=5, sticky=W, padx=8, pady=(2, 8)); r += 1
        self._refresh_color_preview()

    def _settings_row(self, parent, r, label, key, kind="entry"):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky=W, padx=8, pady=4)
        var = self.settings_vars[key]
        if kind == "check":
            ttk.Checkbutton(parent, variable=var,
                            command=self._on_dark_mode_toggle).grid(row=r, column=1, sticky=W, padx=4)
        elif kind == "size" and hasattr(ttk, "Spinbox"):
            ttk.Spinbox(parent, from_=7, to=24, width=8,
                        textvariable=var).grid(row=r, column=1, sticky=W, padx=4)
        else:
            ttk.Entry(parent, textvariable=var, width=12).grid(row=r, column=1, sticky=W, padx=4)
        if kind == "color":
            self.color_preview = ttk.Label(parent, text="", width=12, relief="sunken")
            self.color_preview.grid(row=r, column=2, sticky=W, padx=4)
            ttk.Button(parent, text="Pick...", width=8,
                       command=self._pick_text_color).grid(row=r, column=3, sticky=W, padx=4)
            var.trace_add("write", lambda *_: self._refresh_color_preview())
        ttk.Button(parent, text="Info", width=6,
                   command=lambda l=label, k=key: self._show_info(l, SETTINGS_INFO.get(k, ""))).grid(
                       row=r, column=4, sticky=W, padx=6)
        return r + 1

    def _refresh_color_preview(self):
        preview = getattr(self, "color_preview", None)
        if preview is None:
            return
        color = self.settings_vars["text_color"].get().strip()
        if not _valid_color(color):
            preview.configure(text="invalid", background="#777777", foreground=DARK_TEXT)
            return
        dark = bool(self.settings_vars["dark_mode"].get())
        preview.configure(text=color, background=color,
                          foreground=DARK_TEXT if dark else LIGHT_TEXT)

    def _pick_text_color(self):
        chosen = colorchooser.askcolor(
            color=self.settings_vars["text_color"].get(), title="Choose text color")
        if chosen and chosen[1]:
            self.settings_vars["text_color"].set(chosen[1])
            self._refresh_color_preview()

    def _on_dark_mode_toggle(self):
        dark = bool(self.settings_vars["dark_mode"].get())
        current = self.settings_vars["text_color"].get().strip().lower()
        if current in {LIGHT_TEXT.lower(), DARK_TEXT.lower()}:
            self.settings_vars["text_color"].set(DARK_TEXT if dark else LIGHT_TEXT)
        self._refresh_color_preview()
        self._apply_settings_now()

    def _collect_settings_from_vars(self, strict: bool = False) -> dict[str, object]:
        color = self.settings_vars["text_color"].get().strip()
        if not _valid_color(color):
            if strict:
                raise ValueError("Text color must be a hex color such as #336699.")
            color = str(self.settings.get("text_color", SHIPPED_SETTINGS["text_color"]))
        return {
            "font_size": _setting_int(self.settings_vars["font_size"].get(), "Text size",
                                      7, 24, int(self.settings["font_size"]), strict=strict),
            "window_width": _setting_int(self.settings_vars["window_width"].get(), "Window width",
                                         640, 3840, int(self.settings["window_width"]), strict=strict),
            "window_height": _setting_int(self.settings_vars["window_height"].get(), "Window height",
                                          480, 2160, int(self.settings["window_height"]), strict=strict),
            "text_color": color,
            "dark_mode": bool(self.settings_vars["dark_mode"].get()),
        }

    def _sync_settings_vars(self, values: dict[str, object]) -> None:
        if not self.settings_vars:
            return
        for key in ("font_size", "window_width", "window_height", "text_color"):
            self.settings_vars[key].set(str(values[key]))
        self.settings_vars["dark_mode"].set(bool(values["dark_mode"]))

    def _walk_widgets(self, widget=None):
        if widget is None:
            widget = self.root
        yield widget
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            yield from self._walk_widgets(child)

    def _apply_ui_settings(self, values: dict[str, object] | None = None) -> None:
        """Apply appearance preferences to the live Tk widgets."""
        values = values or self.settings
        size = _setting_int(values.get("font_size"), "Text size", 7, 24, 10)
        width = _setting_int(values.get("window_width"), "Window width", 640, 3840, 1100)
        height = _setting_int(values.get("window_height"), "Window height", 480, 2160, 1080)
        color = str(values.get("text_color") or LIGHT_TEXT)
        if not _valid_color(color):
            color = str(SHIPPED_SETTINGS["text_color"])
        dark = bool(values.get("dark_mode"))

        self.root.geometry(f"{width}x{height}")

        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(size=size)
            except tk.TclError:
                pass
        for name, font_size in ((HEADER_FONT, size), (SECTION_FONT, max(7, size - 1))):
            try:
                font = tkfont.nametofont(name)
            except tk.TclError:
                font = tkfont.Font(name=name, exists=False, family="TkDefaultFont",
                                   size=font_size, weight="bold")
            font.configure(family="TkDefaultFont", size=font_size, weight="bold")

        style = ttk.Style()
        theme = "clam" if dark and "clam" in style.theme_names() else self._base_theme
        if theme in style.theme_names() and style.theme_use() != theme:
            style.theme_use(theme)

        bg = "#1E1E1E" if dark else "#FFFFFF"
        frame = "#252526" if dark else "#F4F4F4"
        field = "#2D2D30" if dark else "#FFFFFF"
        button = "#3A3F4B" if dark else "#E6E6E6"
        try:
            style.configure(".", background=bg, foreground=color, font=("TkDefaultFont", size))
            for cls in ("TFrame", "TLabelframe", "TLabelframe.Label", "TLabel",
                        "TButton", "TCheckbutton", "TRadiobutton", "TScale"):
                style.configure(cls, background=bg, foreground=color)
            style.configure("TButton", background=button, foreground=color)
            style.configure("TNotebook", background=frame)
            style.configure("TNotebook.Tab", background=frame, foreground=color, padding=(8, 4))
            style.map("TNotebook.Tab", background=[("selected", field)],
                      foreground=[("selected", color)])
            for cls in ("TEntry", "TCombobox", "TSpinbox"):
                style.configure(cls, fieldbackground=field, foreground=color,
                                insertcolor=color, arrowcolor=color)
        except tk.TclError:
            pass  # Some platform themes do not allow every style option.

        self.root.configure(background=bg)
        for widget in self._walk_widgets():
            try:
                wclass = widget.winfo_class()
                if wclass == "Text":
                    widget.configure(background=field, foreground=color,
                                     insertbackground=color, font=("TkFixedFont", size))
                elif wclass == "Canvas":
                    widget.configure(background=bg)
            except tk.TclError:
                continue

    def _apply_settings_now(self):
        try:
            values = self._collect_settings_from_vars(strict=True)
        except ValueError as e:
            messagebox.showerror("Settings", str(e))
            return
        self.settings = values
        self._sync_settings_vars(values)
        self._apply_ui_settings(values)
        self._refresh_color_preview()
        self.status_var.set("Appearance settings applied (not saved yet).")

    def _save_settings(self):
        if not self._apply_settings_from_ui():
            return
        try:
            write_ui_settings(self.settings)
            self.status_var.set(f"Saved GUI appearance settings to {SETTINGS_FILE}")
            messagebox.showinfo("Settings", f"Saved GUI appearance settings to:\n{SETTINGS_FILE}")
        except Exception as e:
            messagebox.showerror("Settings", str(e))

    def _apply_settings_from_ui(self) -> bool:
        try:
            values = self._collect_settings_from_vars(strict=True)
        except ValueError as e:
            messagebox.showerror("Settings", str(e))
            return False
        self.settings = values
        self._sync_settings_vars(values)
        self._apply_ui_settings(values)
        self._refresh_color_preview()
        return True

    def _reset_settings(self):
        if not messagebox.askyesno(
            "Settings",
            "Reset text size, window size, text color, and dark mode to shipped defaults?",
        ):
            return
        values = dict(SHIPPED_SETTINGS)
        self.settings = values
        self._sync_settings_vars(values)
        self._apply_ui_settings(values)
        self._refresh_color_preview()
        try:
            write_ui_settings(values)
            self.status_var.set("Appearance settings reset to shipped defaults.")
        except Exception as e:
            messagebox.showerror("Settings", str(e))

    # ---- config/build helpers ----
    def _g(self, key: str) -> str:
        v = self.vars[key]
        return v.get() if isinstance(v, StringVar) else ("1" if v.get() else "0")

    def _gb(self, key: str) -> bool:
        v = self.vars[key]
        return bool(v.get()) if isinstance(v, BooleanVar) else v.get().strip().lower() in {"1", "true", "yes", "y"}

    def _parse_active_box(self, s: str):
        parts = [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]
        if len(parts) != 3:
            raise ValueError("Active box must be three comma-separated numbers X,Y,Z")
        vals = tuple(float(x) for x in parts)
        if min(vals) <= 0:
            raise ValueError("Active box values must be positive")
        return vals

    def _collect_cfg(self) -> dict:
        geo = self._g("geometry")
        out_dir = Path(self._g("output_dir")).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        step_file = Path(self._g("step_file")).expanduser().resolve() if self._g("step_file").strip() else None
        crm_obj = Path(self._g("crm_obj")).expanduser().resolve()
        fea_node = Path(self._g("fea_node")).expanduser().resolve()
        fea_ele = Path(self._g("fea_ele")).expanduser().resolve()
        cad_axis = self._g("cad_axis") or "Y"
        if cad_axis == "auto" and step_file and step_file.is_file():
            cad_axis = wiz.detect_cad_axis(step_file)
        elif cad_axis == "auto":
            cad_axis = "Y"
        unit_scale = float(self._g("unit_scale") or 0.001)
        mesh_size = float(self._g("mesh_size") or 0.012)
        surface_faces = int(float(self._g("surface_target_faces") or 8000))
        surface_max_faces = int(float(self._g("surface_max_faces") or 256000))
        surface_repair_order = self._g("surface_repair_order") or "none"
        min_solid_volume = float(self._g("min_solid_volume") or 0.0)
        hub_radius = float(self._g("hub_radius") or 0.0015)
        drum_radius = float(self._g("drum_radius") or 0.061)
        drum_half_length = float(self._g("drum_half_length") or 0.033)
        inner_radius = float(self._g("inner_radius") or 0.055)
        cap_raw = self._g("capacity_volume").strip()
        capacity_volume = float(cap_raw) if cap_raw else None

        mesh_steps = []
        if geo == "step":
            if not step_file or not step_file.is_file():
                raise ValueError(f"STEP file not found: {step_file}")
            mesh_dir = out_dir / "mesh"
            mesh_dir.mkdir(parents=True, exist_ok=True)
            prefix = mesh_dir / f"{step_file.stem}_fea"
            fea_node = Path(f"{prefix}.node")
            fea_ele = Path(f"{prefix}.ele")
            surface_stl = Path(f"{prefix}_surface.stl")
            crm_obj = Path(f"{prefix}_surface.obj")
            cmd = [
                str(ROOT / "chrono-env" / "bin" / "python"),
                str(ROOT / "step_to_chrono_fea.py"),
                str(step_file),
                "--out-prefix", str(prefix),
                "--unit-scale", f"{unit_scale:g}",
                "--mesh-size", f"{mesh_size:g}",
                "--surface-target-faces", str(surface_faces),
                "--surface-max-faces", str(surface_max_faces),
                "--min-solid-volume", f"{min_solid_volume:g}",
                "--hub-radius", f"{hub_radius:g}",
                "--axis", cad_axis,
                "--tetgen-switches", "pYQ",
                "--time-stages",
            ]
            if surface_repair_order == "after":
                cmd.append("--repair-after-decimation")
            mesh_steps.append({"name": "generate-FEA-mesh", "command": cmd})
            mesh_steps.append({
                "name": "make-CRM-surface-OBJ",
                "command": [str(ROOT / "chrono-env" / "bin" / "python"),
                            str(ROOT / "stl_to_obj.py"), str(surface_stl)],
            })
        elif geo == "files":
            if not crm_obj.is_file():
                raise ValueError(f"CRM OBJ not found: {crm_obj}")
            if not fea_node.is_file():
                raise ValueError(f"FEA .node not found: {fea_node}")
            if not fea_ele.is_file():
                raise ValueError(f"FEA .ele not found: {fea_ele}")

        duration = float(self._g("duration"))
        fea_duration = float(self._g("fea_duration"))
        spacing = float(self._g("spacing"))
        d0_multiplier = float(self._g("d0_multiplier"))
        artificial_viscosity = float(self._g("artificial_viscosity") or 0.5)
        shifting_ppst_push = float(self._g("shifting_ppst_push") or 3.0)
        crm_dt_raw = self._g("crm_dt").strip().lower()
        if crm_dt_raw in {"", "auto"}:
            crm_dt = wiz.suggested_sph_dt(spacing, d0_multiplier)
        else:
            crm_dt = float(crm_dt_raw)
        crm_fps = float(self._g("crm_fps"))
        fea_fps = float(self._g("fea_fps"))
        loads_fps = float(self._g("loads_fps") or 20)
        particle_fps = float(self._g("particle_fps") or 5)
        particle_writers = max(1, int(float(self._g("particle_writers") or 4)))
        fea_dt = float(self._g("fea_dt"))
        bed_height = float(self._g("bed_height"))
        active_box = self._parse_active_box(self._g("active_box"))
        settle_time = float(self._g("settle_time"))
        descent_speed = float(self._g("descent_speed"))
        omega_mag = float(self._g("omega"))
        reverse_spin = self._gb("reverse_spin")
        omega = -abs(omega_mag) if reverse_spin else abs(omega_mag)
        spin_axis = self._g("spin_axis") or "X"
        solver = self._g("solver") or "pardiso-mkl"
        num_threads = int(float(self._g("num_threads") or 8))
        load_radius_fraction = float(self._g("load_radius_fraction") or 0.95)
        raw_stress_every = max(1, int(float(self._g("raw_stress_every") or 5)))
        export_workers = max(1, int(float(self._g("export_workers") or 4)))

        soil = {k: float(self._g(f"soil_{k}")) for k in wiz.SOIL_DEFAULTS}
        pla = {k: float(self._g(f"pla_{k}")) for k in wiz.PLA_DEFAULTS}

        cfg = {
            "study_name": self._g("study_name"),
            "output_dir": out_dir,
            "geometry": geo,
            "step_file": step_file,
            "crm_obj": crm_obj,
            "fea_node": fea_node,
            "fea_ele": fea_ele,
            "mesh_steps": mesh_steps,
            "geometry_defaults": wiz.geometry_defaults(None, auto=False),
            "auto_geometry": self._gb("auto_geometry"),
            "cad_axis": cad_axis,
            "unit_scale": unit_scale,
            "mesh_size": mesh_size,
            "surface_faces": surface_faces,
            "surface_max_faces": surface_max_faces,
            "surface_repair_order": surface_repair_order,
            "hub_radius": hub_radius,
            "drum_radius": drum_radius,
            "drum_half_length": drum_half_length,
            "min_solid_volume": min_solid_volume,
            "duration": duration,
            "fea_duration": fea_duration,
            "spacing": spacing,
            "d0_multiplier": d0_multiplier,
            "artificial_viscosity": artificial_viscosity,
            "shifting_ppst_push": shifting_ppst_push,
            "crm_dt": crm_dt,
            "auto_crm_dt": wiz.suggested_sph_dt(spacing, d0_multiplier),
            "crm_fps": crm_fps,
            "fea_fps": fea_fps,
            "loads_fps": loads_fps,
            "particle_fps": particle_fps,
            "particle_writers": particle_writers,
            "bed_height": bed_height,
            "active_box": active_box,
            "settle_time": settle_time,
            "descent_speed": descent_speed,
            "omega": omega,
            "reverse_spin": reverse_spin,
            "spin_axis": spin_axis,
            "inner_radius": inner_radius,
            "capacity_volume": capacity_volume,
            "soil": soil,
            "fea_dt": fea_dt,
            "solver": solver,
            "num_threads": num_threads,
            "load_radius_fraction": load_radius_fraction,
            "raw_stress_every": raw_stress_every,
            "export_workers": export_workers,
            "pla": pla,
            "save_particles": self._gb("save_particles"),
            "fea_export_vtk": self._gb("fea_export_vtk"),
            "fea_vtk_stride": max(1, int(float(self._g("fea_vtk_stride") or 1))),
            "export_vtk": self._gb("export_vtk"),
            "paraview_stride": max(1, int(float(self._g("paraview_stride") or 1))),
            "paraview_particle_render_stride": max(1, int(float(self._g("paraview_particle_render_stride") or 1))),
            "export_workers": export_workers,
            "open_results": self._gb("open_results"),
            "open_paraview": self._gb("open_paraview"),
            "no_open": False,
            "dry_run": False,
        }
        return cfg

    def _chrono_env_python(self) -> str | None:
        cand = ROOT / "chrono-env" / "bin" / "python"
        return str(cand) if cand.is_file() else None

    def _measure_step_via_chrono_env(self, step_file: Path, axis: str, unit_scale: float):
        """Measure STEP geometry using chrono-env Python (for OCP), if available."""
        py = self._chrono_env_python()
        if not py:
            return None
        code = r"""
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
step = Path(sys.argv[2])
axis = sys.argv[3]
unit_scale = float(sys.argv[4])
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'crm'))
import study_wizard as wiz
if axis == 'auto':
    axis = wiz.detect_cad_axis(step)
m = wiz.measure_geometry(step, is_step=True, axis=axis, unit_scale=unit_scale)
print(json.dumps({'axis': axis, 'measured': m}, default=float))
"""
        try:
            r = subprocess.run(
                [py, "-c", code, str(ROOT), str(step_file), axis, str(unit_scale)],
                cwd=ROOT, capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout.strip().splitlines()[-1])
                if isinstance(data, dict):
                    return data
        except Exception:
            return None
        return None

    def _apply_geo_defaults(self):
        try:
            geo = self._g("geometry")
            cad_axis = self._g("cad_axis") or "Y"
            unit_scale = float(self._g("unit_scale") or 0.001)
            measured = None
            if geo == "step":
                step_file = Path(self._g("step_file")).expanduser().resolve()
                if not step_file.is_file():
                    messagebox.showwarning("Geometry", f"STEP file not found:\n{step_file}")
                    return
                # Try in-process first (works when running under chrono-env or if OCP is installed)
                if cad_axis == "auto":
                    try:
                        cad_axis = wiz.detect_cad_axis(step_file)
                        self.vars["cad_axis"].set(cad_axis)
                    except Exception:
                        pass
                if step_file.is_file():
                    try:
                        measured = wiz.measure_geometry(
                            step_file, is_step=True, axis=cad_axis, unit_scale=unit_scale
                        )
                    except Exception:
                        measured = None
                # Fallback: use chrono-env Python for OCP/STEP measurement (allows GUI to run
                # under system Python for proper display scaling while using project deps).
                if not measured:
                    data = self._measure_step_via_chrono_env(step_file, cad_axis, unit_scale)
                    if isinstance(data, dict):
                        if data.get("axis"):
                            cad_axis = str(data["axis"])
                            self.vars["cad_axis"].set(cad_axis)
                        measured = data.get("measured")
                if not measured:
                    try:
                        import OCP  # noqa: F401
                        ocp_ok = True
                    except Exception:
                        ocp_ok = False
                    chrono_py = self._chrono_env_python()
                    if not ocp_ok and not chrono_py:
                        messagebox.showwarning(
                            "Geometry",
                            "Could not measure STEP geometry because OCP is not available in the "
                            "current Python and chrono-env/bin/python was not found.\n\n"
                            f"STEP file: {step_file}\n"
                            f"Python: {sys.executable}\n\n"
                            "Install/use the project chrono-env (with OCP/cadquery-ocp) or run with a Python that has OCP."
                        )
                    elif not ocp_ok and chrono_py:
                        messagebox.showwarning(
                            "Geometry",
                            "Could not measure STEP geometry. OCP is not available in the current "
                            "Python and the chrono-env fallback also failed.\n\n"
                            f"STEP file: {step_file}\n"
                            f"GUI Python: {sys.executable}\n"
                            f"chrono-env: {chrono_py}\n"
                            f"CAD axis: {cad_axis}, unit scale: {unit_scale:g}"
                        )
                    else:
                        messagebox.showwarning(
                            "Geometry",
                            "Could not measure STEP geometry (file may be unreadable or unsupported).\n\n"
                            f"STEP file: {step_file}\n"
                            f"CAD axis: {cad_axis}, unit scale: {unit_scale:g}"
                        )
                    return
            else:
                crm_obj = Path(self._g("crm_obj")).expanduser().resolve()
                fea_node = Path(self._g("fea_node")).expanduser().resolve()
                if not crm_obj.is_file():
                    messagebox.showwarning("Geometry", f"CRM surface OBJ not found:\n{crm_obj}")
                    return
                try:
                    measured = wiz.measure_geometry(
                        crm_obj, is_step=False, axis="Z", unit_scale=1.0,
                        metadata=wiz.read_mesh_metadata(fea_node)
                    )
                except Exception:
                    measured = None
                if not measured:
                    messagebox.showwarning(
                        "Geometry",
                        "Could not measure mesh geometry (file may be unreadable or dependencies "
                        "such as trimesh may be unavailable in this Python).\n\n"
                        f"Mesh: {crm_obj}\nPython: {sys.executable}"
                    )
                    return
            gd = wiz.geometry_defaults(measured, auto=True)
            for k in ["mesh_size", "surface_target_faces", "min_solid_volume", "hub_radius",
                      "drum_radius", "drum_half_length", "inner_radius", "bed_height",
                      "descent_speed", "spacing"]:
                if k in gd and k in self.vars:
                    self.vars[k].set(f"{gd[k]:g}")
            if "active_box" in gd:
                self.vars["active_box"].set(",".join(f"{v:g}" for v in gd["active_box"]))
            self.vars["fea_duration"].set(self.vars["duration"].get())
            self.status_var.set("Applied CAD/mesh-derived defaults (you can still edit them).")
            messagebox.showinfo("Geometry", "Applied geometry-derived defaults. Review and adjust any field before running.")
        except Exception as e:
            messagebox.showerror("Geometry", str(e))

    def _load_config(self):
        path = filedialog.askopenfilename(initialdir=str(ROOT), filetypes=[("JSON", "*.json"), ("All", "*")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self._apply_config(data)
            self.status_var.set(f"Loaded config: {path}")
        except Exception as e:
            messagebox.showerror("Load config", str(e))

    def _apply_config(self, data: dict):
        def setv(k, v):
            if k in self.vars and v is not None:
                if isinstance(self.vars[k], BooleanVar):
                    self.vars[k].set(bool(v))
                else:
                    self.vars[k].set(str(v))
        for k in ["study_name", "geometry", "cad_axis", "surface_repair_order", "spin_axis", "solver", "crm_dt"]:
            setv(k, data.get(k))
        for k in ["unit_scale", "mesh_size", "surface_target_faces", "surface_faces", "surface_max_faces",
                  "min_solid_volume", "hub_radius",
                  "drum_radius", "drum_half_length", "inner_radius", "capacity_volume", "duration",
                  "fea_duration", "spacing", "d0_multiplier", "artificial_viscosity",
                   "shifting_ppst_push", "crm_fps", "fea_fps", "fea_dt",
                  "bed_height", "settle_time", "descent_speed", "omega", "num_threads",
                  "load_radius_fraction", "raw_stress_every", "export_workers",
                  "fea_vtk_stride", "paraview_stride", "paraview_particle_render_stride"]:
            setv(k, data.get(k))
        for k in ["reverse_spin", "save_particles", "fea_export_vtk", "export_vtk", "open_results", "open_paraview", "auto_geometry"]:
            if k in data:
                setv(k, data.get(k))
        for k, v in {"output_dir": data.get("output_dir"), "step_file": data.get("step_file"),
                     "crm_obj": data.get("crm_obj"), "fea_node": data.get("fea_node"),
                     "fea_ele": data.get("fea_ele")}.items():
            if v:
                setv(k, v)
        ab = data.get("active_box")
        if isinstance(ab, (list, tuple)) and len(ab) == 3:
            setv("active_box", ",".join(f"{float(x):g}" for x in ab))
        soil = data.get("soil") or {}
        for k in wiz.SOIL_DEFAULTS:
            if k in soil:
                setv(f"soil_{k}", soil[k])
        pla = data.get("pla") or {}
        for k in wiz.PLA_DEFAULTS:
            if k in pla:
                setv(f"pla_{k}", pla[k])

    def _refresh_preview(self):
        try:
            cfg = self._collect_cfg()
            steps = wiz.build_commands(cfg)
            wiz.write_reproducibility_files(cfg, steps)
            lines = [f"Output: {cfg['output_dir']}", f"Config: {cfg['output_dir'] / 'study_config.json'}", ""]
            for s in steps:
                lines.append(f"[{s['name']}]")
                lines.append(shlex.join(s['command']))
                lines.append("")
            self._set_text(self.cmd_text, "\n".join(lines))
            self.status_var.set("Preview/config updated. You can still go back and edit before running.")
        except Exception as e:
            messagebox.showerror("Preview", str(e))

    def _save_only(self):
        try:
            self._refresh_preview()
            messagebox.showinfo("Saved", "study_config.json and commands.sh were written to the output directory.")
        except Exception as e:
            messagebox.showerror("Save", str(e))

    # ---- run ----
    def _run_study(self):
        if self.proc and self.proc.poll() is None:
            messagebox.showwarning("Run", "A process is already running.")
            return
        try:
            cfg = self._collect_cfg()
            steps = wiz.build_commands(cfg)
            wiz.write_reproducibility_files(cfg, steps)
            self._set_text(self.log_text, "")
            self.status_var.set("Running study...")
            self.step_nb.select(len(self.pages) - 1)
            self._refresh_preview()
            self.proc = "starting"  # marker
            t = threading.Thread(target=self._run_thread, args=(cfg, steps), daemon=True)
            t.start()
        except Exception as e:
            messagebox.showerror("Run", str(e))

    def _run_thread(self, cfg, steps):
        try:
            started = time.perf_counter()
            for step in steps:
                self.log_queue.put(("line", f"\n[{step['name']}] {shlex.join(step['command'])}\n"))
                log_path = Path(step["log"])
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("w", encoding="utf-8") as log:
                    proc = subprocess.Popen(step["command"], cwd=ROOT, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, bufsize=1)
                    self.proc = proc
                    assert proc.stdout is not None
                    for line in proc.stdout:
                        log.write(line)
                        log.flush()
                        self.log_queue.put(("line", line))
                    rc = proc.wait()
                if rc != 0:
                    self.log_queue.put(("line", f"[{step['name']}] FAILED (exit {rc})\n"))
                    self.log_queue.put(("done", f"failed:{step['name']}"))
                    return
                self.log_queue.put(("line", f"[{step['name']}] complete\n"))
            # post results (capture summary text)
            self.log_queue.put(("line", f"\nTotal wall time: {time.perf_counter()-started:.1f} s\n"))
            self.log_queue.put(("done", "ok"))
        except Exception as e:
            self.log_queue.put(("line", f"\nERROR: {e}\n"))
            self.log_queue.put(("done", "error"))

    def _stop(self):
        p = self.proc
        if isinstance(p, subprocess.Popen) and p.poll() is None:
            try:
                p.terminate()
                self.log_queue.put(("line", "\n[stop requested]\n"))
            except Exception:
                pass

    def _drain_log(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "line":
                    self.log_text.insert(END, str(payload))
                    self.log_text.see(END)
                elif kind == "done":
                    status = str(payload)
                    self.proc = None
                    if status == "ok":
                        self.status_var.set("Study completed. See output folder and ParaView export if enabled.")
                        try:
                            cfg = self._collect_cfg()
                            # print summary paths in log
                            out_dir = Path(self._g("output_dir")).expanduser().resolve()
                            self.log_text.insert(END, f"\nResults folder: {out_dir}\n")
                            if (out_dir / "paraview").is_dir():
                                self.log_text.insert(
                                    END,
                                    f"ParaView 3D: QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR={out_dir/'paraview'} "
                                    f"paraview --script={ROOT/'view_study_paraview.py'}\n",
                                )
                            self.log_text.see(END)
                        except Exception:
                            pass
                    else:
                        self.status_var.set(f"Study stopped ({status}). Check logs.")
        except queue.Empty:
            pass
        self.root.after(120, self._drain_log)

    def _drain_tools_log(self):
        try:
            while True:
                kind, payload = self.tools_queue.get_nowait()
                if kind == "line":
                    text = str(payload)
                    if not text.endswith("\n"):
                        text += "\n"
                    self.tools_log.insert(END, text)
                    self.tools_log.see(END)
                elif kind == "done":
                    self._tool_log(str(payload))
        except queue.Empty:
            pass
        self.root.after(120, self._drain_tools_log)

    # ---- tools ----
    def _tool_log(self, msg: str):
        if hasattr(self, "tools_log"):
            self.tools_log.insert(END, msg + "\n")
            self.tools_log.see(END)

    def _run_tool_cmd(self, cmd, cwd=None, stream: bool = False):
        self._tool_log("$ " + shlex.join(cmd))
        try:
            p = subprocess.Popen(cmd, cwd=cwd or ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if stream:
                # Keep the GUI responsive while a long export streams progress.
                threading.Thread(target=self._watch_tool_process,
                                 args=(p, "export"), daemon=True).start()
                return 0
            out, _ = p.communicate(timeout=600)
            if out:
                self.tools_log.insert(END, out[-8000:] + ("\n" if not out.endswith("\n") else ""))
                self.tools_log.see(END)
            self._tool_log(f"[exit {p.returncode}]")
            return p.returncode
        except Exception as e:
            self._tool_log(f"[error] {e}")
            return 1

    def _tool_export(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        stride = max(1, int(float(self.tools_stride.get() or 1)))
        particle_render_stride = max(1, int(float(self.tools_particle_render_stride.get() or 1)))
        workers = max(1, int(float(self.tools_workers.get() or 4)))
        cmd = [py_exec(), str(ROOT / "export_study_vtk.py"), "--study-dir", str(d),
               "--stride", str(stride), "--workers", str(workers),
               "--particle-render-stride", str(particle_render_stride)]
        self._tool_log(f"# exporting {workers} frame worker process(es) "
                       f"(stride {stride}, particle render stride "
                       f"{particle_render_stride}, columnar CSV parsing)")
        self._run_tool_cmd(cmd, stream=True)

    def _watch_tool_process(self, proc: subprocess.Popen, label: str):
        """Stream a long-lived tool's output into the tools log."""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.tools_queue.put(("line", line))
            self.tools_queue.put(("done", f"[{label} exited {proc.wait()}]"))
        except Exception as exc:
            self.tools_queue.put(("done", f"[{label} log error: {exc}]"))

    def _tool_paraview(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        pdir = d / "paraview" if (d / "paraview").is_dir() else d
        script = str(ROOT / "view_study_paraview.py")
        env = os.environ.copy()
        env["PYCHRONO_EXPORT_DIR"] = str(pdir)
        env["PYCHRONO_STUDY_DIR"] = str(d)
        # ParaView 6.2 can abort on Wayland; X11/XWayland is the stable backend here.
        env["QT_QPA_PLATFORM"] = os.environ.get("PYCHRONO_PARAVIEW_QT_PLATFORM", "xcb")

        # The full ParaView GUI stays open after --script finishes; pvpython
        # needs the viewer's Interact() loop to avoid exiting immediately.
        paraview_bin = shutil.which("paraview")
        if paraview_bin:
            cmd = [paraview_bin, f"--script={script}"]
            self._tool_log("$ " + shlex.join(cmd))
            self._tool_log(f"# PYCHRONO_EXPORT_DIR={pdir} QT_QPA_PLATFORM={env['QT_QPA_PLATFORM']}")
            try:
                proc = subprocess.Popen(
                    cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                    start_new_session=True,
                )
                self._tool_log("[launched ParaView GUI; close its window to exit]")
                threading.Thread(target=self._watch_tool_process, args=(proc, "ParaView"), daemon=True).start()
                return
            except Exception as exc:
                self._tool_log(f"[paraview launch failed: {exc}] trying pvpython...")

        pvpython_bin = shutil.which("pvpython") or "pvpython"
        cmd = [pvpython_bin, script, str(pdir)]
        self._tool_log("$ " + shlex.join(cmd))
        try:
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                start_new_session=True,
            )
            self._tool_log("[launched pvpython render window; close it to exit]")
            threading.Thread(target=self._watch_tool_process, args=(proc, "pvpython"), daemon=True).start()
        except Exception as exc:
            self._tool_log(f"[error launching viewer: {exc}]")

    def _tool_results(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        cmd = [py_exec(), str(ROOT / "view_results.py"), "--output-dir", str(d), "--show"]
        self._tool_log("$ " + shlex.join(cmd))
        try:
            subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._tool_log("[launched interactive results plot]")
        except Exception as e:
            self._tool_log(f"[error] {e}")

    def _tool_geometry(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        threads = max(0, int(float(self._g("num_threads") or 8)))
        cmd = [py_exec(), str(ROOT / "view_geometry.py"), "--study-dir", str(d),
               "--threads", str(threads)]
        self._tool_log("$ " + shlex.join(cmd))
        self._tool_log(f"# OCC parallel tessellation/fuse enabled ({threads or 'all'} threads)")
        try:
            subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._tool_log("[launched geometry view]")
        except Exception as e:
            self._tool_log(f"[error] {e}")

    def _tool_open_folder(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        wiz.open_path(d)
        self._tool_log(f"[open folder] {d}")

    @staticmethod
    def _set_text(widget, text):
        widget.delete("1.0", END)
        widget.insert("1.0", text)


def main():
    root = Tk()
    # Create app (applies scaling/fonts/theme) before showing UI
    app = StudyGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
