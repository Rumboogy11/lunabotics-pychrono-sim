#!/usr/bin/env python3
"""ParaView visualization script for a CRM/FEA study exported by export_study_vtk.py.

Recommended full ParaView GUI (pass the export dir via environment variable):
    QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=/path/to/study/paraview paraview --script=/path/to/view_study_paraview.py

pvpython render window (stays open until the render window is closed):
    pvpython /path/to/view_study_paraview.py /path/to/study/paraview
    # add the validated FEA stress/deformation overlay when available:
    pvpython /path/to/view_study_paraview.py /path/to/study/paraview --with-fea

By default this loads the *non-FEA* CRM animation, as separate pipeline sources
so each can have its own render settings:
  - crm_particles.pvd : particle point cloud over time, rendered as 3D spheres
        sized from the study's SPH spacing/radius and colored by state.
  - excavator.pvd     : rigid excavator/drum surface over time.
The equivalent combined collection is crm_animation.pvd (also written as the
backwards-compatible combined.pvd); neither contains FEA data.

FEA is opt-in via --with-fea/--fea-only and is loaded only when its PVD is a
valid, complete time series according to export_manifest.json (or, without a
manifest, after local validation).  A one-frame/t=0 FEA PVD from a cancelled run
is ignored, so it cannot obscure or freeze the excavation animation.

If no argument is given, the script tries ./paraview in the current directory.

A camera-orbit animation is created by default.  Tune it with --orbit-speed
(revolutions per simulation second), --orbit-radius, --orbit-inclination,
--orbit-start-angle, and --orbit-keyframes; use --no-camera-orbit to disable it.
The GUI launcher passes the same settings through PYCHRONO_ORBIT_* environment
variables.  Press Play in ParaView to run the camera orbit with the time series.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path


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
                    help="never load FEA (FEA is already excluded by default)")
    ap.add_argument("--with-fea", action="store_true",
                    help="also overlay a validated FEA series on the non-FEA CRM animation")
    ap.add_argument("--fea-only", action="store_true",
                    help="load only the validated FEA series (stress/deformation)")
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
                    help="horizontal orbit radius in meters; 0 uses the current horizontal camera distance (default: 0)")
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
    in_paraview_gui = ("--script=" in " ".join(argv)
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
    """Local sanity check used when no export manifest is available.

    The FEA writer updates fea.pvd incrementally, so a cancelled run can leave
    a valid-looking PVD with a single t=0 frame.  Require more than one frame
    with a positive final time and existing frame files.
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
    if len(entries) < 2 or max(times) <= 0.0:
        return False, (f"incomplete FEA series ({len(entries)} frame(s), last t={max(times):g}s); "
                       "likely a cancelled run")
    return True, f"{len(entries)} frames to {max(times):g}s"


def find_valid_fea_pvd(export_dir: Path, manifest: dict) -> tuple[Path | None, str]:
    """Locate a validated FEA series, never falling back to arbitrary rglob hits."""
    series = manifest.get("series") if isinstance(manifest.get("series"), dict) else {}
    if manifest.get("fea_valid") is False:
        return None, (f"manifest marks FEA export invalid: {manifest.get('fea_status', 'incomplete')}")
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
            return c, reason
        notes.append(f"{c}: {reason}")
    if notes:
        return None, "; ".join(notes)
    return None, "no fea.pvd found in the export or study FEA directory"


def _center_at_time(reader, time_s: float, fallback: tuple[float, float, float]):
    """Return the center of a time-varying reader's bounds at ``time_s``."""
    if reader is None:
        return fallback
    try:
        from paraview.simple import UpdatePipeline  # type: ignore

        UpdatePipeline(time=float(time_s), proxy=reader)
        bounds = reader.GetDataInformation().GetBounds()
        if bounds and len(bounds) == 6:
            return ((float(bounds[0]) + float(bounds[1])) / 2.0,
                    (float(bounds[2]) + float(bounds[3])) / 2.0,
                    (float(bounds[4]) + float(bounds[5])) / 2.0)
    except Exception:
        pass
    return fallback


def _install_camera_orbit(args, view, scene, excav_reader, times):
    """Add a normalized camera track that orbits the excavator while time plays."""
    from paraview.simple import CameraKeyFrame, GetCameraTrack  # type: ignore

    if not times:
        return "no scene times available"
    t0, t1 = float(times[0]), float(times[-1])
    duration = max(0.0, t1 - t0)
    camera = view.GetActiveCamera()
    pos = tuple(float(v) for v in camera.GetPosition())
    base_center = _center_at_time(excav_reader, t0,
                                  tuple(float(v) for v in camera.GetFocalPoint()))
    dx, dy = pos[0] - base_center[0], pos[1] - base_center[1]
    radius = float(args.orbit_radius) if args.orbit_radius and args.orbit_radius > 0.0 else math.hypot(dx, dy)
    if radius <= 0.0:
        radius = math.dist(pos, base_center) or 0.5
    start_angle = (float(args.orbit_start_angle) if args.orbit_start_angle is not None
                   else math.degrees(math.atan2(dy, dx)))
    inclination = max(-80.0, min(80.0, float(args.orbit_inclination)))
    speed = float(args.orbit_speed)  # revolutions per simulation second
    if args.orbit_keyframes and args.orbit_keyframes > 0:
        n = max(2, int(args.orbit_keyframes))
    else:
        n = int(max(24, min(360, math.ceil(max(abs(speed) * duration, 1.0) * 24))))
    inc = math.radians(inclination)
    rxy = radius * math.cos(inc)
    zoff = radius * math.sin(inc)
    view_angle = float(camera.GetViewAngle())

    frames = []
    for i in range(n + 1):
        frac = i / n
        t = t0 + frac * duration
        center = _center_at_time(excav_reader, t, base_center) if i else base_center
        azimuth = math.radians(start_angle + 360.0 * speed * (t - t0))
        camera_pos = (center[0] + rxy * math.cos(azimuth),
                      center[1] + rxy * math.sin(azimuth),
                      center[2] + zoff)
        key = CameraKeyFrame()
        key.KeyTime = frac
        key.Position = list(camera_pos)
        key.FocalPoint = list(center)
        key.ViewUp = [0.0, 0.0, 1.0]
        key.ViewAngle = view_angle
        frames.append(key)

    cue = GetCameraTrack(view=view)
    cue.Mode = 'Interpolate Camera'
    cue.TimeMode = 'Normalized'
    cue.Interpolation = 'Linear'
    cue.KeyFrames = frames
    camera.SetFocalPoint(*base_center)
    camera.SetPosition(*frames[0].Position)
    camera.SetViewUp(0.0, 0.0, 1.0)
    return (f"{n + 1} keyframes, {speed:g} rev/s, radius {radius:g} m, "
            f"inclination {inclination:g} deg")


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
            PVDReader, Show, Hide, ColorBy, GetDisplayProperties, GetActiveViewOrCreate,
            GetColorTransferFunction, GetOpacityTransferFunction, GetScalarBar, ResetCamera,
            Render, SetActiveSource, WarpByVector, Glyph, Sphere, _DisableFirstRenderCameraReset,
            GetAnimationScene, UpdatePipeline,
        )
        try:
            from paraview.simple import PointGaussian  # noqa
            has_point_gaussian = True
        except Exception:
            has_point_gaussian = False
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

    want_fea = (args.with_fea or args.fea_only) and not args.no_fea
    want_crm = not args.fea_only
    if args.fea_only:
        active_series = "FEA only (validated series)"
    elif want_fea:
        active_series = "CRM animation (non-FEA) + validated FEA overlay"
    else:
        active_series = "CRM animation (non-FEA; FEA excluded)"
    print(f"[view_study_paraview] active series: {active_series}")
    print("  PVD files: crm_particles.pvd + excavator.pvd = crm_animation.pvd (aka combined.pvd); "
          "fea.pvd/with_fea.pvd only when FEA export is valid")

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
        if args.particle_style == "spheres":
            try:
                sphere = Sphere()
                sphere.Radius = particle_radius
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
                particle_display_src = glyph
                print(f"  particles: rendered as 3D spheres (radius={particle_radius:g} m)")
            except Exception as e:
                print(f"  particles: sphere glyph unavailable ({e}); falling back to point display")
                particle_display_src = particles

        disp = Show(particle_display_src, view)
        if particle_display_src is particles:
            try:
                if args.particle_style == "points":
                    disp.Representation = 'Points'
                    disp.PointSize = 4.0
                elif has_point_gaussian:
                    disp.Representation = 'Point Gaussian'
                    disp.GaussianRadius = particle_radius
                    try:
                        disp.ShaderPreset = 'Sphere'
                    except Exception:
                        pass
                else:
                    disp.Representation = 'Points'
                    disp.PointSize = 4.0
            except Exception:
                try:
                    disp.Representation = 'Surface'
                except Exception:
                    pass
        else:
            try:
                disp.Representation = 'Surface'
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
            disp.Representation = 'Surface'
            disp.Opacity = 0.85
            # neutral color to distinguish from particles/FEA
            disp.AmbientColor = [0.7, 0.7, 0.75]
            disp.DiffuseColor = [0.7, 0.7, 0.75]
            ColorBy(disp, None)
        except Exception:
            pass
        SetActiveSource(excav)
        sources_shown.append(('excavator', excav))

    # --- FEA (stress + deformation), opt-in and validated ---
    fea_pvd = None
    fea_status = ""
    if want_fea:
        fea_pvd, fea_status = find_valid_fea_pvd(export_dir, manifest)
    if fea_pvd is not None:
        print(f"  loading FEA ({fea_status}): {fea_pvd}")
        fea = PVDReader(FileName=str(fea_pvd))
        try:
            UpdatePipeline(proxy=fea)
        except Exception:
            pass
        # Prefer showing deformed shape if Displacement exists
        fea_source = fea
        disp_source = fea
        try:
            # Try to warp by displacement for visible deformation
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
            disp_source = warp
            fea_source = warp
            print(f"  FEA: Warp By Vector active (scale={args.warp_scale}). "
                  "Increase --warp-scale to exaggerate elastic deformation.")
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
        # hide raw grid if warped
        if disp_source is not fea:
            try:
                Hide(fea, view)
            except Exception:
                pass
        SetActiveSource(disp_source)
        sources_shown.append(('fea', disp_source))
    elif want_fea:
        print(f"  FEA: not loaded ({fea_status}). To view 3D stress/deformation, rerun one-way FEA "
              "to completion with --export-vtk, then re-run export_study_vtk.py.")
    elif not args.no_fea:
        print("  FEA: excluded from the default non-FEA series. Use --with-fea to overlay a "
              "validated FEA export (requires a complete FEA VTK run).")

    if not sources_shown:
        print("[view_study_paraview] nothing loaded. Expected crm_particles.pvd/excavator.pvd/fea.pvd in", export_dir)
        return 1

    try:
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
    if fea_pvd is not None:
        print("  - FEA: validated overlay, colored by von Mises stress and warped by displacement by default. "
              "Adjust warp 'Scale Factor' to exaggerate deformation.")
    elif want_fea:
        print("  - FEA: no valid series loaded; the non-FEA animation remains available without FEA.")
    else:
        print("  - FEA: excluded by default. Use --with-fea after a complete FEA VTK run and re-export.")
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
    in_paraview_gui = ("--script=" in " ".join(sys.argv)
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
