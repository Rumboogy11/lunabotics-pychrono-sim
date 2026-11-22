#!/usr/bin/env python3
"""Render the original STEP CAD and its converted FEA surface side by side.

The STEP converter (:mod:`step_to_chrono_fea`) rotates the drum axis onto the
local +Z axis and scales STEP coordinates into metres.  This tool applies the
same transforms to a fresh OCC tessellation of the CAD, so the two panels and
the overlay share one frame and any difference is a conversion artefact, not a
coordinate-system mismatch.

Typical use::

    ./chrono-env/bin/python view_geometry.py --study-dir Output_my_study
    ./chrono-env/bin/python view_geometry.py \\
        --step 'geometry/BucketDrumExcavator.step' \\
        --fea-node 'Output_my_study/mesh/BucketDrumExcavator_fea.node'

By default it opens an interactive window when a display is available (drag to
rotate; the three views are linked).  Pass ``--output PATH`` to save a PNG
instead; the default PNG goes to ``geometry_checks/<mesh>_geometry_check.png``
under the project directory.  ``--show`` forces the interactive window even when
``--output`` is given.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The shebang uses ``/usr/bin/env python3``, which can resolve to a system
# Python without pyvista/trimesh.  Relaunch once under the project's conda env
# so ``./view_geometry.py`` works directly, from any working directory.
_VENV_DIR = ROOT / "chrono-env"
_VENV_PYTHON = _VENV_DIR / "bin" / "python"
_REEXEC_FLAG = "PYCHRONO_VIEW_GEOMETRY_ENV"
if (
    os.environ.get(_REEXEC_FLAG) != "1"
    and _VENV_PYTHON.is_file()
    and os.access(_VENV_PYTHON, os.X_OK)
    and Path(sys.prefix).resolve() != _VENV_DIR.resolve()
):
    os.environ[_REEXEC_FLAG] = "1"
    os.execv(
        str(_VENV_PYTHON),
        [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
    )

import numpy as np
import pyvista as pv
import trimesh

import step_to_chrono_fea as conv  # noqa: E402  (reuse the converter's OCC route)


def load_sidecar(fea_node: Path) -> dict[str, Any]:
    path = fea_node.with_suffix(".json")
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {}


def to_polydata(mesh: trimesh.Trimesh) -> pv.PolyData:
    faces = np.asarray(mesh.faces, dtype=np.int64)
    vertices = np.asarray(mesh.vertices, dtype=float)
    pv_faces = np.column_stack(
        [np.full(len(faces), 3, dtype=np.int64), faces]
    ).ravel()
    return pv.PolyData(vertices, pv_faces)


def load_surface(path: Path) -> pv.PolyData:
    mesh = trimesh.load(str(path), process=False, force="mesh")
    return to_polydata(mesh)


def normalize_surface_units(surface: pv.PolyData, meta: dict[str, Any],
                            unit_scale: float) -> tuple[pv.PolyData, float]:
    """Bring the converted surface into metres using the sidecar bbox.

    Older converter runs wrote the surface STL in raw CAD units (mm) while the
    ``.node`` mesh was already in metres.  Detect that by comparing the surface
    extents with the recorded metre-scale bounding box and scale if needed.
    """
    if "bbox_min_m" not in meta or "bbox_max_m" not in meta or unit_scale >= 1.0:
        return surface, 1.0
    lo = np.asarray(meta["bbox_min_m"], dtype=float)
    hi = np.asarray(meta["bbox_max_m"], dtype=float)
    expected = float(np.max(hi - lo))
    actual = float(np.max(np.asarray(surface.bounds[1]) - np.asarray(surface.bounds[0])))
    if expected <= 0.0 or actual <= 0.0:
        return surface, 1.0
    if actual / expected > 2.0:  # surface is in CAD units, not metres
        points = np.asarray(surface.points, dtype=float) * unit_scale
        surface = surface.copy()
        surface.points = points
        return surface, unit_scale
    return surface, 1.0


def tessellate_step(step_path: Path, meta: dict[str, Any], *, unit_scale: float,
                    deflection: float, angular: float, source_axis: str | None,
                    tmp_dir: Path, parallel: bool = True,
                    threads: int = 0) -> tuple[pv.PolyData, float, int, str]:
    """Tessellate the CAD with the converter's exact fusion and deflection.

    The fuse is the dominant cost for multi-solid STEP assemblies (a 43-solid
    drum took ~89 s as a serial accumulator fold).  Use the converter's
    all-at-once general Boolean fuse with OCC threads here: it is the same
    geometric union (identical CAD volume and bounding box, verified) but
    finishes in ~3 s instead of ~89 s.  This is a display/verification path
    only; production mesh generation keeps its historic sequential fuse order.
    """
    if source_axis:
        axis_name = source_axis.upper()
    else:
        # New sidecars use source_drum_axis; legacy ones used rotation_axis.
        axis_name = str(meta.get("source_drum_axis", meta.get("rotation_axis", "Y"))).upper()
    axis = conv.AXES.get(axis_name, conv.AXES["Y"])
    min_volume = float(meta.get("min_solid_volume_m3", 0.0))

    conv.enable_occ_parallel(threads, parallel=parallel)
    shape, n_solids, n_faces, cad_volume_cad, kept, _solid_features = conv.read_cad(
        step_path,
        fuse_assembly=True,
        min_volume_m3=min_volume,
        unit_scale=unit_scale,
        fuse_method="general" if parallel else "sequential",
        parallel_fuse=parallel,
    )
    stl_path = tmp_dir / "cad_surface.stl"
    conv.write_surface_stl(shape, stl_path, deflection / unit_scale, angular)

    cad = pv.read(str(stl_path))
    points = np.asarray(cad.points, dtype=float) * unit_scale
    points = points @ conv.source_axis_to_z(axis).T
    cad.points = points
    info = (f"{n_solids} solids -> {kept} kept, {n_faces} CAD faces, "
            f"{axis_name} axis -> local Z")
    return cad, cad_volume_cad * unit_scale**3, kept, info


def fmt_bounds(mesh: pv.PolyData) -> str:
    # PyVista bounds are (xmin, xmax, ymin, ymax, zmin, zmax).
    xmin, xmax, ymin, ymax, zmin, zmax = mesh.bounds
    return (f"[x {xmin:.4g}..{xmax:.4g}, "
            f"y {ymin:.4g}..{ymax:.4g}, z {zmin:.4g}..{zmax:.4g}]")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--study-dir", type=Path, default=None,
                   help="wizard study output dir; auto-discovers STEP, surface and metadata")
    p.add_argument("--step", type=Path, default=None, help="original STEP CAD file")
    p.add_argument("--surface", type=Path, default=None,
                   help="converted surface .stl or .obj (default: from mesh metadata)")
    p.add_argument("--fea-node", type=Path, default=None,
                   help="converted FEA .node file; metadata read from sibling .json")
    p.add_argument("--output", type=Path, default=None,
                   help="save a PNG screenshot here (project-local by default); "
                        "skips the interactive window unless --show is given")
    p.add_argument("--show", action="store_true",
                   help="open an interactive linked-view window (the default when a display is available)")
    p.add_argument("--source-axis", choices=("X", "Y", "Z"), default=None,
                   help="STEP drum axis (default: from metadata; legacy sidecars use Z)")
    p.add_argument("--deflection", type=float, default=None,
                   help="OCC STEP tessellation deflection in metres (default: metadata value)")
    p.add_argument("--max-cad-faces", type=int, default=200000,
                   help="decimate the CAD display mesh to this many faces (0 disables)")
    p.add_argument("--mesh-volume-error", type=float, default=None,
                   help="override the volume-error tolerance for the pass/fail line (percent)")
    p.add_argument("--threads", type=int, default=0,
                   help="limit OpenCASCADE/OMP worker threads (0 = all logical cores)")
    p.add_argument("--no-parallel", action="store_true",
                   help="disable OCC parallel meshing/fuse (diagnostic; much slower)")
    return p


def main() -> int:
    args = build_parser().parse_args()

    study_cfg: dict[str, Any] = {}
    if args.study_dir:
        cfg_path = args.study_dir / "study_config.json"
        if not cfg_path.is_file():
            raise SystemExit(f"no study_config.json in {args.study_dir}")
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            study_cfg = data

    def from_config(key: str) -> Path | None:
        value = study_cfg.get(key)
        if not value:
            return None
        path = Path(value)
        return path if path.is_absolute() else ROOT / path

    step_path = args.step or from_config("step_file")
    fea_node = args.fea_node or from_config("fea_node")

    meta: dict[str, Any] = {}
    if fea_node is not None:
        meta = load_sidecar(fea_node)

    surface_path = args.surface
    if surface_path is None:
        recorded = meta.get("outputs", {}).get("surface_stl")
        if recorded:
            recorded_path = Path(recorded)
            if recorded_path.is_absolute():
                surface_path = recorded_path
            else:
                # Sidecar paths are relative to the repository root, but also
                # accept paths relative to the sidecar itself (older exports).
                candidates = [ROOT / recorded_path]
                if fea_node is not None:
                    candidates.insert(0, fea_node.parent / recorded_path)
                surface_path = next((p for p in candidates if p.is_file()), candidates[-1])
    if surface_path is None:
        surface_path = from_config("crm_obj")
    if surface_path is None and fea_node is not None:
        surface_path = fea_node.with_name(fea_node.stem + "_surface.stl")
    if surface_path is None or not surface_path.is_file():
        raise SystemExit("could not locate the converted surface; pass --surface or --fea-node")

    unit_scale = float(meta.get("unit_scale", 0.001))
    deflection = (args.deflection if args.deflection is not None
                  else float(meta.get("surface_deflection_m", 0.001)))
    angular = float(meta.get("angular_deflection_rad", 0.15))
    if args.source_axis:
        axis_name = args.source_axis.upper()
    else:
        axis_name = str(meta.get("source_drum_axis", meta.get("rotation_axis", "Y"))).upper()

    converted = load_surface(surface_path)
    converted, surface_scale = normalize_surface_units(converted, meta, unit_scale)
    if surface_scale != 1.0:
        print(f"Note: surface STL was in CAD units; rescaled by {surface_scale:g} to metres.")
    cad: pv.PolyData | None = None
    cad_volume: float | None = None
    cad_info = ""
    if step_path is not None and step_path.is_file():
        with tempfile.TemporaryDirectory(prefix="view_geometry_", dir=ROOT) as tmp:
            cad, cad_volume, _kept, cad_info = tessellate_step(
                step_path, meta, unit_scale=unit_scale, deflection=deflection,
                angular=angular, source_axis=args.source_axis,
                tmp_dir=Path(tmp), parallel=not args.no_parallel,
                threads=args.threads,
            )
    else:
        print("No original STEP found; showing the converted mesh only. "
              "Pass --step to enable the comparison.")

    print("Geometry verification")
    print(f"  original STEP: {step_path if step_path else '(none)'}")
    print(f"  converted:     {surface_path}")
    print(f"  unit scale:    {unit_scale:g}; source axis {axis_name} -> exported Z")
    if cad is not None:
        print(f"  CAD detail:    {cad_info}")
        print(f"  CAD bbox:      {fmt_bounds(cad)}  ({cad.n_cells} display faces)")
    print(f"  conv bbox:     {fmt_bounds(converted)}  ({converted.n_cells} faces)")

    cad_volume_m3 = cad_volume if cad_volume is not None else meta.get("cad_volume_m3")
    mesh_volume_m3 = meta.get("mesh_volume_m3")
    recorded_error = meta.get("volume_relative_error")
    if cad_volume_m3 is not None and mesh_volume_m3 is not None:
        error = abs(float(mesh_volume_m3) - float(cad_volume_m3)) / float(cad_volume_m3) * 100.0
        print(f"  CAD volume:    {float(cad_volume_m3):.8g} m^3")
        print(f"  mesh volume:   {float(mesh_volume_m3):.8g} m^3")
        print(f"  volume error:  {error:.3f}%"
              + (f" (recorded {float(recorded_error) * 100:.3f}%)" if recorded_error is not None else ""))
        limit = (args.mesh_volume_error if args.mesh_volume_error is not None
                 else float(meta.get("max_volume_error", 5.0)))
        print(f"  verdict:       {'PASS' if error <= limit else 'FAIL'} (limit {limit:g}%)")
    elif recorded_error is not None:
        print(f"  recorded volume error: {float(recorded_error) * 100:.3f}%")

    panels: list[tuple[str, str]] = []
    if cad is not None:
        panels.append(("Original STEP (OCC tessellation)", "cad"))
    panels.append(("Converted FEA surface", "converted"))
    if cad is not None:
        panels.append(("Overlay: STEP + converted", "overlay"))

    cad_display = cad
    if (cad is not None and args.max_cad_faces > 0
            and cad.n_cells > args.max_cad_faces):
        try:
            cad_display = cad.decimate(
                target_reduction=1.0 - args.max_cad_faces / cad.n_cells
            )
        except Exception:
            cad_display = cad

    use_show = args.show or (args.output is None and bool(os.environ.get("DISPLAY")))

    def build_plotter(off_screen: bool) -> pv.Plotter:
        p = pv.Plotter(
            shape=(1, len(panels)),
            off_screen=off_screen,
            window_size=(500 * len(panels), 540),
        )
        for index, (title, kind) in enumerate(panels):
            p.subplot(0, index)
            p.add_text(title, font_size=11, color="black")
            p.add_axes(color="black")
            if kind == "cad":
                p.add_mesh(cad_display, color="#1f77b4", show_edges=False)
            elif kind == "converted":
                p.add_mesh(converted, color="#ff7f0e", show_edges=False)
            else:
                p.add_mesh(cad_display, color="#1f77b4", opacity=0.45)
                p.add_mesh(converted, color="#ff7f0e", style="wireframe", opacity=0.9)
            p.view_isometric()
        p.link_views()
        return p

    if args.output and not args.show:
        output = args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        build_plotter(off_screen=True).show(screenshot=str(output), auto_close=True)
        print(f"Wrote {output}")
        print("Tip: rerun with --show (or omit --output) for an interactive 3D view.")
    elif use_show:
        print("Interactive view: drag to rotate; views are linked. Close the window to exit.")
        try:
            build_plotter(off_screen=False).show(auto_close=True)
        except Exception as exc:
            output = ROOT / "geometry_checks" / f"{surface_path.stem}_geometry_check.png"
            output.parent.mkdir(parents=True, exist_ok=True)
            build_plotter(off_screen=True).show(screenshot=str(output), auto_close=True)
            print(f"Interactive view unavailable ({exc}); wrote {output} instead.")
    else:
        output = ROOT / "geometry_checks" / f"{surface_path.stem}_geometry_check.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        build_plotter(off_screen=True).show(screenshot=str(output), auto_close=True)
        print(f"Wrote {output}")
        print("Tip: rerun with --show (or set DISPLAY) for an interactive 3D view.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
