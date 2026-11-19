#!/usr/bin/env python3
"""Interactive wrapper for a complete CRM -> one-way FEA excavator study.

It asks for geometry, run time, bed/numerics, and solver settings, writes a
reproducible ``commands.sh`` plus ``study_config.json``, then runs the rigid
Chrono::CRM stage followed by the one-way FEA replay.

All important prompts also have CLI flags, so the same wizard can be used
non-interactively for automation:

    ./run_study.sh --dry-run --duration 0.5 --spacing 0.012

"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "crm"))

try:
    from lunar_excavator_crm import suggested_sph_dt
except Exception:  # pragma: no cover - fallback keeps the wizard usable
    def suggested_sph_dt(spacing: float, d0_multiplier: float = 1.0) -> float:
        return min(5.0e-4, 8.0e-5 * (spacing / 0.004) * d0_multiplier)


def discover_default(relative: str) -> Path:
    """Find a shipped asset, including geometry relocated into a subfolder."""
    preferred = ROOT / relative
    candidates = [preferred]
    candidates.extend(sorted((ROOT / "geometry").glob(f"*/{Path(relative).name}")))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return preferred


DEFAULT_CRM_OBJ = discover_default("geometry/LunarDrumScrew_m_coarse.obj")
DEFAULT_STEP_FILE = discover_default("geometry/LunarDrumScrew_m.step")
DEFAULT_FEA_NODE = ROOT / "fea" / "LunarDrumScrew_m_fea_decim8k.node"
DEFAULT_FEA_ELE = DEFAULT_FEA_NODE.with_suffix(".ele")
INTERACTIVE = sys.stdin.isatty()
# Set from --yes so an interactive terminal can still be used for fully
# scripted runs without requiring a here-document of blank prompt answers.
ACCEPT_DEFAULTS = False

# Baseline model dimensions used to scale CAD-derived run defaults.  The rigid
# CRM config is calibrated around this 61 mm-radius drum; scaling the bed,
# descent rate, and SPH spacing by the measured radius keeps the particle count
# and the descent time approximately constant for a differently sized model.
LEGACY_DRUM_RADIUS = 0.061
LEGACY_DRUM_HALF_LENGTH = 0.033
LEGACY_BED_HEIGHT = 0.06
LEGACY_DESCENT_SPEED = 0.0015
LEGACY_ACTIVE_BOX = 0.244
LEGACY_SPACING = 0.004

SOIL_DEFAULTS = {
    "density": 1650.0,
    "youngs_modulus": 2.0e6,
    "poisson_ratio": 0.30,
    "friction": 0.75,
    "cohesion": 1200.0,
    "grain_diameter": 2.0e-4,
}
PLA_DEFAULTS = {
    "youngs_modulus": 3.0e9,
    "poisson_ratio": 0.38,
    "density": 1240.0,
    "rayleigh_beta": 0.01,
}
SOIL_FIELDS = {
    "density": ("Bulk density (kg/m^3)", {"minimum": 1.0}),
    "youngs_modulus": ("Soil Young's modulus (Pa)", {"minimum": 1.0}),
    "poisson_ratio": ("Soil Poisson ratio", {"minimum": 0.0, "maximum": 0.499}),
    "friction": ("Static friction mu_s", {"minimum": 0.0}),
    "cohesion": ("Cohesion (Pa)", {"minimum": 0.0}),
    "grain_diameter": ("Physical grain diameter (m)", {"minimum": 1e-7}),
}
PLA_FIELDS = {
    "youngs_modulus": ("PLA Young's modulus (Pa)", {"minimum": 1.0}),
    "poisson_ratio": ("PLA Poisson ratio", {"minimum": 0.0, "maximum": 0.499}),
    "density": ("PLA density (kg/m^3)", {"minimum": 1.0}),
    "rayleigh_beta": ("Rayleigh damping beta (s)", {"minimum": 0.0}),
}


def detect_cad_axis(step_file: Path) -> str:
    """Return the likely drum axis: the smallest bounding-box extent."""
    try:
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib
        from OCP.IFSelect import IFSelect_RetDone
        from OCP.STEPControl import STEPControl_Reader
    except ImportError:
        return "Y"
    reader = STEPControl_Reader()
    if reader.ReadFile(str(step_file)) != IFSelect_RetDone:
        return "Y"
    reader.TransferRoots()
    box = Bnd_Box()
    BRepBndLib.Add_s(reader.OneShape(), box)
    extents = [
        box.GetXMax() - box.GetXMin(),
        box.GetYMax() - box.GetYMin(),
        box.GetZMax() - box.GetZMin(),
    ]
    return ("X", "Y", "Z")[extents.index(min(extents))]


def read_mesh_metadata(fea_node: Path) -> dict[str, Any]:
    """Read the sidecar written by step_to_chrono_fea.py, if present."""
    path = fea_node.with_suffix(".json")
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            # Older converter runs recorded the value in a human-readable note.
            if "hub_radius_m" not in data:
                match = re.search(
                    r"radial distance <=\s*([-+0-9.eE]+)\s*m",
                    str(data.get("hub_selection", "")),
                )
                if match:
                    data["hub_radius_m"] = float(match.group(1))
            return data
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    return {}


def measure_geometry(path: Path, *, is_step: bool, axis: str, unit_scale: float,
                     metadata: dict[str, Any] | None = None
                     ) -> dict[str, float | int] | None:
    """Measure outer radius and half-length in the exported local frame.

    The FEA converter exports every drum with its long axis on local Z.  A STEP
    file is measured in its original frame and mapped onto that convention; a
    CRM surface mesh is already in the local frame.  Converter metadata is
    preferred for existing meshes because it records the exact exported bounds.
    """
    metadata_hub = float(metadata["hub_radius_m"]) if metadata and metadata.get(
        "hub_radius_m") is not None else None
    if metadata and "bbox_min_m" in metadata and "bbox_max_m" in metadata:
        lo = [float(v) for v in metadata["bbox_min_m"]]
        hi = [float(v) for v in metadata["bbox_max_m"]]
        extents = [hi[i] - lo[i] for i in range(3)]
        n_solids = int(metadata.get("cad_solids_kept", metadata.get("cad_solids", 1)))
    elif is_step:
        try:
            from OCP.Bnd import Bnd_Box
            from OCP.BRepBndLib import BRepBndLib
            from OCP.IFSelect import IFSelect_RetDone
            from OCP.STEPControl import STEPControl_Reader
            from OCP.TopAbs import TopAbs_SOLID
            from OCP.TopExp import TopExp_Explorer
        except ImportError:
            return None
        reader = STEPControl_Reader()
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            return None
        reader.TransferRoots()
        shape = reader.OneShape()
        box = Bnd_Box()
        BRepBndLib.Add_s(shape, box)
        extents = [
            (box.GetXMax() - box.GetXMin()) * unit_scale,
            (box.GetYMax() - box.GetYMin()) * unit_scale,
            (box.GetZMax() - box.GetZMin()) * unit_scale,
        ]
        explorer = TopExp_Explorer(shape, TopAbs_SOLID)
        n_solids = 0
        while explorer.More():
            n_solids += 1
            explorer.Next()
    else:
        try:
            import trimesh
        except ImportError:
            return None
        if not path.is_file():
            return None
        try:
            surface = trimesh.load(str(path), process=False)
        except Exception:
            return None
        extents = [float(v) for v in surface.extents]
        n_solids = 1

    if axis == "X":
        local_extents = [extents[1], extents[2], extents[0]]
    elif axis == "Y":
        local_extents = [extents[0], extents[2], extents[1]]
    else:
        local_extents = extents
    result: dict[str, float | int] = {
        "drum_radius": max(local_extents[0], local_extents[1]) / 2.0,
        "drum_half_length": local_extents[2] / 2.0,
        "cad_solids": n_solids,
    }
    if metadata_hub is not None:
        result["hub_radius"] = metadata_hub
    return result


def geometry_defaults(measured: dict[str, float | int] | None,
                      *, auto: bool = True) -> dict[str, Any]:
    """Turn CAD dimensions into first-run CRM/FEA settings."""
    if not auto or not measured:
        radius = LEGACY_DRUM_RADIUS
        half_length = LEGACY_DRUM_HALF_LENGTH
        scale = 1.0
        n_solids = 1
    else:
        radius = float(measured["drum_radius"])
        half_length = float(measured["drum_half_length"])
        scale = radius / LEGACY_DRUM_RADIUS
        n_solids = int(measured.get("cad_solids", 1))

    radius = max(radius, 1.0e-3)
    half_length = max(half_length, 1.0e-3)
    scale = max(scale, 0.05)
    bed_height = LEGACY_BED_HEIGHT * scale
    descent_speed = LEGACY_DESCENT_SPEED * scale
    # Fit the active domain to the drum instead of cubing the scale factor:
    # a 3x larger drum needs ~3x more cells per axis, not 27x the particle
    # count from scaling every side by the full legacy box.
    active_box = (
        max(LEGACY_ACTIVE_BOX, 2.0 * (half_length + 0.01 * scale) + 0.04 * scale),
        max(LEGACY_ACTIVE_BOX, 2.0 * (radius + 0.005 * scale) + 0.04 * scale),
        max(LEGACY_ACTIVE_BOX, bed_height + 2.0 * radius + 0.02 * scale),
    )
    # Keep the particle count in the validated range; a large drum needs a
    # proportionally coarser SPH spacing, capped at the validated 12 mm case.
    spacing = min(0.012, LEGACY_SPACING * scale)
    inner_radius = min(0.9 * radius, 0.055 * scale)
    # Multi-solid assemblies in this workspace have a much larger structural
    # core than the legacy single-solid screw drum.  A generated mesh records
    # the exact hub radius, which takes precedence over the heuristic.
    hub_radius = 0.2 * radius if n_solids > 1 else 0.0015 * scale
    if measured and measured.get("hub_radius") is not None:
        hub_radius = float(measured["hub_radius"])
    # FEA tet edge target: keep elements reasonably sized for the drum scale
    # while staying near the validated 12 mm baseline for the legacy model.
    mesh_size = min(0.02, max(0.006, 0.012 * scale))

    def clean(value: float) -> float:
        return float(f"{value:.6g}")

    return {
        "drum_radius": clean(radius),
        "drum_half_length": clean(half_length),
        "surface_target_faces": 80000 if n_solids > 1 else 8000,
        "min_solid_volume": 1.0e-5 if n_solids > 1 else 0.0,
        "mesh_size": clean(mesh_size),
        "bed_height": clean(bed_height),
        "descent_speed": clean(descent_speed),
        "active_box": tuple(clean(v) for v in active_box),
        "spacing": clean(spacing),
        "inner_radius": clean(inner_radius),
        "hub_radius": clean(hub_radius),
        "initial_height": clean(bed_height + radius),
        "lowering_duration": clean(bed_height / descent_speed),
        "scale": clean(scale),
    }


def _ensure_chrono_python() -> None:
    """Re-exec under the project chrono-env Python when run from another interpreter.

    The wizard relies on CAD/mesh libraries (OCP, trimesh, etc.) that live in
    chrono-env.  Running ``./study_wizard.py`` directly picks system Python via
    the shebang and silently disables STEP measurement and other features.
    """
    env_python = (ROOT / "chrono-env" / "bin" / "python").resolve()
    if not env_python.is_file():
        return
    try:
        current = Path(sys.executable).resolve()
    except OSError:
        current = Path(sys.executable)
    if current == env_python:
        return
    if os.environ.get("PYCHRONO_WIZARD_REEXEC") == "1":
        return
    env = os.environ.copy()
    env["PYCHRONO_WIZARD_REEXEC"] = "1"
    os.execv(str(env_python), [str(env_python), str(Path(__file__).resolve()), *sys.argv[1:]])


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--study-name", default=None, help="study name (default: prompted or timestamped)")
    p.add_argument("--output-dir", type=Path, default=None, help="root output directory")
    p.add_argument("--geometry", choices=("supplied", "step", "files"), default=None,
                   help="geometry source: supplied defaults, new STEP CAD, or existing mesh files")
    p.add_argument("--step-file", type=Path, default=None,
                   help=f"new CAD STEP file for --geometry step (default: {DEFAULT_STEP_FILE})")
    p.add_argument("--crm-obj", type=Path, default=None, help="full-detail surface OBJ for CRM")
    p.add_argument("--fea-node", type=Path, default=None, help="TetGen .node file for FEA")
    p.add_argument("--fea-ele", type=Path, default=None, help="TetGen .ele file for FEA")

    p.add_argument("--duration", type=float, default=None, help="simulated CRM duration in s (default: 20)")
    p.add_argument("--fea-duration", type=float, default=None,
                   help="FEA duration in s (default: same as CRM)")
    p.add_argument("--spacing", type=float, default=None, help="SPH spacing in m (default: 0.004)")
    p.add_argument("--crm-dt", default=None,
                   help="CRM timestep in s, or 'auto' for spacing-dependent default")
    p.add_argument("--d0-multiplier", type=float, default=None, help="SPH kernel length multiplier")
    p.add_argument("--fps", type=float, default=None, help="CRM/FEA output cadence in Hz (default: 20)")
    p.add_argument("--bed-height", type=float, default=None, help="initial soil bed height in m")
    p.add_argument("--active-box", default=None, help="SPH domain size X,Y,Z in metres")
    p.add_argument("--settle-time", type=float, default=None, help="settle time before spin/descent")
    p.add_argument("--descent-speed", type=float, default=None, help="drum descent speed in m/s")
    p.add_argument("--omega", type=float, default=None, help="drum spin rate in rad/s")
    p.add_argument("--spin-axis", choices=("X", "Y", "Z"), default=None, help="global spin axis")
    p.add_argument("--drum-radius", type=float, default=None, help="outer drum radius in m")
    p.add_argument("--drum-half-length", type=float, default=None, help="half drum axial length in m")
    p.add_argument("--no-auto-geometry", action="store_true",
                   help="keep legacy fixed bed/spacing defaults instead of scaling from CAD size")
    p.add_argument("--inner-radius", type=float, default=None, help="nominal drum cavity radius in m")
    p.add_argument("--capacity-volume", type=float, default=None, help="CAD-derived cavity volume override")

    p.add_argument("--density", type=float, default=None, help="soil bulk density in kg/m^3")
    p.add_argument("--youngs-modulus", type=float, default=None, help="soil Young's modulus in Pa")
    p.add_argument("--poisson-ratio", type=float, default=None, help="soil Poisson ratio")
    p.add_argument("--friction", type=float, default=None, help="soil static friction mu_s")
    p.add_argument("--cohesion", type=float, default=None, help="soil cohesion in Pa")
    p.add_argument("--grain-diameter", type=float, default=None, help="physical grain diameter in m")

    p.add_argument("--cad-axis", choices=("auto", "X", "Y", "Z"), default=None,
                   help="drum axis in the original STEP space; new models default to Y "
                        "(auto uses the smallest bbox dimension)")
    p.add_argument("--unit-scale", type=float, default=None,
                   help="STEP coordinate scale to metres (default: 0.001 for mm CAD)")
    p.add_argument("--mesh-size", type=float, default=None, help="FEA target tet size in m")
    p.add_argument("--surface-target-faces", type=int, default=None,
                   help="STEP surface decimation target faces")
    p.add_argument("--surface-repair-order", choices=("before", "after"), default=None,
                   help="STEP MeshFix order: before decimation (legacy) or after decimation "
                        "(recommended for multi-solid assemblies; default: after)")
    p.add_argument("--min-solid-volume", type=float, default=None,
                   help="drop STEP assembly solids smaller than this volume in m^3 before meshing")
    p.add_argument("--hub-radius", type=float, default=None, help="rigid hub radius in m")

    p.add_argument("--fea-dt", type=float, default=None, help="FEA structural timestep in s")
    p.add_argument("--pla-youngs-modulus", type=float, default=None, help="PLA Young's modulus in Pa")
    p.add_argument("--pla-poisson-ratio", type=float, default=None, help="PLA Poisson ratio")
    p.add_argument("--pla-density", type=float, default=None, help="PLA density in kg/m^3")
    p.add_argument("--rayleigh-beta", type=float, default=None, help="FEA Rayleigh damping beta in s")
    p.add_argument("--solver", choices=("pardiso-mkl", "sparse-lu", "sparse-qr", "gmres"),
                   default=None, help="one-way FEA linear solver")
    p.add_argument("--num-threads", type=int, default=None, help="FEA solver threads")
    p.add_argument("--save-particles", action="store_true", help="write CRM particle CSV frames")
    p.add_argument("--no-particles", action="store_true", help="do not write CRM particle CSV frames")

    p.add_argument("--dry-run", action="store_true", help="write config and commands, but do not simulate")
    p.add_argument("--yes", action="store_true", help="accept defaults and skip confirmation")
    p.add_argument("--open-results", action="store_true", help="open the results folder after a successful run")
    p.add_argument("--no-open", action="store_true", help="never offer to open the results folder")
    return p


def _show_detail(detail: str | None) -> None:
    if detail and INTERACTIVE and not ACCEPT_DEFAULTS:
        print(f"  {detail}")


def prompt_text(label: str, default: str | None = None, *, allow_empty: bool = False,
                detail: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    if not INTERACTIVE or ACCEPT_DEFAULTS:
        if default is None:
            raise SystemExit(f"missing required value: {label}")
        return str(default)
    _show_detail(detail)
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if not value and default is not None:
            return str(default)
        if value or allow_empty:
            return value
        print("Please enter a value.")


def prompt_bool(label: str, default: bool = True, detail: str | None = None) -> bool:
    hint = "Y/n" if default else "y/N"
    if not INTERACTIVE or ACCEPT_DEFAULTS:
        return default
    _show_detail(detail)
    value = input(f"{label} [{hint}]: ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "true", "1"}


def prompt_choice(label: str, choices: tuple[str, ...], default: str,
                  detail: str | None = None) -> str:
    if not INTERACTIVE or ACCEPT_DEFAULTS:
        return default
    _show_detail(detail)
    while True:
        value = input(f"{label} [{default}]: ").strip() or default
        for choice in choices:
            if value.lower() == str(choice).lower():
                return choice
        print(f"Choose one of: {', '.join(choices)}")


def prompt_float(label: str, default: float, *, minimum: float | None = None,
                 maximum: float | None = None, detail: str | None = None) -> float:
    if not INTERACTIVE or ACCEPT_DEFAULTS:
        value = float(default)
    else:
        _show_detail(detail)
        raw = input(f"{label} [{default:g}]: ").strip()
        try:
            value = float(raw) if raw else float(default)
        except ValueError:
            print("Please enter a number.")
            return prompt_float(label, default, minimum=minimum, maximum=maximum, detail=detail)
    if minimum is not None and value < minimum:
        raise SystemExit(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise SystemExit(f"{label} must be <= {maximum}")
    return value


def prompt_optional_float(label: str, default: float | None = None, *,
                          minimum: float | None = None,
                          detail: str | None = None) -> float | None:
    suffix = f" [{default:g}]" if default is not None else ""
    if not INTERACTIVE or ACCEPT_DEFAULTS:
        value = default
    else:
        _show_detail(detail)
        raw = input(f"{label}{suffix}: ").strip()
        if not raw:
            value = default
        else:
            try:
                value = float(raw)
            except ValueError:
                print("Please enter a number or press Enter.")
                return prompt_optional_float(label, default, minimum=minimum, detail=detail)
    if value is not None and minimum is not None and value < minimum:
        raise SystemExit(f"{label} must be >= {minimum}")
    return value


def prompt_int(label: str, default: int, *, minimum: int = 1,
               detail: str | None = None) -> int:
    if not INTERACTIVE or ACCEPT_DEFAULTS:
        value = int(default)
    else:
        _show_detail(detail)
        raw = input(f"{label} [{default}]: ").strip()
        try:
            value = int(raw) if raw else int(default)
        except ValueError:
            print("Please enter an integer.")
            return prompt_int(label, default, minimum=minimum, detail=detail)
    if value < minimum:
        raise SystemExit(f"{label} must be >= {minimum}")
    return value


def prompt_path(label: str, default: Path | None = None, *, must_exist: bool = True,
                detail: str | None = None) -> Path:
    while True:
        raw = prompt_text(label, str(default) if default else None, detail=detail)
        path = Path(raw).expanduser().resolve()
        if not must_exist or path.exists():
            return path
        print(f"File not found: {path}")
        if not INTERACTIVE:
            raise SystemExit(f"missing input file: {path}")


def prompt_box(label: str, default: str, detail: str | None = None) -> tuple[float, float, float]:
    raw = prompt_text(label, default, detail=detail)
    try:
        parts = tuple(float(x) for x in raw.replace(";", ",").split(","))
    except ValueError as exc:
        raise SystemExit("active box must be three comma-separated numbers") from exc
    if len(parts) != 3 or min(parts) <= 0.0:
        raise SystemExit("active box must be three positive comma-separated numbers")
    return parts  # type: ignore[return-value]


def collect_settings(args: argparse.Namespace) -> dict[str, Any]:
    no_flags = not any(value not in (None, False) for value in vars(args).values())
    print("Lunar excavator CRM -> FEA study setup")
    if no_flags and INTERACTIVE:
        print("No flags given: each setting will be prompted individually.")
    print("Press Enter to accept values in brackets.\n")

    default_name = datetime.now().strftime("study_%Y%m%d_%H%M%S")
    name = args.study_name or prompt_text(
        "Study name", default_name,
        detail="Short label for this run; used in the default output folder name.",
    )
    if args.output_dir:
        out_dir = args.output_dir.expanduser().resolve()
    else:
        default_out = ROOT / f"Output_{name}"
        out_dir = Path(prompt_text(
            "Output directory", str(default_out),
            detail="Folder for meshes, logs, configs and results (created if needed).",
        )).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if (out_dir / "study_config.json").exists() and not args.dry_run and not args.yes:
        if not prompt_bool(
            "Output directory already has a study config; overwrite/continue?", False,
            detail="Continuing may overwrite previous study files in this directory.",
        ):
            raise SystemExit("aborted; choose a new output directory")

    geometry = args.geometry or prompt_choice(
        "Geometry source", ("supplied", "step", "files"), "supplied",
        detail="supplied = built-in meshes; step = convert a STEP CAD file; "
               "files = provide existing CRM .obj and FEA .node/.ele meshes.",
    )

    crm_obj: Path
    fea_node: Path
    fea_ele: Path
    step_file: Path | None = None
    mesh_steps: list[dict[str, Any]] = []
    cad_axis = "Y"
    unit_scale = args.unit_scale if args.unit_scale is not None else 0.001
    mesh_size = args.mesh_size if args.mesh_size is not None else 0.012
    surface_faces = args.surface_target_faces if args.surface_target_faces is not None else 8000
    surface_repair_order = args.surface_repair_order or "after"
    hub_radius = args.hub_radius if args.hub_radius is not None else 0.0015
    min_solid_volume = args.min_solid_volume if args.min_solid_volume is not None else 0.0

    if geometry == "supplied":
        crm_obj = DEFAULT_CRM_OBJ
        fea_node = DEFAULT_FEA_NODE
        fea_ele = DEFAULT_FEA_ELE
    elif geometry == "step":
        step_file = args.step_file or prompt_path(
            "CAD STEP file", DEFAULT_STEP_FILE if DEFAULT_STEP_FILE.is_file() else None,
            detail="Path to the .step/.stp excavator drum model to convert for CRM and FEA.",
        )
        requested_axis = args.cad_axis or prompt_choice(
            "CAD drum axis in original STEP space",
            ("auto", "X", "Y", "Z"), "Y",
            detail="Drum rotation axis in the STEP file (new models are usually Y). "
                   "Exported mesh/CRM surface are rotated so this axis becomes local +Z.",
        )
        if requested_axis == "auto":
            print("Detecting CAD drum axis from STEP...")
            cad_axis = detect_cad_axis(step_file)
            print(f"Detected CAD drum axis: {cad_axis}")
        else:
            cad_axis = requested_axis.upper()
        if args.unit_scale is None:
            unit_scale = prompt_float(
                "STEP coordinate scale to metres", unit_scale, minimum=1e-9,
                detail="Multiply STEP coordinates by this factor (0.001 for mm-valued CAD).",
            )
    else:
        crm_obj = args.crm_obj or prompt_path(
            "CRM surface OBJ", DEFAULT_CRM_OBJ,
            detail="Triangulated drum surface for rigid CRM contact (metres, local +Z axis).",
        )
        fea_node = args.fea_node or prompt_path(
            "FEA .node file", DEFAULT_FEA_NODE,
            detail="TetGen node file for the solid drum mesh used in one-way FEA.",
        )
        default_ele = args.fea_ele or Path(f"{fea_node.with_suffix('')}.ele")
        fea_ele = prompt_path(
            "FEA .ele file", default_ele,
            detail="TetGen element file paired with the .node mesh.",
        )

    # Read geometry and offer auto-detected dimensions/mesh defaults.
    measured: dict[str, float | int] | None = None
    auto_geo = not args.no_auto_geometry
    if geometry == "step":
        if auto_geo and INTERACTIVE and not ACCEPT_DEFAULTS:
            auto_geo = prompt_bool(
                "Analyze STEP file to auto-detect drum dimensions and mesh defaults?", True,
                detail="Reads the CAD to size drum radius/length, bed, SPH box, spacing and mesh settings.",
            )
        if auto_geo:
            print("Reading STEP geometry...")
            measured = measure_geometry(step_file, is_step=True, axis=cad_axis, unit_scale=unit_scale)
            if measured is None:
                print("Could not measure STEP geometry; using legacy defaults.")
        else:
            measured = None
    else:
        if auto_geo:
            if geometry == "files":
                measured = measure_geometry(crm_obj, is_step=False, axis="Z", unit_scale=1.0,
                                            metadata=read_mesh_metadata(fea_node))
            else:
                measured = measure_geometry(crm_obj, is_step=False, axis="Z", unit_scale=1.0,
                                            metadata=read_mesh_metadata(fea_node))

    if geometry != "step":
        for label, path in (("CRM surface OBJ", crm_obj), ("FEA .node file", fea_node),
                            ("FEA .ele file", fea_ele)):
            if not path.is_file():
                raise SystemExit(f"missing {label}: {path}")

    geo = geometry_defaults(measured, auto=auto_geo)
    if geometry == "step":
        if args.mesh_size is None:
            mesh_size = float(geo.get("mesh_size", mesh_size))
        if args.surface_target_faces is None:
            surface_faces = int(geo["surface_target_faces"])
        if args.min_solid_volume is None:
            min_solid_volume = float(geo["min_solid_volume"])
        if args.hub_radius is None and auto_geo and measured is not None:
            hub_radius = float(geo["hub_radius"])
    if measured and auto_geo:
        print("\nCAD-derived starting values (press Enter to accept or type a replacement):")
        print(
            f"  drum R={geo['drum_radius']:g} m, half-length={geo['drum_half_length']:g} m, "
            f"initial centre z≈{geo['initial_height']:g} m"
        )
        print(
            f"  bed={geo['bed_height']:g} m, box="
            f"{','.join(f'{v:g}' for v in geo['active_box'])} m, "
            f"descent={geo['descent_speed']:g} m/s, spacing={geo['spacing']:g} m"
        )
        if geometry == "step":
            print(
                f"  mesh: tet size={geo.get('mesh_size', mesh_size):g} m, "
                f"faces={geo['surface_target_faces']}, min solid={geo['min_solid_volume']:g} m^3, "
                f"hub R={geo['hub_radius']:g} m"
            )

    if geometry == "step":
        if args.mesh_size is None:
            mesh_size = prompt_float(
                "FEA target tet size (m)", mesh_size, minimum=1e-4,
                detail="Approximate tetrahedral edge length; smaller gives a finer, slower FEA mesh.",
            )
        if args.surface_target_faces is None:
            surface_faces = prompt_int(
                "STEP surface target faces", surface_faces, minimum=100,
                detail="Target triangle count for the CRM/FEA contact surface (80k for complex assemblies).",
            )
        if args.min_solid_volume is None:
            min_solid_volume = prompt_float(
                "Drop assembly solids smaller than (m^3)", min_solid_volume, minimum=0.0,
                detail="Ignore tiny STEP solids (fasteners/debris) below this volume before meshing.",
            )
        if args.surface_repair_order is None:
            surface_repair_order = prompt_choice(
                "STEP MeshFix order (before | after decimation)",
                ("before", "after"), surface_repair_order,
                detail="'after' preserves volume better for fused multi-solid assemblies.",
            )

        mesh_dir = out_dir / "mesh"
        mesh_dir.mkdir(parents=True, exist_ok=True)
        prefix = mesh_dir / f"{step_file.stem}_fea"
        fea_node = Path(f"{prefix}.node")
        fea_ele = Path(f"{prefix}.ele")
        surface_stl = Path(f"{prefix}_surface.stl")
        crm_obj = Path(f"{prefix}_surface.obj")
        print("Note: the STEP-derived surface OBJ is suitable for a first study. If you have a")
        print("      full-detail manufacturing OBJ, use --geometry files for the CRM contact mesh.")

    drum_radius = args.drum_radius if args.drum_radius is not None else prompt_float(
        "Outer drum radius (m)", float(geo["drum_radius"]), minimum=1e-3,
        detail="Crest/outer radius; sets initial drum height (bed + radius) and bed sizing.",
    )
    drum_half_length = args.drum_half_length if args.drum_half_length is not None else prompt_float(
        "Drum half-length (m)", float(geo["drum_half_length"]), minimum=1e-3,
        detail="Half the drum length along its axis; used for bed box and CRM placement.",
    )
    if args.hub_radius is not None:
        hub_radius = args.hub_radius
    elif geometry == "step" or (auto_geo and measured is not None):
        hub_radius = prompt_float(
            "Rigid hub radius (m)", float(geo["hub_radius"]), minimum=0.0,
            detail="Nodes within this radius of the axis are tied rigidly to the spindle in FEA.",
        )
    else:
        hub_radius = prompt_float(
            "Rigid hub radius (m)", hub_radius, minimum=0.0,
            detail="Nodes within this radius of the axis are tied rigidly to the spindle in FEA.",
        )

    if geometry == "step":
        mesh_steps.append({
            "name": "generate-FEA-mesh",
            "command": [
                str(ROOT / "chrono-env" / "bin" / "python"),
                str(ROOT / "step_to_chrono_fea.py"),
                str(step_file),
                "--out-prefix", str(prefix),
                "--unit-scale", f"{unit_scale:g}",
                "--mesh-size", f"{mesh_size:g}",
                "--surface-target-faces", str(surface_faces),
                "--min-solid-volume", f"{min_solid_volume:g}",
                "--hub-radius", f"{hub_radius:g}",
                "--axis", cad_axis,
                "--tetgen-switches", "pYQ",
                "--time-stages",
            ],
        })
        if surface_repair_order == "after":
            mesh_steps[0]["command"].append("--repair-after-decimation")
        mesh_steps.append({
            "name": "make-CRM-surface-OBJ",
            "command": [str(ROOT / "chrono-env" / "bin" / "python"),
                        str(ROOT / "stl_to_obj.py"), str(surface_stl)],
        })

    default_duration = max(20.0, float(geo["lowering_duration"]))
    duration = args.duration if args.duration is not None else prompt_float(
        "CRM duration (s)", default_duration, minimum=1e-4,
        detail="Total rigid-CRM simulation time. For a full excavation use ~bed_height/descent_speed.",
    )
    fea_duration = args.fea_duration if args.fea_duration is not None else prompt_float(
        "FEA duration (s; Enter = same as CRM)", duration, minimum=1e-4,
        detail="One-way FEA replay length; cannot exceed the CRM load-history duration.",
    )
    if fea_duration > duration + 1e-12:
        raise SystemExit("FEA duration cannot exceed the CRM load history duration")

    spacing = args.spacing if args.spacing is not None else prompt_float(
        "SPH spacing (m)", float(geo["spacing"]), minimum=1e-4,
        detail="Initial particle spacing (smaller = more particles/slower, higher fidelity).",
    )
    d0_multiplier = args.d0_multiplier if args.d0_multiplier is not None else prompt_float(
        "SPH kernel multiplier h/spacing", 1.0, minimum=0.5,
        detail="Smoothing-length factor h = multiplier * spacing (1.0 is the standard choice).",
    )
    auto_dt = suggested_sph_dt(spacing, d0_multiplier)
    if args.crm_dt is None:
        use_auto = prompt_bool(
            f"Use automatic CRM dt ({auto_dt:g} s)?", True,
            detail="Chooses a spacing-dependent stable SPH timestep; say no to enter a custom dt.",
        )
        crm_dt: float | None = auto_dt if use_auto else prompt_float(
            "Custom CRM dt (s)", auto_dt, minimum=1e-6,
            detail="Explicit CRM timestep (must satisfy the spacing stability limit).",
        )
    elif str(args.crm_dt).strip().lower() == "auto":
        crm_dt = auto_dt
    else:
        crm_dt = float(args.crm_dt)
    if crm_dt is not None:
        crm_dt = float(crm_dt)

    fps = args.fps if args.fps is not None else prompt_float(
        "Output cadence (Hz)", 20.0, minimum=1e-3,
        detail="How often summary/particle/load frames are written (higher = larger outputs).",
    )
    bed_height = args.bed_height if args.bed_height is not None else prompt_float(
        "Initial bed height (m)", float(geo["bed_height"]), minimum=1e-3,
        detail="Depth of the soil bed; drum starts at bed_height + radius above the ground.",
    )
    geo_box = ",".join(f"{v:g}" for v in geo["active_box"])
    active_box = prompt_box(
        "Active SPH box X,Y,Z (m)", args.active_box or geo_box,
        detail="Particle domain size (X along drum axis, Y lateral, Z vertical); must contain the bed+drum.",
    )
    settle_time = args.settle_time if args.settle_time is not None else prompt_float(
        "Settle time (s)", 0.001, minimum=0.0,
        detail="Time to let the bed settle before excavation motion begins.",
    )
    descent_speed = args.descent_speed if args.descent_speed is not None else prompt_float(
        "Descent speed (m/s)", float(geo["descent_speed"]), minimum=0.0,
        detail="Vertical lowering speed of the drum into the bed (0 keeps it at constant height).",
    )
    omega = args.omega if args.omega is not None else prompt_float(
        "Spin rate (rad/s)", 3.14, minimum=0.0,
        detail="Drum angular velocity about the spin axis (~3.14 rad/s = 30 rpm).",
    )
    spin_axis = (args.spin_axis or prompt_choice(
        "Spin axis", ("X", "Y", "Z"), "X",
        detail="Global axis the drum spins about during excavation (usually X).",
    )).upper()
    inner_radius = args.inner_radius if args.inner_radius is not None else prompt_float(
        "Nominal cavity radius (m)", float(geo["inner_radius"]), minimum=1e-3,
        detail="Inner/cavity radius used to estimate fill ratio and capacity volume.",
    )
    capacity_volume = args.capacity_volume if args.capacity_volume is not None else prompt_optional_float(
        "CAD cavity volume override (m^3; Enter = derive from radius)", None, minimum=1e-9,
        detail="Known internal cavity volume; leave blank to derive capacity from radii/length.",
    )

    soil_flags = ("density", "youngs_modulus", "poisson_ratio", "friction",
                  "cohesion", "grain_diameter")
    soil = {name: value for name in soil_flags
            if (value := getattr(args, name)) is not None}
    soil_detail = {
        "density": "Regolith bulk density (kg/m^3).",
        "youngs_modulus": "Soil elastic stiffness (Pa) for contact response.",
        "poisson_ratio": "Soil lateral/axial strain ratio.",
        "friction": "Static friction coefficient (mu_s) at particle contacts.",
        "cohesion": "Cohesive shear strength (Pa).",
        "grain_diameter": "Physical grain diameter (m) used by the rheology model.",
    }
    if INTERACTIVE and not any(getattr(args, name) is not None for name in soil_flags):
        if no_flags or prompt_bool(
            "Change soil parameters?", False,
            detail="Defaults are lunar-regolith estimates; answer yes to review each soil property.",
        ):
            print("Soil properties (press Enter to accept each default):")
            soil = {
                name: prompt_float(label, soil.get(name, SOIL_DEFAULTS[name]),
                                   detail=soil_detail.get(name), **limits)
                for name, (label, limits) in SOIL_FIELDS.items()
            }

    fea_dt = args.fea_dt if args.fea_dt is not None else prompt_float(
        "FEA structural dt (s)", 0.002, minimum=1e-5,
        detail="Structural integrator timestep for one-way FEA (independent of CRM dt).",
    )
    solver = args.solver or prompt_choice(
        "FEA solver", ("pardiso-mkl", "sparse-lu", "sparse-qr", "gmres"), "pardiso-mkl",
        detail="Linear solver: pardiso-mkl is default; sparse-lu/qr are fallbacks; gmres is diagnostic.",
    )
    num_threads = args.num_threads if args.num_threads is not None else prompt_int(
        "FEA threads", 8, minimum=1,
        detail="Thread count for the FEA solver (effective with pardiso-mkl).",
    )

    pla_flags = {
        "youngs_modulus": "pla_youngs_modulus",
        "poisson_ratio": "pla_poisson_ratio",
        "density": "pla_density",
        "rayleigh_beta": "rayleigh_beta",
    }
    pla_detail = {
        "youngs_modulus": "Drum material stiffness (Pa), e.g. PLA ~3 GPa.",
        "poisson_ratio": "Drum material lateral/axial strain ratio.",
        "density": "Drum material density (kg/m^3).",
        "rayleigh_beta": "Rayleigh stiffness-proportional damping (s) to stabilize vibration.",
    }
    pla = {name: value for name, flag in pla_flags.items()
           if (value := getattr(args, flag)) is not None}
    if INTERACTIVE and not any(getattr(args, flag) is not None for flag in pla_flags.values()):
        if no_flags or prompt_bool(
            "Change FEA drum material parameters?", False,
            detail="Defaults assume a PLA drum; answer yes to review density, stiffness and damping.",
        ):
            print("FEA drum material (press Enter to accept each default):")
            pla = {
                name: prompt_float(label, pla.get(name, PLA_DEFAULTS[name]),
                                   detail=pla_detail.get(name), **limits)
                for name, (label, limits) in PLA_FIELDS.items()
            }

    if args.save_particles:
        save_particles = True
    elif args.no_particles:
        save_particles = False
    else:
        save_particles = prompt_bool(
            "Save CRM particle CSV frames?", True,
            detail="Write per-frame particle positions from CRM (useful for visualization, uses disk space).",
        )

    dry_run = args.dry_run
    if INTERACTIVE and not args.dry_run and not args.yes:
        dt_text = f"auto ({auto_dt:g} s)" if crm_dt == auto_dt else f"{crm_dt:g} s"
        summary = [
            f"Study:        {name}",
            f"Output:       {out_dir}",
            f"CRM mesh:     {crm_obj}",
            f"FEA mesh:     {fea_node} / {fea_ele}",
            f"Duration:     CRM {duration:g} s, FEA {fea_duration:g} s",
            f"Drum:         R={drum_radius:g} m, half-length={drum_half_length:g} m, "
            f"z0≈{bed_height + drum_radius:g} m",
            f"SPH:          spacing={spacing:g} m, dt={dt_text}",
            f"Bed:          height={bed_height:g} m, box={','.join(f'{v:g}' for v in active_box)} m, "
            f"descent={descent_speed:g} m/s",
            f"FEA:          dt={fea_dt:g} s, solver={solver}, threads={num_threads}",
        ]
        print("\n" + "\n".join(summary) + "\n")
        dry_run = prompt_bool(
            "Dry run (write commands only)?", False,
            detail="If yes, generate study_config.json and commands.sh without running simulations.",
        )
        if not dry_run and not prompt_bool(
            "Run these simulations now?", True,
            detail="Starts mesh conversion (if needed), then rigid CRM and one-way FEA in sequence.",
        ):
            raise SystemExit("aborted before running")

    return {
        "study_name": name,
        "output_dir": out_dir,
        "geometry": geometry,
        "step_file": step_file,
        "crm_obj": crm_obj,
        "fea_node": fea_node,
        "fea_ele": fea_ele,
        "mesh_steps": mesh_steps,
        "geometry_defaults": geo,
        "auto_geometry": not args.no_auto_geometry,
        "cad_axis": cad_axis,
        "unit_scale": unit_scale,
        "mesh_size": mesh_size,
        "surface_faces": surface_faces,
        "surface_repair_order": surface_repair_order,
        "hub_radius": hub_radius,
        "drum_radius": drum_radius,
        "drum_half_length": drum_half_length,
        "min_solid_volume": min_solid_volume,
        "duration": duration,
        "fea_duration": fea_duration,
        "spacing": spacing,
        "d0_multiplier": d0_multiplier,
        "crm_dt": crm_dt,
        "auto_crm_dt": auto_dt,
        "fps": fps,
        "bed_height": bed_height,
        "active_box": active_box,
        "settle_time": settle_time,
        "descent_speed": descent_speed,
        "omega": omega,
        "spin_axis": spin_axis,
        "inner_radius": inner_radius,
        "capacity_volume": capacity_volume,
        "soil": soil,
        "fea_dt": fea_dt,
        "solver": solver,
        "num_threads": num_threads,
        "pla": pla,
        "save_particles": save_particles,
        "open_results": args.open_results,
        "no_open": args.no_open,
        "dry_run": dry_run,
    }


def build_commands(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    out_dir: Path = cfg["output_dir"]
    crm_dir = out_dir / "crm"
    fea_dir = out_dir / "fea"
    loads = crm_dir / "soil_loads.csv"
    crm_dir.mkdir(parents=True, exist_ok=True)
    fea_dir.mkdir(parents=True, exist_ok=True)

    steps: list[dict[str, Any]] = list(cfg["mesh_steps"])

    crm_cmd = [
        str(ROOT / "crm" / "run.sh"),
        "--duration", f"{cfg['duration']:g}",
        "--spacing", f"{cfg['spacing']:g}",
        "--fps", f"{cfg['fps']:g}",
        "--bed-height", f"{cfg['bed_height']:g}",
        "--active-box", ",".join(f"{v:g}" for v in cfg["active_box"]),
        "--settle-time", f"{cfg['settle_time']:g}",
        "--descent-speed", f"{cfg['descent_speed']:g}",
        "--omega", f"{cfg['omega']:g}",
        "--spin-axis", cfg["spin_axis"],
        "--drum-radius", f"{cfg['drum_radius']:g}",
        "--drum-half-length", f"{cfg['drum_half_length']:g}",
        "--inner-radius", f"{cfg['inner_radius']:g}",
        "--d0-multiplier", f"{cfg['d0_multiplier']:g}",
        "--mesh-file", str(cfg["crm_obj"]),
        "--loads-file", str(loads),
        "--output-dir", str(crm_dir),
    ]
    if cfg["crm_dt"] is not None:
        crm_cmd.extend(["--dt", f"{cfg['crm_dt']:g}"])
    if cfg["capacity_volume"] is not None:
        crm_cmd.extend(["--capacity-volume", f"{cfg['capacity_volume']:g}"])
    if cfg["save_particles"]:
        crm_cmd.append("--save-particles")
    soil_flags = {
        "density": "--density",
        "youngs_modulus": "--youngs-modulus",
        "poisson_ratio": "--poisson-ratio",
        "friction": "--friction",
        "cohesion": "--cohesion",
        "grain_diameter": "--grain-diameter",
    }
    for key, value in cfg["soil"].items():
        crm_cmd.extend([soil_flags[key], f"{value:g}"])
    steps.append({"name": "rigid-CRM", "command": crm_cmd})

    fea_cmd = [
        str(ROOT / "crm" / "run_oneway_fea.sh"),
        "--duration", f"{cfg['fea_duration']:g}",
        "--dt", f"{cfg['fea_dt']:g}",
        "--fps", f"{cfg['fps']:g}",
        "--loads", str(loads),
        "--fea-node", str(cfg["fea_node"]),
        "--fea-ele", str(cfg["fea_ele"]),
        "--bed-height", f"{cfg['bed_height']:g}",
        "--settle-time", f"{cfg['settle_time']:g}",
        "--descent-speed", f"{cfg['descent_speed']:g}",
        "--omega", f"{cfg['omega']:g}",
        "--spin-axis", cfg["spin_axis"],
        "--drum-radius", f"{cfg['drum_radius']:g}",
        "--drum-half-length", f"{cfg['drum_half_length']:g}",
        "--inner-radius", f"{cfg['inner_radius']:g}",
        "--hub-radius", f"{cfg['hub_radius']:g}",
        "--solver", cfg["solver"],
        "--num-threads", str(cfg["num_threads"]),
        "--output-dir", str(fea_dir),
    ]
    pla_flags = {
        "youngs_modulus": "--pla-youngs-modulus",
        "poisson_ratio": "--pla-poisson-ratio",
        "density": "--pla-density",
        "rayleigh_beta": "--rayleigh-beta",
    }
    for key, value in cfg["pla"].items():
        fea_cmd.extend([pla_flags[key], f"{value:g}"])
    steps.append({"name": "one-way-FEA", "command": fea_cmd})

    for step in steps:
        step["log"] = str(out_dir / f"{step['name']}.log")
    cfg["commands"] = [{"name": s["name"], "command": s["command"]} for s in steps]
    return steps


def write_reproducibility_files(cfg: dict[str, Any], steps: list[dict[str, Any]]) -> None:
    out_dir: Path = cfg["output_dir"]
    serializable = {
        k: (str(v) if isinstance(v, Path) else v)
        for k, v in cfg.items()
    }
    (out_dir / "study_config.json").write_text(
        json.dumps(serializable, indent=2) + "\n", encoding="utf-8"
    )

    lines = [
        "#!/usr/bin/env bash",
        "# Generated by study_wizard.py. Run from the repository root.",
        "set -euo pipefail",
        f"cd {shlex.quote(str(ROOT))}",
        "",
    ]
    for step in steps:
        lines.append(f"# {step['name']}")
        lines.append(shlex.join(step["command"]) + " \\\n    | tee " + shlex.quote(step["log"]))
        lines.append("")
    script = out_dir / "commands.sh"
    script.write_text("\n".join(lines), encoding="utf-8")
    script.chmod(0o755)


def print_tail(path: str | Path, lines: int = 30) -> None:
    p = Path(path)
    if p.exists():
        text = p.read_text(encoding="utf-8", errors="replace").splitlines()
        print("\n".join(text[-lines:]))


def run_step(step: dict[str, Any]) -> bool:
    name = step["name"]
    command = step["command"]
    print(f"\n[{name}] {shlex.join(command)}", flush=True)
    log_path = Path(step["log"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
        returncode = proc.wait()
    if returncode != 0:
        print(f"[{name}] FAILED with exit code {returncode}", flush=True)
        print_tail(log_path)
        return False
    print(f"[{name}] complete", flush=True)
    return True


def last_csv_row(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return rows[-1] if rows else None


def open_path(path: Path) -> bool:
    """Open a directory with the desktop file manager, if available."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        return True
    except (OSError, FileNotFoundError):
        return False


def print_results(cfg: dict[str, Any], *, offer_open: bool = True) -> None:
    out_dir: Path = cfg["output_dir"]
    crm_path = out_dir / "crm" / "summary.csv"
    fea_path = out_dir / "fea" / "summary.csv"
    loads_path = out_dir / "crm" / "soil_loads.csv"
    crm = last_csv_row(crm_path)
    fea = last_csv_row(fea_path)
    print("\nStudy results")
    if crm:
        print(
            f"  CRM: t={crm.get('time_s')} s  fill={crm.get('fill_ratio_mass')}  "
            f"inside={crm.get('inside_drum')}  excavated={crm.get('excavated')}  "
            f"work={crm.get('work_j')} J"
        )
    if fea:
        stress = float(fea.get("max_von_mises_pa", 0.0)) / 1e6
        print(
            f"  FEA: t={fea.get('time_s')} s  deformation={fea.get('max_deformation_m')} m  "
            f"peak_von_mises={stress:.2f} MPa  work={fea.get('work_j')} J"
        )

    plot_script = ROOT / "view_results.py"
    plot_png = out_dir / "results_overview.png"
    if plot_script.is_file():
        result = subprocess.run(
            [sys.executable, str(plot_script), "--output-dir", str(out_dir)],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        if result.returncode == 0 and plot_png.is_file():
            print(f"  Plot: {plot_png}")
        else:
            print(f"  Plot generation failed: {(result.stderr or result.stdout).strip()}")

    print("\nHow to view the results")
    print(f"  Study folder:  {out_dir}")
    print(f"  CRM metrics:   {crm_path}")
    print(f"  FEA metrics:   {fea_path}")
    if loads_path.is_file():
        print(f"  Soil wrench:   {loads_path}")
    if cfg.get("save_particles"):
        print(f"  CRM particles: {out_dir / 'crm' / 'particles_*.csv'}")
    print(f"  Interactive plot: {sys.executable} {plot_script} --output-dir {out_dir} --show")
    print(f"  Open folder:      xdg-open {shlex.quote(str(out_dir))}")

    if offer_open and cfg.get("open_results"):
        if open_path(out_dir):
            print("Opened the study folder.")
        else:
            print("Could not open the folder automatically; use the xdg-open command above.")
    elif offer_open and INTERACTIVE and not cfg.get("no_open"):
        if prompt_bool("Open the results folder now?", False):
            if open_path(out_dir):
                print("Opened the study folder.")
            else:
                print("Could not open the folder automatically; use the xdg-open command above.")
        if prompt_bool("Open interactive result plots now?", False):
            try:
                subprocess.Popen(
                    [sys.executable, str(plot_script), "--output-dir", str(out_dir), "--show"],
                    cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                print("Opened the result plots.")
            except OSError:
                print("Could not open plots automatically; run the interactive plot command above.")


def main() -> int:
    global ACCEPT_DEFAULTS
    _ensure_chrono_python()
    args = build_parser().parse_args()
    ACCEPT_DEFAULTS = bool(args.yes)
    started = time.perf_counter()
    cfg = collect_settings(args)
    steps = build_commands(cfg)
    write_reproducibility_files(cfg, steps)

    out_dir: Path = cfg["output_dir"]
    print(f"\nWrote {out_dir / 'study_config.json'}")
    print(f"Wrote {out_dir / 'commands.sh'}")

    if cfg["dry_run"]:
        print("\nDry run; no simulation executed.")
        for step in steps:
            print(f"  {step['name']}: {shlex.join(step['command'])}")
        return 0

    for step in steps:
        if not run_step(step):
            print(f"\nStudy stopped at {step['name']}. See {step['log']}", flush=True)
            return 1

    print_results(cfg, offer_open=not cfg["dry_run"])
    print(f"\nTotal wall time: {time.perf_counter() - started:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
