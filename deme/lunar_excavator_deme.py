#!/usr/bin/env python3
"""Lunar bucket-drum excavator -- GPU DEM port built with DEM-Engine (pyDEME).

Equivalent of MercuryDPM's ``LunarBucketDrumExcavator.cpp``:

* one pre-instantiated granular bed (spherical grains, seeded jitter),
* five infinite retaining walls (floor + four sides), top open,
* a CAD drum+screw imported as a triangle mesh,
* settle -> spin about global X (configurable) -> slow kinematic descent -> spin in place.

DEM-Engine is Project Chrono's GPU DEM solver and is the closest PyChrono-family
counterpart to MercuryDPM's per-triangle-wall DEM.  All physical parameters come
from ``lunar_excavator_config.py``.

Usage
-----
    python lunar_excavator_deme.py --smoke-test
    python lunar_excavator_deme.py --duration 10 --particle-radius 0.004

Output (CSV spheres + VTK mesh/analytical geometry) goes to ``--output-dir``.
Open the VTK sequences in ParaView; see ``visualise.py`` in this directory.
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lunar_excavator_config import (  # noqa: E402
    DEFAULT,
    OBJ_DEME,
    OBJ_MEDIUM,
    OBJ_SIM,
    ExcavatorParams,
    grain_positions,
)

# Family numbers (MercuryDPM has no explicit families; these are a DEME detail).
# NOTE: leave analytical planes in their default family.  In DEME 3.1.0 calling
# SetFamily() on a DEMExternObj plane silently removes its contact with grains.
GRAIN_FAMILY = 0
DRUM_FAMILY = 2


def fmt(x: float) -> str:
    """JIT-safe float literal for DEME expression strings."""
    return f"{float(x):.17g}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--device", type=int, default=0, help="CUDA device index")
    p.add_argument("--duration", type=float, default=None, help="simulated seconds (default: config t_max)")
    p.add_argument("--particle-radius", type=float, default=None, help="grain radius in metres")
    p.add_argument("--fps", type=float, default=None, help="output frames per simulated second")
    p.add_argument("--settle-time", type=float, default=None, help="seconds before the drum starts moving")
    p.add_argument(
        "--spin-axis",
        choices=("X", "Y", "Z"),
        default=None,
        help="override the configured GLOBAL drum spin axis (default: config value)",
    )
    p.add_argument("--output-dir", type=Path, default=Path("Output_DEME_LunarExcavator"))
    p.add_argument(
        "--mesh",
        choices=("coarse", "medium", "deme"),
        default="coarse",
        help="drum mesh: full coarse (default, keeps holes), full medium, or legacy 1200-face simplification",
    )
    p.add_argument(
        "--smoke-test",
        action="store_true",
        help="coarse grains and short run (still requires a GPU and first-time kernel compilation)",
    )
    p.add_argument("--no-output", action="store_true", help="skip frame output (benchmark runs)")
    return p


def make_params(args: argparse.Namespace) -> ExcavatorParams:
    overrides: dict[str, object] = {}
    if args.particle_radius is not None:
        overrides["particle_radius"] = args.particle_radius
    if args.fps is not None:
        overrides["fps"] = args.fps
    if args.settle_time is not None:
        overrides["settle_time"] = args.settle_time
    if args.spin_axis is not None:
        overrides["drum_spin_axis"] = args.spin_axis
    if args.duration is not None:
        overrides["t_max"] = args.duration
    if args.mesh == "coarse":
        overrides["stl_file"] = "LunarDrumScrew_m_coarse.stl"
    elif args.mesh == "medium":
        overrides["stl_file"] = "LunarDrumScrew_m_medium.stl"
    elif args.mesh == "deme":
        overrides["stl_file"] = "LunarDrumScrew_m_coarse.stl"  # legacy simplified OBJ below
    if args.smoke_test:
        overrides.setdefault("particle_radius", 0.004)
        overrides.setdefault("t_max", 0.05)
        overrides.setdefault("fps", 50.0)
        overrides.setdefault("settle_time", 0.001)
    return ExcavatorParams(**overrides)  # type: ignore[arg-type]


def add_retaining_walls(solver, mat, p: ExcavatorParams) -> None:
    """Five infinite planes, matching the five InfiniteWalls in the C++ driver.

    DEME plane normals point into the free space (the side containing grains),
    which is the opposite convention from MercuryDPM's InfiniteWall::set().
    """
    x, y, z = p.domain_bounds
    planes = [
        ([0.0, 0.0, p.floor_z], [0.0, 0.0, 1.0]),          # floor
        ([x[0], 0.0, 0.0], [1.0, 0.0, 0.0]),               # x min
        ([x[1], 0.0, 0.0], [-1.0, 0.0, 0.0]),              # x max
        ([0.0, y[0], 0.0], [0.0, 1.0, 0.0]),               # y min
        ([0.0, y[1], 0.0], [0.0, -1.0, 0.0]),              # y max
    ]
    for pos, normal in planes:
        # Do not SetFamily() on these planes: see the note above.
        solver.AddBCPlane(pos, normal, mat)


def prescribe_drum_motion(solver, p: ExcavatorParams) -> None:
    """Settle, then spin about the configured GLOBAL axis and descend to z_target.

    DEME family prescriptions are C++ expressions evaluated at simulation time
    ``t`` and JIT-compiled into the solver kernels.  Prescribed angular velocity
    components are in the drum's LOCAL frame, so the requested global axis is
    converted with ``global_axis_local()``.  For the default global-X spin this
    requests local Z, which is the mesh's drum axis after the initial 90 deg Y
    rotation.
    """
    # z(t) = z0 until settle_time, then a linear ramp clamped at z_target.
    z_expr = (
        f"fmaxf({fmt(p.z_target)}, "
        f"{fmt(p.drum_center_z0)} - {fmt(p.lower_speed)} * "
        f"fmaxf(0.0, t - {fmt(p.settle_time)}))"
    )
    gate = f"(t > {fmt(p.settle_time)})"
    local_axis = p.global_axis_local(p.drum_spin_axis)
    comps = []
    for component in local_axis:
        if abs(component) < 1.0e-12:
            comps.append("0")
        else:
            comps.append(f"{fmt(component * p.drum_omega)} * {gate}")
    wx, wy, wz = comps

    # dictate=False keeps force/torque feedback off the prescribed components
    # while letting the remaining rotational DOFs integrate normally.
    solver.SetFamilyPrescribedPosition(DRUM_FAMILY, "0", "0", z_expr, False)
    solver.SetFamilyPrescribedAngVel(DRUM_FAMILY, wx, wy, wz, False)


def main() -> int:
    args = build_parser().parse_args()
    p = make_params(args)
    print(p.summary(), flush=True)

    # Import here so --help works on machines without the CUDA wheel installed.
    try:
        import deme
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("pyDEME is not installed. See environment/INSTALL.md") from exc

    obj = {"coarse": OBJ_SIM, "medium": OBJ_MEDIUM, "deme": OBJ_DEME}[args.mesh]
    if not obj.exists():
        raise SystemExit(f"missing {obj}; run `python3 stl_to_obj.py` first")
    print(f"drum mesh: {obj.name} ({args.mesh})", flush=True)

    solver = deme.DEMSolver([args.device])
    solver.SetVerbosity("ERROR")
    solver.SetNoForceRecord(True)
    solver.SetOutputFormat("CSV")
    solver.SetOutputContent(["XYZ", "FAMILY"])
    solver.SetMeshOutputFormat("VTK")

    # Material: MercuryDPM k/c/mu mapped to Hertz E/Poisson/CoR/mu/Crr.
    mat = solver.LoadMaterial(
        {
            "E": p.youngs_modulus,
            "nu": p.poisson_ratio,
            "CoR": p.restitution,
            "mu": p.sliding_friction,
            "Crr": 0.0,
        }
    )
    solver.SetGravitationalAcceleration([0.0, 0.0, p.gravity])

    x, y, z = p.domain_bounds
    solver.InstructBoxDomainDimension(list(x), list(y), list(z))
    solver.InstructBoxDomainBoundingBC("none", mat)  # explicit planes below
    add_retaining_walls(solver, mat, p)

    # ---- pre-instantiated bed ----
    positions = grain_positions(p)
    grain = solver.LoadSphereType(p.particle_mass, p.particle_radius, mat)
    bed = solver.AddClumps([grain] * len(positions), [list(pos) for pos in positions])
    bed.SetFamily(GRAIN_FAMILY)

    # ---- meshed drum + screw ----
    drum = solver.AddWavefrontMeshObject(str(obj), mat, False, False)
    drum.InformCentroidPrincipal([0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 1.0])  # quaternion x,y,z,w
    drum.SetMass(p.drum_mass)
    drum.SetMOI(list(p.drum_moi))
    drum.SetInitPos([0.0, 0.0, p.drum_center_z0])
    drum.SetInitQuat(list(p.initial_quaternion_xyzw()))
    drum.SetFamily(DRUM_FAMILY)
    prescribe_drum_motion(solver, p)

    solver.SetTimeStepSize(p.deme_dt)
    solver.SetCDMaxUpdateFreq(1)
    solver.SetExpandSafetyType("auto")
    solver.SetMaxVelocity(10.0)
    print("Initializing CUDA kernels (cold cache can take a long time)...", flush=True)
    solver.Initialize()

    out_dir = args.output_dir.resolve()
    if not args.no_output:
        out_dir.mkdir(parents=True, exist_ok=True)

    frame_dt = 1.0 / p.fps
    n_frames = math.ceil(p.t_max / frame_dt)
    start = time.monotonic()
    for frame in range(n_frames + 1):
        if frame:
            solver.DoDynamicsThenSync(min(frame_dt, p.t_max - (frame - 1) * frame_dt))
        if not args.no_output:
            solver.WriteSphereFile(str(out_dir / f"spheres_{frame:06d}.csv"))
            solver.WriteMeshFile(str(out_dir / f"drum_{frame:06d}.vtk"))
            solver.WriteAnalyticalFile(str(out_dir / f"walls_{frame:06d}.vtk"))
        if frame % max(1, int(p.fps)) == 0:
            print(f"frame {frame}/{n_frames}  t={solver.GetSimTime():.3f}s", flush=True)

    solver.DoDynamicsThenSync(0.0)  # join outstanding async work
    print(f"Done: {len(positions)} grains, {p.t_max:g} s in {time.monotonic() - start:.1f} s wall time")
    print(f"Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
