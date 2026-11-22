#!/usr/bin/env python3
"""ParaView visualization script for a CRM/FEA study exported by export_study_vtk.py.

Recommended full ParaView GUI (pass the export dir via environment variable):
    QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=/path/to/study/paraview paraview --script=/path/to/view_study_paraview.py

pvpython render window (stays open until the render window is closed):
    pvpython /path/to/view_study_paraview.py /path/to/study/paraview
    # optional 3D FEA stress/deformation overlay, when a complete VTK series exists:
    pvpython /path/to/view_study_paraview.py /path/to/study/paraview --with-fea

By default this loads the CRM animation and does not display one-way FEA scalar
or deformation history charts. CRM series are separate pipeline sources so each
can have its own render settings:
  - crm_particles.pvd : particle point cloud over time, rendered as 3D sphere
        point sprites (glyph fallback) sized from the study's SPH radius and
        colored by state.
  - excavator.pvd     : rigid excavator/drum surface over time, rendered at 40%
                        opacity so it does not hide the FEA stress overlay.
The equivalent combined collection is crm_animation.pvd (also written as the
backwards-compatible combined.pvd); neither contains FEA data.

The fast one-way FEA path writes scalar summaries only.  Their XY histories are
not displayed by default.  Use --show-fea-charts (or PYCHRONO_FEA_CHARTS=1 with
``paraview --script=...``) to opt in to the peak von Mises stress/deformation
charts; they use a tiny derived table and do not require VTK frames.  --no-fea
still suppresses all FEA output.

The UI's "Export full 3D FEA stress animation" option writes a complete
per-frame VTU series with von Mises stress and displacement.  Load it with
--with-fea/--fea-only (or the GUI Tools toggle); it must pass validation against
export_manifest.json (or local validation without a manifest).  The raw
undeformed FEA grid is shown and colored by von Mises stress by default; the
Warp By Vector filter is created but hidden, so toggle it in the Pipeline
Browser to see the deformed shape.  A one-frame/t=0 FEA PVD from a cancelled run
is ignored, so it cannot obscure or freeze the excavation animation.

If no argument is given, the script tries ./paraview in the current directory.

A camera-orbit animation around the vertical Z axis is created by default.
Tune it with --orbit-speed (revolutions per simulation second),
--orbit-radius (0 auto-fits the excavator),
--orbit-inclination, --orbit-start-angle, and --orbit-keyframes; use
--no-camera-orbit to disable it.
The GUI launcher passes the same settings through PYCHRONO_ORBIT_* environment
variables.  Press Play in ParaView to run the camera orbit with the time series.
The viewer primes the camera track before the first render, so the first frame
is already framed; you do not need to step backwards and forwards to wake the
camera.  It also chooses a non-parallel view-up before any camera reset, which
avoids VTK's "Resetting view-up since view plane normal is parallel" warning
during GUI startup.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path


# Default render settings.  The excavator (PVDReader2 in a fresh ParaView
# session) is translucent so it does not hide the FEA stress overlay, and the
# displacement warp is available in the pipeline but starts hidden so the raw
# undeformed grid is what the user sees first.
EXCAVATOR_OPACITY = 0.4
FEA_WARP_VISIBLE = False


def _script_directory() -> Path:
    """Find this script's folder under python, pvpython and paraview --script.

    ParaView's ``--script=/path/script.py`` executes the file with ``exec()``
    and does not define ``__file__``.  Fall back to the script switch itself,
    the GUI-provided PYCHRONO_ROOT/PYCHRONO_SCRIPT_DIR, and finally the current
    directory, choosing the first candidate that actually contains the local
    ``fea_summary`` helper.
    """
    candidates: list[Path] = []
    if "__file__" in globals():
        candidates.append(Path(__file__).resolve().parent)
    for arg in sys.argv[1:]:
        if arg.startswith("--script="):
            candidates.append(Path(arg.split("=", 1)[1]).expanduser().resolve().parent)
    for name in ("PYCHRONO_SCRIPT_DIR", "PYCHRONO_ROOT"):
        value = os.environ.get(name)
        if value:
            candidates.append(Path(value).expanduser().resolve())
    candidates.append(Path.cwd().resolve())
    for candidate in candidates:
        if (candidate / "fea_summary.py").is_file():
            return candidate
    raise RuntimeError(
        "Cannot find fea_summary.py; searched: "
        + ", ".join(str(path) for path in candidates)
    )


# Local helper: derives a compact ParaView table from fea/summary.csv.  It uses
# only the standard library and is safe to import outside ParaView.
sys.path.insert(0, str(_script_directory()))
import fea_summary  # noqa: E402


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float | None) -> float | None:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int | None) -> int | None:
    value = os.environ.get(name)
    if value is None or not str(value).strip():
        return default
    try:
        return int(float(value))
    except ValueError:
        return default


def _representation_name(display) -> str:
    """Read a ParaView Representation property without its StringList quoting."""
    value = getattr(display, "Representation", "")
    if not isinstance(value, str):
        try:
            value = value[0]
        except Exception:
            pass
    return str(value).strip("'\"")


def _parse_args(argv: list[str]):
    # Prefer explicit environment variables (works with `paraview --script=...`).
    env_dir = os.environ.get("PYCHRONO_EXPORT_DIR") or os.environ.get("PYCHRONO_STUDY_DIR")
    # ParaView often passes its own args; user args may come after '--'.
    if "--" in argv:
        user_argv = argv[argv.index("--") + 1:]
    else:
        user_argv = argv[1:]
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("export_dir", nargs="?", default=None)
    ap.add_argument("--export-dir", dest="export_dir_opt", default=None)
    ap.add_argument("--warp-scale", type=float, default=1.0)
    ap.add_argument("--color-by", choices=("excavated", "inside_drum"), default="excavated")
    ap.add_argument("--particle-radius", type=float, default=None)
    ap.add_argument("--particle-style", choices=("spheres", "points"), default="spheres")
    ap.add_argument("--no-particles", action="store_true")
    ap.add_argument("--no-excavator", action="store_true")
    ap.add_argument("--no-fea", action="store_true",
                    help="do not load the FEA scalar summary charts or 3D FEA VTK series")
    ap.add_argument("--show-fea-charts", action="store_true",
                    default=_env_bool("PYCHRONO_FEA_CHARTS", False),
                    help="display one-way FEA scalar/deformation XY history charts "
                         "(off by default; PYCHRONO_FEA_CHARTS=1 also works with paraview --script)")
    ap.add_argument("--with-fea", action="store_true",
                    default=_env_bool("PYCHRONO_WITH_FEA", False),
                    help="also overlay a validated FEA VTK stress/deformation series on the CRM animation "
                         "(GUI Tools toggle sets PYCHRONO_WITH_FEA)")
    ap.add_argument("--fea-only", action="store_true",
                    help="load only FEA outputs (charts require --show-fea-charts; "
                         "3D FEA requires a validated VTK series)")
    ap.add_argument("--fea-series", default=None,
                    help="comma-separated summary columns to chart when --show-fea-charts is used "
                         "(default: peak von Mises stress and deformation)")
    ap.add_argument("--no-interact", action="store_true",
                    help="do not keep the pvpython render window open (batch setups only)")
    # Camera orbit keyframes.  Environment variables are used by the full
    # ParaView GUI launcher, where passing flags after --script is unreliable.
    ap.add_argument("--camera-orbit", dest="camera_orbit", action="store_true", default=None,
                    help="create a camera track orbiting the drum (default: on)")
    ap.add_argument("--no-camera-orbit", dest="camera_orbit", action="store_false",
                    help="do not create a camera orbit track")
    ap.add_argument("--orbit-speed", type=float,
                    default=_env_float("PYCHRONO_ORBIT_SPEED", 0.10),
                    help="camera revolutions per simulation second (default: 0.10)")
    ap.add_argument("--orbit-radius", type=float,
                    default=_env_float("PYCHRONO_ORBIT_RADIUS", 0.0),
                    help="orbit radius in meters around the vertical Z axis; 0 auto-fits the excavator bounds (default: 0)")
    ap.add_argument("--orbit-inclination", type=float,
                    default=_env_float("PYCHRONO_ORBIT_INCLINATION", 20.0),
                    help="camera elevation above the orbit plane in degrees (default: 20)")
    ap.add_argument("--orbit-start-angle", type=float,
                    default=_env_float("PYCHRONO_ORBIT_START_ANGLE", None),
                    help="starting azimuth in degrees; default continues the current camera azimuth")
    ap.add_argument("--orbit-keyframes", type=int,
                    default=_env_int("PYCHRONO_ORBIT_KEYFRAMES", 0),
                    help="number of camera keyframes; 0 chooses automatically (default: 0)")
    args, unknown = ap.parse_known_args(user_argv)
    if args.camera_orbit is None:
        args.camera_orbit = _env_bool("PYCHRONO_CAMERA_ORBIT", True)
    # In ParaView GUI script mode the environment variable is authoritative;
    # ParaView's own argv can otherwise be mistaken for a positional directory.
    in_paraview_gui = (_env_bool("PYCHRONO_PARAVIEW_GUI", False)
                       or "--script=" in " ".join(argv)
                       or "paraview" in Path(sys.executable).name.lower()
                       or "vtkpython" in Path(sys.executable).name.lower())
    if env_dir and in_paraview_gui:
        export_dir = env_dir
    else:
        export_dir = args.export_dir or args.export_dir_opt or env_dir
    if export_dir is None:
        # Heuristic: pick the last path-like existing argument (useful if ParaView
        # appended it in a nonstandard way, or user passed a study dir).
        candidates = []
        for token in reversed(unknown + user_argv):
            if token.startswith("-"):
                continue
            p = Path(token).expanduser()
            if p.exists() or p.name in {"paraview", "combined.pvd"} or str(token).endswith("paraview"):
                candidates.append(token)
        if candidates:
            export_dir = candidates[0]
    args.export_dir = export_dir
    return args


def find_pvd(export_dir: Path, name: str) -> Path | None:
    p = export_dir / name
    return p if p.is_file() else None


def _resolve_export_dir(path_like: str | None) -> Path | None:
    if not path_like:
        return None
    p = Path(path_like).expanduser()
    try:
        p = p.resolve()
    except Exception:
        p = Path(path_like).expanduser()
    if p.is_file() and p.name.endswith(".pvd"):
        p = p.parent
    if p.is_dir():
        if (p / "combined.pvd").is_file() or (p / "crm_particles.pvd").is_file() or (p / "excavator.pvd").is_file():
            return p
        if (p / "paraview").is_dir():
            return (p / "paraview").resolve()
        return p
    if (p.parent / "combined.pvd").is_file():
        return p.parent
    return p if p.exists() else None


def validate_fea_pvd_file(path: Path) -> tuple[bool, str]:
    """Sanity-check an FEA PVD before the GUI loads it.

    This is the authority for GUI loading, not the export manifest: an older
    manifest may mark a long partial run invalid even though its PVD/frames are
    usable.  Reject only structurally unusable series (no frames, a single
    t=0 frame, missing frame files, or non-monotonic timesteps).
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        return False, f"cannot parse {path}: {exc}"
    entries: list[tuple[float, Path]] = []
    for ds in root.findall(".//DataSet"):
        try:
            t = float(ds.attrib.get("timestep", "0"))
        except Exception:
            t = 0.0
        file = ds.attrib.get("file", "")
        if not file:
            continue
        frame = Path(file)
        if not frame.is_absolute():
            frame = path.parent / frame
        entries.append((t, frame))
    if not entries:
        return False, f"{path} has no DataSet entries"
    missing = [str(f) for _, f in entries if not f.is_file() or f.stat().st_size == 0]
    if missing:
        return False, f"{len(missing)} FEA frame(s) missing/empty"
    times = [t for t, _ in entries]
    if any(b < a - 1.0e-9 for a, b in zip(times, times[1:])):
        return False, "FEA PVD timesteps are not monotonic"
    if len(entries) < 2 or max(times) <= 0.0:
        return False, (f"incomplete FEA series ({len(entries)} frame(s), last t={max(times):g}s); "
                       "likely a cancelled run")
    return True, f"{len(entries)} frames to {max(times):g}s"


def find_valid_fea_pvd(export_dir: Path, manifest: dict) -> tuple[Path | None, str]:
    """Locate a usable FEA series, never falling back to arbitrary rglob hits.

    A stale manifest must not hide a real multi-frame PVD.  Older exporters
    marked partial runs invalid, but their frames are still viewable.  Validate
    the actual source files here and only reject structurally unusable series.
    """
    series = manifest.get("series") if isinstance(manifest.get("series"), dict) else {}
    manifest_note = ""
    if manifest.get("fea_valid") is False:
        status = manifest.get("fea_status") or "incomplete"
        manifest_note = f"manifest marked invalid ({status}); re-checked source"
    candidates: list[Path] = []
    for key in ("fea", "fea_source"):
        value = series.get(key)
        if value:
            candidates.append(Path(str(value)))
    candidates.extend([
        export_dir / "fea.pvd",
        export_dir.parent / "fea" / "vtk" / "fea.pvd",
    ])
    checked: set[Path] = set()
    notes: list[str] = []
    for cand in candidates:
        c = cand if cand.is_absolute() else (export_dir / cand)
        try:
            c = c.resolve()
        except Exception:
            pass
        if c in checked:
            continue
        checked.add(c)
        if not c.is_file():
            continue
        valid, reason = validate_fea_pvd_file(c)
        if valid:
            if manifest_note:
                reason = f"{reason} ({manifest_note})"
            return c, reason
        notes.append(f"{c}: {reason}")
    detail = "; ".join(notes) if notes else "no fea.pvd found in the export or study FEA directory"
    if manifest_note:
        detail = f"{manifest_note}; {detail}"
    return None, detail


def _fea_y_axis_title(series: list[str]) -> str:
    if any(name.startswith("max_von_mises") for name in series):
        return "von Mises stress (MPa)" if any("MPa" in name for name in series) else "von Mises stress (Pa)"
    return ", ".join(series)


def load_fea_summary_charts(export_dir: Path, manifest: dict, render_view,
                            series_requested: str | None = None):
    """Load lightweight XY charts of FEA scalar histories.

    This is the default lightweight FEA view when full 3D VTU export is off.
    It reads only ``fea/summary.csv`` (via a tiny derived table)
    and creates separate ParaView XY views, so no VTK frames or mesh copy are
    needed.  Stress/load histories and deformation are split into separate
    charts because their units and magnitudes differ by orders of magnitude.
    Returns ``(chart_views, status)``.
    """
    summary = fea_summary.ensure_fea_summary_csv(export_dir, manifest)
    if summary is None:
        return [], "no usable fea/summary.csv found"

    header, rows = fea_summary.read_summary_rows(summary)
    if not rows:
        return [], f"{summary} has no data rows"
    available = fea_summary.choose_fea_series(header, series_requested)
    if not available:
        return [], (f"{summary} has no requested series "
                    f"(available: {', '.join(header)})")

    try:
        from paraview.simple import (  # type: ignore
            AssignViewToLayout, CSVReader, CreateXYPlotView, GetLayout,
            Render, SetActiveView, Show, UpdatePipeline,
        )
    except Exception as exc:
        return [], f"ParaView XY chart API unavailable: {exc}"

    try:
        reader = CSVReader(FileName=[str(summary)])
        try:
            reader.HaveHeaders = 1
            reader.DetectNumericColumns = 1
        except Exception:
            pass
        UpdatePipeline(proxy=reader)

        layout = GetLayout()
        charts: list = []
        deformation = [name for name in available if name.startswith("max_deformation")]
        if series_requested:
            groups = [(available, "One-way FEA selected histories")]
        else:
            primary = [name for name in available if name not in deformation]
            groups = []
            if primary:
                groups.append((primary, "One-way FEA scalar history"))
            if deformation:
                groups.append((deformation, "One-way FEA deformation history"))

        for names, title in groups:
            chart = CreateXYPlotView()
            if layout is not None:
                try:
                    AssignViewToLayout(view=chart, layout=layout, hint=1)
                except Exception:
                    pass
            rep = Show(reader, chart)
            rep.UseIndexForXAxis = 0
            rep.XArrayName = "time_s" if "time_s" in header else header[0]
            rep.SeriesVisibility = names

            colors: list[str] = []
            for name in names:
                if name.startswith("max_von_mises") and "raw" not in name:
                    colors.extend([name, "0.89", "0.16", "0.18"])
                elif name.startswith("max_deformation"):
                    colors.extend([name, "0.13", "0.51", "0.88"])
            if colors:
                try:
                    rep.SeriesColor = colors
                except Exception:
                    pass

            chart.ChartTitle = title
            chart.BottomAxisTitle = "time (s)"
            chart.LeftAxisTitle = _fea_y_axis_title(names)
            chart.ShowLegend = 1
            try:
                Render(chart)
            except Exception:
                pass
            charts.append(chart)

        if render_view is not None:
            try:
                SetActiveView(render_view)
            except Exception:
                pass
        status = (f"{len(rows)} rows from {summary}; "
                  f"{len(charts)} chart(s): {', '.join(available)}")
        return charts, status
    except Exception as exc:
        return [], f"could not create FEA summary charts: {exc}"


def _orbit_basis():
    """Return the fixed vertical-axis orbit basis: (Z axis, X, Y)."""
    return (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)


def _dot(a, b):
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _set_nonparallel_view_up(camera) -> tuple[float, float, float]:
    """Give the camera a view-up axis that VTK's ResetCamera will accept.

    VTK warns ("Resetting view-up since view plane normal is parallel") and
    silently substitutes an arbitrary axis when the current view-up is nearly
    parallel to the view-plane normal.  ParaView can start a GUI view in such an
    orientation, so choose an orthogonal axis before calling ResetCamera().
    """
    try:
        up = tuple(float(v) for v in camera.GetViewUp())
        normal = tuple(float(v) for v in camera.GetViewPlaneNormal())
    except Exception:
        up, normal = (), ()
    # A zero-length view-up is just as unusable as a parallel one.
    if len(up) == 3 and math.sqrt(_dot(up, up)) > 1.0e-6:
        if abs(_dot(up, normal)) < 0.99:
            return up
    for candidate in ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0)):
        if abs(_dot(candidate, normal)) < 0.9:
            try:
                camera.SetViewUp(*candidate)
            except Exception:
                pass
            return candidate
    # Degenerate camera (for example a zero view-plane normal): any axis works.
    candidate = (0.0, 0.0, 1.0)
    try:
        camera.SetViewUp(*candidate)
    except Exception:
        pass
    return candidate


def _prime_animation_scene(scene, view=None) -> bool:
    """Force ParaView's camera cue to evaluate its first keyframe.

    With a normalized camera track the animation scene applies no camera at
    exactly the first timestep until it has advanced once.  The uninitialized
    camera sits at the origin, so the first frame renders as an empty (white)
    view.  Advancing one frame and returning to the first frame primes the cue;
    the user no longer has to step backwards and forwards manually.

    Rendering is suppressed during the prime: the intermediate camera update
    can otherwise make VTK log "Resetting view-up since view plane normal is
    parallel" while the cue interpolates from its uninitialized state.
    """
    if scene is None:
        return False
    suppress = None
    try:
        if view is not None:
            suppress = view.SuppressRendering
            view.SuppressRendering = True
        scene.GoToNext()
        scene.GoToFirst()
        return True
    except Exception:
        return False
    finally:
        if suppress is not None:
            try:
                view.SuppressRendering = suppress
            except Exception:
                pass


def _bounds_at_time(reader, time_s: float, fallback_center):
    """Read one frame's bounds and center, or return a fallback center."""
    if reader is None:
        return None, fallback_center
    try:
        from paraview.simple import UpdatePipeline  # type: ignore

        UpdatePipeline(time=float(time_s), proxy=reader)
        bounds = tuple(float(v) for v in reader.GetDataInformation().GetBounds())
        if len(bounds) == 6:
            center = ((bounds[0] + bounds[1]) / 2.0,
                      (bounds[2] + bounds[3]) / 2.0,
                      (bounds[4] + bounds[5]) / 2.0)
            return bounds, center
    except Exception:
        pass
    return None, fallback_center


def _orbit_extents(bounds, center, axis, u, v):
    """Return the excavator's half-extents (radial, axial) around ``center``."""
    if bounds is None:
        return None, None
    corners = [(bounds[0], bounds[2], bounds[4]), (bounds[0], bounds[2], bounds[5]),
               (bounds[0], bounds[3], bounds[4]), (bounds[0], bounds[3], bounds[5]),
               (bounds[1], bounds[2], bounds[4]), (bounds[1], bounds[2], bounds[5]),
               (bounds[1], bounds[3], bounds[4]), (bounds[1], bounds[3], bounds[5])]
    offsets = [tuple(c[i] - center[i] for i in range(3)) for c in corners]
    radial = max(max(abs(_dot(p, u)), abs(_dot(p, v))) for p in offsets)
    axial = max(abs(_dot(p, axis)) for p in offsets)
    return radial, axial


def _fit_orbit_radius(bounds, center, axis, u, v):
    """Radius that keeps the whole excavator inside the orbit around its axis."""
    radial, axial = _orbit_extents(bounds, center, axis, u, v)
    if radial is None:
        return 0.6
    return max(5.0 * radial, 2.4 * axial, 0.25)


def _lowered_orbit_center(bounds, center, axis, u, v):
    """Shift the orbit center down by one third of the excavator's radius."""
    radial, axial = _orbit_extents(bounds, center, axis, u, v)
    if radial is None:
        return center, 0.0
    excavator_radius = max(radial, axial)
    drop = excavator_radius / 3.0
    return tuple(center[i] - axis[i] * drop for i in range(3)), drop


def _install_camera_orbit(args, view, scene, excav_reader, times):
    """Add a normalized camera track orbiting the drum around the vertical Z axis.

    The excavator is sampled once at the first timestep to fit the radius; the
    camera track itself uses fixed keyframes so playback does not repeatedly
    force the large PVD reader to reload frames.
    """
    from paraview.simple import CameraKeyFrame, GetCameraTrack  # type: ignore

    if not times:
        return "no scene times available"
    t0, t1 = float(times[0]), float(times[-1])
    duration = max(0.0, t1 - t0)
    camera = view.GetActiveCamera()
    pos = tuple(float(v) for v in camera.GetPosition())
    axis, u, v = _orbit_basis()

    bounds, bounds_center = _bounds_at_time(
        excav_reader, t0, tuple(float(v) for v in camera.GetFocalPoint()))
    if args.orbit_radius and args.orbit_radius > 0.0:
        radius = float(args.orbit_radius)
        fit_note = "requested"
    else:
        radius = _fit_orbit_radius(bounds, bounds_center, axis, u, v)
        fit_note = "auto-fit"
    center, center_drop = _lowered_orbit_center(
        bounds, bounds_center, axis, u, v)

    off_u, off_v = _dot(pos, u) - _dot(center, u), _dot(pos, v) - _dot(center, v)
    start_angle = (float(args.orbit_start_angle) if args.orbit_start_angle is not None
                   else math.degrees(math.atan2(off_v, off_u)) if (off_u or off_v) else 0.0)
    inclination = max(-80.0, min(80.0, float(args.orbit_inclination)))
    speed = float(args.orbit_speed)  # revolutions per simulation second
    if args.orbit_keyframes and args.orbit_keyframes > 0:
        n = max(2, int(args.orbit_keyframes))
    else:
        n = int(max(24, min(360, math.ceil(max(abs(speed) * duration, 1.0) * 24))))
    inc = math.radians(inclination)
    radial = radius * math.cos(inc)
    axial = radius * math.sin(inc)
    view_angle = float(camera.GetViewAngle())

    frames = []
    for i in range(n + 1):
        frac = i / n
        t = t0 + frac * duration
        azimuth = math.radians(start_angle + 360.0 * speed * (t - t0))
        ca, sa = math.cos(azimuth), math.sin(azimuth)
        camera_pos = tuple(center[j] + radial * (ca * u[j] + sa * v[j]) + axial * axis[j]
                           for j in range(3))
        key = CameraKeyFrame()
        key.KeyTime = frac
        key.Position = list(camera_pos)
        key.FocalPoint = list(center)
        key.ViewUp = list(axis)
        key.ViewAngle = view_angle
        frames.append(key)

    cue = GetCameraTrack(view=view)
    cue.Mode = 'Interpolate Camera'
    cue.TimeMode = 'Normalized'
    cue.Interpolation = 'Linear'
    cue.KeyFrames = frames
    camera.SetFocalPoint(*center)
    camera.SetPosition(*frames[0].Position)
    camera.SetViewUp(*axis)
    # Prime the cue so the first frame is already framed instead of blank.
    _prime_animation_scene(scene, view)
    return (f"{n + 1} keyframes, {speed:g} rev/s, {fit_note} radius {radius:g} m, "
            f"inclination {inclination:g} deg, axis Z, "
            f"center lowered {center_drop:g} m")


def main():
    args = _parse_args(sys.argv)
    export_dir = _resolve_export_dir(args.export_dir or "paraview")
    if export_dir is None or not export_dir.is_dir():
        tried = args.export_dir or "paraview"
        print(f"[view_study_paraview] export directory not found: {tried}")
        print("Usage:")
        print("  pvpython view_study_paraview.py /path/to/study/paraview")
        print("  QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=/path/to/study/paraview paraview --script=view_study_paraview.py")
        print("Run export_study_vtk.py first if the study has no paraview/ export yet.")
        return 1

    try:
        from paraview.simple import (  # type: ignore
            PVDReader, Show, Hide, ColorBy, GetDisplayProperties, GetRepresentation,
            GetActiveViewOrCreate,
            GetColorTransferFunction, GetOpacityTransferFunction, GetScalarBar, ResetCamera,
            Render, SetActiveSource, WarpByVector, Glyph, Sphere, _DisableFirstRenderCameraReset,
            GetAnimationScene, UpdatePipeline,
        )
    except Exception as exc:
        print(f"[view_study_paraview] ParaView Python modules not available: {exc}")
        print("  Run with: pvpython view_study_paraview.py <export_dir>")
        print("  Or in ParaView GUI: QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=<export_dir> paraview --script=view_study_paraview.py")
        return 1

    manifest = {}
    mpath = export_dir / "export_manifest.json"
    if mpath.is_file():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except Exception:
            manifest = {}

    print(f"[view_study_paraview] export dir: {export_dir}")
    print(f"[view_study_paraview] manifest: {manifest or '(none)'}")

    include_fea = not args.no_fea
    want_3d_fea = include_fea and (args.with_fea or args.fea_only)
    want_crm = not args.fea_only
    if args.no_fea:
        active_series = "CRM animation only (FEA excluded)"
    elif args.fea_only:
        active_series = ("FEA only (3D FEA when available; charts only with --show-fea-charts)")
    elif want_3d_fea:
        active_series = ("CRM animation + 3D FEA stress overlay "
                         "(charts only with --show-fea-charts)")
    else:
        active_series = "CRM animation (FEA scalar charts off by default)"
    print(f"[view_study_paraview] active series: {active_series}")
    print("  PVD files: crm_particles.pvd + excavator.pvd = crm_animation.pvd (aka combined.pvd); "
          "3D FEA loads a structurally usable multi-frame FEA PVD, including usable partial runs")

    # disable auto camera reset on first show so multiple sources compose
    try:
        _DisableFirstRenderCameraReset()
    except Exception:
        pass

    view = GetActiveViewOrCreate('RenderView')
    try:
        view.Background = [0.98, 0.98, 0.98]
        view.UseColorPaletteForBackground = 0
    except Exception:
        pass

    sources_shown = []
    excav_reader = None

    # --- CRM particles ---
    particles_pvd = find_pvd(export_dir, "crm_particles.pvd")
    if particles_pvd and not args.no_particles and want_crm:
        print(f"  loading particles: {particles_pvd}")
        particles = PVDReader(FileName=str(particles_pvd))
        particles.PointArrays = ['inside_drum', 'excavated']
        try:
            UpdatePipeline(proxy=particles)
        except Exception:
            pass

        # Particle radius: prefer explicit override, else manifest radius/spacing.
        p_diam = manifest.get("particle_diameter_m") or manifest.get("particle_spacing_m")
        p_radius = manifest.get("particle_radius_m")
        try:
            if p_radius is not None:
                default_radius = float(p_radius)
            elif p_diam is not None:
                default_radius = float(p_diam) / 2.0
            else:
                default_radius = 0.001  # half the 2 mm default SPH spacing
        except Exception:
            default_radius = 0.001
        particle_radius = float(args.particle_radius) if args.particle_radius is not None else default_radius
        if particle_radius <= 0.0:
            particle_radius = default_radius

        particle_display_src = particles
        disp = Show(particles, view)
        # These are large analysis overlays, not interactive geometry.  Keep
        # them out of the GUI picker so it does not build expensive OBB trees.
        try:
            disp.Pickable = 0
        except Exception:
            pass

        if args.particle_style == "spheres":
            # Prefer GPU point sprites.  A CRM frame can contain hundreds of
            # thousands of points; a sphere glyph expands each one into many
            # cells, which is slow and can make ParaView's GUI picker emit
            # vtkMath::Jacobi warnings while building OBBs.  Try the actual
            # representation property first: PointGaussian is not importable
            # as a proxy in every ParaView build even when the representation
            # itself is supported.
            try:
                disp.Representation = 'Point Gaussian'
                disp.GaussianRadius = particle_radius
                try:
                    disp.ShaderPreset = 'Sphere'
                except Exception:
                    pass
                if _representation_name(disp) != 'Point Gaussian':
                    raise RuntimeError('representation was not accepted')
                print(f"  particles: rendered as 3D spheres via point sprites "
                      f"(radius={particle_radius:g} m)")
            except Exception as e:
                print(f"  particles: point sprites unavailable ({e}); using sphere glyphs")
                try:
                    sphere = Sphere()
                    sphere.Radius = particle_radius
                    # Small fallback glyphs do not need high tessellation.
                    for attr, value in (("ThetaResolution", 6), ("PhiResolution", 4)):
                        try:
                            setattr(sphere, attr, value)
                        except Exception:
                            pass
                    glyph = Glyph(Input=particles, GlyphType=sphere)
                    glyph.GlyphMode = 'All Points'
                    for attr, value in (('ScaleMode', 'data_scaling_off'), ('ScaleFactor', 1.0)):
                        try:
                            setattr(glyph, attr, value)
                        except Exception:
                            pass
                    try:
                        glyph.ScaleArray = ['POINTS', 'No scale array']
                    except Exception:
                        pass
                    try:
                        glyph.Orient = 0
                    except Exception:
                        pass
                    try:
                        UpdatePipeline(proxy=glyph)
                    except Exception:
                        pass
                    glyph_disp = Show(glyph, view)
                    try:
                        glyph_disp.Pickable = 0
                    except Exception:
                        pass
                    try:
                        glyph_disp.Representation = 'Surface'
                    except Exception:
                        pass
                    particle_display_src = glyph
                    disp = glyph_disp
                    print(f"  particles: rendered as sphere glyphs (radius={particle_radius:g} m)")
                except Exception as ge:
                    print(f"  particles: sphere glyph unavailable ({ge}); using points")
                    particle_display_src = particles
                    disp = Show(particles, view)
                    try:
                        disp.Pickable = 0
                    except Exception:
                        pass
                    try:
                        disp.Representation = 'Points'
                        disp.PointSize = 4.0
                    except Exception:
                        pass
        else:
            try:
                disp.Representation = 'Points'
                disp.PointSize = 4.0
                print("  particles: rendered as points (size 4 px)")
            except Exception:
                pass

        array_name = args.color_by
        try:
            ColorBy(disp, ('POINTS', array_name))
            lut = GetColorTransferFunction(array_name)
            try:
                lut.ApplyPreset('Cool to Warm', True)
            except Exception:
                pass
            try:
                disp.RescaleTransferFunctionToDataRange(True, False)
            except Exception:
                pass
            try:
                disp.RescaleTransferFunctionToDataRange(False, True)
            except Exception:
                pass
            try:
                disp.SetScalarBarVisibility(view, True)
            except Exception:
                pass
        except Exception as e:
            print(f"  (particles color setup note: {e})")
        try:
            if particle_display_src is not particles:
                Hide(particles, view)
        except Exception:
            pass
        SetActiveSource(particle_display_src)
        sources_shown.append(('particles', particle_display_src))

    # --- Excavator/drum ---
    exc_pvd = find_pvd(export_dir, "excavator.pvd")
    if exc_pvd and not args.no_excavator and want_crm:
        print(f"  loading excavator: {exc_pvd}")
        excav = PVDReader(FileName=str(exc_pvd))
        excav_reader = excav
        try:
            UpdatePipeline(proxy=excav)
        except Exception:
            pass
        disp = Show(excav, view)
        try:
            disp.Pickable = 0
        except Exception:
            pass
        try:
            disp.Representation = 'Surface'
            disp.Opacity = EXCAVATOR_OPACITY
            # neutral color to distinguish from particles/FEA
            disp.AmbientColor = [0.7, 0.7, 0.75]
            disp.DiffuseColor = [0.7, 0.7, 0.75]
            ColorBy(disp, None)
        except Exception:
            pass
        SetActiveSource(excav)
        sources_shown.append(('excavator', excav))

    # --- FEA scalar summary charts (opt-in; never shown for the default script view) ---
    fea_charts: list = []
    fea_summary_status = "FEA excluded by --no-fea"
    if args.no_fea:
        fea_summary_status = "FEA excluded by --no-fea"
    elif not args.show_fea_charts:
        fea_summary_status = "disabled by default (use --show-fea-charts or PYCHRONO_FEA_CHARTS=1)"
    if include_fea and args.show_fea_charts:
        fea_charts, fea_summary_status = load_fea_summary_charts(
            export_dir, manifest, view, args.fea_series)
        if fea_charts:
            print(f"  loading FEA summary chart(s) ({fea_summary_status})")
        else:
            print(f"  FEA summary chart: not loaded ({fea_summary_status})")

    # --- Full 3D FEA deformation/stress VTK series, opt-in and validated ---
    fea_pvd = None
    fea_status = ""
    if want_3d_fea:
        fea_pvd, fea_status = find_valid_fea_pvd(export_dir, manifest)
    if fea_pvd is not None:
        print(f"  loading FEA ({fea_status}): {fea_pvd}")
        fea = PVDReader(FileName=str(fea_pvd))
        try:
            UpdatePipeline(proxy=fea)
        except Exception:
            pass
        # Show the raw undeformed grid by default.  The warp filter is still
        # created so it can be enabled in the Pipeline Browser, but hiding it
        # avoids making a deformed overlay the first thing the user sees.
        fea_source = fea
        disp_source = fea
        warp = None
        try:
            warp = WarpByVector(Input=fea)
            try:
                warp.Vectors = ['POINTS', 'Displacement']
            except Exception:
                pass
            warp.ScaleFactor = float(args.warp_scale)
            try:
                UpdatePipeline(proxy=warp)
            except Exception:
                pass
            if FEA_WARP_VISIBLE:
                disp_source = warp
                fea_source = warp
                print(f"  FEA: Warp By Vector active (scale={args.warp_scale}). "
                      "Increase --warp-scale to exaggerate elastic deformation.")
            else:
                print(f"  FEA: Warp By Vector available but hidden by default "
                      f"(scale={args.warp_scale}); toggle it in the Pipeline Browser "
                      "to see the deformed grid.")
        except Exception as e:
            print(f"  (FEA warp unavailable, showing undeformed grid: {e})")
            disp_source = fea
        disp = Show(disp_source, view)
        try:
            disp.Representation = 'Surface With Edges'
            disp.Opacity = 1.0
            # Avoid a flat solid that hides stress coloring intent.
            disp.ColorArrayName = None
        except Exception:
            pass
        # color by von Mises stress if present (avoid default vtkBlockColors)
        colored = False
        stress_candidates = [
            ('CELLS', 'von_mises_MPa'),
            ('CELLS', 'von_mises_pa'),
            ('POINTS', 'DisplacementMagnitude'),
            ('POINTS', 'Deformation_mm'),
        ]
        for field_type, name in stress_candidates:
            try:
                ColorBy(disp, (field_type, name))
                lut = GetColorTransferFunction(name)
                try:
                    lut.ApplyPreset('Cool to Warm', True)
                except Exception:
                    try:
                        lut.ApplyPreset('Rainbow Uniform', True)
                    except Exception:
                        pass
                for use_timestep in (True, False):
                    try:
                        disp.RescaleTransferFunctionToDataRange(False, use_timestep)
                        break
                    except Exception:
                        continue
                try:
                    disp.SetScalarBarVisibility(view, True)
                    sb = GetScalarBar(lut, view)
                    if sb is not None and 'MPa' in name:
                        sb.Title = 'von Mises stress (MPa)'
                    elif sb is not None and name == 'von_mises_pa':
                        sb.Title = 'von Mises stress (Pa)'
                    elif sb is not None and 'Displacement' in name:
                        sb.Title = 'Displacement magnitude (m)'
                except Exception:
                    pass
                colored = True
                print(f"  FEA colored by {field_type} '{name}' (von Mises/deformation view).")
                break
            except Exception:
                continue
        if not colored:
            try:
                ColorBy(disp, None)
            except Exception:
                pass
            print("  FEA: no von_mises/displacement array found to color by (showing solid). "
                  "If you see 'vtkBlockColors', select von_mises_MPa/von_mises_pa in the Coloring menu.")
        # Keep exactly one FEA grid visible by default: the raw reader is shown
        # and the warp filter starts hidden.  If FEA_WARP_VISIBLE is enabled,
        # reverse that so only the deformed grid is shown.
        try:
            if warp is not None and not FEA_WARP_VISIBLE:
                # Hide() does not create a representation for a source that
                # has never been shown, so set the display property directly.
                GetRepresentation(warp, view).Visibility = 0
            elif disp_source is not fea:
                GetRepresentation(fea, view).Visibility = 0
        except Exception:
            pass
        SetActiveSource(disp_source)
        sources_shown.append(('fea', disp_source))
    elif want_3d_fea:
        print(f"  FEA VTK: not loaded ({fea_status}). Enable 'Export full 3D FEA stress animation' "
              "in the UI and rerun FEA, or use an existing structurally usable multi-frame "
              "fea.pvd; scalar charts are opt-in.")
    elif include_fea:
        print("  FEA VTK: excluded. Use --with-fea (or the GUI Tools toggle) to overlay a "
              "structurally usable 3D FEA stress series; scalar charts are opt-in and do not "
              "need VTK frames.")

    if not sources_shown and not fea_charts:
        print("[view_study_paraview] nothing loaded. Expected crm_particles.pvd/excavator.pvd, "
              "or fea/summary.csv in", export_dir)
        return 1

    try:
        # Avoid VTK's "Resetting view-up since view plane normal is parallel"
        # warning when the GUI starts from a top-down or otherwise degenerate
        # camera orientation.
        _set_nonparallel_view_up(view.GetActiveCamera())
        ResetCamera(view)
        view.ResetCamera()
    except Exception:
        pass

    # sync animation to timesteps
    scene = None
    try:
        scene = GetAnimationScene()
        scene.PlayMode = 'Sequence'
        scene.UpdateAnimationUsingDataTimeSteps()
    except Exception:
        pass

    if args.camera_orbit:
        times = []
        try:
            if scene is not None:
                times = [float(v) for v in scene.TimeKeeper.TimestepValues]
        except Exception:
            times = []
        if not times and excav_reader is not None:
            try:
                times = [float(v) for v in excav_reader.TimestepValues]
            except Exception:
                times = []
        try:
            status = _install_camera_orbit(args, view, scene, excav_reader, times)
            print(f"[view_study_paraview] camera orbit: {status}")
            print("  Press Play in the Animation/Time controls to rotate around the drum while excavation advances.")
        except Exception as exc:
            print(f"[view_study_paraview] camera orbit unavailable: {exc}")

    try:
        Render()
    except Exception:
        pass

    print("[view_study_paraview] Scene ready.")
    print(f"  - Active series: {active_series}")
    print("  - Particles: non-FEA source rendered as 3D spheres (toggle/scale in Pipeline Browser if needed).")
    print("  - Excavator: non-FEA rigid surface source with its own render settings; poses come from CRM summary.")
    if fea_charts:
        print("  - FEA: scalar XY chart(s) in separate view(s) (peak von Mises stress/load history and "
              "deformation vs time). Use --fea-series to choose other columns; --no-fea suppresses them.")
    elif args.no_fea:
        print("  - FEA: excluded by --no-fea.")
    elif not args.show_fea_charts:
        print("  - FEA: scalar/deformation history charts are not displayed "
              "(--show-fea-charts opts in).")
    else:
        print(f"  - FEA: no scalar summary available ({fea_summary_status}).")
    if fea_pvd is not None:
        print("  - FEA 3D VTK: validated stress/deformation series, colored by von Mises stress on the "
              "undeformed grid by default. The excavator is translucent; toggle Warp By Vector in the "
              "Pipeline Browser to show deformation and adjust its 'Scale Factor'.")
    elif want_3d_fea:
        print("  - FEA 3D VTK: no valid series loaded; the non-FEA animation remains available "
              "(scalar charts are opt-in).")
    print("  - Use time controls to animate; if particles obscure geometry, adjust opacity/visibility per source.")

    # pvpython exits as soon as this script returns unless an interactive render
    # loop is started.  The full ParaView GUI (paraview --script=...) stays open
    # by itself, so only do this for pvpython.
    if not args.no_interact and "pvpython" in Path(sys.executable).name.lower():
        print("[view_study_paraview] Opening interactive render window; close it to exit.")
        try:
            from paraview.simple import Interact
            Interact(view)
        except Exception as exc:
            print(f"[view_study_paraview] Could not start interactive loop: {exc}")
            print("  Use the full GUI instead: QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=<dir> paraview --script=view_study_paraview.py")
    return 0


if __name__ == "__main__":
    in_paraview_gui = (_env_bool("PYCHRONO_PARAVIEW_GUI", False)
                       or "--script=" in " ".join(sys.argv)
                       or "paraview" in Path(sys.executable).name.lower()
                       or "vtkpython" in Path(sys.executable).name.lower())
    if in_paraview_gui:
        # Running Render()/Glyph setup while ParaView is still starting can
        # segfault on some builds.  Defer until the Qt event loop is up.
        def _setup_scene():
            try:
                main()
            except BaseException:
                import traceback
                traceback.print_exc()

        globals()["_delayed_viewer_setup"] = _setup_scene
        try:
            from PySide6.QtCore import QTimer  # type: ignore
            QTimer.singleShot(1000, _setup_scene)
        except Exception:
            _setup_scene()
    else:
        sys.exit(main() or 0)
