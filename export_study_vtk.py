#!/usr/bin/env python3
"""Export a CRM/one-way-FEA study to ParaView-friendly VTK time series.

This script converts existing study outputs into files that ParaView can load
as time series. Every export regenerates the CRM particle and excavator frames
from the current CRM data; existing frames are never reused, so cancelled/older
runs cannot leave a static or stale non-FEA animation behind. FEA VTU frames
are produced by the FEA driver and are validated/referenced here.

  <out>/crm_particles.pvd + crm_particles/frame_*.vtp
      SPH/CRM particle positions per output frame (from crm/particles_*.csv),
      with point data: inside_drum, excavated.

  <out>/excavator.pvd + excavator/frame_*.vtp
      Rigid drum/excavator surface transformed to the CRM pose at each frame
      (from crm/summary.csv + mesh surface + config), so it can be shown
      together with the particles.

  <out>/crm_animation.pvd
      Canonical *non-FEA* series: CRM particles + rigid excavator poses only.
      <out>/combined.pvd is written with the same content for backwards
      compatibility. Neither file ever includes FEA data.

The fast one-way FEA path writes scalar summaries only. Scalar FEA histories
remain in <fea>/summary.csv and are displayed by the GUI's Output Preview. A
compact <out>/fea_summary.csv is derived from it with MPa/mm units; ParaView
history charts are opt-in via --show-fea-charts/PYCHRONO_FEA_CHARTS and are off
by default. When the UI's full 3D FEA stress option is enabled, the driver also
writes:
  <fea>/vtk/frames/frame_*.vtu  and  <fea>/vtk/fea.pvd
with point data (Displacement, DisplacementMagnitude) and cell data
(von_mises_pa, von_mises_MPa). A revalidated copy is written to <out>/fea.pvd
and the optional overlay <out>/with_fea.pvd adds FEA to the CRM animation. A
structurally usable partial multi-frame run is viewable and noted in the
manifest; a one-frame t=0 FEA PVD (e.g. from a cancelled run) is ignored and
never mixed into the non-FEA series.

Typical use::

    ./chrono-env/bin/python export_study_vtk.py \
        --study-dir Output_study_20261119_144040

    # then in ParaView: CRM animation (FEA history charts are opt-in)
    pvpython /path/to/view_study_paraview.py Output_study_20261119_144040/paraview
    # 3D FEA stress overlay, when a multi-frame VTK series is available:
    pvpython /path/to/view_study_paraview.py Output_study_20261119_144040/paraview --with-fea
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
sys.path.insert(0, str(ROOT))
import fea_summary  # noqa: E402

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


def read_numeric_csv(path: Path, columns: tuple[str, ...],
                     row_stride: int = 1) -> dict[str, np.ndarray]:
    """Read selected numeric columns with a C-backed NumPy pass.

    ``row_stride`` keeps only every Nth data row, which lets the exporter render
    a sparse subset of particles without paying full CSV parse cost.  Keep the
    original parser as a fallback for ragged/non-numeric rows so a partially
    written CSV does not abort an export.
    """
    row_stride = max(1, int(row_stride))
    result = {name: np.empty(0, dtype=float) for name in columns}
    with path.open("r", encoding="utf-8") as handle:
        header = handle.readline().rstrip("\r\n").split(",")
        indices = []
        for name in columns:
            try:
                indices.append(header.index(name))
            except ValueError:
                indices.append(-1)
        if indices and all(i >= 0 for i in indices):
            try:
                if row_stride == 1:
                    values = np.loadtxt(path, delimiter=",", skiprows=1,
                                        usecols=tuple(indices), dtype=float, ndmin=2)
                else:
                    selected = [line for i, line in enumerate(handle)
                                if i % row_stride == 0 and line.strip()]
                    values = (np.loadtxt(selected, delimiter=",",
                                         usecols=tuple(indices), dtype=float, ndmin=2)
                              if selected else np.empty((0, len(indices)), dtype=float))
                for offset, name in enumerate(columns):
                    result[name] = values[:, offset] if values.size else np.empty(0, dtype=float)
                return result
            except ValueError:
                pass  # malformed/partial row: use the tolerant parser below
        handle.seek(0)
        handle.readline()  # skip header
        rows = []
        for i, line in enumerate(handle):
            if row_stride > 1 and i % row_stride != 0:
                continue
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
                        path: Path, point_cells: bool = True) -> None:
    from vtk.util.numpy_support import numpy_to_vtk

    poly = vtk.vtkPolyData()
    poly.SetPoints(_vtk_points(points))
    if faces is not None and len(faces):
        poly.SetPolys(_vtk_cell_array(3, faces, len(points)))
    elif point_cells:
        poly.SetVerts(_vtk_cell_array(1, None, len(points)))
    # Particle frames are point clouds.  Glyph/PointGaussian display in ParaView
    # uses the points directly, so omitting per-point vertex cells avoids
    # writing (and reading) two large connectivity arrays per frame.
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


def parse_pvd_entries(path: Path) -> list[tuple[float, Path]]:
    """Return (timestep, resolved dataset path) entries from a PVD collection."""
    if not path.is_file():
        return []
    import xml.etree.ElementTree as ET
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return []
    out: list[tuple[float, Path]] = []
    for ds in root.findall('.//DataSet'):
        try:
            t = float(ds.attrib.get('timestep', '0'))
        except Exception:
            t = 0.0
        file = ds.attrib.get('file', '')
        if not file:
            continue
        p = Path(file)
        if not p.is_absolute():
            p = path.parent / p
        out.append((t, p.resolve()))
    return out


def write_combined_pvd(parts: list[tuple[str, Path]], path: Path) -> None:
    """Write one PVD containing several named series, preserving timesteps.

    PVD does not support nesting collections portably, so each source series is
    flattened into DataSet entries with a distinct ``group`` (series name) and
    ``part`` index.  An empty collection is written when there are no parts so
    that stale PVD content from a previous export is never left behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    series = [(name, parse_pvd_entries(p)) for name, p in parts if p.is_file()]
    lines = [
        '<?xml version="1.0"?>',
        '<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">',
        '  <Collection>',
    ]
    for part_idx, (name, entries) in enumerate(series):
        for t, file in entries:
            try:
                rel = os.path.relpath(file, start=path.parent).replace(os.sep, "/")
            except Exception:
                rel = str(file)
            lines.append(f'    <DataSet timestep="{t:.9g}" group="{name}" part="{part_idx}" file="{rel}"/>')
    lines += ['  </Collection>', '</VTKFile>', '']
    path.write_text("\n".join(lines), encoding="utf-8")


def prune_frame_files(frames_dir: Path, keep: set[str], suffix: str = ".vtp") -> None:
    """Remove generated frames that are no longer referenced by a PVD."""
    if not frames_dir.is_dir():
        return
    for path in frames_dir.glob(f"frame_*{suffix}"):
        if path.name not in keep:
            try:
                path.unlink()
            except OSError:
                pass


def remove_if_exists(path: Path) -> None:
    try:
        if path.is_file() or path.is_symlink():
            path.unlink()
    except OSError:
        pass


def expected_fea_duration(study_cfg: dict[str, Any], fea_dir: Path) -> float | None:
    """Best-effort expected FEA duration, preferring the FEA run config."""
    candidates: list[Any] = []
    fea_cfg_path = fea_dir / "config.json"
    if fea_cfg_path.is_file():
        try:
            fea_cfg = read_json(fea_cfg_path)
            run_cfg = fea_cfg.get("run") or {}
            candidates.extend([run_cfg.get("duration"), fea_cfg.get("duration"),
                               fea_cfg.get("loads_duration_s")])
        except Exception:
            pass
    candidates.extend([study_cfg.get("fea_duration"), study_cfg.get("duration")])
    for value in candidates:
        try:
            duration = float(value)
            if math.isfinite(duration) and duration > 0.0:
                return duration
        except (TypeError, ValueError):
            continue
    return None


def validate_fea_pvd(fea_pvd: Path, study_cfg: dict[str, Any], fea_dir: Path) -> tuple[bool, str, list[tuple[float, Path]]]:
    """Check that an FEA PVD is a usable time series.

    The one-way FEA writer updates fea.pvd after every frame, so a cancelled run
    can leave a syntactically valid PVD with only the t=0 frame.  Such a file is
    still rejected.  A multi-frame partial run is viewable and is exported with
    a status note that records how much of the configured duration it covers.
    """
    if not fea_pvd.is_file():
        return False, "fea.pvd not found", []
    entries = parse_pvd_entries(fea_pvd)
    if not entries:
        return False, "fea.pvd contains no DataSet entries", []
    missing = [str(p) for _, p in entries if not p.is_file() or p.stat().st_size == 0]
    if missing:
        return False, f"fea.pvd references {len(missing)} missing/empty frame(s)", []
    times = [t for t, _ in entries]
    last_t = max(times)
    if len(entries) < 2 or last_t <= 0.0:
        return False, (f"incomplete FEA export ({len(entries)} frame(s), last t={last_t:g}s); "
                       "likely a cancelled run"), []
    if any(b < a - 1.0e-9 for a, b in zip(times, times[1:])):
        return False, "fea.pvd timesteps are not monotonic", []
    duration = expected_fea_duration(study_cfg, fea_dir)
    partial_note = ""
    if duration is not None and last_t < duration - 1.0e-9:
        partial_note = f" (partial: {last_t:g}s of expected {duration:g}s)"
    return True, f"{len(entries)} frames to {last_t:g}s{partial_note}", entries


def _read_particle_frame(args):
    idx, path_str, time_s, radius, out_dir_str, render_stride = args
    path = Path(path_str)
    out_path = Path(out_dir_str) / f"frame_{idx:06d}.vtp"
    # Always rewrite: a frame from an earlier export may reflect older CSV data.
    cols = read_numeric_csv(path, ("X", "Y", "Z", "inside_drum", "excavated"),
                            row_stride=render_stride)
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
    write_polydata_vtp(pts, None, point_data, out_path, point_cells=False)
    return (time_s, str(out_path), 1)


def export_crm_particles(crm_dir: Path, out_dir: Path, stride: int = 1,
                         particle_radius_m: float | None = None,
                         workers: int = 1, render_stride: int = 1):
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
                     particle_radius_m, str(frames_dir), render_stride))
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
    prune_frame_files(frames_dir, {Path(f).name for _, f in entries})
    pvd = out_dir / "crm_particles.pvd"
    if entries:
        write_pvd(entries, pvd)
        print(f"  particles: regenerated {len(entries)} frames -> {pvd}")
        return pvd
    # Keep a stale PVD from an earlier export from pointing at pruned frames.
    remove_if_exists(pvd)
    print("  particles: no frames exported")
    return None


# Worker-local surface data.  Sending the full transformed mesh with every job
# was the largest remaining exporter cost for high-frame-count studies.
_EXCAV_BASE: np.ndarray | None = None
_EXCAV_FACES: np.ndarray | None = None
_EXCAV_AXIS = "X"


def _init_excavator_worker(base: np.ndarray, faces: np.ndarray, spin_axis: str) -> None:
    global _EXCAV_BASE, _EXCAV_FACES, _EXCAV_AXIS
    _EXCAV_BASE = base
    _EXCAV_FACES = faces
    _EXCAV_AXIS = spin_axis


def _write_excavator_frame(args):
    idx, spin_deg, drum_z_m, time_s, out_dir_str = args
    if _EXCAV_BASE is None or _EXCAV_FACES is None:
        raise RuntimeError("excavator worker was not initialized")
    Rspin = axis_angle_matrix(_EXCAV_AXIS, float(spin_deg))
    pts = np.ascontiguousarray((Rspin @ _EXCAV_BASE.T).T)
    pts[:, 2] += float(drum_z_m)
    out_path = Path(out_dir_str) / f"frame_{idx:06d}.vtp"
    # Force overwrite: poses come from the current CRM summary/config, so any
    # existing frame is potentially stale (e.g. from a cancelled earlier run).
    write_polydata_vtp(pts, _EXCAV_FACES, None, out_path)
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
    # Only scalar pose data crossed the process boundary; each worker keeps one
    # shared copy of the surface instead of receiving it for every frame.
    _init_excavator_worker(v0, faces, spin_axis)
    jobs = []
    for idx in range(len(cols["time_s"])):
        if idx % stride != 0 and idx != len(cols["time_s"]) - 1:
            continue
        jobs.append((idx, float(cols["spin_deg"][idx]), float(cols["drum_z_m"][idx]),
                     float(cols["time_s"][idx]), str(frames_dir)))
    entries = []
    if workers > 1 and len(jobs) > 1:
        chunksize = max(1, min(16, len(jobs) // (workers * 2)))
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_excavator_worker,
            initargs=(v0, faces, spin_axis),
        ) as pool:
            for result in pool.map(_write_excavator_frame, jobs, chunksize=chunksize):
                if result:
                    entries.append(result[:2])
    else:
        for job in jobs:
            result = _write_excavator_frame(job)
            if result:
                entries.append(result[:2])
    prune_frame_files(frames_dir, {Path(f).name for _, f in entries})
    pvd = out_dir / "excavator.pvd"
    if entries:
        write_pvd(entries, pvd)
        print(f"  excavator: regenerated {len(entries)} frames -> {pvd} (surface {surface_path.name})")
        return pvd
    remove_if_exists(pvd)
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
    ap.add_argument("--particle-stride", type=int, default=None,
                    help="export every Nth CRM particle frame independently of the "
                         "excavator series (default: same as --stride; 0 also means same)")
    ap.add_argument("--particle-render-stride", type=int, default=1,
                    help="render only every Nth particle in each exported frame "
                         "(default: 1 = all particles). Use >1 to reduce particle "
                         "file size and viewer load without skipping frames.")
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
    particle_stride_requested = max(0, int(args.particle_stride or 0))
    particle_frame_stride = particle_stride_requested if particle_stride_requested > 0 else stride
    particle_render_stride = max(1, int(args.particle_render_stride or 1))
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
    print(f"  spin axis: {spin_axis}  stride: {stride}  "
          f"particle frame stride: {particle_frame_stride}  "
          f"particle render stride: {particle_render_stride}  workers: {workers}")
    if particle_radius_m is not None:
        print(f"  particle radius: {particle_radius_m:g} m (spacing {particle_spacing:g} m)")
    if surface:
        print(f"  surface: {surface}")
    else:
        print("  surface: (not found)")

    crm_parts: list[tuple[str, Path]] = []
    if not args.excavator_only:
        p = export_crm_particles(crm_dir, out_dir, stride=particle_frame_stride,
                                 particle_radius_m=particle_radius_m, workers=workers,
                                 render_stride=particle_render_stride)
        if p:
            crm_parts.append(("crm_particles", p))
    if not args.particles_only:
        if surface:
            p = export_excavator(crm_dir, surface, out_dir, spin_axis=spin_axis,
                                 stride=stride, workers=workers)
            if p:
                crm_parts.append(("excavator", p))
        else:
            print("  skip excavator: no surface mesh found")

    # Canonical non-FEA series.  combined.pvd keeps the old filename but is no
    # longer allowed to contain FEA data, so a cancelled FEA run cannot freeze
    # or obscure the CRM excavation animation.
    animation = out_dir / "crm_animation.pvd"
    combined = out_dir / "combined.pvd"
    if crm_parts:
        write_combined_pvd(crm_parts, animation)
        write_combined_pvd(crm_parts, combined)
        print(f"  crm animation (non-FEA): {animation}")
        print(f"  combined (alias): {combined}")
    else:
        remove_if_exists(animation)
        remove_if_exists(combined)

    # FEA scalar summary: always regenerate the tiny viewer table from the
    # driver's CSV.  The deformation VTK series below remains opt-in.
    fea_summary_source = fea_dir / "summary.csv"
    fea_summary_out = out_dir / "fea_summary.csv"
    has_fea_summary = (fea_summary_source.is_file()
                       and fea_summary.write_fea_summary_csv(fea_summary_source, fea_summary_out))
    if has_fea_summary:
        print(f"  fea summary: {fea_summary_source.name} -> {fea_summary_out} (MPa/mm XY chart)")
    else:
        remove_if_exists(fea_summary_out)
        if fea_summary_source.is_file():
            print(f"  fea summary: {fea_summary_source} has no usable FEA scalar columns")
        else:
            print(f"  fea summary: no {fea_summary_source} (scalar FEA view unavailable)")

    # Full 3D FEA stress/deformation VTK is opt-in and must have usable frames.
    fea_source = fea_dir / "vtk" / "fea.pvd"
    fea_valid, fea_status, fea_entries = validate_fea_pvd(
        fea_source, info["study_cfg"], fea_dir)
    fea_export = out_dir / "fea.pvd"
    with_fea = out_dir / "with_fea.pvd"
    if fea_valid:
        write_pvd([(t, str(p)) for t, p in fea_entries], fea_export)
        write_combined_pvd(crm_parts + [("fea", fea_export)], with_fea)
        print(f"  fea 3D VTK: {fea_status} -> {fea_export} (+ {with_fea})")
    else:
        # A previously valid export must not survive a later cancelled run.
        remove_if_exists(fea_export)
        remove_if_exists(with_fea)
        if fea_source.is_file():
            print(f"  fea 3D VTK: ignoring {fea_source} ({fea_status}); scalar charts remain available")
        else:
            print(f"  fea 3D VTK: no complete series at {fea_source} "
                  "(enable 'Export full 3D FEA stress animation' and rerun FEA to create one)")

    def series_name(name: str) -> str | None:
        return name if (out_dir / name).is_file() else None

    meta = {
        "study_dir": str(study_dir),
        "out_dir": str(out_dir),
        "spin_axis": spin_axis,
        "stride": stride,
        "particle_stride": particle_frame_stride,
        "particle_render_stride": particle_render_stride,
        "workers": workers,
        "surface": str(surface) if surface else None,
        "particle_spacing_m": particle_spacing,
        "particle_diameter_m": (particle_spacing if particle_spacing is not None else None),
        "particle_radius_m": particle_radius_m,
        "default_series": "crm_animation",
        "series": {
            "crm_particles": series_name("crm_particles.pvd"),
            "excavator": series_name("excavator.pvd"),
            "crm_animation": series_name("crm_animation.pvd"),
            "combined": series_name("combined.pvd"),
            "fea": series_name("fea.pvd") if fea_valid else None,
            "with_fea": series_name("with_fea.pvd") if fea_valid else None,
            "fea_source": str(fea_source.resolve()) if fea_source.is_file() else None,
            "fea_summary": series_name("fea_summary.csv") if has_fea_summary else None,
            "fea_summary_source": (str(fea_summary_source.resolve())
                                   if has_fea_summary else None),
        },
        "has_particles": bool(crm_parts and crm_parts[0][0] == "crm_particles"),
        "has_excavator": bool(crm_parts and any(name == "excavator" for name, _ in crm_parts)),
        "has_fea_vtk_source": fea_source.is_file(),
        "has_fea_vtk": bool(fea_valid),
        "has_fea_summary": bool(has_fea_summary),
        "fea_3d_requested": bool(info["study_cfg"].get("fea_export_vtk")),
        "fea_3d_valid_frames": len(fea_entries) if fea_valid else 0,
        "fea_3d_last_t_s": fea_entries[-1][0] if fea_valid else None,
        "fea_valid": fea_valid,
        "fea_status": fea_status,
    }
    (out_dir / "export_manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"  manifest: {out_dir / 'export_manifest.json'}")
    print("Done. Open in ParaView, e.g.:")
    print(f"  paraview {combined}")
    print("  # CRM animation (one-way FEA history charts are opt-in):")
    print(f"  pvpython {ROOT / 'view_study_paraview.py'} {out_dir}")
    print("  # also show lightweight FEA history charts when summary.csv exists:")
    print(f"  pvpython {ROOT / 'view_study_paraview.py'} {out_dir} --show-fea-charts")
    print("  # suppress all FEA views:")
    print(f"  pvpython {ROOT / 'view_study_paraview.py'} {out_dir} --no-fea")
    print("  # 3D FEA stress overlay, when a multi-frame VTK series is available:")
    print(f"  pvpython {ROOT / 'view_study_paraview.py'} {out_dir} --with-fea")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
