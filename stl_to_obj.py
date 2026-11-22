#!/usr/bin/env python3
"""Convert STL meshes to Wavefront OBJ.

Chrono mesh loaders commonly consume OBJ files. Converted study surfaces are
already in metres and centred on the drum axis, so this is a pure format
conversion (no scaling or reorientation). No meshes are bundled with the
project; the legacy default names in ``lunar_excavator_config.py`` require local
files.

Usage
-----
    python3 stl_to_obj.py                 # convert default medium + coarse meshes
    python3 stl_to_obj.py path/to/a.stl   # convert one or more files
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from lunar_excavator_config import GEOMETRY_DIR, OBJ_COARSE, OBJ_MEDIUM, STL_COARSE, STL_MEDIUM


def read_stl(path: Path) -> list[tuple[tuple[float, float, float], ...]]:
    """Read a binary or ASCII STL into a list of triangle vertex triples."""
    raw = path.read_bytes()
    if len(raw) < 84:
        raise ValueError(f"{path}: file too small to be an STL")

    n_declared = struct.unpack_from("<I", raw, 80)[0]
    expected = 84 + 50 * n_declared
    if n_declared > 0 and len(raw) == expected:
        # Binary STL: declared triangle count matches the byte length exactly.
        tris = []
        for i in range(n_declared):
            vals = struct.unpack_from("<12fH", raw, 84 + 50 * i)
            tris.append(tuple(tuple(vals[3 + 3 * k : 6 + 3 * k]) for k in range(3)))
        return tris  # type: ignore[return-value]

    # ASCII fallback.
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    verts: list[tuple[float, float, float]] = []
    tris: list[tuple[tuple[float, float, float], ...]] = []
    for line in lines:
        parts = line.strip().split()
        if len(parts) == 4 and parts[0].lower() == "vertex":
            verts.append(tuple(float(x) for x in parts[1:]))  # type: ignore[arg-type]
            if len(verts) == 3:
                tris.append(tuple(verts))
                verts = []
    if not tris:
        raise ValueError(f"{path}: could not parse binary or ASCII STL")
    return tris


def write_obj(path: Path, tris: list[tuple[tuple[float, float, float], ...]], name: str) -> None:
    """Write a minimal OBJ (positions + triangular faces, no normals/UVs)."""
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# Converted from {name} by stl_to_obj.py\n")
        f.write(f"o {Path(name).stem}\n")
        for tri in tris:
            for v in tri:
                f.write(f"v {v[0]:.9g} {v[1]:.9g} {v[2]:.9g}\n")
        for i in range(0, 3 * len(tris), 3):
            f.write(f"f {i + 1} {i + 2} {i + 3}\n")


def convert(stl: Path, obj: Path | None = None) -> tuple[Path, int]:
    tris = read_stl(stl)
    obj = obj or stl.with_suffix(".obj")
    write_obj(obj, tris, stl.name)
    return obj, len(tris)


def main() -> None:
    pairs = [(STL_MEDIUM, OBJ_MEDIUM), (STL_COARSE, OBJ_COARSE)]
    if len(sys.argv) > 1:
        pairs = [(Path(a), None) for a in sys.argv[1:]]
    for stl, obj in pairs:
        if not Path(stl).is_file():
            print(f"skip: {stl} (not found)")
            continue
        out, n = convert(Path(stl), obj)
        print(f"{Path(stl).name}: {n} triangles -> {out}")


if __name__ == "__main__":
    main()
