#!/usr/bin/env python3
"""Live 3D adjuster for the analytical fill-detector cylinder.

The tool tessellates the original STEP model (or loads the converted surface
when no STEP is available) with the same transforms used by the study, shows it
with adjustable opacity, and overlays the detector cylinder.  Radius, length,
and excavator/detector opacity sliders update the view immediately, and a
curved arrow shows the configured drum spin direction in the detector's local
frame.  Values are written to ``--result-json`` while dragging so the study GUI
can copy them into its fields in real time.

Typical use from the GUI (or directly)::

    ./chrono-env/bin/python detector_size_tool.py \\
        --step geometry/MyDrum.step \\
        --radius 0.061037 --length 0.066 --spacing 0.002 \\
        --source-axis Z --unit-scale 0.001 --show \\
        --result-json /tmp/detector_size.json
"""

from __future__ import annotations

import argparse
import atexit
import json
import math
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lunar_excavator_config import DEFAULT as MOTION_DEFAULT  # noqa: E402
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
                   help="original STEP CAD file (excavator opacity is adjustable in the viewer)")
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
    p.add_argument("--spin-axis", choices=("X", "Y", "Z"), default="X",
                    help="study-frame drum spin axis shown by the rotation arrow (default: X)")
    p.add_argument("--reverse-spin", action="store_true",
                    help="show the reverse drum spin direction")
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


def rotation_axis(spin_axis: str, reverse: bool = False) -> tuple[float, float, float]:
    """Spin axis in the detector-local frame, including the reverse flag."""
    try:
        axis = list(MOTION_DEFAULT.global_axis_local(spin_axis))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"--spin-axis must be X, Y, or Z: {exc}") from exc
    if reverse:
        axis = [-float(v) for v in axis]
    norm = math.sqrt(sum(float(v) ** 2 for v in axis))
    if not math.isfinite(norm) or norm <= 0.0:
        raise SystemExit(f"invalid spin axis vector for {spin_axis!r}")
    return tuple(float(v) / norm for v in axis)  # type: ignore[return-value]


def rotation_arrow(radius: float, length: float,
                   center: tuple[float, float, float],
                   axis: tuple[float, float, float]) -> tuple[Any, Any]:
    """Build a curved arrow showing positive rotation about *axis*.

    The arc lies just beyond the detector's axial end so it does not hide the
    cylinder.  A right-hand rotation about the supplied (already reversed)
    local axis is drawn, which makes the arrow reverse when the study does.
    """
    import numpy as np
    import pyvista as pv

    axis_vec = np.asarray(axis, dtype=np.float64)
    axis_vec /= np.linalg.norm(axis_vec)
    helper = (np.array((0.0, 0.0, 1.0)) if abs(axis_vec[2]) < 0.9
              else np.array((1.0, 0.0, 0.0)))
    e1 = np.cross(axis_vec, helper)
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(axis_vec, e1)

    arc_radius = max(0.75 * radius, 1.0e-5)
    axial_offset = 0.5 * length + 0.35 * arc_radius
    base = np.asarray(center, dtype=np.float64) + axis_vec * axial_offset
    angles = np.linspace(-1.0, 1.0, 36)
    points = base + arc_radius * (
        np.cos(angles)[:, None] * e1 + np.sin(angles)[:, None] * e2
    )

    line = pv.PolyData(points, lines=np.array([len(points), *range(len(points))]))
    shaft = line.tube(radius=max(0.022 * arc_radius, 2.0e-6), n_sides=8)

    end = float(angles[-1])
    tangent = -math.sin(end) * e1 + math.cos(end) * e2
    head_height = max(0.28 * arc_radius, 2.0e-5)
    head = pv.Cone(
        center=points[-1] - 0.5 * head_height * tangent,
        direction=tangent,
        height=head_height,
        radius=max(0.11 * arc_radius, 1.0e-5),
        resolution=16,
    )
    return shaft, head


def set_actor_opacity(actor: Any, value: float) -> float:
    """Apply a 0..1 opacity to a PyVista actor and return the stored value."""
    opacity = min(1.0, max(0.0, float(value)))
    actor.GetProperty().SetOpacity(opacity)
    return opacity


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
        label = "STEP model"
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
    axis = rotation_axis(args.spin_axis, args.reverse_spin)

    import pyvista as pv

    state = {
        "radius": radius,
        "length": length,
        "excavator_opacity": 0.5,
        "detector_opacity": 0.45,
        "dirty": False,
    }
    result_path = args.result_json
    write_result(result_path, radius, length, spacing, False)

    def publish() -> None:
        write_result(result_path, state["radius"], state["length"],
                     spacing, state["dirty"])

    atexit.register(publish)

    mesh, label = load_display_mesh(args)
    print(f"Detector sizer: {label}; {mesh.n_cells} display faces")
    print("Drag the sliders to resize the cylinder or adjust opacity; close the window to finish.")

    show = args.show or (args.output is None and bool(os.environ.get("DISPLAY")))
    plotter = pv.Plotter(off_screen=not show, window_size=(1100, 760))
    plotter.add_text("Live fill-detector size adjuster", font_size=12,
                     color="black")
    plotter.add_text(
        f"{label}; detector-local Z. Orange = nominal cylinder; "
        "green wire = eroded classifier; magenta arrow = spin direction",
        position="lower_left", font_size=9, color="black")
    excavator = plotter.add_mesh(mesh, color="#1f77b4",
                                 opacity=state["excavator_opacity"],
                                 show_edges=False)
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
                                  opacity=state["detector_opacity"],
                                  name="classifier")

    arrow_shaft_data, arrow_head_data = rotation_arrow(
        state["radius"], state["length"], center, axis)
    arrow_shaft = plotter.add_mesh(arrow_shaft_data, color="#cc33cc",
                                   name="rotation_direction")
    arrow_head = plotter.add_mesh(arrow_head_data, color="#cc33cc",
                                  name="rotation_direction_head")
    dominant = max(range(3), key=lambda i: abs(axis[i]))
    spin_label = ("-" if axis[dominant] < 0.0 else "+") + "XYZ"[dominant]

    def refresh_info() -> None:
        eff_radius, eff_length = effective_dimensions(
            state["radius"], state["length"], spacing)
        info.set_text(
            "upper_right",
            f"Detector radius: {state['radius']:.5f} m\n"
            f"Detector length: {state['length']:.5f} m\n"
            f"Effective R/L: {eff_radius:.5f} / {eff_length:.5f} m\n"
            f"SPH spacing: {spacing:g} m\n"
            f"Spin axis (local): {spin_label}",
        )

    def refresh_cylinders() -> None:
        detector.mapper.dataset = cylinder()
        classifier.mapper.dataset = classifier_cylinder()
        shaft, head = rotation_arrow(
            state["radius"], state["length"], center, axis)
        arrow_shaft.mapper.dataset = shaft
        arrow_head.mapper.dataset = head
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

    def set_excavator_opacity(value: float) -> None:
        state["excavator_opacity"] = set_actor_opacity(excavator, value)

    def set_detector_opacity(value: float) -> None:
        opacity = set_actor_opacity(detector, value)
        set_actor_opacity(classifier, opacity)
        state["detector_opacity"] = opacity

    plotter.add_slider_widget(set_radius, (r_min, r_max), value=radius,
                              title="Detector radius (m)", fmt="%.5f",
                              color="#ff7f0e", pointa=(0.06, 0.86),
                              pointb=(0.47, 0.86), interaction_event="always")
    plotter.add_slider_widget(set_length, (l_min, l_max), value=length,
                              title="Detector length (m)", fmt="%.5f",
                              color="#2ca02c", pointa=(0.53, 0.86),
                              pointb=(0.94, 0.86), interaction_event="always")
    plotter.add_slider_widget(
        set_excavator_opacity, (0.05, 1.0),
        value=state["excavator_opacity"], title="Excavator opacity",
        fmt="%.2f", color="#1f77b4", pointa=(0.06, 0.77),
        pointb=(0.47, 0.77), interaction_event="always")
    plotter.add_slider_widget(
        set_detector_opacity, (0.05, 1.0),
        value=state["detector_opacity"], title="Detector opacity",
        fmt="%.2f", color="#ff7f0e", pointa=(0.53, 0.77),
        pointb=(0.94, 0.77), interaction_event="always")

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
