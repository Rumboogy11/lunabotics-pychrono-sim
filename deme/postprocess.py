#!/usr/bin/env python3
"""Lightweight post-processing for DEME LunarExcavator output.

The CSV + legacy-VTK frames written by ``lunar_excavator_deme.py`` are already
readable by ParaView (see ``visualise.py``).  This script adds the small
quantities that are awkward to extract interactively:

* ``summary.csv``       -- per-frame particle count, bed centroid/extent and
                           drum bottom height,
* ``spheres_merged.csv`` -- optional single file with an extra ``frame,time``
                           column (off by default; can be large).

Usage:
    python3 postprocess.py --output-dir Output_DEME_LunarExcavator
    python3 postprocess.py --output-dir ... --merged --fps 120
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


def natural_key(text: str):
    return [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", text)]


def frame_number(path: Path) -> int:
    match = re.search(r"(\d+)\.csv$", path.name)
    return int(match.group(1)) if match else -1


def read_spheres(path: Path) -> tuple[list[tuple[float, float, float, float]], int]:
    rows: list[tuple[float, float, float, float]] = []
    family_count = 0
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append((float(row["X"]), float(row["Y"]), float(row["Z"]), float(row["r"])))
            family_count += int(float(row.get("family", 0)))
    return rows, family_count


def read_vtk_min_z(path: Path) -> float | None:
    """Return the minimum z of a DEME legacy-VTK POINTS block, if present."""
    lines = path.read_text(errors="replace").splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("POINTS "):
            continue
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            n = int(parts[1])
        except ValueError:
            continue
        zs = []
        for point_line in lines[i + 1 : i + 1 + n]:
            coords = point_line.split()
            if len(coords) >= 3:
                try:
                    zs.append(float(coords[2]))
                except ValueError:
                    pass
        return min(zs) if zs else None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output-dir", type=Path, default=Path("Output_DEME_LunarExcavator"))
    ap.add_argument("--fps", type=float, default=120.0, help="output frames per second, used to label time")
    ap.add_argument("--merged", action="store_true", help="also write spheres_merged.csv")
    args = ap.parse_args()

    output_dir = args.output_dir.resolve()
    sphere_files = sorted(
        (p for p in output_dir.glob("spheres_*.csv") if "merged" not in p.name),
        key=lambda p: natural_key(p.name),
    )
    if not sphere_files:
        raise SystemExit(f"No spheres_*.csv files in {output_dir}")

    summary_path = output_dir / "summary.csv"
    merged_path = output_dir / "spheres_merged.csv"
    merged_file = merged_path.open("w", newline="", encoding="utf-8") if args.merged else None
    merged_writer = csv.writer(merged_file) if merged_file else None
    if merged_writer:
        merged_writer.writerow(["frame", "time", "X", "Y", "Z", "r"])

    last = None
    with summary_path.open("w", newline="", encoding="utf-8") as summary_file:
        writer = csv.writer(summary_file)
        writer.writerow([
            "frame", "time", "particles", "mean_x", "mean_y", "mean_z",
            "min_z", "max_z", "drum_min_z",
        ])
        for path in sphere_files:
            frame = frame_number(path)
            t = frame / args.fps
            positions, family_count = read_spheres(path)
            n = len(positions)
            if n:
                mean_x = sum(p[0] for p in positions) / n
                mean_y = sum(p[1] for p in positions) / n
                mean_z = sum(p[2] for p in positions) / n
                min_z = min(p[2] for p in positions)
                max_z = max(p[2] for p in positions)
            else:
                mean_x = mean_y = mean_z = min_z = max_z = float("nan")

            drum_file = output_dir / f"drum_{frame:06d}.vtk"
            drum_min_z = read_vtk_min_z(drum_file) if drum_file.exists() else None
            writer.writerow([
                frame, f"{t:.9g}", n, f"{mean_x:.9g}", f"{mean_y:.9g}",
                f"{mean_z:.9g}", f"{min_z:.9g}", f"{max_z:.9g}",
                "" if drum_min_z is None else f"{drum_min_z:.9g}",
            ])

            if merged_writer:
                for x, y, z, r in positions:
                    merged_writer.writerow([frame, f"{t:.9g}", f"{x:.9g}", f"{y:.9g}", f"{z:.9g}", f"{r:.9g}"])

            last = (frame, t, n, family_count, drum_min_z)

    print(f"Processed {len(sphere_files)} frames -> {summary_path}")
    if merged_file is not None:
        merged_file.close()
        print(f"Merged particle data -> {merged_path}")
    if last:
        frame, t, n, family_count, drum_min_z = last
        drum_text = "n/a" if drum_min_z is None else f"{drum_min_z:.6g} m"
        print(
            f"last frame {frame}: t={t:.3f} s, {n} grains "
            f"(family sum {family_count}), drum bottom={drum_text}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
