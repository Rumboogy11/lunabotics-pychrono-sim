#!/usr/bin/env python3
"""ParaView setup for DEME LunarExcavator output.

Mirror of MercuryDPM's ``VisualisationScript.py``, adapted to the CSV/legacy-VTK
files written by ``lunar_excavator_deme.py``:

* ``spheres_*.csv``  -> ``CSVReader`` + ``TableToPoints`` + sphere ``Glyph``
* ``drum_*.vtk``     -> ``LegacyVTKReader`` (moving drum + screw)
* ``walls_*.vtk``    -> ``LegacyVTKReader`` (five analytical planes)

Usage
-----
    ./view_paraview.sh --output-dir Output_DEME_LunarExcavator
    ./view.sh --paraview --output-dir Output_DEME_LunarExcavator
    ./view_paraview.sh --output-dir ... --no-render       # headless validation

ParaView is an optional viewer inherited from the original MercuryDPM helper;
neither DEME nor PyChrono requires it.  `view_paraview.sh` passes the output
directory through LUNAR_OUTPUT_DIR because ParaView's --script option does not
forward script arguments.
"""

from __future__ import annotations

import argparse
import glob
import os
import re
from pathlib import Path


def natural_key(text: str):
    return [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", text)]


def find_frames(output_dir: Path, pattern: str, required: bool = True) -> list[str]:
    files = [f for f in glob.glob(str(output_dir / pattern)) if "merged" not in Path(f).name]
    files.sort(key=natural_key)
    if required and not files:
        raise SystemExit(f"No files matching {pattern!r} in {output_dir}. Run the simulation first.")
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("LUNAR_OUTPUT_DIR", "Output_DEME_LunarExcavator")),
        help="frame directory (or set LUNAR_OUTPUT_DIR when launching via `paraview --script`)",
    )
    ap.add_argument(
        "--no-render",
        action="store_true",
        help="build the pipeline but skip Render()/ResetCamera() (or set LUNAR_NO_RENDER=1)",
    )
    args = ap.parse_args()
    if os.environ.get("LUNAR_NO_RENDER", "0").lower() in {"1", "true", "yes", "on"}:
        args.no_render = True

    try:
        from paraview.simple import (
            CSVReader,
            GetActiveViewOrCreate,
            Glyph,
            LegacyVTKReader,
            Render,
            ResetCamera,
            Show,
            TableToPoints,
            ColorBy,
        )
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("ParaView Python module not available; run with `paraview --script=visualise.py`") from exc

    output_dir = args.output_dir.resolve()
    sphere_files = find_frames(output_dir, "spheres_*.csv")
    drum_files = find_frames(output_dir, "drum_*.vtk", required=False)
    wall_files = find_frames(output_dir, "walls_*.vtk", required=False)

    print(f"Found {len(sphere_files)} sphere frames")
    if drum_files:
        print(f"Found {len(drum_files)} drum frames")
    if wall_files:
        print(f"Found {len(wall_files)} wall frames")

    # ---- particles: CSV table -> points -> sphere glyphs ----
    particles = CSVReader(FileName=sphere_files)
    particles.UpdatePipeline()
    points = TableToPoints(Input=particles)
    points.XColumn = "X"
    points.YColumn = "Y"
    points.ZColumn = "Z"
    points.KeepAllDataArrays = 1
    points.UpdatePipeline()

    glyph = Glyph(Input=points, registrationName="Glyph Particle")
    glyph.GlyphType = "Sphere"
    glyph.ScaleArray = "r"
    glyph.ScaleFactor = 2.0  # sphere glyph source has radius 0.5
    glyph.GlyphMode = "All Points"

    particle_display = Show(glyph)
    ColorBy(particle_display, ("POINTS", "family"))
    particle_display.SetScalarBarVisibility(GetActiveViewOrCreate("RenderView"), False)

    # ---- moving drum ----
    if drum_files:
        drum = LegacyVTKReader(FileNames=drum_files, registrationName="Moving Drum")
        drum_display = Show(drum)
        drum_display.Opacity = 0.35

    # ---- retaining walls ----
    if wall_files:
        walls = LegacyVTKReader(FileNames=wall_files, registrationName="Stationary Walls")
        wall_display = Show(walls)
        wall_display.Opacity = 2.5e-3

    scene = GetActiveViewOrCreate("RenderView")
    scene.InteractionMode = "3D"
    if not args.no_render:
        Render()
        ResetCamera()

    print(f"ParaView pipeline ready for {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
