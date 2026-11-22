#!/usr/bin/env python3
"""Convert a STEP CAD model into a Chrono-ready tetrahedral FEA mesh.

This converter uses OpenCASCADE to read and tessellate the STEP solid, MeshFix to
repair the triangulated surface, and TetGen to generate linear tetrahedra.  That
route is much more robust than Gmsh's direct 3D mesher for bucket drum excavator
CAD, whose curved bucket surfaces often make Gmsh report segment/facet intersections.

Outputs
-------
* ``*.inp`` -- Abaqus C3D4 mesh with ``HUB`` and ``SURFACE`` node sets
* ``*.node``/``*.ele`` -- equivalent TetGen files
* ``*_surface.stl`` -- repaired watertight surface used by TetGen
* ``*.json`` -- validation metadata (CAD volume, mesh volume, node sets, quality)

Why this format
---------------
Chrono 11's ``fea.ChMeshFileLoader.FromAbaqusFile`` reads C3D4 tetrahedra and
returns named node sets in C++, but its ``std::map<string, vector<...>>``
node-set typemap is not wrapped in Python.  The Python FEA driver therefore
loads ``*.node``/``*.ele`` with ``FromTetGenFile`` and selects hub nodes itself;
``*.inp`` is retained for C++ or external tooling.  Hub nodes are tied to the
kinematic spindle while the rest of the solid remains free to deform.

Units and frame
---------------
STEP files carry no reliable SI unit metadata.  ``--unit-scale`` converts STEP
coordinates to metres (use ``0.001`` for mm-valued CAD).  All size
options are specified in metres and converted internally.  ``--axis`` names the
drum axis in the **original STEP space** and defaults to Y for new models; the
exported mesh and CRM surface are rotated so that axis becomes local +Z, which
is the convention used by the FEA drivers.  Optional ``--rotate`` and
``--translate`` are applied after that conversion.

Example
-------
    # Legacy-style model: source drum axis is Z.
    ./chrono-env/bin/python step_to_chrono_fea.py geometry/MyDrum.step \\
        --out-prefix fea/MyDrum_fea --unit-scale 0.001 \\
        --mesh-size 0.012 --surface-target-faces 8000 \\
        --hub-radius 0.0015 --axis Z

    # New model: source drum axis is Y and is rotated to local Z on export.
    ./chrono-env/bin/python step_to_chrono_fea.py geometry/NewDrum.step \\
        --out-prefix fea/NewDrum_fea --unit-scale 0.001 \\
        --mesh-size 0.012 --axis Y --hub-radius 0.035

    # Recommended for bucket drum excavator CAD: start at 8k faces, preserve exterior faces,
    # and raise the target automatically until the measured exterior fit and TetGen pass.
    # The wizard/GUI enable this by default.
    ./chrono-env/bin/python step_to_chrono_fea.py "geometry/BucketDrumExcavator.step" \\
        --out-prefix fea/BucketDrumExcavator_fea --unit-scale 0.001 \\
        --mesh-size 0.02 --surface-target-faces 8000 --surface-fit-tolerance 0.001 --min-solid-volume 1e-05 \\
        --hub-radius 0.0357669 --axis Y \\
        --part-aware-simplify --part-aware-radius 0.05 --part-aware-reduction 0.75
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import pymeshfix
import pyvista as pv
import tetgen
import trimesh
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BOPAlgo import BOPAlgo_Builder
from OCP.BRepTools import BRepTools
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.BRep import BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.ShapeFix import ShapeFix_Shape
from OCP.StlAPI import StlAPI_Writer
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.OSD import OSD_Parallel

AXES = {"X": 0, "Y": 1, "Z": 2}
AXES_TO_NAME = {0: "X", 1: "Y", 2: "Z"}


def radial_axes(axis: int) -> list[int]:
    """Indices of the two coordinate axes perpendicular to ``axis``."""
    return [i for i in range(3) if i != axis]


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


def source_axis_to_z(axis: int) -> np.ndarray:
    """Rotation taking a CAD drum axis onto the exported local +Z axis."""
    if axis == AXES["X"]:
        return rotation_matrix(np.array([0.0, 1.0, 0.0]), -90.0)
    if axis == AXES["Y"]:
        return rotation_matrix(np.array([1.0, 0.0, 0.0]), 90.0)
    return np.eye(3)


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
        help="starting decimation target; the converter increases it until the surface stays "
             "watertight and preserves volume. Start modest: very large targets can create "
             "sliver tetrahedra (0 disables; default: 8000)",
    )
    p.add_argument(
        "--surface-max-faces",
        type=int,
        default=256000,
        help="upper bound for the watertight decimation retry (default: 256000)",
    )
    p.add_argument(
        "--surface-fit-tolerance",
        type=float,
        default=0.001,
        help="maximum measured exterior-surface deviation from the raw OCC tessellation, "
             "in metres (default: 0.001). Decimation targets are raised until this fit "
             "and TetGen both pass; 0 disables the fit check",
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

    p.add_argument("--axis", type=parse_axis, default=AXES["Y"],
                   help="drum rotation axis in the original STEP space; output is rotated to local +Z "
                        "(default: Y)")
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
        help="rigid-hub selection: max radial distance from the exported local Z axis in metres",
    )
    p.add_argument("--hub-axial-min", type=float, default=None, help="hub axial lower bound in metres")
    p.add_argument("--hub-axial-max", type=float, default=None, help="hub axial upper bound in metres")
    p.add_argument("--hub-node-set", default="HUB", help="Abaqus node set name for the rigid connection")
    p.add_argument("--surface-node-set", default="SURFACE", help="Abaqus node set name for boundary nodes")
    p.add_argument(
        "--no-fuse-assembly",
        action="store_true",
        help="do not boolean-fuse multi-solid STEP assemblies before meshing",
    )
    p.add_argument(
        "--repair-after-decimation",
        action="store_true",
        help="decimate the raw OCC surface before MeshFix repair; preserves volume better "
             "on multi-solid assemblies",
    )
    p.add_argument(
        "--skip-meshfix",
        action="store_true",
        help="skip MeshFix repair when the raw OCC surface is already watertight",
    )
    p.add_argument(
        "--skip-decimation",
        action="store_true",
        help="keep the full OCC surface instead of quadric-decimating it",
    )
    p.add_argument(
        "--min-solid-volume",
        type=float,
        default=0.0,
        help="drop assembly solids smaller than this volume in m^3 before fusion; "
             "useful for removing tiny fasteners (default: 0)",
    )

    p.add_argument(
        "--part-aware-simplify",
        action="store_true",
        help="keep exterior/large surface faces at the validated fine fidelity and decimate only "
             "hub/interior faces within --part-aware-radius. The study wizard/GUI enable this by "
             "default; this converter flag itself is off when invoked directly.",
    )
    p.add_argument(
        "--part-aware-radius",
        type=float,
        default=0.05,
        help="maximum radial distance from the source drum axis (metres) for a face to be "
             "eligible for part-aware interior decimation; faces touching this radius are "
             "kept as a fixed boundary (default: 0.05)",
    )
    p.add_argument(
        "--part-aware-reduction",
        type=float,
        default=0.75,
        help="fraction of eligible interior faces to remove with topology-preserving, "
             "boundary-fixed decimation (default: 0.75)",
    )
    p.add_argument(
        "--part-aware-min-faces",
        type=int,
        default=500,
        help="skip part-aware simplification if fewer than this many interior faces are found "
             "(default: 500)",
    )
    p.add_argument(
        "--part-aware-solid-analysis-deflection",
        type=float,
        default=None,
        help="OCC tessellation deflection in metres used only to classify CAD solids for "
             "part-aware metadata (default: same as --surface-deflection)",
    )

    p.add_argument(
        "--no-occ-parallel",
        action="store_true",
        help="disable OpenCASCADE's parallel tessellation mode",
    )
    p.add_argument(
        "--time-stages",
        action="store_true",
        help="print per-stage timings to help find meshing bottlenecks",
    )
    p.add_argument(
        "--surface-only",
        action="store_true",
        help="export only the watertight, local-frame surface STL and its JSON metadata; "
             "skip TetGen and all volume-mesh outputs",
    )

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


def analyze_cad_solids(
    shape,
    *,
    axis: int,
    unit_scale: float,
    deflection_cad: float,
    angular_deflection: float,
) -> list[dict]:
    """Describe each STEP solid before fusion for part-aware diagnostics.

    Only used when ``--part-aware-simplify`` is requested.  Sampling OCC
    vertices (rather than bounding-box corners) avoids misclassifying circular
    end rings as exterior flight geometry.
    """
    axes = radial_axes(axis)
    rows: list[dict] = []
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    index = 0
    while explorer.More():
        solid = explorer.Current()
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(solid, props)
        volume = float(props.Mass()) * unit_scale**3
        centre = props.CentreOfMass()
        com = np.array([centre.X(), centre.Y(), centre.Z()]) * unit_scale

        box = Bnd_Box()
        BRepBndLib.Add_s(solid, box, True)
        bmin = np.array([box.GetXMin(), box.GetYMin(), box.GetZMin()]) * unit_scale
        bmax = np.array([box.GetXMax(), box.GetYMax(), box.GetZMax()]) * unit_scale

        face_explorer = TopExp_Explorer(solid, TopAbs_FACE)
        n_faces = 0
        while face_explorer.More():
            n_faces += 1
            face_explorer.Next()

        mesher = BRepMesh_IncrementalMesh(
            solid, deflection_cad, False, angular_deflection, True
        )
        mesher.Perform()
        vertex_explorer = TopExp_Explorer(solid, TopAbs_VERTEX)
        sampled = []
        while vertex_explorer.More():
            point = BRep_Tool.Pnt_s(TopoDS.Vertex(vertex_explorer.Current()))
            sampled.append((point.X() * unit_scale, point.Y() * unit_scale, point.Z() * unit_scale))
            vertex_explorer.Next()
        if sampled:
            sampled = np.asarray(sampled, dtype=float)
            radii = np.linalg.norm(sampled[:, axes], axis=1)
            radial_min, radial_max = float(radii.min()), float(radii.max())
        else:
            radial_min = radial_max = float(np.linalg.norm(com[axes]))

        rows.append(
            {
                "index": index,
                "volume_m3": volume,
                "faces": n_faces,
                "com_m": com.tolist(),
                "bbox_min_m": bmin.tolist(),
                "bbox_max_m": bmax.tolist(),
                "radial_min_m": radial_min,
                "radial_max_m": radial_max,
            }
        )
        index += 1
        explorer.Next()
    return rows


def enable_occ_parallel(threads: int | None = None, *, parallel: bool = True) -> int:
    """Enable OpenCASCADE's internal TBB threads for meshing and booleans.

    ``BRepMesh_IncrementalMesh`` already receives an ``isParallel`` flag at some
    call sites, but the Boolean fuse and OCC's thread pool are global settings;
    without this call a 40+ solid assembly fuses one solid at a time on a single
    core.  Returns the number of logical processors OCC will use.
    """
    BRepMesh_IncrementalMesh.SetParallelDefault_s(parallel)
    OSD_Parallel.SetUseOcctThreads_s(parallel)
    if threads and threads > 0:
        # OCP does not expose SetNbThreads directly; OCC honours OMP_NUM_THREADS
        # when it initialises its TBB pool.  Set it early so child tools inherit it.
        os.environ.setdefault("OMP_NUM_THREADS", str(int(threads)))
    return int(OSD_Parallel.NbLogicalProcessors_s())


def _fuse_pair(a, b, *, parallel: bool):
    operation = BRepAlgoAPI_Fuse(a, b)
    operation.SetRunParallel(bool(parallel))
    operation.Build()
    return operation.Shape()


def _fuse_general(solids, *, parallel: bool):
    """One general Boolean fuse of every solid (BOPAlgo_Builder).

    Pairwise fusing rebuilds an ever-growing accumulator and takes ~25 s for a
    40+ solid drum.  Fusing all arguments in a single General Fuse produces the
    identical geometric union in ~3 s.  Splitter edges are unified by the caller
    just as for the pairwise result.
    """
    builder = BOPAlgo_Builder()
    builder.SetRunParallel(bool(parallel))
    for solid in solids:
        builder.AddArgument(solid)
    builder.Perform()
    return builder.Shape()


def fuse_solids(shape, *, min_volume_m3: float = 0.0, unit_scale: float = 1.0,
                method: str = "sequential", parallel: bool = False):
    """Fuse a multi-solid assembly into one valid solid for meshing.

    A STEP assembly usually contains many overlapping components. Meshing the
    compound directly makes MeshFix merge coincident shells without resolving
    overlaps, which corrupts the volume and bounding box. Boolean fusion gives
    one watertight outer/inner surface and a meaningful CAD volume.

    ``method`` selects the Boolean strategy:
      * ``"sequential"`` (default) folds solids one at a time onto an
        accumulator -- the historic production-mesh path, kept unchanged.
      * ``"balanced"`` merges pairwise in a tree; same union, ~3x faster.
      * ``"general"`` fuses all solids at once via BOPAlgo_Builder; same union,
        identical volume/bbox, ~8x faster on large assemblies.

    ``parallel=True`` asks OCC to run the Booleans on its internal thread pool.
    """
    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    solids = []
    while explorer.More():
        solid = explorer.Current()
        if min_volume_m3 > 0.0:
            props = GProp_GProps()
            BRepGProp.VolumeProperties_s(solid, props)
            volume = float(props.Mass()) * unit_scale**3
            if volume < min_volume_m3:
                explorer.Next()
                continue
        solids.append(solid)
        explorer.Next()
    if not solids:
        raise RuntimeError("--min-solid-volume removed every STEP solid")
    kept = len(solids)
    if kept <= 1:
        return solids[0], kept

    if method == "general":
        fused = _fuse_general(solids, parallel=parallel)
    elif method == "balanced":
        current = list(solids)
        while len(current) > 1:
            merged = []
            for i in range(0, len(current) - 1, 2):
                merged.append(_fuse_pair(current[i], current[i + 1], parallel=parallel))
            if len(current) % 2:
                merged.append(current[-1])
            current = merged
        fused = current[0]
    elif method == "sequential":
        fused = solids[0]
        for solid in solids[1:]:
            fused = _fuse_pair(fused, solid, parallel=parallel)
    else:
        raise ValueError(f"unknown fuse method {method!r} "
                         "(expected sequential, balanced, or general)")
    if not BRepCheck_Analyzer(fused).IsValid():
        fixer = ShapeFix_Shape(fused)
        fixer.Perform()
        fused = fixer.Shape()
    if not BRepCheck_Analyzer(fused).IsValid():
        raise RuntimeError("could not fuse the STEP assembly into a valid solid")

    # A Boolean fuse leaves splitter edges where solids touch.  If those edges
    # reach the STL writer, the surface has coincident internal faces: MeshFix
    # then seals the wrong shell and inflates the volume by several hundred
    # percent.  Unify coplanar/cylindrical faces into real CAD faces first.
    unify = ShapeUpgrade_UnifySameDomain(fused, True, False, False)
    unify.Build()
    unified = unify.Shape()
    if BRepCheck_Analyzer(unified).IsValid():
        fused = unified
    # The STEP reader and earlier OCC operations may leave stale triangulations
    # that ignore later deflection settings; remove them before meshing.
    BRepTools.Clean_s(fused)
    return fused, kept


def read_cad(
    step_path: Path,
    *,
    fuse_assembly: bool = True,
    min_volume_m3: float = 0.0,
    unit_scale: float = 1.0,
    axis: int = AXES["Y"],
    angular_deflection: float = 0.15,
    solid_analysis_deflection_cad: float | None = None,
    fuse_method: str = "sequential",
    parallel_fuse: bool = False,
):
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

    solid_features = None
    if solid_analysis_deflection_cad is not None:
        solid_features = analyze_cad_solids(
            shape,
            axis=axis,
            unit_scale=unit_scale,
            deflection_cad=solid_analysis_deflection_cad,
            angular_deflection=angular_deflection,
        )

    kept = solids
    if fuse_assembly:
        shape, kept = fuse_solids(
            shape, min_volume_m3=min_volume_m3, unit_scale=unit_scale,
            method=fuse_method, parallel=parallel_fuse,
        )

    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return shape, solids, faces, float(props.Mass()), kept, solid_features


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


def decimate_surface(
    points: np.ndarray,
    faces: np.ndarray,
    target_faces: int,
    *,
    repair_result: bool = True,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Reduce a surface to roughly ``target_faces`` triangles.

    The CAD triangulation is far finer than the 12 mm CRM SPH spacing.  Using
    it directly as an FEA contact surface creates tens of thousands of flexible
    BCE markers, which overestimates SPH boundary density and produces huge
    artificial contact pressures.  Quadric decimation preserves the overall
    volume while bringing marker spacing close to the SPH spacing.

    ``repair_result`` controls whether MeshFix is invoked after decimation.
    Leave it enabled for the legacy repair-then-decimate route.  Disable it
    when MeshFix will be run explicitly afterward on the smaller surface:
    repairing a fused multi-solid assembly before decimation can inflate the
    volume dramatically.
    """
    points = np.asarray(points, dtype=float)
    faces = np.asarray(faces, dtype=np.int32)
    before = len(faces)
    if target_faces <= 0 or before <= target_faces:
        return points, faces, before

    reduction = 1.0 - target_faces / before
    pv_faces = np.column_stack(
        [np.full(len(faces), 3, dtype=np.int64), faces.astype(np.int64)]
    ).ravel()
    reduced = pv.PolyData(points, pv_faces).decimate(
        target_reduction=reduction, volume_preservation=True
    )
    points = np.asarray(reduced.points, dtype=float)
    faces = reduced.faces.reshape(-1, 4)[:, 1:].astype(np.int32)

    if not repair_result:
        return points, faces, before

    surface = trimesh.Trimesh(points, faces, process=False)
    if not surface.is_watertight:
        repaired = repair_surface(points, faces)
        points = np.asarray(repaired.points, dtype=float)
        faces = repaired.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
        surface = trimesh.Trimesh(points, faces, process=False)
    if not surface.is_watertight:
        raise RuntimeError("surface decimation broke watertightness")
    return points, faces, before


def decimate_to_watertight(
    points: np.ndarray,
    faces: np.ndarray,
    target_faces: int,
    max_faces: int,
) -> tuple[np.ndarray, np.ndarray, int, int]:
    """Decimate as far as possible while keeping a watertight surface.

    Quadric decimation is topology-agnostic: an overly aggressive reduction
    opens holes or creates non-manifold edges in complex bucket drum excavator assemblies.
    MeshFix can close those holes, but on fused shells it often fills the wrong
    side and inflates the volume by hundreds of percent.  Retrying with a
    progressively larger target finds the coarsest topology-safe surface
    without changing the geometry the tet mesher sees.
    """
    points = np.asarray(points, dtype=float)
    faces = np.asarray(faces, dtype=np.int32)
    max_faces = max(max_faces, target_faces)
    target = target_faces
    while True:
        reduced_points, reduced_faces, before = decimate_surface(
            points, faces, target, repair_result=False
        )
        surface = trimesh.Trimesh(reduced_points, reduced_faces, process=False)
        if surface.is_watertight:
            return reduced_points, reduced_faces, before, target
        if target >= max_faces:
            raise RuntimeError(
                f"surface decimation is not watertight even at {target} faces; "
                "increase --surface-max-faces or --surface-target-faces"
            )
        target = min(target * 2, max_faces)


def polydata_from_triangles(points: np.ndarray, faces: np.ndarray) -> pv.PolyData:
    """Build a PyVista triangle mesh from an ``(N, 3)`` face array."""
    points = np.asarray(points, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)
    connectivity = np.column_stack(
        [np.full(len(faces), 3, dtype=np.int64), faces]
    ).ravel()
    return pv.PolyData(points, connectivity)


def exterior_vertex_indices(
    points: np.ndarray,
    faces: np.ndarray,
    *,
    axis: int,
    radius_cad: float,
) -> np.ndarray:
    """Return vertices belonging to faces outside the radial hub/interior band."""
    points = np.asarray(points, dtype=float)
    faces = np.asarray(faces, dtype=np.int32)
    if len(faces) == 0:
        return np.empty(0, dtype=np.int64)
    axes = radial_axes(axis)
    radii = np.linalg.norm(points[:, axes], axis=1)
    exterior_faces = (radii[faces] > radius_cad).all(axis=1)
    if not np.any(exterior_faces):
        return np.empty(0, dtype=np.int64)
    return np.unique(faces[exterior_faces])


def evenly_spaced_indices(count: int, limit: int) -> np.ndarray:
    """Deterministically cap a sample while covering the whole index range."""
    if count <= 0:
        return np.empty(0, dtype=np.int64)
    if count <= limit:
        return np.arange(count, dtype=np.int64)
    return np.unique(np.linspace(0, count - 1, limit, dtype=np.int64))


def distance_percentiles(distances: np.ndarray) -> dict[str, float]:
    """Summarise a nearest-surface distance sample in metres."""
    if distances.size == 0:
        return {"max_m": 0.0, "p999_m": 0.0, "mean_m": 0.0}
    return {
        "max_m": float(np.max(distances)),
        "p999_m": float(np.percentile(distances, 99.9)),
        "mean_m": float(np.mean(distances)),
    }


def nearest_surface_distances(
    query_points_m: np.ndarray,
    base_mesh_m: pv.PolyData,
) -> np.ndarray:
    """Distance from each query point to the closest triangle of ``base_mesh_m``."""
    if query_points_m.shape[0] == 0:
        return np.empty(0, dtype=float)
    _cells, closest = base_mesh_m.find_closest_cell(
        query_points_m, return_closest_point=True
    )
    return np.linalg.norm(query_points_m - np.asarray(closest, dtype=float), axis=1)


def measure_exterior_fit(
    raw_points: np.ndarray,
    raw_faces: np.ndarray,
    raw_mesh_m: pv.PolyData,
    candidate_points: np.ndarray,
    candidate_faces: np.ndarray,
    *,
    axis: int,
    exterior_radius_cad: float,
    unit_scale: float,
    tolerance_m: float,
    sample_limit: int = 20000,
) -> dict:
    """Measure bidirectional exterior deviation from the raw OCC surface.

    Only faces outside the radial hub/interior band are sampled, so the fit
    check protects the bucket drum excavator exterior detail while allowing the hub to
    be coarsened.  The reported error is the larger of the two p99.9 distances;
    maxima are retained for diagnostics.  Distances are measured in metres in
    the original CAD frame, before the export-axis rotation.
    """
    if tolerance_m <= 0.0:
        return {
            "enabled": False,
            "tolerance_m": 0.0,
            "exterior_radius_m": exterior_radius_cad * unit_scale,
            "candidate_to_raw": distance_percentiles(np.empty(0)),
            "raw_to_candidate": distance_percentiles(np.empty(0)),
            "error_m": 0.0,
            "passed": True,
        }

    raw_points = np.asarray(raw_points, dtype=float)
    raw_faces = np.asarray(raw_faces, dtype=np.int32)
    candidate_points = np.asarray(candidate_points, dtype=float)
    candidate_faces = np.asarray(candidate_faces, dtype=np.int32)

    raw_ids = exterior_vertex_indices(
        raw_points, raw_faces, axis=axis, radius_cad=exterior_radius_cad
    )
    candidate_ids = exterior_vertex_indices(
        candidate_points, candidate_faces, axis=axis, radius_cad=exterior_radius_cad
    )
    raw_sample_m = raw_points[raw_ids[evenly_spaced_indices(len(raw_ids), sample_limit)]] * unit_scale
    candidate_sample_m = (
        candidate_points[candidate_ids[evenly_spaced_indices(len(candidate_ids), sample_limit)]]
        * unit_scale
    )

    if candidate_sample_m.shape[0]:
        c2b_distances = nearest_surface_distances(candidate_sample_m, raw_mesh_m)
    else:
        c2b_distances = np.empty(0, dtype=float)
    if raw_sample_m.shape[0] and candidate_faces.shape[0]:
        candidate_mesh_m = polydata_from_triangles(
            candidate_points * unit_scale, candidate_faces
        )
        b2c_distances = nearest_surface_distances(raw_sample_m, candidate_mesh_m)
    else:
        b2c_distances = np.empty(0, dtype=float)

    c2b = distance_percentiles(c2b_distances)
    b2c = distance_percentiles(b2c_distances)
    error = max(c2b["p999_m"], b2c["p999_m"])
    return {
        "enabled": True,
        "tolerance_m": float(tolerance_m),
        "exterior_radius_m": float(exterior_radius_cad * unit_scale),
        "candidate_to_raw": c2b,
        "raw_to_candidate": b2c,
        "error_m": float(error),
        "passed": bool(error <= tolerance_m + 1.0e-12),
    }


def simplify_interior_region(
    points: np.ndarray,
    faces: np.ndarray,
    *,
    axis: int,
    radius_cad: float,
    unit_scale: float,
    reduction: float,
    min_faces: int,
) -> tuple[np.ndarray, np.ndarray, dict]:
    """Decimate only the hub/interior patch of a watertight surface.

    Faces with *all* vertices inside ``radius_cad`` are eligible.  Their shared
    boundary with the rest of the surface is extracted as an open patch and
    simplified with ``vtkDecimatePro`` configured to preserve topology and never
    delete boundary vertices.  The protected (exterior/flight) faces are copied
    verbatim, so their geometry is bit-for-bit identical to the validated fine
    surface.  retries lower the requested reduction if a watertight result
    cannot be produced.
    """
    points = np.asarray(points, dtype=float)
    faces = np.asarray(faces, dtype=np.int32)
    axes = radial_axes(axis)
    radii = np.linalg.norm(points[:, axes], axis=1)
    interior_mask = (radii[faces] <= radius_cad).all(axis=1)
    interior_faces = faces[interior_mask]
    protected_faces = faces[~interior_mask]

    meta = {
        "enabled": True,
        "applied": False,
        "reason": "",
        "radius_m": radius_cad * unit_scale,
        "radius_cad_units": radius_cad,
        "reduction_requested": reduction,
        "faces_eligible": int(len(interior_faces)),
        "faces_protected": int(len(protected_faces)),
        "min_faces_required": int(min_faces),
    }
    if len(interior_faces) < min_faces:
        meta["reason"] = (
            f"only {len(interior_faces)} interior faces found (< {min_faces}); "
            "left the surface unchanged"
        )
        return points, faces, meta
    if len(protected_faces) == 0:
        meta["reason"] = "no protected exterior faces found; left the surface unchanged"
        return points, faces, meta

    patch_vertices = np.unique(interior_faces)
    protected_vertices = np.unique(protected_faces)
    boundary_vertices = np.intersect1d(patch_vertices, protected_vertices, assume_unique=True)
    local_index = np.full(len(points), -1, dtype=np.int64)
    local_index[patch_vertices] = np.arange(len(patch_vertices), dtype=np.int64)
    patch_faces = local_index[interior_faces]
    patch_points = points[patch_vertices]

    parent = np.arange(len(patch_points), dtype=np.int64)

    def find_root(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = int(parent[node])
        return node

    for tri in patch_faces:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2])):
            root_a, root_b = find_root(int(a)), find_root(int(b))
            if root_a != root_b:
                parent[root_b] = root_a
    patch_components = len({find_root(int(i)) for i in local_index[patch_vertices]})

    def coordinate_key(point: np.ndarray) -> tuple[float, ...]:
        return tuple(np.round(point, 9))

    original_coords = {coordinate_key(point): i for i, point in enumerate(points)}
    boundary_keys = {coordinate_key(points[i]) for i in boundary_vertices}

    current_reduction = reduction
    attempts: list[dict] = []
    last_error = ""
    for attempt in range(4):
        target_faces = max(4, int(round(len(patch_faces) * (1.0 - current_reduction))))
        patch = pv.PolyData(
            patch_points,
            np.column_stack(
                [np.full(len(patch_faces), 3, dtype=np.int64), patch_faces.astype(np.int64)]
            ).ravel(),
        )
        try:
            reduced = patch.decimate_pro(
                reduction=1.0 - target_faces / len(patch_faces),
                feature_angle=45.0,
                split_angle=75.0,
                splitting=False,
                preserve_topology=True,
                boundary_vertex_deletion=False,
            )
            reduced_points = np.asarray(reduced.points, dtype=float)
            reduced_faces = reduced.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
        except Exception as exc:  # pragma: no cover - defensive against VTK edge cases
            last_error = f"decimation failed: {exc}"
            current_reduction *= 0.5
            continue

        remapped = np.empty(len(reduced_points), dtype=np.int64)
        appended: list[np.ndarray] = []
        appended_keys: dict[tuple[float, ...], int] = {}
        for j, point in enumerate(reduced_points):
            key = coordinate_key(point)
            original = original_coords.get(key)
            if original is not None:
                remapped[j] = original
                continue
            appended_index = appended_keys.get(key)
            if appended_index is None:
                appended_index = len(points) + len(appended)
                appended_keys[key] = appended_index
                appended.append(point)
            remapped[j] = appended_index

        remaining_boundary = boundary_keys - {
            coordinate_key(point) for point in reduced_points
        }
        candidate_faces = np.vstack([protected_faces, remapped[reduced_faces]])
        degenerate = np.any(
            (candidate_faces[:, 0] == candidate_faces[:, 1])
            | (candidate_faces[:, 1] == candidate_faces[:, 2])
            | (candidate_faces[:, 2] == candidate_faces[:, 0])
        )
        watertight = False
        if not degenerate and not remaining_boundary:
            candidate_points = (
                np.vstack([points, np.asarray(appended, dtype=float)])
                if appended
                else points.copy()
            )
            watertight = bool(
                trimesh.Trimesh(candidate_points, candidate_faces, process=False).is_watertight
            )
        attempts.append(
            {
                "attempt": attempt + 1,
                "reduction": current_reduction,
                "faces_after": int(len(reduced_faces)),
                "boundary_vertices_missing": len(remaining_boundary),
                "watertight": watertight,
            }
        )
        if watertight:
            surface_before = trimesh.Trimesh(points, faces, process=False)
            surface_after = trimesh.Trimesh(candidate_points, candidate_faces, process=False)
            meta.update(
                {
                    "applied": True,
                    "reason": (
                        f"interior faces within {radius_cad * unit_scale:g} m of the drum axis "
                        "decimated with fixed boundary and topology preservation; exterior "
                        "faces copied unchanged"
                    ),
                    "reduction_achieved": 1.0 - len(reduced_faces) / len(patch_faces),
                    "reduction_used": current_reduction,
                    "attempts": attempts,
                    "interior_patches": patch_components,
                    "boundary_vertices": len(boundary_vertices),
                    "faces_before": int(len(faces)),
                    "faces_after": int(len(candidate_faces)),
                    "surface_volume_before_m3": abs(float(surface_before.volume)) * unit_scale**3,
                    "surface_volume_after_m3": abs(float(surface_after.volume)) * unit_scale**3,
                    "exterior_faces_unchanged": True,
                }
            )
            return candidate_points, candidate_faces, meta
        last_error = (
            f"watertight={watertight}, missing boundary vertices={len(remaining_boundary)}, "
            f"degenerate={degenerate}"
        )
        current_reduction *= 0.5

    meta["reason"] = f"part-aware simplification skipped: {last_error}"
    meta["attempts"] = attempts
    return points, faces, meta


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
    if args.surface_fit_tolerance < 0.0:
        raise SystemExit("--surface-fit-tolerance must be non-negative")
    if args.hub_radius < 0.0:
        raise SystemExit("--hub-radius must be non-negative")
    if args.part_aware_radius <= 0.0:
        raise SystemExit("--part-aware-radius must be positive")
    if not 0.0 <= args.part_aware_reduction < 1.0:
        raise SystemExit("--part-aware-reduction must be in [0, 1)")
    if args.part_aware_min_faces < 0:
        raise SystemExit("--part-aware-min-faces must be non-negative")
    if args.part_aware_solid_analysis_deflection is not None and args.part_aware_solid_analysis_deflection <= 0.0:
        raise SystemExit("--part-aware-solid-analysis-deflection must be positive")
    if args.part_aware_simplify and args.repair_after_decimation:
        raise SystemExit(
            "--part-aware-simplify is not compatible with the legacy "
            "--repair-after-decimation route"
        )

    prefix = (args.out_prefix or (Path("fea") / f"{step_path.stem}_fea")).resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)

    if not args.no_occ_parallel:
        BRepMesh_IncrementalMesh.SetParallelDefault_s(True)

    last_mark = time.perf_counter()

    def mark(label: str) -> None:
        nonlocal last_mark
        now = time.perf_counter()
        if args.time_stages:
            print(f"[mesh timing] {label}: {now - last_mark:.2f} s", file=sys.stderr)
        last_mark = now

    source_to_local = source_axis_to_z(args.axis)
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

    solid_analysis_deflection = (
        args.part_aware_solid_analysis_deflection
        if args.part_aware_solid_analysis_deflection is not None
        else surface_deflection
    )
    shape, n_solids, n_faces, cad_volume_cad, kept_solids, solid_features = read_cad(
        step_path,
        fuse_assembly=not args.no_fuse_assembly,
        min_volume_m3=args.min_solid_volume,
        unit_scale=args.unit_scale,
        axis=args.axis,
        angular_deflection=args.angular_deflection,
        solid_analysis_deflection_cad=(
            solid_analysis_deflection / args.unit_scale if args.part_aware_simplify else None
        ),
    )
    cad_volume = cad_volume_cad * args.unit_scale**3
    mark("read/fuse CAD")

    stl_path = prefix.parent / f"{prefix.name}_surface.stl"
    write_surface_stl(
        shape,
        stl_path,
        deflection_cad=surface_deflection / args.unit_scale,
        angular_deflection=args.angular_deflection,
    )
    mark("OCC surface tessellation")
    raw = pv.read(str(stl_path))
    raw_points = np.asarray(raw.points, dtype=float)
    raw_faces = raw.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
    raw_surface = trimesh.Trimesh(vertices=raw_points, faces=raw_faces, process=True)
    raw_watertight = bool(raw_surface.is_watertight)
    raw_fit_mesh_m = (
        polydata_from_triangles(raw_points * args.unit_scale, raw_faces)
        if args.surface_fit_tolerance > 0.0
        else None
    )
    meshfix_used = False
    surface_target_used = args.surface_target_faces

    if args.repair_after_decimation and args.skip_decimation:
        raise SystemExit("--repair-after-decimation and --skip-decimation are mutually exclusive")

    if args.repair_after_decimation:
        # Legacy route, kept for compatibility.  It can inflate the volume on
        # fused assemblies; prefer the default topology-preserving route below.
        clean_points, clean_faces, faces_before_decimation = decimate_surface(
            raw_points, raw_faces, args.surface_target_faces, repair_result=False
        )
        if args.skip_meshfix:
            decimated = trimesh.Trimesh(clean_points, clean_faces, process=False)
            if not decimated.is_watertight:
                raise SystemExit(
                    "--skip-meshfix requires a watertight surface; the decimated OCC "
                    "surface is not watertight"
                )
            print("Skipping MeshFix: decimated OCC surface is watertight", file=sys.stderr)
        else:
            clean = repair_surface(clean_points, clean_faces)
            clean_points = np.asarray(clean.points, dtype=float)
            clean_faces = clean.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
            meshfix_used = True
        repaired_points, repaired_faces = clean_points, clean_faces
        surface_pipeline = "OpenCASCADE STL -> quadric decimation -> MeshFix -> TetGen"
    elif not raw_watertight:
        raise RuntimeError(
            "the unified OCC surface is not watertight; refine --surface-deflection "
            "or use --repair-after-decimation (legacy, may inflate volume)"
        )
    else:
        clean_points = np.asarray(raw_surface.vertices, dtype=float)
        clean_faces = np.asarray(raw_surface.faces, dtype=np.int32)
        if args.skip_decimation or args.surface_target_faces == 0:
            repaired_points, repaired_faces = clean_points, clean_faces
            faces_before_decimation = len(clean_faces)
            surface_target_used = 0
        else:
            (
                repaired_points,
                repaired_faces,
                faces_before_decimation,
                surface_target_used,
            ) = decimate_to_watertight(
                clean_points,
                clean_faces,
                args.surface_target_faces,
                args.surface_max_faces,
            )
            if surface_target_used != args.surface_target_faces:
                print(
                    f"Raised surface target from {args.surface_target_faces} to "
                    f"{surface_target_used} faces to keep the surface watertight",
                    file=sys.stderr,
                )
        surface_pipeline = "OpenCASCADE STL -> topology-preserving decimation -> TetGen"

    part_aware_meta = {
        "enabled": bool(args.part_aware_simplify),
        "applied": False,
        "reason": "optional part-aware path was not requested",
    }

    def apply_part_aware(pts: np.ndarray, fcs: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
        return simplify_interior_region(
            pts,
            fcs,
            axis=args.axis,
            radius_cad=args.part_aware_radius / args.unit_scale,
            unit_scale=args.unit_scale,
            reduction=args.part_aware_reduction,
            min_faces=args.part_aware_min_faces,
        )

    if args.part_aware_simplify:
        repaired_points, repaired_faces, part_aware_meta = apply_part_aware(
            repaired_points, repaired_faces
        )
        surface_pipeline = surface_pipeline.replace(
            " -> TetGen", " -> part-aware interior decimation -> TetGen"
        )
        print(
            "[part-aware] "
            f"eligible faces={part_aware_meta['faces_eligible']} "
            f"applied={part_aware_meta['applied']} "
            f"faces={part_aware_meta.get('faces_before', len(repaired_faces))}->"
            f"{part_aware_meta.get('faces_after', len(repaired_faces))}",
            file=sys.stderr,
        )
        if not part_aware_meta["applied"]:
            print(f"[part-aware] skipped: {part_aware_meta['reason']}", file=sys.stderr)
    mark("surface repair/decimation")

    if args.tetgen_switches:
        switches = args.tetgen_switches
    else:
        max_volume_cad = max_tet_volume / args.unit_scale**3
        switches = (
            f"pq{args.tet_quality:g}/{args.min_dihedral:g}a{max_volume_cad:.10g}YQ"
        )

    # Quadric decimation can leave a watertight surface that still has tiny
    # folded/self-intersecting triangles or drifts away from the fine exterior.
    # The measured fit check and TetGen are both authoritative: retry with
    # progressively finer targets until each accepts the surface.
    can_retry_faces = (
        not args.skip_decimation
        and not args.repair_after_decimation
        and args.surface_target_faces > 0
    )
    retry_ceiling = max(args.surface_max_faces, args.surface_target_faces)
    fit_radius_cad = max(args.part_aware_radius, args.hub_radius) / args.unit_scale
    surface_target_current = surface_target_used
    surface_fit_meta: dict = {
        "enabled": args.surface_fit_tolerance > 0.0,
        "tolerance_m": args.surface_fit_tolerance,
        "passed": True,
        "error_m": 0.0,
    }

    def advance_surface_target(reason: str) -> None:
        """Double the surface face target, or fail if the retry ceiling is reached."""
        nonlocal surface_target_current
        if not can_retry_faces:
            raise SystemExit(
                f"{reason}; surface retries are disabled by skip-decimation, "
                "MeshFix repair-after-decimation, or a zero face target"
            )
        base = max(surface_target_current, surface_target_used, args.surface_target_faces)
        next_target = min(base * 2, retry_ceiling)
        if next_target <= base:
            raise SystemExit(
                f"{reason}; surface target already reached the retry ceiling "
                f"of {retry_ceiling} faces"
            )
        surface_target_current = next_target
        print(
            f"Raising surface target from {base} to {next_target} faces: {reason}",
            file=sys.stderr,
        )

    while True:
        if can_retry_faces and surface_target_current != surface_target_used:
            (
                repaired_points,
                repaired_faces,
                faces_before_decimation,
                surface_target_used,
            ) = decimate_to_watertight(
                clean_points,
                clean_faces,
                surface_target_current,
                retry_ceiling,
            )
            if args.part_aware_simplify:
                repaired_points, repaired_faces, part_aware_meta = apply_part_aware(
                    repaired_points, repaired_faces
                )

        if raw_fit_mesh_m is not None:
            surface_fit_meta = measure_exterior_fit(
                raw_points,
                raw_faces,
                raw_fit_mesh_m,
                repaired_points,
                repaired_faces,
                axis=args.axis,
                exterior_radius_cad=fit_radius_cad,
                unit_scale=args.unit_scale,
                tolerance_m=args.surface_fit_tolerance,
            )
            print(
                "[surface-fit] "
                f"target={surface_target_used} error={surface_fit_meta['error_m']:.6g} m "
                f"tolerance={args.surface_fit_tolerance:.6g} m "
                f"passed={surface_fit_meta['passed']}",
                file=sys.stderr,
            )
            if not surface_fit_meta["passed"]:
                advance_surface_target(
                    f"exterior fit error {surface_fit_meta['error_m']:.6g} m exceeds "
                    f"{args.surface_fit_tolerance:.6g} m"
                )
                continue

        # OCC leaves a few very long triangles on planar end faces.  Adaptive
        # longest-edge bisection fixes those without inflating already fine
        # curved regions, and unlike VTK's subdivision it preserves indices.
        max_surface_edge_cad = args.mesh_size / args.unit_scale
        surface_points, surface_faces = trimesh.remesh.subdivide_to_size(
            repaired_points, repaired_faces, max_edge=max_surface_edge_cad, max_iter=20
        )
        if not trimesh.Trimesh(surface_points, surface_faces, process=False).is_watertight:
            raise RuntimeError("surface subdivision broke watertightness")

        # Rotate into the FEA local frame (+Z drum axis).  Keep TetGen in raw CAD
        # units so its switches match the validated pipeline; only the exported
        # CRM contact STL is scaled into metres.
        surface_points = np.asarray(surface_points, dtype=float) @ source_to_local.T
        surface_points_m = surface_points * args.unit_scale
        # Decimation can leave near-coincident vertices differing by only one
        # float32 ULP (~8 nm at this drum scale).  TetGen preserves those
        # one-ULP edges as sliver tetrahedra, so merge them on a 0.1 um grid
        # and repair any topology the merge collapses.
        merged = trimesh.Trimesh(
            np.round(surface_points_m, decimals=7),
            surface_faces,
            process=True,
        )
        if merged.is_watertight:
            surface_points_m = np.asarray(merged.vertices, dtype=float)
            surface_faces = np.asarray(merged.faces, dtype=np.int32)
        else:
            repaired = repair_surface(np.asarray(merged.vertices, dtype=float), merged.faces)
            surface_points_m = np.asarray(repaired.points, dtype=float)
            surface_faces = repaired.faces.reshape(-1, 4)[:, 1:].astype(np.int32)
        surface_points = surface_points_m / args.unit_scale
        local_surface = trimesh.Trimesh(surface_points_m, surface_faces, process=False)
        if args.surface_only and not local_surface.is_watertight:
            raise RuntimeError("surface-only export is not watertight after scaling/repair")
        local_surface.export(stl_path)

        if args.surface_only:
            surface_metadata = {
                "source_step": str(step_path),
                "pipeline": surface_pipeline.replace(" -> TetGen", " -> local-frame surface export"),
                "surface_only": True,
                "meshfix_used": meshfix_used,
                "raw_surface_watertight": raw_watertight,
                "outputs": {"surface_stl": str(stl_path)},
                "units": "metres",
                "unit_scale": args.unit_scale,
                "cad_solids": n_solids,
                "cad_solids_kept": kept_solids,
                "cad_solids_dropped": n_solids - kept_solids,
                "min_solid_volume_m3": args.min_solid_volume,
                "cad_faces": n_faces,
                "surface_deflection_m": surface_deflection,
                "angular_deflection_rad": args.angular_deflection,
                "max_surface_edge_m": args.mesh_size,
                "surface_target_faces_requested": args.surface_target_faces,
                "surface_target_faces_used": surface_target_used,
                "surface_fit_tolerance_m": args.surface_fit_tolerance,
                "surface_fit_status": (
                    "disabled" if not surface_fit_meta["enabled"]
                    else "pass" if surface_fit_meta["passed"] else "fail"
                ),
                "surface_fit": surface_fit_meta,
                "surface_faces_before_decimation": int(faces_before_decimation),
                "surface_points": int(len(surface_points_m)),
                "surface_faces": int(len(surface_faces)),
                "source_drum_axis": AXES_TO_NAME[args.axis],
                "export_drum_axis": "Z",
                "bbox_min_m": np.asarray(surface_points_m).min(axis=0).tolist(),
                "bbox_max_m": np.asarray(surface_points_m).max(axis=0).tolist(),
                "cad_volume_m3": cad_volume,
            }
            if args.part_aware_simplify:
                surface_metadata.update(
                    {
                        "part_aware": part_aware_meta,
                        "part_aware_solid_analysis_deflection_m": solid_analysis_deflection,
                    }
                )
            surface_meta_path = prefix.with_suffix(".json")
            with surface_meta_path.open("w", encoding="utf-8") as f:
                json.dump(surface_metadata, f, indent=2)
                f.write("\n")
            if surface_fit_meta["enabled"]:
                print(
                    f"  exterior fit error={surface_fit_meta['error_m']:.6g} m "
                    f"(tolerance {args.surface_fit_tolerance:.6g} m)"
                )
                if not surface_fit_meta["passed"]:
                    raise SystemExit("surface fit tolerance was not met")
            print(f"Wrote {stl_path}")
            print(f"  surface points={len(surface_points_m)} faces={len(surface_faces)}")
            mark("surface-only export")
            return 0

        tet = tetgen.TetGen(surface_points, surface_faces)
        try:
            nodes_cad, tets, _attributes, _markers = tet.tetrahedralize(switches=switches)
        except RuntimeError as exc:
            retryable = (
                "self-intersect" in str(exc).lower()
                and not args.skip_decimation
                and not args.repair_after_decimation
                and not args.skip_meshfix
                and args.surface_target_faces > 0
            )
            if not retryable:
                raise
            advance_surface_target("TetGen rejected the surface for self-intersections")
            continue
        break
    mark("surface subdivision/axis rotation and TetGen volume meshing")
    if nodes_cad.shape[0] == 0 or tets.shape[0] == 0:
        raise RuntimeError("TetGen produced an empty volume mesh")

    points = np.asarray(nodes_cad, dtype=float) * args.unit_scale
    points = points @ rotation.T + translation
    tets = np.asarray(tets, dtype=np.int32)
    if tets.min() < 0 or tets.max() >= len(points):
        raise RuntimeError("TetGen returned out-of-range node indices")

    surface_nodes_0 = np.unique(np.asarray(tet.trifaces, dtype=np.int32))
    surface_ids = (surface_nodes_0 + 1).tolist()

    hub_radial_axes = [0, 1]
    axial = points[:, 2]
    radial = np.linalg.norm(points[:, hub_radial_axes], axis=1)
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
    # Sliver tets with nanometre edges make the FEA stiffness matrix singular in
    # practice.  Reject them here instead of letting the solver emit NaNs or
    # crash inside Pardiso/MKL.
    if quality["median_edge_m"] > 0.0 and quality["min_edge_m"] < 1.0e-5 * quality["median_edge_m"]:
        raise SystemExit(
            f"mesh has degenerate tetrahedra: min edge {quality['min_edge_m']:.3g} m is "
            f"<1e-5 of median edge {quality['median_edge_m']:.3g} m. "
            "Refine the surface or adjust --surface-target-faces before running FEA."
        )

    write_outputs(prefix, points, tets, hub_ids, surface_ids, args.hub_node_set, args.surface_node_set)
    mark("write mesh outputs")

    metadata = {
        "source_step": str(step_path),
        "pipeline": surface_pipeline,
        "meshfix_used": meshfix_used,
        "raw_surface_watertight": raw_watertight,
        "outputs": {
            "abaqus": str(prefix.with_suffix(".inp")),
            "tetgen_node": str(prefix.with_suffix(".node")),
            "tetgen_ele": str(prefix.with_suffix(".ele")),
            "surface_stl": str(stl_path),
        },
        "units": "metres",
        "unit_scale": args.unit_scale,
        "cad_solids": n_solids,
        "cad_solids_kept": kept_solids,
        "cad_solids_dropped": n_solids - kept_solids,
        "min_solid_volume_m3": args.min_solid_volume,
        "cad_faces": n_faces,
        "surface_deflection_m": surface_deflection,
        "angular_deflection_rad": args.angular_deflection,
        "max_surface_edge_m": args.mesh_size,
        "surface_target_faces_requested": args.surface_target_faces,
        "surface_target_faces_used": surface_target_used,
        "surface_fit_tolerance_m": args.surface_fit_tolerance,
        "surface_fit_status": (
            "disabled" if not surface_fit_meta["enabled"]
            else "pass" if surface_fit_meta["passed"] else "fail"
        ),
        "surface_fit": surface_fit_meta,
        "surface_faces_before_decimation": int(faces_before_decimation),
        "surface_points": int(len(surface_points)),
        "surface_faces": int(len(surface_faces)),
        "tetgen_switches": switches,
        "source_drum_axis": AXES_TO_NAME[args.axis],
        "export_drum_axis": "Z",
        "nodes": int(len(points)),
        "tetrahedra": int(len(tets)),
        "hub_node_set": args.hub_node_set,
        "hub_nodes": len(hub_ids),
        "hub_radius_m": args.hub_radius,
        "hub_selection": (
            f"radial distance <= {args.hub_radius:g} m about exported local Z axis "
            f"(source axis {AXES_TO_NAME[args.axis]})"
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
    if args.part_aware_simplify:
        metadata.update(
            {
                "part_aware": part_aware_meta,
                "part_aware_classified_interior_solids": [
                    feature
                    for feature in (solid_features or [])
                    if feature["volume_m3"] >= args.min_solid_volume
                    and feature["radial_max_m"] <= args.part_aware_radius
                ],
                "part_aware_solid_analysis_deflection_m": solid_analysis_deflection,
            }
        )
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
    if surface_fit_meta["enabled"]:
        print(
            f"  exterior fit error={surface_fit_meta['error_m']:.6g} m "
            f"(tolerance {args.surface_fit_tolerance:.6g} m)"
        )
        if not surface_fit_meta["passed"]:
            raise SystemExit("surface fit tolerance was not met")
    if volume_error > args.max_volume_error:
        raise SystemExit(
            f"mesh volume error {volume_error:.3%} exceeds --max-volume-error "
            f"{args.max_volume_error:.3%}; refine --surface-deflection or --max-tet-volume"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
