#!/usr/bin/env -S /mnt/P3/Programs/Linux/pychrono/chrono-env/bin/python
"""Export a CRM/one-way-FEA study to ParaView-friendly VTK time series.

This script converts existing study outputs into files that ParaView can load
as time series:

  <out>/crm_particles.pvd + crm_particles/frame_*.vtp
      SPH/CRM particle positions per output frame (from crm/particles_*.csv),
      with point data: inside_drum, excavated.

  <out>/excavator.pvd + excavator/frame_*.vtp
      Rigid drum/excavator surface transformed to the CRM pose at each frame
      (from crm/summary.csv + mesh surface + config), so it can be shown
      together with the particles.

  <out>/combined.pvd
      A ParaView DataSet collection referencing both series so they open in the
      same view with a shared time axis.

If the one-way FEA driver was run with VTK export enabled, FEA frames live in
  <fea>/vtk/frame_*.vtu  and  <fea>/vtk/fea.pvd
with point data (Displacement, DisplacementMagnitude) and cell data
(von_mises_pa). This script will include fea/vtk/fea.pvd in combined.pvd when
present.

Typical use::

    ./chrono-env/bin/python export_study_vtk.py \
        --study-dir Output_study_20261119_144040

    # then in ParaView (recommended):
    pvpython /path/to/view_study_paraview.py Output_study_20261119_144040/paraview
    # or ParaView GUI:
    # PYCHRONO_EXPORT_DIR=Output_study_20261119_144040/paraview paraview --script=/path/to/view_study_paraview.py

Notes on coordinates/poses:
  - CRM particle CSVs are already in world coordinates.
  - The drum pose per frame is taken from crm/summary.csv (drum_z_m, spin_deg).
    Rotation is about the configured spin axis by spin_deg (degrees) composed
    with the drum's initial orientation (initial_quaternion_xyzw from
    lunar_excavator_config), applied to the exported mesh local coordinates and
    translated to (0, 0, drum_z_m). This matches the rigid-body CRM/FEA drivers.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np

# Prefer vendored/conda env when run directly.
ROOT = Path(__file__).resolve().parent
_VENV_PYTHON = ROOT / "chrono-env" / "bin" / "python"
_REEXEC = "PYCHRONO_EXPORT_STUDY_VTK"
if (
    os.environ.get(_REEXEC) != "1"
    and _VENV_PYTHON.is_file()
    and Path(sys.prefix).resolve() != (ROOT / "chrono-env").resolve()
):
    os.environ[_REEXEC] = "1"
    os.execv(str(_VENV_PYTHON), [str(_VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])

try:
    import vtk  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit(f"VTK is required (tried vtk python module): {exc}") from exc

try:
    import pyvista as pv  # type: ignore
except Exception:
    pv = None  # type: ignore


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_numeric_csv(path: Path, columns: tuple[str, ...]) -> dict[str, np.ndarray]:
    """Read selected numeric columns with one NumPy pass instead of Python dicts."""
    result = {name: np.empty(0, dtype=float) for name in columns}
    with path.open("r", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\r\n").split(",")
        indices = []
        for name in columns:
            try:
                indices.append(header.index(name))
            except ValueError:
                indices.append(-1)
        rows = []
        for line in handle:
            if not line.strip():
                continue
            fields = line.rstrip("\r\n").split(",")
            rows.append(fields)
        if not rows:
            return result
        ncols = max((len(r) for r in rows), default=len(header))
        data = np.empty((len(rows), ncols), dtype=float)
        data.fill(np.nan)
        for i, fields in enumerate(rows):
            for j, value in enumerate(fields):
                try:
                    data[i, j] = float(value)
                except ValueError:
                    pass
        for name, col_idx in zip(columns, indices):
            result[name] = data[:, col_idx] if col_idx >= 0 else np.zeros(len(rows))
    return result


def quat_xyzw_to_matrix(qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    """Rotation matrix for quaternion (x,y,z,w)."""
    n = math.sqrt(qx*qx + qy*qy + qz*qz + qw*qw)
    if n == 0:
        return np.eye(3)
    x, y, z, w = qx/n, qy/n, qz/n, qw/n
    return np.array([
        [1 - 2*(y*y + z*z),     2*(x*y - z*w),     2*(x*z + y*w)],
        [    2*(x*y + z*w), 1 - 2*(x*x + z*z),     2*(y*z - x*w)],
        [    2*(x*z - y*w),     2*(y*z + x*w), 1 - 2*(x*x + y*y)],
    ], dtype=float)


def axis_angle_matrix(axis: str, angle_deg: float) -> np.ndarray:
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    axis = axis.upper()
    if axis == "X":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=float)
    if axis == "Y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=float)
    if axis == "Z":
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=float)
    raise ValueError(f"unknown spin axis: {axis!r}")


def initial_quaternion_xyzw(spin_axis: str = "X") -> tuple[float, float, float, float]:
    # Matches lunar_excavator_config.ExcavatorParams.initial_quaternion_xyzw
    s = math.sqrt(0.5)
    return (0.0, s, 0.0, s)  # 90 deg about Y: local Z -> global X


def discover(study_dir: Path) -> dict[str, Any]:
    cfg_path = study_dir / "study_config.json"
    study_cfg = read_json(cfg_path) if cfg_path.is_file() else {}
    crm_dir = study_dir / "crm"
    fea_dir = study_dir / "fea"
    mesh_dir = study_dir / "mesh"

    def resolve(p: Any) -> Path | None:
        if not p:
            return None
        path = Path(str(p))
        if not path.is_absolute():
            path = (ROOT / path) if not (study_dir / path).exists() else (study_dir / path)
        return path

    surface = resolve(study_cfg.get("crm_obj"))
    if surface is None or not surface.exists():
        # try mesh/*_surface.stl
        cands = sorted(mesh_dir.glob("*_surface.stl")) if mesh_dir.is_dir() else []
        if cands:
            surface = cands[0]
    fea_node = resolve(study_cfg.get("fea_node"))
    step_file = resolve(study_cfg.get("step_file"))
    return {
        "study_cfg": study_cfg,
        "crm_dir": crm_dir,
        "fea_dir": fea_dir,
        "mesh_dir": mesh_dir,
        "surface": surface,
        "fea_node": fea_node,
        "step_file": step_file,
    }


def load_surface_polydata(path: Path):
    if pv is not None:
        mesh = pv.read(str(path))
        return mesh
    reader = vtk.vtkSTLReader() if path.suffix.lower() == ".stl" else vtk.vtkOBJReader()
    reader.SetFileName(str(path))
    reader.Update()
    return reader.GetOutput()


def surface_points_faces(path: Path) -> tuple[np.ndarray, np.ndarray]:
    if pv is not None:
        mesh = pv.read(str(path))
        pts = np.asarray(mesh.points, dtype=float)
        # faces as (n,3) assuming triangles
        faces = mesh.faces.reshape(-1, 4)[:, 1:4].copy()
        return pts, faces
    # fallback via vtk
    pd = load_surface_polydata(path)
    npts = pd.GetNumberOfPoints()
    pts = np.array([pd.GetPoint(i) for i in range(npts)], dtype=float)
    faces = []
    pd.GetPolys().InitTraversal()
    idlist = vtk.vtkIdList()
    while pd.GetPolys().GetNextCell(idlist):
        if idlist.GetNumberOfIds() == 3:
            faces.append([idlist.GetId(0), idlist.GetId(1), idlist.GetId(2)])
    return pts, np.asarray(faces, dtype=np.int64)


def _vtk_points(points: np.ndarray):
    from vtk.util.numpy_support import numpy_to_vtk

    vtk_pts = vtk.vtkPoints()
    vtk_pts.SetData(numpy_to_vtk(np.ascontiguousarray(points, dtype=float), deep=True))
    return vtk_pts


def _vtk_cell_array(n_per_cell: int, indices: np.ndarray | None, n_points: int):
    from vtk.util.numpy_support import numpy_to_vtkIdTypeArray

    ca = vtk.vtkCellArray()
    if indices is not None and len(indices):
        cells = np.empty((len(indices), n_per_cell + 1), dtype=np.int64)
        cells[:, 0] = n_per_cell
        cells[:, 1:] = np.asarray(indices, dtype=np.int64).reshape(len(indices), n_per_cell)
        ca.ImportLegacyFormat(numpy_to_vtkIdTypeArray(cells.ravel(), deep=True))
    elif n_per_cell == 1 and n_points:
        from vtk.util.numpy_support import numpy_to_vtk

        offsets = np.arange(n_points + 1, dtype=np.int64)
        connectivity = np.arange(n_points, dtype=np.int64)
        ca.SetData(numpy_to_vtkIdTypeArray(offsets, deep=True),
                   numpy_to_vtkIdTypeArray(connectivity, deep=True))
    return ca


def write_polydata_vtp(points: np.ndarray, faces: np.ndarray | None,
                        point_data: dict[str, np.ndarray] | None,
                        path: Path) -> None:
    from vtk.util.numpy_support import numpy_to_vtk

    poly = vtk.vtkPolyData()
    poly.SetPoints(_vtk_points(points))
    if faces is not None and len(faces):
        poly.SetPolys(_vtk_cell_array(3, faces, len(points)))
    else:
        poly.SetVerts(_vtk_cell_array(1, None, len(points)))
    if point_data:
        for name, arr in point_data.items():
            a = np.asarray(arr)
            components = 1 if a.ndim == 1 else int(a.shape[1])
            va = numpy_to_vtk(np.ascontiguousarray(a, dtype=float).reshape(-1),
                              deep=True, array_type=vtk.VTK_DOUBLE)
            va.SetName(name)
            va.SetNumberOfComponents(components)
            poly.GetPointData().AddArray(va)
        # set first scalar/vector as active if sensible
        names = list(point_data.keys())
        if names:
            arr = poly.GetPointData().GetArray(names[0])
            if arr and arr.GetNumberOfComponents() == 1:
                poly.GetPointData().SetActiveScalars(names[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = vtk.vtkXMLPolyDataWriter()
    writer.SetFileName(str(path))
    writer.SetInputData(poly)
    # binary for size/speed
    if hasattr(writer, "SetDataModeToBinary"):
        writer.SetDataModeToBinary()
    elif hasattr(writer, "SetDataModeToAppended"):
        writer.SetDataModeToAppended()
    writer.Write()


def write_pvd(entries: list[tuple[float, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        '<?xml version="1.0"?>',
        '<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">',
        '  <Collection>',
    ]
    for t, file in entries:
        rel = os.path.relpath(file, start=path.parent)
        lines.append(f'    <DataSet timestep="{t:.9g}" group="" part="0" file="{rel}"/>')
    lines += ['  </Collection>', '</VTKFile>', '']
    path.write_text("\n".join(lines), encoding="utf-8")


def write_combined_pvd(parts: list[tuple[str, Path]], path: Path) -> None:
    # Collection of collections/datasets: reference pvd files by nesting datasets is not
    # standard, but ParaView can load a PVD that points to other datasets? Instead, write
    # a MultiBlock .vtm is different; simplest portable way: write a PVD that includes
    # multiple parts per timestep if aligned. Here CRM particles and excavator share
    # timesteps, so emit one DataSet per part per timestep by reading their entries.
    # Easier: create a PVD collection where each timestep has multiple DataSet entries
    # with different part indices/names via 'part' - ParaView treats parts separately but
    # same timestep; we can also just write separate entries.
    # We'll merge aligned series (particles+excavator) by timestep.
    path.parent.mkdir(parents=True, exist_ok=True)
    # load entries from each pvd
    def parse_pvd(p: Path):
        if not p.is_file():
            return []
        import xml.etree.ElementTree as ET
        try:
            root = ET.parse(p).getroot()
        except Exception:
            return []
        out = []
        for ds in root.findall('.//DataSet'):
            try:
                t = float(ds.attrib.get('timestep', '0'))
            except Exception:
                t = 0.0
            f = ds.attrib.get('file', '')
            out.append((t, str((p.parent / f).resolve())))
        return out
    series = [(name, parse_pvd(p)) for name, p in parts if p.is_file()]
    if not series:
        return
    lines = [
        '<?xml version="1.0"?>',
        '<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">',
        '  <Collection>',
    ]
    for part_idx, (name, entries) in enumerate(series):
        for t, file in entries:
            try:
                rel = os.path.relpath(file, start=path.parent)
            except Exception:
                rel = file
            # include name in group for clarity
            lines.append(f'    <DataSet timestep="{t:.9g}" group="{name}" part="{part_idx}" file="{rel}"/>')
    lines += ['  </Collection>', '</VTKFile>', '']
    path.write_text("\n".join(lines), encoding="utf-8")


def _read_particle_frame(args):
    idx, path_str, time_s, radius, out_dir_str = args
    path = Path(path_str)
    out_path = Path(out_dir_str) / f"frame_{idx:06d}.vtp"
    if out_path.exists():
        return (time_s, str(out_path), 0)
    cols = read_numeric_csv(path, ("X", "Y", "Z", "inside_drum", "excavated"))
    n = len(cols["X"])
    if not n:
        return None
    pts = np.column_stack([cols["X"], cols["Y"], cols["Z"]])
    point_data = {
        "inside_drum": cols["inside_drum"],
        "excavated": cols["excavated"],
    }
    if radius is not None and radius > 0.0:
        point_data["radius_m"] = np.full(n, float(radius), dtype=float)
        point_data["diameter_m"] = np.full(n, float(2.0 * radius), dtype=float)
    write_polydata_vtp(pts, None, point_data, out_path)
    return (time_s, str(out_path), 1)


def export_crm_particles(crm_dir: Path, out_dir: Path, stride: int = 1,
                         particle_radius_m: float | None = None,
                         workers: int = 1):
    summary_path = crm_dir / "summary.csv"
    if not summary_path.is_file():
        print(f"  skip particles: missing {summary_path}")
        return None
    summary = read_numeric_csv(summary_path, ("time_s",))
    part_files = sorted(crm_dir.glob("particles_*.csv"))
    if not part_files:
        print(f"  skip particles: no particles_*.csv in {crm_dir}")
        return None
    by_idx = {}
    for path in part_files:
        try:
            idx = int(path.stem.split('_')[-1])
        except Exception:
            continue
        by_idx[idx] = path
    frames_dir = out_dir / "crm_particles"
    frames_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for idx in range(len(summary["time_s"])):
        if idx % stride != 0 and idx != len(summary["time_s"]) - 1:
            continue
        path = by_idx.get(idx)
        if path is None:
            continue
        jobs.append((idx, str(path), float(summary["time_s"][idx]),
                     particle_radius_m, str(frames_dir)))
    entries = []
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(_read_particle_frame, jobs, chunksize=2):
                if result:
                    entries.append(result[:2])
    else:
        for job in jobs:
            result = _read_particle_frame(job)
            if result:
                entries.append(result[:2])
    pvd = out_dir / "crm_particles.pvd"
    if entries:
        write_pvd(entries, pvd)
        print(f"  particles: {len(entries)} frames -> {pvd}")
        return pvd
    print("  particles: no frames exported")
    return None


def _write_excavator_frame(args):
    idx, xyz, faces, time_s, out_dir_str = args
    out_path = Path(out_dir_str) / f"frame_{idx:06d}.vtp"
    if out_path.exists():
        return (time_s, str(out_path), 0)
    write_polydata_vtp(xyz, faces, None, out_path)
    return (time_s, str(out_path), 1)


def export_excavator(crm_dir: Path, surface_path: Path, out_dir: Path,
                     spin_axis: str = "X", stride: int = 1,
                     workers: int = 1):
    summary_path = crm_dir / "summary.csv"
    if not summary_path.is_file() or surface_path is None or not surface_path.is_file():
        print("  skip excavator: missing summary or surface mesh")
        return None
    cols = read_numeric_csv(summary_path, ("time_s", "drum_z_m", "spin_deg"))
    base_pts, faces = surface_points_faces(surface_path)
    q0 = initial_quaternion_xyzw(spin_axis)
    R0 = quat_xyzw_to_matrix(*q0)
    v0 = np.ascontiguousarray((R0 @ base_pts.T).T)
    frames_dir = out_dir / "excavator"
    frames_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for idx in range(len(cols["time_s"])):
        if idx % stride != 0 and idx != len(cols["time_s"]) - 1:
            continue
        Rspin = axis_angle_matrix(spin_axis, float(cols["spin_deg"][idx]))
        pts = np.ascontiguousarray((Rspin @ v0.T).T)
        pts[:, 2] += float(cols["drum_z_m"][idx])
        jobs.append((idx, pts, faces, float(cols["time_s"][idx]), str(frames_dir)))
    entries = []
    if workers > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(_write_excavator_frame, jobs, chunksize=2):
                if result:
                    entries.append(result[:2])
    else:
        for job in jobs:
            result = _write_excavator_frame(job)
            if result:
                entries.append(result[:2])
    pvd = out_dir / "excavator.pvd"
    if entries:
        write_pvd(entries, pvd)
        print(f"  excavator: {len(entries)} frames -> {pvd} (surface {surface_path.name})")
        return pvd
    print("  excavator: no frames exported")
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study-dir", type=Path, required=True, help="study output directory")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="export directory (default: <study-dir>/paraview)")
    ap.add_argument("--stride", type=int, default=1,
                    help="export every Nth CRM frame (default: 1 = all). "
                         "Use >1 to reduce size for long/high-fps runs.")
    ap.add_argument("--particles-only", action="store_true", help="export only particles")
    ap.add_argument("--excavator-only", action="store_true", help="export only excavator surface")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel worker processes for frame export (default: 1; try 4-8)")
    args = ap.parse_args()

    study_dir = args.study_dir.expanduser().resolve()
    if not study_dir.is_dir():
        raise SystemExit(f"study directory not found: {study_dir}")
    out_dir = (args.out_dir.expanduser().resolve() if args.out_dir
               else (study_dir / "paraview"))
    out_dir.mkdir(parents=True, exist_ok=True)
    stride = max(1, int(args.stride))
    workers = max(1, int(args.workers))

    info = discover(study_dir)
    crm_dir = info["crm_dir"]
    fea_dir = info["fea_dir"]
    surface = info["surface"]

    # determine spin axis and particle sizing
    spin_axis = "X"
    particle_spacing = None
    crm_cfg = crm_dir / "config.json"
    if crm_cfg.is_file():
        try:
            c = read_json(crm_cfg)
            run_cfg = c.get("run") or {}
            spin_axis = str(run_cfg.get("spin_axis") or c.get("geometry", {}).get("spin_axis_global") or "X").upper()
            if run_cfg.get("spacing") is not None:
                particle_spacing = float(run_cfg.get("spacing"))
        except Exception:
            pass
    particle_radius_m = (particle_spacing / 2.0) if particle_spacing is not None else None
    print(f"Exporting study: {study_dir}")
    print(f"  out: {out_dir}")
    print(f"  spin axis: {spin_axis}  stride: {stride}  workers: {workers}")
    if particle_radius_m is not None:
        print(f"  particle radius: {particle_radius_m:g} m (spacing {particle_spacing:g} m)")
    if surface:
        print(f"  surface: {surface}")
    else:
        print("  surface: (not found)")

    parts = []
    if not args.excavator_only:
        p = export_crm_particles(crm_dir, out_dir, stride=stride,
                                 particle_radius_m=particle_radius_m, workers=workers)
        if p:
            parts.append(("crm_particles", p))
    if not args.particles_only:
        if surface:
            p = export_excavator(crm_dir, surface, out_dir, spin_axis=spin_axis,
                                 stride=stride, workers=workers)
            if p:
                parts.append(("excavator", p))
        else:
            print("  skip excavator: no surface mesh found")

    fea_pvd = fea_dir / "vtk" / "fea.pvd"
    if fea_pvd.is_file():
        parts.append(("fea", fea_pvd))
        print(f"  fea: found {fea_pvd}")
    else:
        print(f"  fea: no VTK export found at {fea_pvd} (run one-way FEA with VTK export enabled to visualize stress/deformation)")

    if parts:
        combined = out_dir / "combined.pvd"
        write_combined_pvd(parts, combined)
        print(f"  combined: {combined}")

    meta = {
        "study_dir": str(study_dir),
        "out_dir": str(out_dir),
        "spin_axis": spin_axis,
        "stride": stride,
        "workers": workers,
        "surface": str(surface) if surface else None,
        "particle_spacing_m": particle_spacing,
        "particle_diameter_m": (particle_spacing if particle_spacing is not None else None),
        "particle_radius_m": particle_radius_m,
        "series": {
            "crm_particles": "crm_particles.pvd",
            "excavator": "excavator.pvd",
            "fea": str(fea_pvd.resolve()) if fea_pvd.is_file() else None,
        },
        "has_particles": (out_dir / "crm_particles.pvd").is_file(),
        "has_excavator": (out_dir / "excavator.pvd").is_file(),
        "has_fea_vtk": fea_pvd.is_file(),
    }
    (out_dir / "export_manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"  manifest: {out_dir / 'export_manifest.json'}")
    print("Done. Open in ParaView, e.g.:")
    print(f"  paraview {out_dir / 'combined.pvd'}")
    print("  # recommended (separate sources, spheres, and FEA stress by default):")
    print(f"  pvpython {ROOT / 'view_study_paraview.py'} {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
