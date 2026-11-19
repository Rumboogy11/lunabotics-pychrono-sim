#!/usr/bin/env python3
"""One-way CRM -> FEA lunar excavator structural driver.

This variant avoids the unstable two-way SPH/flex-BCE coupling in
``lunar_excavator_crm_fea.py``.  A rigid-body Chrono::CRM run first records the
soil wrench (force and torque) acting on the drum at every timestep with
``--loads-file``.  This driver then replays that wrench on the solid PLA mesh:

* the same motor-driven spindle provides the prescribed descent and spin;
* hub nodes are tied to the spindle with ``ChLinkNodeFrame``;
* the soil wrench is transformed into the drum's local frame and distributed
  over exterior solid nodes as equivalent nodal forces (minimum-norm, lumped
  mass weighted), reproducing both the net force and the net torque;
* no CRMTerrain/SPH system is created, so flexible BCE markers cannot corrupt
  the contact solution.

The result is a structural response to measured rigid-CRM soil loads, not a
fully coupled FSI result.  It is intended as a defensible engineering estimate
of PLA stress while the two-way coupled path remains under investigation.
PardisoMKL is used by default when available because it is much faster than
Eigen SparseLU and gives identical results for this model; GMRES does not
enforce the spindle/hub constraints accurately with this stiffness matrix.

Usage
-----
    # 1. rigid CRM production run with wrench export
    ./crm/run.sh --duration 0.2 --spacing 0.012 --dt 0.00025 \\
        --loads-file Output_OneWay/crm/soil_loads.csv --output-dir Output_OneWay/crm

    # 2. replay the wrench on the FEA drum (default dt=2e-3 s, PardisoMKL)
    ./crm/run_oneway_fea.sh --duration 0.2 \\
        --loads Output_OneWay/crm/soil_loads.csv \\
        --output-dir Output_OneWay/fea

Use identical motion options (settle time, descent speed, bed height, spin axis,
and omega) in both runs; the driver checks that the recorded drum trajectory
matches the FEA motion.  The reported peak stress excludes tetrahedra below
``--min-stress-volume`` (default 1e-12 m^3) to avoid sliver-element artifacts;
the raw peak is also written to the CSV.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lunar_excavator_crm as rigid  # noqa: E402
import lunar_excavator_crm_fea as fea_driver  # noqa: E402
from lunar_excavator_config import DEFAULT as MOTION_DEFAULT  # noqa: E402

ONEWAY_FIELDS = [
    "frame",
    "time_s",
    "wall_s",
    "drum_z_m",
    "spin_deg",
    "omega_rad_s",
    "linear_motor_pos_m",
    "kinematic_descent_error_m",
    "motor_angle_rad",
    "motor_angle_error_rad",
    "motor_speed_rad_s",
    "applied_force_n",
    "applied_torque_nm",
    "applied_axis_torque_nm",
    "input_power_w",
    "work_j",
    "max_deformation_m",
    "max_von_mises_pa",
    "max_von_mises_raw_pa",
    "hot_element_volume_m3",
]


def read_tetgen_ele(path: Path) -> np.ndarray:
    """Read TetGen .ele connectivity as a (n_tet, 4) int64 array (0-based)."""
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        header = handle.readline().split()
        if not header:
            return np.empty((0, 4), dtype=np.int64)
        n_elem = int(header[0])
        conn = np.empty((n_elem, 4), dtype=np.int64)
        i = 0
        for line in handle:
            parts = line.split()
            if len(parts) < 5:
                continue
            conn[i, 0] = int(parts[1]) - 1
            conn[i, 1] = int(parts[2]) - 1
            conn[i, 2] = int(parts[3]) - 1
            conn[i, 3] = int(parts[4]) - 1
            i += 1
            if i >= n_elem:
                break
        return conn[:i]


def _vtk_append_array(data, name: str, values: np.ndarray, components: int = 1) -> None:
    import vtk  # type: ignore

    arr = vtk.vtkDoubleArray()
    arr.SetName(name)
    arr.SetNumberOfComponents(components)
    n = len(values)
    arr.SetNumberOfTuples(n)
    flat = np.asarray(values, dtype=float)
    if components == 1:
        flat = flat.reshape(-1)
        for i in range(n):
            arr.SetValue(i, float(flat[i]))
    else:
        flat = flat.reshape(n, components)
        for i in range(n):
            arr.SetTuple(i, [float(x) for x in flat[i]])
    data.AddArray(arr)


class FeaVtkExporter:
    """Write per-frame FEA unstructured-grid VTU files and a PVD collection."""

    def __init__(self, out_dir: Path, connectivity: np.ndarray, ref_positions: np.ndarray):
        self.out_dir = Path(out_dir)
        self.frames_dir = self.out_dir / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.connectivity = np.asarray(connectivity, dtype=np.int64)
        self.ref_positions = np.asarray(ref_positions, dtype=float)
        self.entries: list[tuple[float, str]] = []

    def write_frame(self, fea, mesh, nodes, local_positions: np.ndarray,
                    spindle, frame_index: int, time_s: float) -> Path | None:
        try:
            import vtk  # type: ignore
        except Exception as exc:  # pragma: no cover
            print(f"  VTK export unavailable: {exc}", flush=True)
            return None

        n_nodes = len(nodes)
        points = np.empty((n_nodes, 3), dtype=float)
        for i, node in enumerate(nodes):
            points[i] = rigid.vec_parts(node.GetPos())

        # Deformation relative to the current rigid spindle pose.
        q_tuple = rigid.quat_parts(spindle.GetRot())
        center = np.asarray(rigid.vec_parts(spindle.GetPos()), dtype=float)
        expected = fea_driver.rotate_vectors(q_tuple, local_positions) + center
        disp = points - expected
        disp_mag = np.linalg.norm(disp, axis=1)

        grid = vtk.vtkUnstructuredGrid()
        vtk_points = vtk.vtkPoints()
        vtk_points.SetDataTypeToDouble()
        for p in points:
            vtk_points.InsertNextPoint(float(p[0]), float(p[1]), float(p[2]))
        grid.SetPoints(vtk_points)

        tet_type = vtk.VTK_TETRA
        conn = self.connectivity
        for j in range(conn.shape[0]):
            grid.InsertNextCell(tet_type, 4)
            grid.InsertCellPoint(int(conn[j, 0]))
            grid.InsertCellPoint(int(conn[j, 1]))
            grid.InsertCellPoint(int(conn[j, 2]))
            grid.InsertCellPoint(int(conn[j, 3]))

        _vtk_append_array(grid.GetPointData(), "Displacement", disp, components=3)
        _vtk_append_array(grid.GetPointData(), "DisplacementMagnitude", disp_mag, components=1)
        _vtk_append_array(grid.GetPointData(), "Deformation_mm", disp_mag * 1.0e3, components=1)

        # Per-element von Mises stress (Pa). Export cadence/stride controls cost.
        n_elem = mesh.GetNumElements()
        vm = np.zeros(n_elem, dtype=float)
        for i in range(n_elem):
            element = fea.CastToChElementTetraCorot_4(mesh.GetElement(i))
            if element is None:
                continue
            vm[i] = float(element.GetStress().GetEquivalentVonMises())
        _vtk_append_array(grid.GetCellData(), "von_mises_pa", vm, components=1)
        _vtk_append_array(grid.GetCellData(), "von_mises_MPa", vm * 1.0e-6, components=1)

        out_path = self.frames_dir / f"frame_{frame_index:06d}.vtu"
        writer = vtk.vtkXMLUnstructuredGridWriter()
        writer.SetFileName(str(out_path))
        writer.SetInputData(grid)
        if hasattr(writer, "SetDataModeToBinary"):
            writer.SetDataModeToBinary()
        elif hasattr(writer, "SetDataModeToAppended"):
            writer.SetDataModeToAppended()
        writer.Write()

        self.entries.append((float(time_s), str(out_path)))
        self._write_pvd()
        return out_path

    def _write_pvd(self) -> None:
        pvd = self.out_dir / "fea.pvd"
        lines = [
            '<?xml version="1.0"?>',
            '<VTKFile type="Collection" version="0.1" byte_order="LittleEndian">',
            '  <Collection>',
        ]
        for t, file in self.entries:
            rel = f"frames/{Path(file).name}"
            lines.append(f'    <DataSet timestep="{t:.9g}" group="" part="0" file="{rel}"/>')
        lines += ['  </Collection>', '</VTKFile>', '']
        pvd.write_text("\n".join(lines), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = fea_driver.build_parser()
    p.description = __doc__
    # Two-way CRM options do not apply here: no SPH terrain is constructed.
    for action in list(p._actions):
        flags = set(action.option_strings)
        if flags & {"--use-contact-skin", "--contact-obj", "--bce-layers",
                    "--check-fea-embedded", "--initial-gap", "--loads-file",
                    "--save-particles", "--smoke-test", "--mesh", "--mesh-file"}:
            p._remove_action(action)
        elif "--spin-ramp-time" in flags:
            # The recorded rigid-CRM load assumes instant spin after settling.
            action.default = 0.0
            action.help = "spin ramp in s (default: 0, matching the rigid CRM load)"
        elif "--dt" in flags:
            action.help = "FEA structural step in s (default: 2e-3)"
        elif "--solver" in flags:
            # PardisoMKL lives in the optional pychrono.pardisomkl module.
            action.choices = ("gmres", "sparse-lu", "sparse-qr", "pardiso-mkl")
            action.help = ("MBS linear solver for the FEA stiffness matrix; pardiso-mkl uses "
                           "pychrono.pardisomkl (default: sparse-lu)")
    p.add_argument("--loads", type=Path, required=True,
                   help="soil_loads.csv written by lunar_excavator_crm.py --loads-file")
    p.add_argument("--load-radius-fraction", type=float, default=0.8,
                   help="distribute loads on nodes with r >= fraction * drum radius (default: 0.8)")
    p.add_argument("--load-scale", type=float, default=1.0,
                   help="multiply all recorded soil loads by this factor (default: 1)")
    p.add_argument("--min-stress-volume", type=float, default=1.0e-12,
                   help="exclude tetrahedra smaller than this volume m^3 from the reported peak stress "
                        "(default: 1e-12; raw peak is still reported separately)")
    p.add_argument("--no-lock-sparsity-pattern", action="store_true",
                   help="disable direct-solver sparsity-pattern locking; the one-way system structure is "
                        "constant, so locking is enabled by default")
    p.add_argument("--export-vtk", action="store_true",
                   help="export per-frame FEA VTU files (displacement and von Mises) for ParaView/3D viewing")
    p.add_argument("--vtk-dir", type=Path, default=None,
                   help="directory for VTK export (default: <output-dir>/vtk)")
    p.add_argument("--vtk-stride", type=int, default=1,
                   help="export every Nth recorded frame to VTK (default: 1). "
                        "Increase to reduce disk/time for large meshes.")
    # GMRES does not enforce the spindle/hub constraints accurately with this
    # stiffness matrix. PardisoMKL and SparseLU give the commanded kinematics;
    # PardisoMKL is much faster and is available through pychrono.pardisomkl.
    p.set_defaults(solver="pardiso-mkl", output_dir=Path("Output_CRM_OneWay_FEA"))
    return p


class LoadHistory:
    """Time-interpolated rigid-CRM soil wrench."""

    FIELDS = ("force_x_n", "force_y_n", "force_z_n",
              "torque_x_nm", "torque_y_nm", "torque_z_nm")

    def __init__(self, path: Path):
        path = path.resolve()
        if not path.is_file():
            raise SystemExit(f"missing soil loads file: {path}")
        times: list[float] = []
        drum_z: list[float] = []
        values: list[np.ndarray] = []
        with path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            required = ("time_s", "drum_z_m", *self.FIELDS)
            missing = [name for name in required if name not in (reader.fieldnames or [])]
            if missing:
                raise SystemExit(f"{path} is missing columns: {', '.join(missing)}")
            for row in reader:
                times.append(float(row["time_s"]))
                drum_z.append(float(row["drum_z_m"]))
                values.append(np.array([float(row[name]) for name in self.FIELDS], dtype=float))
        if len(times) < 2:
            raise SystemExit(f"{path} has fewer than two load samples")
        if any(times[i + 1] < times[i] for i in range(len(times) - 1)):
            raise SystemExit(f"{path} is not sorted by time")
        self.path = path
        self.times = times
        self.drum_z = drum_z
        self.values = np.asarray(values)
        self.warned = False

    @property
    def t_end(self) -> float:
        return self.times[-1]

    def validate_motion(self, motion) -> None:
        """Reject load histories recorded with a different descent trajectory."""
        expected = np.asarray([motion.drum_z(t) for t in self.times], dtype=float)
        recorded = np.asarray(self.drum_z, dtype=float)
        error = float(np.max(np.abs(recorded - expected)))
        if error > 2.0e-4:
            raise SystemExit(
                f"load history descent trajectory differs from the FEA motion by {error:.3g} m; "
                "rerun the rigid CRM with the same --settle-time, --descent-speed, and --bed-height"
            )

    def at(self, t: float) -> tuple[np.ndarray, np.ndarray]:
        if t > self.times[-1] + 1.0e-12 and not self.warned:
            print(f"WARNING: t={t:g} s exceeds the load history ({self.times[-1]:g} s); "
                  "holding the final wrench", flush=True)
            self.warned = True
        index = bisect.bisect_right(self.times, min(max(t, self.times[0]), self.times[-1])) - 1
        index = max(0, min(index, len(self.times) - 2))
        t0, t1 = self.times[index], self.times[index + 1]
        alpha = 0.0 if t1 <= t0 else (min(max(t, t0), t1) - t0) / (t1 - t0)
        value = self.values[index] * (1.0 - alpha) + self.values[index + 1] * alpha
        return value[:3], value[3:]


def rotate_vectors(q_tuple, vectors: np.ndarray) -> np.ndarray:
    """Rotate vectors by a Chrono-style quaternion (w, x, y, z)."""
    w, x, y, z = q_tuple
    vectors = np.asarray(vectors, dtype=float)
    q_vec = np.array((x, y, z), dtype=float)
    cross_q_v = np.cross(q_vec, vectors)
    return vectors + 2.0 * (w * cross_q_v + np.cross(q_vec, cross_q_v))


def worst_deformation(nodes, local_positions: np.ndarray, spindle) -> tuple[int, float, np.ndarray]:
    """Largest displacement relative to the prescribed rigid spindle motion.

    ``GetX0()`` includes the initial pose but never advances with the spindle, so
    comparing live positions to ``GetX0()`` mostly measures rigid spin.  Here the
    reference is the current spindle pose applied to each node's local arm.
    """
    q_tuple = rigid.quat_parts(spindle.GetRot())
    center = np.asarray(rigid.vec_parts(spindle.GetPos()), dtype=float)
    expected = rotate_vectors(q_tuple, local_positions) + center
    actual = np.asarray([rigid.vec_parts(node.GetPos()) for node in nodes], dtype=float)
    delta = actual - expected
    mag2 = np.einsum("ij,ij->i", delta, delta)
    index = int(np.argmax(mag2))
    return index, float(math.sqrt(mag2[index])), delta[index]


def build_load_mapping(nodes, local_positions: np.ndarray, masses: np.ndarray,
                       min_radius: float):
    """Return load-node indices and a matrix mapping (F, T) to nodal forces.

    For selected exterior nodes with local arms r_i we solve the minimum-norm
    lumped-mass-weighted problem

        sum f_i = F,       sum r_i x f_i = T

    exactly in the least-squares sense.  The matrix is constant because the
    load nodes rotate rigidly with the spindle; only the wrench is transformed
    into the local frame at each step.
    """
    radii = np.linalg.norm(local_positions[:, :2], axis=1)
    indices = np.nonzero(radii >= min_radius)[0]
    if len(indices) < 6:
        raise SystemExit(
            f"only {len(indices)} solid nodes at r >= {min_radius:g} m; "
            "lower --load-radius-fraction"
        )
    arms = local_positions[indices]
    weights = np.maximum(masses[indices], 1.0e-12)
    count = len(indices)

    A = np.zeros((6, 3 * count), dtype=float)
    for i, (x, y, z) in enumerate(arms):
        column = slice(3 * i, 3 * i + 3)
        A[0:3, column] = np.eye(3)
        # r x f = skew(r) @ f
        A[3:6, column] = [[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]]

    WAt = A.T * weights.repeat(3)[:, None]
    normal = A @ WAt
    # A small diagonal regularizer only protects against degenerate meshes;
    # the 6 wrench rows are otherwise solved exactly.
    try:
        inverse = np.linalg.solve(normal, np.eye(6))
    except np.linalg.LinAlgError:
        inverse = np.linalg.lstsq(normal, np.eye(6), rcond=None)[0]
    if not np.all(np.isfinite(inverse)):
        raise SystemExit("cannot invert the load-distribution matrix")
    matrix = WAt @ inverse
    residual = float(np.max(np.abs(A @ matrix - np.eye(6))))
    if residual > 1.0e-8:
        raise SystemExit(f"load mapping does not reproduce the net wrench (residual={residual:.3g})")
    return indices, matrix, residual


def apply_loads(chrono, nodes, indices: np.ndarray, matrix: np.ndarray,
                wrench: tuple[np.ndarray, np.ndarray], q_tuple, scale: float) -> None:
    force_local, torque_local = wrench
    target = np.concatenate((force_local, torque_local))
    nodal = (matrix @ target).reshape(-1, 3) * scale
    rotated = np.array([rigid.quat_rotate(q_tuple, tuple(row)) for row in nodal])
    for node_index, f in zip(indices, rotated):
        nodes[int(node_index)].SetForce(chrono.ChVector3d(float(f[0]), float(f[1]), float(f[2])))


def write_config(path: Path, args, run, pla, motion, node_path: Path, ele_path: Path,
                 mesh, hub_nodes, loads: LoadHistory, load_indices, load_scale: float) -> None:
    config = {
        "model": "one-way CRM -> FEA (rigid soil wrench replayed on solid drum)",
        "loads_file": str(loads.path),
        "loads_duration_s": loads.t_end,
        "load_node_count": len(load_indices),
        "load_radius_fraction": args.load_radius_fraction,
        "load_scale": load_scale,
        "spin_ramp_time_s": args.spin_ramp_time,
        "soil": asdict(rigid.make_soil(args)),
        "run": asdict(run),
        "pla": asdict(pla),
        "motion": asdict(motion),
        "fea_mesh": {"node": str(node_path), "ele": str(ele_path),
                     "nodes": mesh.GetNumNodes(), "elements": mesh.GetNumElements(),
                     "hub_nodes": len(hub_nodes)},
        "solver": {"name": args.solver, "iterations": args.solver_iterations,
                   "tolerance": args.solver_tolerance, "timestepper": args.timestepper,
                   "threads": args.num_threads,
                   "lock_sparsity_pattern": not args.no_lock_sparsity_pattern},
        "min_stress_volume_m3": args.min_stress_volume,
        "caveat": "Structural response to rigid CRM loads; not two-way coupled FSI.",
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")


def main() -> int:
    args = build_parser().parse_args()
    # --smoke-test is removed from the one-way CLI, but the shared CRM run
    # parameter builder still reads the attribute.
    args.smoke_test = False
    if args.dt is None:
        args.dt = 2.0e-3
    if args.load_radius_fraction <= 0.0 or args.load_radius_fraction > 1.0:
        raise SystemExit("--load-radius-fraction must be in (0, 1]")
    if args.load_scale <= 0.0:
        raise SystemExit("--load-scale must be positive")
    if args.min_stress_volume < 0.0:
        raise SystemExit("--min-stress-volume cannot be negative")

    soil = rigid.make_soil(args)
    # The one-way replay has no SPH fluid, so the rigid-CRM CFL limit does not
    # apply to its structural timestep.
    run = rigid.make_run_params(args, validate_sph_dt=False)
    pla = fea_driver.make_pla(args)
    if args.print_soil:
        print(soil.summary())
        return 0
    # Options removed from the two-way parser still need values for the shared
    # mesh validator and status messages.
    args.use_contact_skin = False
    args.bce_layers = 1
    args.check_fea_embedded = False
    args.initial_gap = 0.0
    node_path, ele_path = fea_driver.resolve_fea_mesh(args)
    loads = LoadHistory(args.loads)
    if loads.t_end + 1.0e-12 < run.duration:
        print(f"WARNING: load history ends at {loads.t_end:g} s but run duration is "
              f"{run.duration:g} s; the final wrench will be held", flush=True)

    motion = replace(
        MOTION_DEFAULT,
        t_max=run.duration,
        settle_time=run.settle_time,
        lower_speed=run.descent_speed,
        drum_omega=run.omega,
        bed_height=run.bed_height,
        drum_spin_axis=run.spin_axis,
        drum_radius=run.drum_radius,
        drum_half_length=run.drum_half_length,
    )
    loads.validate_motion(motion)

    print(soil.summary(), flush=True)
    print(pla.summary(), flush=True)
    print(
        "\nOne-way CRM -> FEA run: "
        f"dt={run.dt:g} s, duration={run.duration:g} s, fps={run.fps:g}\n"
        f"loads: {loads.path} ({len(loads.times)} samples to {loads.t_end:g} s)\n"
        f"mesh: {node_path.name} / {ele_path.name}, load radius >= "
        f"{args.load_radius_fraction * motion.drum_radius:.4g} m, scale={args.load_scale:g}\n"
        f"spindle: omega={run.omega:g} rad/s about {run.spin_axis.upper()}, "
        f"spin ramp={args.spin_ramp_time:g} s",
        flush=True,
    )

    chrono, _fsi, _veh = rigid.import_crm_modules()
    try:
        import pychrono.fea as fea
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("this PyChrono build does not expose pychrono.fea") from exc

    q0 = rigid.initial_chrono_quat(chrono, motion)

    out_dir = args.output_dir.resolve()
    if not args.no_output:
        out_dir.mkdir(parents=True, exist_ok=True)
    chrono.SetChronoOutputPath(str(out_dir) + "/")

    system = chrono.ChSystemNSC()
    system.SetNumThreads(args.num_threads, 1, args.num_threads)
    system.SetGravitationalAcceleration(chrono.ChVector3d(0.0, 0.0, MOTION_DEFAULT.gravity))
    if hasattr(system, "SetCollisionSystemType") and hasattr(chrono, "ChCollisionSystem"):
        system.SetCollisionSystemType(chrono.ChCollisionSystem.Type_BULLET)
    if args.timestepper == "hht":
        system.SetTimestepperType(chrono.ChTimestepper.Type_HHT)
        hht = chrono.CastToChTimestepperHHT(system.GetTimestepper())
        hht.SetAlpha(-0.2)
        hht.SetMaxIters(5)
        hht.SetAbsTolerances(1.0e-2)
        hht.SetStepControl(False)
        hht.SetMinStepSize(run.dt)
    else:
        system.SetTimestepperType(chrono.ChTimestepper.Type_EULER_IMPLICIT_LINEARIZED)
    if args.solver == "sparse-lu":
        solver = chrono.ChSolverSparseLU()
    elif args.solver == "sparse-qr":
        solver = chrono.ChSolverSparseQR()
    elif args.solver == "pardiso-mkl":
        try:
            import pychrono.pardisomkl as pardiso
        except ImportError:
            print(
                "WARNING: pychrono.pardisomkl is unavailable; falling back to SparseLU. "
                "Use a PyChrono build with PardisoMKL for the faster supported path.",
                flush=True,
            )
            args.solver = "sparse-lu"
            solver = chrono.ChSolverSparseLU()
        else:
            solver = pardiso.ChSolverPardisoMKL(args.num_threads)
    else:
        solver = chrono.ChSolverGMRES()
        solver.SetMaxIterations(args.solver_iterations)
        solver.SetTolerance(args.solver_tolerance)
        solver.EnableDiagonalPreconditioner(True)
    if args.solver in ("sparse-lu", "sparse-qr", "pardiso-mkl") and not args.no_lock_sparsity_pattern:
        # The one-way model never changes topology, so re-analyzing the matrix
        # pattern every step is wasted work. This does not make Eigen's LU
        # multithreaded, but it avoids repeated setup overhead.
        solver.LockSparsityPattern(True)
    system.SetSolver(solver)

    drive = fea_driver.create_spindle(chrono, system, motion)
    spindle = drive["spindle"]
    fea_driver.configure_spindle_drive(chrono, system, motion, drive, args.spin_ramp_time, run.duration)
    mesh, nodes, hub_nodes, _material = fea_driver.load_fea_drum(
        chrono, fea, system, motion, node_path, ele_path, pla
    )
    if not args.no_initial_solid_velocity:
        fea_driver.set_solid_initial_velocity(chrono, nodes, motion, 0.0, args.spin_ramp_time)
    links = fea_driver.attach_hub_nodes(fea, system, spindle, hub_nodes)
    if hasattr(system, "Setup"):
        system.Setup()
    print(
        f"FEA drum: {mesh.GetNumNodes()} nodes, {mesh.GetNumElements()} tets, "
        f"{len(hub_nodes)} hub nodes, {3 * len(links)} spindle constraints",
        flush=True,
    )

    # Local load arms from the undeformed mesh (the FEA loader applies the same
    # translation/rotation to every node, so GetX0() is the reference pose).
    center0 = np.array((0.0, 0.0, motion.drum_center_z0), dtype=float)
    q0_tuple = rigid.quat_parts(q0)
    local_positions = np.array([
        rigid.quat_rotate(rigid.quat_conj(q0_tuple),
                          tuple(rigid.vec_parts(node.GetX0())[k] - center0[k] for k in range(3)))
        for node in nodes
    ], dtype=float)
    masses = np.array([node.GetMass() for node in nodes], dtype=float)
    load_indices, matrix, mapping_residual = build_load_mapping(
        nodes, local_positions, masses, args.load_radius_fraction * motion.drum_radius
    )
    print(
        f"One-way loads on {len(load_indices)} exterior nodes; "
        f"total lumped load mass={masses[load_indices].sum():.6g} kg; "
        f"wrench residual={mapping_residual:.3g}",
        flush=True,
    )

    writer = None
    state_file = None
    vtk_exporter = None
    if not args.no_output:
        write_config(out_dir / "config.json", args, run, pla, motion, node_path, ele_path,
                     mesh, hub_nodes, loads, load_indices, args.load_scale)
        state_file = (out_dir / "summary.csv").open("w", newline="", encoding="utf-8")
        writer = csv.DictWriter(state_file, fieldnames=ONEWAY_FIELDS)
        writer.writeheader()
        if args.export_vtk:
            vtk_dir = (args.vtk_dir.resolve() if args.vtk_dir else (out_dir / "vtk"))
            try:
                connectivity = read_tetgen_ele(ele_path)
                ref0 = np.array([rigid.vec_parts(n.GetX0()) for n in nodes], dtype=float)
                vtk_exporter = FeaVtkExporter(vtk_dir, connectivity, ref0)
                print(
                    f"VTK export: {vtk_dir} (every {max(1, args.vtk_stride)} recorded frame(s), "
                    f"{len(connectivity)} tets)",
                    flush=True,
                )
            except Exception as exc:
                print(f"VTK export disabled (init failed): {exc}", flush=True)
                vtk_exporter = None

    work_j = 0.0
    last_row = {}
    frame = 0
    wall_start = time.perf_counter()
    next_progress = 0.0
    axis = np.array(fea_driver.spin_axis_tuple(motion), dtype=float)

    def record(t: float, applied_force, applied_torque, motor_speed: float) -> None:
        nonlocal frame, last_row
        index, max_stress, element_volume, _center = fea_driver.worst_stress_info(
            fea, mesh, args.stress_stride, args.min_stress_volume
        )
        _raw_index, raw_stress, _raw_volume, _raw_center = fea_driver.worst_stress_info(
            fea, mesh, 1, 0.0
        )
        _deform_index, max_deformation, _delta = worst_deformation(
            nodes, local_positions, spindle
        )
        motor = drive["rotation_motor"]
        linear = drive["linear_motor"]
        command_z = motion.drum_z(t) - motion.drum_center_z0
        command_angle = fea_driver.spin_ramp_angle(motion, t, args.spin_ramp_time)
        descent_error = abs(float(linear.GetMotorPos()) - command_z)
        angle_error = abs(float(motor.GetMotorAngle()) - command_angle)
        if descent_error > 2.0e-4 or angle_error > 2.0e-2:
            raise SystemExit(
                f"spindle kinematics are not tracking the command at t={t:g} s: "
                f"descent error={descent_error:.3g} m, spin error={angle_error:.3g} rad. "
                "Use --solver sparse-lu; GMRES is not accurate enough for this constrained system."
            )
        axis_torque = float(np.dot(applied_torque, axis)) * args.load_scale
        row = {
            "frame": frame,
            "time_s": f"{t:.6f}",
            "wall_s": f"{time.perf_counter() - wall_start:.3f}",
            "drum_z_m": f"{rigid.vec_parts(spindle.GetPos())[2]:.7f}",
            "spin_deg": f"{math.degrees(fea_driver.spin_ramp_angle(motion, t, args.spin_ramp_time)):.3f}",
            "omega_rad_s": f"{motor_speed:.6g}",
            "linear_motor_pos_m": f"{linear.GetMotorPos():.6g}",
            "kinematic_descent_error_m": f"{descent_error:.6g}",
            "motor_angle_rad": f"{motor.GetMotorAngle():.6g}",
            "motor_angle_error_rad": f"{angle_error:.6g}",
            "motor_speed_rad_s": f"{motor_speed:.6g}",
            "applied_force_n": f"{np.linalg.norm(applied_force) * args.load_scale:.6g}",
            "applied_torque_nm": f"{np.linalg.norm(applied_torque) * args.load_scale:.6g}",
            "applied_axis_torque_nm": f"{axis_torque:.6g}",
            "input_power_w": f"{max(0.0, -axis_torque * motor_speed):.6g}",
            "work_j": f"{work_j:.6g}",
            "max_deformation_m": f"{max_deformation:.6g}",
            "max_von_mises_pa": f"{max_stress:.6g}",
            "max_von_mises_raw_pa": f"{raw_stress:.6g}",
            "hot_element_volume_m3": f"{element_volume:.6g}",
        }
        if writer is not None:
            writer.writerow(row)
            state_file.flush()
        last_row = row
        # Optional 3D FEA export (stress + deformation) for ParaView.
        if vtk_exporter is not None and (frame % max(1, args.vtk_stride) == 0):
            try:
                vtk_exporter.write_frame(fea, mesh, nodes, local_positions, spindle, frame, t)
            except Exception as exc:
                print(f"  VTK export frame {frame} failed: {exc}", flush=True)
        frame += 1
        print(
            f"frame {frame - 1:05d}  t={t:7.3f}s  F={np.linalg.norm(applied_force) * args.load_scale:.4g}N  "
            f"tau={axis_torque:.4g}Nm  deform={max_deformation:.4g}m  "
            f"vm={max_stress / 1e6:.4g}MPa (raw {raw_stress / 1e6:.4g}MPa)  "
            f"hot={index} (V={element_volume:.3g}m^3)",
            flush=True,
        )

    force_world, torque_world = loads.at(0.0)
    record(0.0, force_world, torque_world, 0.0)
    next_output = 1.0 / run.fps

    t = 0.0
    try:
        while t < run.duration - 1.0e-12:
            h = min(run.dt, run.duration - t)
            force_world, torque_world = loads.at(t)
            q_tuple = rigid.quat_parts(spindle.GetRot())
            q_inv = rigid.quat_conj(q_tuple)
            wrench = (
                rigid.quat_rotate(q_inv, tuple(force_world)),
                rigid.quat_rotate(q_inv, tuple(torque_world)),
            )
            apply_loads(chrono, nodes, load_indices, matrix, wrench, q_tuple, args.load_scale)
            system.DoStepDynamics(h)
            t += h

            motor_speed = drive["rotation_motor"].GetMotorAngleDt()
            axis_torque = float(np.dot(torque_world, axis)) * args.load_scale
            work_j += max(0.0, -axis_torque * motor_speed) * h

            if t >= next_output - 1.0e-12 or t >= run.duration - 1.0e-12:
                # Report the wrench at the output time, not the wrench used at
                # the start of the step that got us here.
                report_force, report_torque = loads.at(t)
                record(t, report_force, report_torque, motor_speed)
                next_output += 1.0 / run.fps
            if t >= next_progress:
                print(f"  t={t:7.3f}s / {run.duration:g}s", flush=True)
                next_progress += 1.0
    finally:
        if state_file is not None:
            state_file.close()

    print(
        "\nOne-way CRM -> FEA run complete: "
        f"max_deformation={last_row.get('max_deformation_m')} m "
        f"max_von_mises={last_row.get('max_von_mises_pa')} Pa "
        f"applied_axis_torque={last_row.get('applied_axis_torque_nm')} Nm "
        f"work={work_j:.4g} J",
        flush=True,
    )
    if not args.no_output:
        print(f"Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
