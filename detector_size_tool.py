#!/usr/bin/env python3
"""Live 3D adjuster for the analytical fill-detector cylinder.

The tool tessellates the original STEP model (or loads the converted surface
when no STEP is available) with the same transforms used by the study, shows it
at 50% opacity, and overlays the detector cylinder.  Radius and full length
sliders update the cylinder immediately.  Values are written to ``--result-json``
while dragging so the study GUI can copy them into its fields in real time.

Typical use from the GUI (or directly)::

    ./chrono-env/bin/python detector_size_tool.py \\
        --step geometry/LunarDrumScrew_m.step \\
        --radius 0.061037 --length 0.066 --spacing 0.002 \\
        --source-axis Z --unit-scale 0.001 --show \\
        --result-json /tmp/detector_size.json
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
_VENV_DIR = ROOT / "chrono-env"
_VENV_PYTHON = _VENV_DIR / "bin" / "python"
_REEXEC_FLAG = "PYCHRONO_DETECTOR_SIZE_ENV"


def _maybe_reexec() -> None:
    """Run the viewer under chrono-env, which provides PyVista and OCP."""
    if os.environ.get(_REEXEC_FLAG) == "1":
        return
    if not _VENV_PYTHON.is_file() or not os.access(_VENV_PYTHON, os.X_OK):
        return
    try:
        if Path(sys.prefix).resolve() == _VENV_DIR.resolve():
            return
    except OSError:
        return
    os.environ[_REEXEC_FLAG] = "1"
    os.execv(
        str(_VENV_PYTHON),
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--step", type=Path, default=None,
                   help="original STEP CAD file (shown at 50%% opacity)")
    p.add_argument("--surface", type=Path, default=None,
                   help="converted surface OBJ/STL; used when --step is unavailable")
    p.add_argument("--fea-node", type=Path, default=None,
                   help="converted FEA .node file; metadata read from sibling .json")
    p.add_argument("--radius", type=float, default=0.061037,
                   help="initial detector cylinder radius in metres")
    p.add_argument("--length", type=float, default=0.066,
                   help="initial detector cylinder full length in metres")
    p.add_argument("--spacing", type=float, default=0.002,
                   help="SPH spacing in metres; reported with the slider values")
    p.add_argument("--source-axis", choices=("X", "Y", "Z"), default="Y",
                   help="STEP drum axis rotated onto local Z (default: Y)")
    p.add_argument("--unit-scale", type=float, default=0.001,
                   help="STEP units to metres (default: 0.001 for mm CAD)")
    p.add_argument("--deflection", type=float, default=None,
                   help="OCC tessellation deflection in metres (default: metadata or 0.001)")
    p.add_argument("--center", type=str, default="0,0,0",
                   help="detector centre in the local frame as X,Y,Z (default: 0,0,0)")
    p.add_argument("--max-cad-faces", type=int, default=200000,
                   help="decimate the STEP display mesh to this many faces (0 disables)")
    p.add_argument("--threads", type=int, default=0,
                   help="limit OCC worker threads (0 = all logical cores)")
    p.add_argument("--no-parallel", action="store_true",
                   help="disable OCC parallel meshing/fuse (much slower)")
    p.add_argument("--result-json", type=Path, default=None,
                   help="write live radius/length values here for the GUI")
    p.add_argument("--output", type=Path, default=None,
                   help="save a PNG instead of opening an interactive window")
    p.add_argument("--show", action="store_true",
                   help="force the interactive window even when --output is given")
    return p


def parse_center(text: str) -> tuple[float, float, float]:
    parts = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
    if len(parts) != 3:
        raise SystemExit("--center must be three comma-separated numbers X,Y,Z")
    try:
        center = tuple(float(part) for part in parts)
    except ValueError as exc:
        raise SystemExit(f"--center values must be numeric: {exc}") from exc
    return center  # type: ignore[return-value]


def slider_bounds(radius: float, length: float, spacing: float) -> tuple[
        tuple[float, float], tuple[float, float]]:
    """Return sensible radius/length slider ranges containing the initial values."""
    if radius <= 0.0 or length <= 0.0 or spacing <= 0.0:
        raise SystemExit("radius, length, and spacing must be positive")
    r_min = max(1.0e-5, 0.1 * radius)
    r_max = max(3.0 * radius, radius + 20.0 * spacing, 2.0 * r_min)
    l_min = max(2.0 * spacing, 0.1 * length)
    l_max = max(3.0 * length, length + 40.0 * spacing, 2.0 * l_min)
    return (r_min, r_max), (l_min, l_max)


def effective_dimensions(radius: float, length: float, spacing: float
                         ) -> tuple[float, float]:
    """Return the eroded cylinder actually used by the fill classifier."""
    if radius <= 0.0 or length <= 0.0 or spacing <= 0.0:
        raise SystemExit("radius, length, and spacing must be positive")
    rp = 0.5 * spacing
    return max(0.0, radius - rp), max(0.0, length - 2.0 * rp)


def write_result(path: Path | None, radius: float, length: float,
                 spacing: float, dirty: bool) -> None:
    """Atomically publish the current slider values for the GUI poller."""
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "radius": float(radius),
        "length": float(length),
        "spacing": float(spacing),
        "dirty": bool(dirty),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(path)


def load_display_mesh(args: argparse.Namespace) -> tuple[Any, str]:
    """Tessellate the STEP (or load the surface) in the detector's local frame."""
    import view_geometry as vg

    meta: dict[str, Any] = {}
    if args.fea_node is not None and args.fea_node.is_file():
        meta = vg.load_sidecar(args.fea_node)

    surface_path = args.surface
    if surface_path is None and args.fea_node is not None and args.fea_node.is_file():
        # Mirrors view_geometry.py's fallback for existing converted studies.
        candidates: list[Path] = []
        recorded = meta.get("outputs", {}).get("surface_stl")
        if recorded:
            candidate = Path(str(recorded))
            if candidate.is_absolute():
                candidates.append(candidate)
            else:
                candidates.extend([args.fea_node.parent / candidate, ROOT / candidate])
        candidates.append(args.fea_node.with_name(args.fea_node.stem + "_surface.stl"))
        surface_path = next((p for p in candidates if p.is_file()), None)

    label = "Converted surface"
    if args.step is not None and args.step.is_file():
        deflection = args.deflection
        if deflection is None:
            deflection = float(meta.get("surface_deflection_m", 0.001))
        with tempfile.TemporaryDirectory(prefix="detector_size_") as tmp:
            mesh, _volume, _kept, _info = vg.tessellate_step(
                args.step,
                meta,
                unit_scale=args.unit_scale,
                deflection=deflection,
                angular=float(meta.get("angular_deflection_rad", 0.15)),
                source_axis=args.source_axis,
                tmp_dir=Path(tmp),
                parallel=not args.no_parallel,
                threads=args.threads,
            )
        label = "STEP model (50% opacity)"
    elif surface_path is not None and surface_path.is_file():
        mesh = vg.load_surface(surface_path)
        mesh, _scale = vg.normalize_surface_units(mesh, meta, args.unit_scale)
    else:
        raise SystemExit(
            "No STEP or converted surface found; pass --step, --surface, or --fea-node"
        )

    if (args.max_cad_faces > 0 and label.startswith("STEP")
            and mesh.n_cells > args.max_cad_faces):
        try:
            mesh = mesh.decimate(
                target_reduction=1.0 - args.max_cad_faces / mesh.n_cells
            )
        except Exception:
            pass
    return mesh, label


def main() -> int:
    _maybe_reexec()
    args = build_parser().parse_args()
    radius = float(args.radius)
    length = float(args.length)
    spacing = float(args.spacing)
    if radius <= 0.0 or length <= 0.0 or spacing <= 0.0:
        raise SystemExit("radius, length, and spacing must be positive")
    (r_min, r_max), (l_min, l_max) = slider_bounds(radius, length, spacing)
    center = parse_center(args.center)

    import pyvista as pv

    state = {"radius": radius, "length": length, "dirty": False}
    result_path = args.result_json
    write_result(result_path, radius, length, spacing, False)

    def publish() -> None:
        write_result(result_path, state["radius"], state["length"],
                     spacing, state["dirty"])

    atexit.register(publish)

    mesh, label = load_display_mesh(args)
    print(f"Detector sizer: {label}; {mesh.n_cells} display faces")
    print("Drag the sliders to resize the cylinder; close the window to finish.")

    show = args.show or (args.output is None and bool(os.environ.get("DISPLAY")))
    plotter = pv.Plotter(off_screen=not show, window_size=(1100, 760))
    plotter.add_text("Live fill-detector size adjuster", font_size=12,
                     color="black")
    plotter.add_text(
        f"{label}; detector-local Z. Orange = nominal cylinder; "
        "green wire = eroded classifier",
        position="lower_left", font_size=9, color="black")
    plotter.add_mesh(mesh, color="#1f77b4", opacity=0.5, show_edges=False)
    plotter.add_axes(color="black")
    plotter.show_grid(color="gray")

    info = plotter.add_text("", position="upper_right", font_size=10,
                            color="#b22222")

    def cylinder() -> Any:
        return pv.Cylinder(center=center, direction=(0.0, 0.0, 1.0),
                           radius=state["radius"], height=state["length"],
                           resolution=96, capping=True)

    detector = plotter.add_mesh(cylinder(), color="#ff7f0e", opacity=0.45,
                                show_edges=True, line_width=1.2,
                                name="detector")

    def classifier_cylinder() -> Any:
        # Match compute_metrics(): the live classifier is eroded by one SPH
        # radius radially and at both ends. Show it inside the nominal cylinder.
        eff_radius, eff_length = effective_dimensions(
            state["radius"], state["length"], spacing)
        return pv.Cylinder(center=center, direction=(0.0, 0.0, 1.0),
                           radius=max(eff_radius, 1.0e-6),
                           height=max(eff_length, 1.0e-6),
                           resolution=64, capping=True)

    classifier = plotter.add_mesh(classifier_cylinder(), style="wireframe",
                                  color="#2ca02c", line_width=1.4,
                                  name="classifier")

    def refresh_info() -> None:
        eff_radius, eff_length = effective_dimensions(
            state["radius"], state["length"], spacing)
        info.set_text(
            "upper_right",
            f"Detector radius: {state['radius']:.5f} m\n"
            f"Detector length: {state['length']:.5f} m\n"
            f"Effective R/L: {eff_radius:.5f} / {eff_length:.5f} m\n"
            f"SPH spacing: {spacing:g} m",
        )

    def refresh_cylinders() -> None:
        detector.mapper.dataset = cylinder()
        classifier.mapper.dataset = classifier_cylinder()
        refresh_info()

    refresh_info()

    def set_radius(value: float) -> None:
        state["radius"] = float(value)
        refresh_cylinders()
        state["dirty"] = True
        publish()

    def set_length(value: float) -> None:
        state["length"] = float(value)
        refresh_cylinders()
        state["dirty"] = True
        publish()

    plotter.add_slider_widget(set_radius, (r_min, r_max), value=radius,
                              title="Detector radius (m)", fmt="%.5f",
                              color="#ff7f0e", pointa=(0.06, 0.86),
                              pointb=(0.47, 0.86), interaction_event="always")
    plotter.add_slider_widget(set_length, (l_min, l_max), value=length,
                              title="Detector length (m)", fmt="%.5f",
                              color="#2ca02c", pointa=(0.53, 0.86),
                              pointb=(0.94, 0.86), interaction_event="always")

    plotter.view_isometric()
    if args.output is not None and not args.show:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        plotter.show(screenshot=str(args.output), auto_close=True)
        print(f"Wrote {args.output}")
    else:
        try:
            plotter.show(auto_close=True)
        except Exception as exc:
            if args.output is None:
                raise
            args.output.parent.mkdir(parents=True, exist_ok=True)
            plotter.show(screenshot=str(args.output), auto_close=True)
            print(f"Interactive view unavailable ({exc}); wrote {args.output}")
    publish()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
