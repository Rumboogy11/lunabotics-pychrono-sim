#!/usr/bin/env python3
"""Regenerate the optional legacy 1,200-face drum OBJ.

Production runs now use the full coarse OBJ: the quadric simplification closes
some of the screw holes, so it is no longer the default for either solver.  This
script remains as a preprocessing tool for faster, lower-fidelity experiments
with ``--mesh deme``.  Its output is already shipped, so ``trimesh`` is not
needed for a normal simulation.

Usage
-----
    python3 simplify_for_deme.py                 # 1,200 faces (legacy mesh)
    python3 simplify_for_deme.py --faces 2000
"""

from __future__ import annotations

import argparse
from pathlib import Path

from lunar_excavator_config import OBJ_DEME, STL_COARSE


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", type=Path, default=STL_COARSE)
    ap.add_argument("--output", type=Path, default=OBJ_DEME)
    ap.add_argument("--faces", type=int, default=1200)
    args = ap.parse_args()

    try:
        import trimesh
    except ImportError as exc:  # pragma: no cover - preprocessing dependency
        raise SystemExit("Install trimesh and fast-simplification to regenerate the DEME mesh") from exc

    mesh = trimesh.load(args.source, force="mesh")
    simplified = mesh.simplify_quadric_decimation(face_count=args.faces)
    simplified.process(validate=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    simplified.export(args.output, file_type="obj")
    print(f"{args.source.name}: {len(mesh.faces)} faces -> {len(simplified.faces)} faces")
    print(f"wrote {args.output} (bbox {simplified.bounds.tolist()}, volume {simplified.volume:.9g} m^3)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
