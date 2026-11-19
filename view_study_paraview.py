#!/usr/bin/env python3
"""ParaView visualization script for a CRM/FEA study exported by export_study_vtk.py.

This is intended to be run inside ParaView's Python environment so it can build
a ready-to-view scene:

    # from the study export directory produced by export_study_vtk.py
    paraview --script=/path/to/view_study_paraview.py -- /path/to/study/paraview

    # or with pvpython (offscreen/state setup depends on your ParaView build)
    pvpython /path/to/view_study_paraview.py /path/to/study/paraview

It loads, when available:
  - crm_particles.pvd : particle point cloud over time
        colored by 'excavated' (or 'inside_drum'), rendered as 3D spheres
        sized from the study's SPH spacing when available
  - excavator.pvd     : rigid excavator/drum surface over time
  - fea/vtk/fea.pvd   : one-way FEA unstructured grid over time (if exported)
        colored by cell data 'von_mises_MPa'/'von_mises_pa' (stress gradient)
        and deformable via point data 'Displacement' (Warp By Vector).
        Increase --warp-scale (e.g. 100, 1000) to exaggerate elastic deformation.

All sources share the study time steps, so they animate together in one view.

If no argument is given, the script tries ./paraview in the current directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _parse_args(argv: list[str]):
    # ParaView passes its own args; user args typically come after '--'
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        # try to drop program name and keep trailing path-like args
        argv = [a for a in argv[1:] if not a.startswith("-") and ("/" in a or a.endswith("paraview") or Path(a).exists())]
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("export_dir", nargs="?", default=None,
                    help="directory containing combined.pvd / *_pvd files (default: ./paraview)")
    ap.add_argument("--warp-scale", type=float, default=1.0,
                    help="scale factor for FEA Warp By Vector (displacement). "
                         "Increase (e.g. 100, 1000) to exaggerate elastic deformation.")
    ap.add_argument("--color-by", choices=("excavated", "inside_drum"), default="excavated",
                    help="particle coloring array")
    ap.add_argument("--particle-radius", type=float, default=None,
                    help="particle sphere radius in metres (default: half of exported SPH spacing)")
    ap.add_argument("--particle-style", choices=("spheres", "points"), default="spheres",
                    help="how to render CRM particles (default: spheres)")
    ap.add_argument("--no-particles", action="store_true")
    ap.add_argument("--no-excavator", action="store_true")
    ap.add_argument("--no-fea", action="store_true")
    # be tolerant of unknown ParaView args
    args, _unknown = ap.parse_known_args(argv)
    return args


def find_pvd(export_dir: Path, name: str) -> Path | None:
    p = export_dir / name
    return p if p.is_file() else None


def main():
    args = _parse_args(sys.argv)
    export_dir = Path(args.export_dir or "paraview").expanduser().resolve()
    if not export_dir.is_dir():
        # maybe user passed study dir
        cand = export_dir / "paraview"
        if cand.is_dir():
            export_dir = cand
    if not export_dir.is_dir():
        print(f"[view_study_paraview] export directory not found: {export_dir}")
        print("  Run export_study_vtk.py first, then pass the export/paraview directory.")
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
        print("  Run this script with: paraview --script=view_study_paraview.py -- <export_dir>")
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

        # Particle radius: prefer explicit override, else half SPH spacing from manifest.
        p_diam = manifest.get("particle_diameter_m") or manifest.get("particle_spacing_m")
        try:
            default_radius = float(p_diam) / 2.0 if p_diam is not None else 0.004
        except Exception:
            default_radius = 0.004
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
                try:
                    glyph.ScaleArray = ['POINTS', 'No scale array']
                except Exception:
                    pass
                try:
                    glyph.ScaleFactor = 1.0
                except Exception:
                    pass
                try:
                    UpdatePipeline(proxy=glyph)
                except Exception:
                    pass
                particle_display_src = glyph
                print(f"  particles: rendered as spheres (radius={particle_radius:g} m)")
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
            disp.RescaleTransferFunctionToDataRange(True, False)
            disp.SetScalarBarVisibility(view, True)
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
    # common locations
    for cand in [export_dir / "fea.pvd", export_dir / "fea" / "fea.pvd",
                 export_dir.parent / "fea" / "vtk" / "fea.pvd"]:
        if cand.is_file():
            fea_pvd = cand
            break
    # also allow manifest / study-relative hints
    if fea_pvd is None:
        # search
        for cand in export_dir.rglob("fea.pvd"):
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
            warp = WarpByVector(Input=fea, Vectors=['POINTS', 'Displacement'])
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
                    pass
                try:
                    disp.RescaleTransferFunctionToDataRange(True, False)
                except Exception:
                    try:
                        disp.RescaleTransferFunctionToDataRange(False, True)
                    except Exception:
                        pass
                try:
                    disp.SetScalarBarVisibility(view, True)
                    sb = GetScalarBar(lut, view)
                    if sb is not None and 'MPa' in name:
                        sb.Title = 'von Mises (MPa)'
                    elif sb is not None and name == 'von_mises_pa':
                        sb.Title = 'von Mises (Pa)'
                    elif sb is not None and 'Displacement' in name:
                        sb.Title = 'Displacement (m)'
                except Exception:
                    pass
                colored = True
                print(f"  FEA colored by {field_type} '{name}'")
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
    print("  - Use the time controls to animate CRM and FEA together (shared timesteps where available).")
    print("  - Particles are colored by the selected flag; excavator is the rigid drum surface.")
    if fea_pvd and not args.no_fea:
        print("  - FEA shows von Mises (color) and deformation (Warp By Vector). "
              "Adjust the warp 'Scale Factor' in Properties to exaggerate displacement.")
    print("  - If particles obscure the drum, toggle visibility/opacity in the Pipeline Browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
