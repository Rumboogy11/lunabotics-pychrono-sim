#!/usr/bin/env python3
"""Convert a STEP CAD model into a Chrono-ready tetrahedral FEA mesh.

This converter uses OpenCASCADE to read and tessellate the STEP solid, MeshFix to
repair the triangulated surface, and TetGen to generate linear tetrahedra.  That
route is much more robust than Gmsh's direct 3D mesher for the supplied Onshape
model, whose screw/thread surfaces make Gmsh report segment/facet intersections.

Outputs
-------
* ``*.inp`` -- Abaqus C3D4 mesh with ``HUB`` and ``SURFACE`` node sets
* ``*.node``/``*.ele`` -- equivalent TetGen files
* ``*_surface.stl`` -- repaired watertight surface used by TetGen
* ``*.json`` -- validation metadata (CAD volume, mesh volume, node sets, quality)

Why this format
---------------
Chrono 10's ``fea.ChMeshFileLoader.FromAbaqusFile`` reads C3D4 tetrahedra and
returns named node sets in C++, but its ``std::map<string, vector<...>>``
node-set typemap is not wrapped in Python.  The Python FEA driver therefore
loads ``*.node``/``*.ele`` with ``FromTetGenFile`` and selects hub nodes itself;
``*.inp`` is retained for C++ or external tooling.  Hub nodes are tied to the
kinematic spindle while the rest of the solid remains free to deform.

Units and frame
---------------
STEP files carry no reliable SI unit metadata.  ``--unit-scale`` converts STEP
coordinates to metres (use ``0.001`` for the supplied mm-valued file).  All size
options are specified in metres and converted internally.  Optional ``--rotate``
and ``--translate`` are applied in metres after scaling so the exported mesh
matches the local frame used by ``lunar_excavator_config.py`` (long axis = Z).

Example
-------
    ./chrono-env/bin/python step_to_chrono_fea.py geometry/LunarDrumScrew_m.step \\
        --out-prefix fea/LunarDrumScrew_m_fea_decim8k --unit-scale 0.001 \\
        --mesh-size 0.012 --surface-target-faces 8000 \\
        --hub-radius 0.0015 --axis Z --tetgen-switches pYQ
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pymeshfix
import pyvista as pv
import tetgen
import trimesh
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.ShapeFix import ShapeFix_Shape
from OCP.StlAPI import StlAPI_Writer
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer

AXES = {"X": 0, "Y": 1, "Z": 2}
AXES_TO_NAME = {0: "X", 1: "Y", 2: "Z"}


def parse_vec3(text: str) -> tuple[float, float, float]:
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected three comma-separated numbers")
    try:
        return float(parts[0]), float(parts[1]), float(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected numeric values") from exc


def parse_axis(text: str) -> int:
    key = text.strip().upper()
    if key not in AXES:
        raise argparse.ArgumentTypeError("axis must be X, Y, or Z")
    return AXES[key]


def rotation_matrix(axis: np.ndarray, angle_deg: float) -> np.ndarray:
    """Rodrigues rotation matrix for a rotation about ``axis`` in degrees."""
    a = np.asarray(axis, dtype=float)
    norm = np.linalg.norm(a)
    if norm == 0.0:
        raise ValueError("rotation axis must be nonzero")
    a = a / norm
    x, y, z = a
    c = math.cos(math.radians(angle_deg))
    s = math.sin(math.radians(angle_deg))
    C = 1.0 - c
    return np.array(
        [
            [c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C],
        ],
        dtype=float,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Convert a STEP CAD solid into a Chrono FEA tetrahedral mesh.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("step", type=Path, help="input STEP file")
    p.add_argument("--out-prefix", type=Path, default=None, help="output prefix")
    p.add_argument(
        "--unit-scale",
        type=float,
        default=1.0,
        help="multiply STEP coordinates by this to get metres (use 0.001 for mm)",
    )
    p.add_argument("--mesh-size", type=float, default=0.003, help="target tetrahedron size in metres")
    p.add_argument(
        "--surface-deflection",
        type=float,
        default=None,
        help="OpenCASCADE surface sagitta in metres (default: mesh-size / 20)",
    )
    p.add_argument(
        "--angular-deflection",
        type=float,
        default=0.15,
        help="OpenCASCADE angular surface deflection in radians",
    )
    p.add_argument(
        "--surface-target-faces",
        type=int,
        default=8000,
        help="decimate the repaired surface to about this many triangles (0 disables; default: 8000)",
    )
    p.add_argument(
        "--max-tet-volume",
        type=float,
        default=None,
        help="TetGen maximum tetrahedron volume in m^3 (default: mesh-size^3)",
    )
    p.add_argument("--tet-quality", type=float, default=1.1, help="TetGen maximum radius-edge ratio")
    p.add_argument("--min-dihedral", type=float, default=10.0, help="TetGen minimum dihedral angle in degrees")
    p.add_argument(
        "--tetgen-switches",
        default=None,
        help="advanced: complete TetGen switch string (overrides the size/quality options)",
    )

    p.add_argument("--axis", type=parse_axis, default=2, help="drum rotation axis in the exported frame")
    p.add_argument("--rotate", default=None, help="optional rotation as 'ax,ay,az,angle_deg' applied after scaling")
    p.add_argument(
        "--translate",
        type=parse_vec3,
        default=(0.0, 0.0, 0.0),
        help="optional translation x,y,z in metres applied after scaling/rotation",
    )

    p.add_argument(
        "--hub-radius",
        type=float,
        default=0.0015,
        help="rigid-hub selection: max radial distance from --axis in metres",
    )
    p.add_argument("--hub-axial-min", type=float, default=None, help="hub axial lower bound in metres")
    p.add_argument("--hub-axial-max", type=float, default=None, help="hub axial upper bound in metres")
    p.add_argument("--hub-node-set", default="HUB", help="Abaqus node set name for the rigid connection")
    p.add_argument("--surface-node-set", default="SURFACE", help="Abaqus node set name for boundary nodes")

    p.add_argument(
        "--max-volume-error",
        type=float,
        default=0.05,
        help="fail if |mesh volume - CAD volume| / CAD volume exceeds this",
    )
    return p


def count_topology(shape) -> tuple[int, int]:
    counts = []
    for top_type in (TopAbs_SOLID, TopAbs_FACE):
        explorer = TopExp_Explorer(shape, top_type)
        n = 0
        while explorer.More():
            n += 1
            explorer.Next()
        counts.append(n)
    return counts[0], counts[1]


def read_cad(step_path: Path):
    reader = STEPControl_Reader()
    if reader.ReadFile(str(step_path)) != IFSelect_RetDone:
        raise RuntimeError(f"could not read STEP file: {step_path}")
    reader.TransferRoots()
    shape = reader.OneShape()

    if not BRepCheck_Analyzer(shape).IsValid():
        fixer = ShapeFix_Shape(shape)
        fixer.Perform()
        shape = fixer.Shape()
        if not BRepCheck_Analyzer(shape).IsValid():
            raise RuntimeError("STEP shape is invalid and OpenCASCADE could not repair it")

    solids, faces = count_topology(shape)
    if solids < 1:
        raise RuntimeError("STEP file contains no 3D solids")

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return shape, solids, faces, float(props.Mass())


def write_surface_stl(shape, stl_path: Path, deflection_cad: float, angular_deflection: float) -> None:
    mesher = BRepMesh_IncrementalMesh(shape, deflection_cad, False, angular_deflection, True)
    mesher.Perform()
    if not mesher.IsDone():
        raise RuntimeError("OpenCASCADE surface triangulation failed")
    writer = StlAPI_Writer()
    writer.ASCIIMode = False
    if not writer.Write(shape, str(stl_path)):
        raise RuntimeError(f"could not write surface STL: {stl_path}")


def repair_surface(pts: np.ndarray, faces: np.ndarray) -> pv.PolyData:
    fixer = pymeshfix.MeshFix(pts, faces)
    fixer.repair(joincomp=True, remove_smallest_components=False)
    clean = fixer.mesh
    if clean.n_faces == 0:
        raise RuntimeError("mesh repair produced an empty surface")
    if not clean.is_manifold:
        raise RuntimeError("repaired surface is still not manifold")
    return clean


def decimate_surface(clean: pv.PolyData, target_faces: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Reduce a repaired surface to roughly ``target_faces`` triangles.

    The CAD triangulation is far finer than the 12 mm CRM SPH spacing.  Using
    it directly as an FEA contact surface creates tens of thousands of flexible
    BCE markers, which overestimates SPH boundary density and produces huge
    artificial contact pressures.  Quadric decimation preserves the overall
    volume while bringing marker spacing close to the SPH spacing.
    """
    points = np.asarray(clean.points, dtype=float)
    faces = clean.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
    before = len(faces)
    if target_faces <= 0 or before <= target_faces:
        return points, faces, before

    reduction = 1.0 - target_faces / before
    reduced = clean.decimate(target_reduction=reduction, volume_preservation=True)
    points = np.asarray(reduced.points, dtype=float)
    faces = reduced.faces.reshape(-1, 4)[:, 1:].astype(np.int32)

    surface = trimesh.Trimesh(points, faces, process=False)
    if not surface.is_watertight:
        repaired = repair_surface(points, faces)
        points = np.asarray(repaired.points, dtype=float)
        faces = repaired.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
        surface = trimesh.Trimesh(points, faces, process=False)
    if not surface.is_watertight:
        raise RuntimeError("surface decimation broke watertightness")
    return points, faces, before


def write_node_set(handle, name: str, ids: list[int], per_line: int = 10) -> None:
    handle.write(f"*NSET, NSET={name}\n")
    for i in range(0, len(ids), per_line):
        handle.write(", ".join(str(v) for v in ids[i : i + per_line]) + "\n")


def write_outputs(
    prefix: Path,
    points: np.ndarray,
    tets: np.ndarray,
    hub_ids: list[int],
    surface_ids: list[int],
    hub_name: str,
    surface_name: str,
) -> None:
    inp_path = prefix.with_suffix(".inp")
    node_path = prefix.with_suffix(".node")
    ele_path = prefix.with_suffix(".ele")

    with inp_path.open("w", encoding="utf-8") as f:
        f.write("*HEADING\n")
        f.write("Chrono tetrahedral FEA mesh generated by step_to_chrono_fea.py\n")
        f.write("*NODE\n")
        for i, (x, y, z) in enumerate(points, start=1):
            f.write(f"{i}, {x:.10g}, {y:.10g}, {z:.10g}\n")
        f.write("*ELEMENT, TYPE=C3D4, ELSET=DRUM\n")
        for i, tet in enumerate(tets, start=1):
            f.write(f"{i}, " + ", ".join(str(int(v) + 1) for v in tet) + "\n")
        if hub_ids:
            write_node_set(f, hub_name, hub_ids)
        if surface_ids:
            write_node_set(f, surface_name, surface_ids)

    with node_path.open("w", encoding="utf-8") as f:
        f.write(f"{len(points)} 3 0 0\n")
        for i, (x, y, z) in enumerate(points, start=1):
            f.write(f"{i} {x:.10g} {y:.10g} {z:.10g}\n")

    with ele_path.open("w", encoding="utf-8") as f:
        f.write(f"{len(tets)} 4 0\n")
        for i, tet in enumerate(tets, start=1):
            f.write(f"{i} " + " ".join(str(int(v) + 1) for v in tet) + "\n")


def mesh_volume(points: np.ndarray, tets: np.ndarray) -> float:
    p0, p1, p2, p3 = points[tets[:, 0]], points[tets[:, 1]], points[tets[:, 2]], points[tets[:, 3]]
    return float(np.abs(np.einsum("ij,ij->i", np.cross(p1 - p0, p2 - p0), p3 - p0)).sum() / 6.0)


def edge_quality(points: np.ndarray, tets: np.ndarray) -> dict[str, float]:
    edges = np.concatenate(
        [
            points[tets[:, 1]] - points[tets[:, 0]],
            points[tets[:, 2]] - points[tets[:, 0]],
            points[tets[:, 3]] - points[tets[:, 0]],
            points[tets[:, 2]] - points[tets[:, 1]],
            points[tets[:, 3]] - points[tets[:, 1]],
            points[tets[:, 3]] - points[tets[:, 2]],
        ],
        axis=0,
    )
    lengths = np.linalg.norm(edges, axis=1)

    # Per-tet minimum/maximum edge length, computed without an explicit Python loop.
    edge_matrix = np.stack(
        [
            np.linalg.norm(points[tets[:, 1]] - points[tets[:, 0]], axis=1),
            np.linalg.norm(points[tets[:, 2]] - points[tets[:, 0]], axis=1),
            np.linalg.norm(points[tets[:, 3]] - points[tets[:, 0]], axis=1),
            np.linalg.norm(points[tets[:, 2]] - points[tets[:, 1]], axis=1),
            np.linalg.norm(points[tets[:, 3]] - points[tets[:, 1]], axis=1),
            np.linalg.norm(points[tets[:, 3]] - points[tets[:, 2]], axis=1),
        ],
        axis=1,
    )
    ratios = edge_matrix.min(axis=1) / np.maximum(edge_matrix.max(axis=1), 1.0e-30)
    return {
        "min_edge_m": float(lengths.min()),
        "median_edge_m": float(np.median(lengths)),
        "max_edge_m": float(lengths.max()),
        "min_max_edge_ratio": float(ratios.min()),
        "median_max_edge_ratio": float(np.median(ratios)),
    }


def main() -> int:
    args = build_parser().parse_args()
    step_path = args.step.resolve()
    if not step_path.is_file():
        raise SystemExit(f"missing STEP file: {step_path}")
    if args.unit_scale <= 0.0:
        raise SystemExit("--unit-scale must be positive")
    if args.mesh_size <= 0.0:
        raise SystemExit("--mesh-size must be positive")
    if args.hub_radius < 0.0:
        raise SystemExit("--hub-radius must be non-negative")

    prefix = (args.out_prefix or (Path("fea") / f"{step_path.stem}_fea")).resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)

    rotation = np.eye(3)
    if args.rotate:
        parts = [p.strip() for p in args.rotate.split(",")]
        if len(parts) != 4:
            raise SystemExit("--rotate must be 'ax,ay,az,angle_deg'")
        try:
            axis = np.array([float(v) for v in parts[:3]])
            angle = float(parts[3])
        except ValueError as exc:
            raise SystemExit(f"invalid --rotate value: {exc}") from exc
        rotation = rotation_matrix(axis, angle)
    translation = np.asarray(args.translate, dtype=float)

    surface_deflection = args.surface_deflection if args.surface_deflection is not None else args.mesh_size / 20.0
    if surface_deflection <= 0.0:
        raise SystemExit("--surface-deflection must be positive")
    max_tet_volume = args.max_tet_volume if args.max_tet_volume is not None else args.mesh_size**3
    if max_tet_volume <= 0.0:
        raise SystemExit("--max-tet-volume must be positive")

    shape, n_solids, n_faces, cad_volume_cad = read_cad(step_path)
    cad_volume = cad_volume_cad * args.unit_scale**3

    stl_path = prefix.parent / f"{prefix.name}_surface.stl"
    write_surface_stl(
        shape,
        stl_path,
        deflection_cad=surface_deflection / args.unit_scale,
        angular_deflection=args.angular_deflection,
    )
    raw = pv.read(str(stl_path))
    raw_faces = raw.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
    clean = repair_surface(np.asarray(raw.points, dtype=float), raw_faces)
    repaired_points, repaired_faces, faces_before_decimation = decimate_surface(
        clean, args.surface_target_faces
    )

    # OCC leaves a few very long triangles on planar end faces.  Adaptive
    # longest-edge bisection fixes those without inflating already fine curved
    # regions, and unlike VTK's subdivision it preserves mesh indices.
    max_surface_edge_cad = args.mesh_size / args.unit_scale
    surface_points, surface_faces = trimesh.remesh.subdivide_to_size(
        repaired_points, repaired_faces, max_edge=max_surface_edge_cad, max_iter=20
    )
    if not trimesh.Trimesh(surface_points, surface_faces, process=False).is_watertight:
        raise RuntimeError("surface subdivision broke watertightness")

    # Replace the raw OCC STL with the actual repaired/decimated surface used
    # for tetrahedralization and CRM contact.
    trimesh.Trimesh(surface_points, surface_faces, process=False).export(stl_path)

    if args.tetgen_switches:
        switches = args.tetgen_switches
    else:
        max_volume_cad = max_tet_volume / args.unit_scale**3
        switches = (
            f"pq{args.tet_quality:g}/{args.min_dihedral:g}a{max_volume_cad:.10g}YQ"
        )

    tet = tetgen.TetGen(surface_points, surface_faces)
    nodes_cad, tets, _attributes, _markers = tet.tetrahedralize(switches=switches)
    if nodes_cad.shape[0] == 0 or tets.shape[0] == 0:
        raise RuntimeError("TetGen produced an empty volume mesh")

    points = np.asarray(nodes_cad, dtype=float) * args.unit_scale
    points = points @ rotation.T + translation
    tets = np.asarray(tets, dtype=np.int32)
    if tets.min() < 0 or tets.max() >= len(points):
        raise RuntimeError("TetGen returned out-of-range node indices")

    surface_nodes_0 = np.unique(np.asarray(tet.trifaces, dtype=np.int32))
    surface_ids = (surface_nodes_0 + 1).tolist()

    radial_axes = [i for i in range(3) if i != args.axis]
    axial = points[:, args.axis]
    radial = np.linalg.norm(points[:, radial_axes], axis=1)
    hub_mask = radial <= args.hub_radius
    if args.hub_axial_min is not None:
        hub_mask &= axial >= args.hub_axial_min
    if args.hub_axial_max is not None:
        hub_mask &= axial <= args.hub_axial_max
    hub_ids = (np.flatnonzero(hub_mask) + 1).tolist()
    if not hub_ids:
        print(
            "WARNING: hub selection produced no nodes; the FEA driver selects hub "
            "nodes from the loaded mesh, so continuing with an empty HUB set",
            file=sys.stderr,
        )

    volume = mesh_volume(points, tets)
    volume_error = abs(volume - cad_volume) / max(cad_volume, 1.0e-30)
    quality = edge_quality(points, tets)

    write_outputs(prefix, points, tets, hub_ids, surface_ids, args.hub_node_set, args.surface_node_set)

    metadata = {
        "source_step": str(step_path),
        "pipeline": "OpenCASCADE STL -> MeshFix -> TetGen",
        "outputs": {
            "abaqus": str(prefix.with_suffix(".inp")),
            "tetgen_node": str(prefix.with_suffix(".node")),
            "tetgen_ele": str(prefix.with_suffix(".ele")),
            "surface_stl": str(stl_path),
        },
        "units": "metres",
        "unit_scale": args.unit_scale,
        "cad_solids": n_solids,
        "cad_faces": n_faces,
        "surface_deflection_m": surface_deflection,
        "angular_deflection_rad": args.angular_deflection,
        "max_surface_edge_m": args.mesh_size,
        "surface_target_faces": args.surface_target_faces,
        "surface_faces_before_decimation": int(faces_before_decimation),
        "surface_points": int(len(surface_points)),
        "surface_faces": int(len(surface_faces)),
        "tetgen_switches": switches,
        "rotation_axis": AXES_TO_NAME[args.axis],
        "nodes": int(len(points)),
        "tetrahedra": int(len(tets)),
        "hub_node_set": args.hub_node_set,
        "hub_nodes": len(hub_ids),
        "hub_selection": (
            f"radial distance <= {args.hub_radius:g} m about {AXES_TO_NAME[args.axis]} axis"
        ),
        "surface_node_set": args.surface_node_set,
        "surface_nodes": len(surface_ids),
        "bbox_min_m": points.min(axis=0).tolist(),
        "bbox_max_m": points.max(axis=0).tolist(),
        "cad_volume_m3": cad_volume,
        "mesh_volume_m3": volume,
        "volume_relative_error": volume_error,
        "edge_quality": quality,
    }
    meta_path = prefix.with_suffix(".json")
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
        f.write("\n")

    print(f"Wrote {prefix.with_suffix('.inp')}")
    print(f"  nodes={len(points)} tets={len(tets)} hub={len(hub_ids)} surface={len(surface_ids)}")
    print(
        f"  CAD volume={cad_volume:.6e} m^3, mesh volume={volume:.6e} m^3, "
        f"error={volume_error:.3%}"
    )
    print(
        f"  edge length min/median/max={quality['min_edge_m']:.6g}/"
        f"{quality['median_edge_m']:.6g}/{quality['max_edge_m']:.6g} m"
    )
    if volume_error > args.max_volume_error:
        raise SystemExit(
            f"mesh volume error {volume_error:.3%} exceeds --max-volume-error "
            f"{args.max_volume_error:.3%}; refine --surface-deflection or --max-tet-volume"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
