#!/usr/bin/env -S /mnt/P3/Programs/Linux/pychrono/chrono-env/bin/python
"""ParaView visualization script for a CRM/FEA study exported by export_study_vtk.py.

Recommended full ParaView GUI (pass the export dir via environment variable):
    QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=/path/to/study/paraview paraview --script=/path/to/view_study_paraview.py

pvpython render window (stays open until the render window is closed):
    pvpython /path/to/view_study_paraview.py /path/to/study/paraview

It loads, when available (as separate pipeline sources so each can have its own
render settings):
  - crm_particles.pvd : particle point cloud over time, rendered as 3D spheres
        sized from the study's SPH spacing/radius and colored by state.
  - excavator.pvd     : rigid excavator/drum surface over time.
  - fea.pvd/fea/vtk/fea.pvd : one-way FEA mesh over time, colored by von Mises
        stress (von_mises_MPa) and warped by Displacement when available.

All sources share study time steps where possible so they can animate together.
If no argument is given, the script tries ./paraview in the current directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


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
    ap.add_argument("--no-fea", action="store_true")
    ap.add_argument("--no-interact", action="store_true",
                    help="do not keep the pvpython render window open (batch setups only)")
    args, unknown = ap.parse_known_args(user_argv)
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
            GetColorTransferFunction, GetOpacityTransferFunction, ResetCamera,
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

    # --- CRM particles ---
    particles_pvd = find_pvd(export_dir, "crm_particles.pvd")
    if particles_pvd and not args.no_particles:
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
    if exc_pvd and not args.no_excavator:
        print(f"  loading excavator: {exc_pvd}")
        excav = PVDReader(FileName=str(exc_pvd))
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

    # --- FEA (stress + deformation) ---
    fea_pvd = None
    manifest_fea = manifest.get("series", {}).get("fea") if isinstance(manifest.get("series"), dict) else None
    candidates = []
    if manifest_fea:
        candidates.append(Path(str(manifest_fea)))
    candidates.extend([
        export_dir / "fea.pvd",
        export_dir / "fea" / "fea.pvd",
        export_dir.parent / "fea" / "vtk" / "fea.pvd",
        export_dir.parent / "vtk" / "fea.pvd",
    ])
    for cand in candidates:
        try:
            c = cand if cand.is_absolute() else (export_dir / cand)
            c = c.resolve()
        except Exception:
            c = cand
        if c.is_file():
            fea_pvd = c
            break
    if fea_pvd is None:
        for cand in export_dir.rglob("fea.pvd"):
            fea_pvd = cand
            break
        if fea_pvd is None and export_dir.parent.is_dir():
            for cand in export_dir.parent.rglob("fea.pvd"):
                fea_pvd = cand
                break
    if fea_pvd and not args.no_fea:
        print(f"  loading FEA: {fea_pvd}")
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
    elif not args.no_fea:
        print("  FEA: no fea.pvd found. To view 3D stress/deformation, rerun one-way FEA with --export-vtk "
              "(and --vtk-stride/--vtk-fps as desired), then re-export or point this script at fea/vtk/fea.pvd.")

    if not sources_shown:
        print("[view_study_paraview] nothing loaded. Expected crm_particles.pvd/excavator.pvd/fea.pvd in", export_dir)
        return 1

    try:
        ResetCamera(view)
        view.ResetCamera()
    except Exception:
        pass

    # sync animation to timesteps
    try:
        scene = GetAnimationScene()
        scene.PlayMode = 'Sequence'
        scene.UpdateAnimationUsingDataTimeSteps()
    except Exception:
        pass

    try:
        Render()
    except Exception:
        pass

    print("[view_study_paraview] Scene ready.")
    print("  - Particles: separate source rendered as 3D spheres (toggle/scale in Pipeline Browser if needed).")
    print("  - Excavator: separate rigid surface source with its own render settings.")
    if fea_pvd and not args.no_fea:
        print("  - FEA: colored by von Mises stress and warped by displacement by default. "
              "Adjust warp 'Scale Factor' to exaggerate deformation.")
    else:
        print("  - FEA: no stress frames found/loaded. Enable --export-vtk in one-way FEA and re-run export_study_vtk.py.")
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
