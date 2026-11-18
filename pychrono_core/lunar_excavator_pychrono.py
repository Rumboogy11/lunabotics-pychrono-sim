#!/usr/bin/env python3
"""Lunar bucket-drum excavator -- CPU PyChrono core port.

This is the small-scale, pure-PyChrono counterpart of MercuryDPM's
``LunarBucketDrumExcavator.cpp``.  It uses ``pychrono.ChSystemNSC`` and the
core collision pipeline (no DEM-Engine, no MPI, no GPU).  The GPU production
port lives in ``../deme/lunar_excavator_deme.py``.

The setup mirrors the MercuryDPM driver:

* five fixed retaining walls (floor + four sides, open top),
* one seeded, pre-instantiated bed of spherical grains,
* the drum/screw imported as a triangle mesh,
* settle -> spin about global X (configurable) -> slow kinematic descent -> spin in place.

All physical parameters come from ``lunar_excavator_config.py``.  The default
here is the deliberately small ``PYCHRONO_DEMO`` parameter set (4 mm grains);
pass ``--particle-radius 0.001`` if you want mercury-scale grains, but expect
many more bodies and a much longer CPU run.

Usage:
    python lunar_excavator_pychrono.py --smoke-test
    python lunar_excavator_pychrono.py --duration 0.5 --vis
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lunar_excavator_config import (  # noqa: E402
    OBJ_COARSE,
    OBJ_DEME,
    PYCHRONO_DEMO,
    grain_positions,
)

# Material pair, not a family: PyChrono core uses one contact material here,
# matching MercuryDPM's single LinearViscoelasticFrictionSpecies.
GRAIN_FAMILY = 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--duration", type=float, default=None, help="simulated seconds (default: demo t_max)")
    p.add_argument("--particle-radius", type=float, default=None, help="grain radius in metres")
    p.add_argument("--fps", type=float, default=None, help="output frames per simulated second")
    p.add_argument("--settle-time", type=float, default=None, help="seconds before the drum starts moving")
    p.add_argument("--dt", type=float, default=None, help="override the integration step (seconds)")
    p.add_argument("--output-dir", type=Path, default=Path("Output_PyChrono_LunarExcavator"))
    p.add_argument(
        "--mesh",
        choices=("coarse", "deme"),
        default="coarse",
        help="OBJ mesh: full coarse STL-derived mesh (default, keeps holes) or legacy 1200-face simplification",
    )
    p.add_argument("--vis", action="store_true", help="enable the Irrlicht interactive view")
    p.add_argument("--no-output", action="store_true", help="skip per-frame CSV output")
    p.add_argument(
        "--smoke-test",
        action="store_true",
        help="short run with 6 mm grains (quickest way to verify the setup)",
    )
    return p


def make_params(args: argparse.Namespace):
    overrides: dict[str, object] = {}
    if args.particle_radius is not None:
        overrides["particle_radius"] = args.particle_radius
    if args.fps is not None:
        overrides["fps"] = args.fps
    if args.settle_time is not None:
        overrides["settle_time"] = args.settle_time
    if args.duration is not None:
        overrides["t_max"] = args.duration
    if args.smoke_test:
        overrides.setdefault("particle_radius", 0.006)
        overrides.setdefault("t_max", 0.02)
        overrides.setdefault("fps", 50.0)
        overrides.setdefault("settle_time", 0.001)
    return replace(PYCHRONO_DEMO, **overrides)  # type: ignore[arg-type]


def make_material(chrono, p):
    """Translate MercuryDPM's linear contact model to a Chrono NSC material.

    Chrono NSC compliance is inverse stiffness, so k=1000 N/m -> 0.001 m/N.
    Chrono's damping coefficient is dimensionless-ish; using MercuryDPM's c
    directly is the closest one-parameter mapping.  The Hertz-equivalent E/e
    values in the config are used by the GPU port, which needs them explicitly.
    """
    mat = chrono.ChContactMaterialNSC()
    mat.SetFriction(p.sliding_friction)
    mat.SetRestitution(p.restitution)
    mat.SetCompliance(1.0 / p.stiffness)
    mat.SetDampingF(p.dissipation)
    return mat


def add_walls(chrono, system, mat, p) -> None:
    """Five fixed thin boxes, matching the C++ driver's five InfiniteWalls."""
    x, y, z = p.domain_bounds
    lx, ly = x[1] - x[0], y[1] - y[0]
    lz = z[1] - z[0]
    t = 0.01  # wall thickness; thin compared with all other dimensions
    walls = [
        ((lx + 0.2, ly + 0.2, t), (0.0, 0.0, p.floor_z - t / 2)),          # floor
        ((t, ly + 0.2, lz + 0.2), (x[0] - t / 2, 0.0, lz / 2)),             # x min
        ((t, ly + 0.2, lz + 0.2), (x[1] + t / 2, 0.0, lz / 2)),             # x max
        ((lx + 0.2, t, lz + 0.2), (0.0, y[0] - t / 2, lz / 2)),             # y min
        ((lx + 0.2, t, lz + 0.2), (0.0, y[1] + t / 2, lz / 2)),             # y max
    ]
    for (sx, sy, sz), (cx, cy, cz) in walls:
        wall = chrono.ChBodyEasyBox(sx, sy, sz, p.density, True, True, mat)
        wall.SetPos(chrono.ChVector3d(cx, cy, cz))
        wall.SetFixed(True)
        wall.SetName("retaining_wall")
        system.Add(wall)


def add_grains(chrono, system, mat, p) -> list:
    grains = []
    for x, y, z in grain_positions(p):
        body = chrono.ChBodyEasySphere(p.particle_radius, p.density, True, True, mat)
        body.SetPos(chrono.ChVector3d(x, y, z))
        body.SetName("grain")
        system.Add(body)
        grains.append(body)
    return grains


def add_drum(chrono, system, mat, p, obj: Path):
    drum = chrono.ChBody()
    drum.SetMass(p.drum_mass)
    drum.SetInertiaXX(chrono.ChVector3d(*p.drum_moi))
    drum.SetName("drum")
    ok = chrono.AddTriangleMeshGeometry(
        drum,
        mat,
        str(obj),
        "drum_mesh",
        chrono.ChVector3d(0.0, 0.0, 0.0),
        chrono.ChQuaterniond(*p.initial_quaternion_xyzw()[::-1]),  # xyzw -> Chrono wxyz
        True,
    )
    if not ok:
        raise SystemExit(f"failed to load drum mesh {obj}")
    drum.EnableCollision(True)
    drum.SetPos(chrono.ChVector3d(0.0, 0.0, p.drum_center_z0))
    # AddTriangleMeshGeometry's `rot` sets the shape transform; the body pose
    # itself must be initialized separately to reproduce MercuryDPM's quaternion.
    drum.SetRot(chrono.ChQuaterniond(*p.initial_quaternion_xyzw()[::-1]))
    system.Add(drum)
    return drum


def set_drum_state(chrono, drum, p, t: float, dt: float) -> None:
    """Impose the same kinematic pose/velocity MercuryDPM sets each step."""
    moving = t >= p.settle_time
    z = p.drum_z(t) if moving else p.drum_center_z0
    omega = p.drum_omega_at(t)
    if p.drum_spin_axis.upper() == "X":
        axis = chrono.ChVector3d(1.0, 0.0, 0.0)
        w = chrono.ChVector3d(omega, 0.0, 0.0)
    elif p.drum_spin_axis.upper() == "Y":
        axis = chrono.ChVector3d(0.0, 1.0, 0.0)
        w = chrono.ChVector3d(0.0, omega, 0.0)
    elif p.drum_spin_axis.upper() == "Z":
        axis = chrono.ChVector3d(0.0, 0.0, 1.0)
        w = chrono.ChVector3d(0.0, 0.0, omega)
    else:
        raise ValueError(f"unsupported drum spin axis: {p.drum_spin_axis!r}")

    q0 = chrono.ChQuaterniond(*p.initial_quaternion_xyzw()[::-1])
    q_spin = chrono.QuatFromAngleAxis(omega * max(0.0, t - p.settle_time), axis)
    drum.SetPos(chrono.ChVector3d(0.0, 0.0, z))
    drum.SetRot(q_spin * q0)
    drum.SetPosDt(chrono.ChVector3d(0.0, 0.0, -p.lower_speed if moving and z > p.z_target else 0.0))
    drum.SetAngVelParent(w)
    del dt  # dt is intentionally unused; the pose is an analytic function of t


def write_spheres(path: Path, grains: list, radius: float) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["X", "Y", "Z", "r", "family"])
        for body in grains:
            pos = body.GetPos()
            writer.writerow([f"{pos.x:.9g}", f"{pos.y:.9g}", f"{pos.z:.9g}", f"{radius:.9g}", GRAIN_FAMILY])


def setup_visualization(chrono, system):
    try:
        import pychrono.irrlicht as chronoirr
    except ImportError as exc:  # pragma: no cover - optional module
        raise SystemExit(
            "--vis requires pychrono.irrlicht, which is not in the PyPI core wheel. "
            "Use the conda-forge/Project Chrono PyChrono package or a source build "
            "with the Irrlicht module enabled (see environment/INSTALL.md)."
        ) from exc

    vis = chronoirr.ChVisualSystemIrrlicht()
    vis.AttachSystem(system)
    vis.SetWindowSize(1024, 768)
    vis.SetWindowTitle("Lunar bucket-drum excavator (PyChrono CPU demo)")
    if not vis.Initialize():
        raise SystemExit("failed to initialize the Irrlicht visualization window")
    vis.AddSkyBox()
    vis.AddCamera(chrono.ChVector3d(0.16, -0.20, 0.16), chrono.ChVector3d(0.0, 0.0, 0.05))
    vis.AddTypicalLights()
    return vis


def main() -> int:
    args = build_parser().parse_args()
    p = make_params(args)
    print(p.summary(), flush=True)

    try:
        import pychrono as chrono
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("PyChrono is not installed. See environment/INSTALL.md") from exc

    obj = OBJ_DEME if args.mesh == "deme" else OBJ_COARSE
    if not obj.exists():
        raise SystemExit(f"missing {obj}; run `python3 stl_to_obj.py` first")

    system = chrono.ChSystemNSC()
    system.SetGravitationalAcceleration(chrono.ChVector3d(0.0, 0.0, p.gravity))
    system.SetTimestepperType(chrono.ChTimestepper.Type_EULER_IMPLICIT_LINEARIZED)
    system.SetSolverType(chrono.ChSolver.Type_PSOR)

    mat = make_material(chrono, p)
    add_walls(chrono, system, mat, p)
    grains = add_grains(chrono, system, mat, p)
    drum = add_drum(chrono, system, mat, p, obj)

    dt = args.dt or p.deme_dt
    frame_dt = 1.0 / p.fps
    n_frames = math.ceil(p.t_max / frame_dt)
    out_dir = args.output_dir.resolve()
    if not args.no_output:
        out_dir.mkdir(parents=True, exist_ok=True)
        state_path = out_dir / "drum_state.csv"
        summary_path = out_dir / "summary.csv"
        state_file = state_path.open("w", newline="", encoding="utf-8")
        summary_file = summary_path.open("w", newline="", encoding="utf-8")
        state_writer = csv.writer(state_file)
        summary_writer = csv.writer(summary_file)
        state_writer.writerow(["time", "x", "y", "z", "qw", "qx", "qy", "qz"])
        summary_writer.writerow(["frame", "time", "particles", "drum_z"])
    else:
        state_file = summary_file = None
        state_writer = summary_writer = None

    vis = setup_visualization(chrono, system) if args.vis else None
    start = time.monotonic()
    print(f"Running {len(grains)} grains for {p.t_max:g} s at dt={dt:.3e} s...", flush=True)

    for frame in range(n_frames + 1):
        t = system.GetChTime()
        if not args.no_output:
            write_spheres(out_dir / f"spheres_{frame:06d}.csv", grains, p.particle_radius)
            pos = drum.GetPos()
            rot = drum.GetRot()
            state_writer.writerow(
                [f"{t:.9g}", f"{pos.x:.9g}", f"{pos.y:.9g}", f"{pos.z:.9g}",
                 f"{rot.e0:.9g}", f"{rot.e1:.9g}", f"{rot.e2:.9g}", f"{rot.e3:.9g}"]
            )
            summary_writer.writerow([frame, f"{t:.9g}", len(grains), f"{pos.z:.9g}"])
            state_file.flush()
            summary_file.flush()

        if vis is not None:
            vis.BeginScene()
            vis.Render()
            vis.EndScene()

        if frame == n_frames:
            break
        frame_end = min((frame + 1) * frame_dt, p.t_max)
        while system.GetChTime() < frame_end - 1e-12:
            set_drum_state(chrono, drum, p, system.GetChTime(), dt)
            system.DoStepDynamics(min(dt, frame_end - system.GetChTime()))
        if frame % max(1, int(p.fps)) == 0:
            print(f"frame {frame}/{n_frames}  t={system.GetChTime():.3f}s", flush=True)

    if state_file is not None:
        state_file.close()
    if summary_file is not None:
        summary_file.close()
    print(f"Done: {len(grains)} grains, {p.t_max:g} s in {time.monotonic() - start:.1f} s wall time")
    if not args.no_output:
        print(f"Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
