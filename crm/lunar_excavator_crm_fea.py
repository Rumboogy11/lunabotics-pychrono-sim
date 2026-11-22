#!/usr/bin/env python3
"""Lunar bucket-drum excavator -- spindle-driven FEA structural check.

This variant couples the generated solid PLA tetrahedral mesh to the same
Chrono::CRM SPH soil used by ``lunar_excavator_crm.py``.  It is a targeted
structural check, not a replacement for the rigid-body production design
driver.

The drum is not added as a rigid FSI obstacle.  Instead:

* a ground-fixed guide, a translating slider, and a rotating spindle are
  connected by Chrono motor constraints that prescribe descent and spin;
* every bore/hub FEA node is tied to that spindle with ``ChLinkNodeFrame``;
* the rest of the tetrahedral PLA mesh is free to deform;
* ``terrain.AddFeaMesh(mesh, False)`` provides two-way CRM contact on the
  boundary faces of the solid mesh.

Spindle motion is prescribed through motor motion functions (not by per-step
state overrides), so constraint reactions remain consistent with the imposed
trajectory.  Drive torque is reconstructed from the sum of the hub-link
reaction moments about the prescribed global spin axis.

Requirements
------------
The local Chrono 11 source build with FSI/SPH, vehicle, CRM, and Python modules
is required.  Supply a generated tetrahedral mesh with ``--fea-node`` (and
optionally ``--fea-ele``); no mesh files are bundled.

Usage
-----
    ./crm/run_fea.sh --smoke-test --no-output --fea-node /path/to/drum.node
    ./crm/run_fea.sh --duration 1.0 --spacing 0.012 --fps 5 \
        --fea-node /path/to/drum.node --output-dir Output_CRM_LunarExcavator_FEA
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys

import numpy as np
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import lunar_excavator_crm as rigid  # noqa: E402
from lunar_excavator_config import DEFAULT as MOTION_DEFAULT  # noqa: E402


@dataclass(frozen=True)
class PlaParams:
    """Linear-elastic isotropic PLA properties for a first structural check."""

    youngs_modulus: float = 3.0e9       # Pa, 100% infill starting estimate
    poisson_ratio: float = 0.38         # -
    density: float = MOTION_DEFAULT.drum_density  # kg/m^3
    rayleigh_beta: float = 0.01         # s, numerical damping
    hub_radius: float = 0.0015          # m, bore radius from the STEP model
    hub_axial_half_length: float | None = None  # m, None means the full bore
    min_nodal_mass: float = 1.0e-6      # kg, floor for sliver nodes to stabilize FEA dynamics

    def __post_init__(self) -> None:
        if self.youngs_modulus <= 0.0:
            raise ValueError("PLA Young's modulus must be positive")
        if not -1.0 < self.poisson_ratio < 0.5:
            raise ValueError("PLA Poisson ratio must be in (-1, 0.5)")
        if self.density <= 0.0:
            raise ValueError("PLA density must be positive")
        if self.rayleigh_beta < 0.0:
            raise ValueError("Rayleigh damping cannot be negative")
        if self.hub_radius <= 0.0:
            raise ValueError("hub radius must be positive")
        if self.min_nodal_mass < 0.0:
            raise ValueError("minimum nodal mass cannot be negative")

    def summary(self) -> str:
        return "\n".join(
            [
                "PLA drum (linear elastic, isotropic):",
                f"  density               {self.density:,.1f} kg/m^3",
                f"  Young's modulus       {self.youngs_modulus:.4g} Pa",
                f"  Poisson ratio         {self.poisson_ratio:.3f}",
                f"  Rayleigh beta         {self.rayleigh_beta:.4g} s",
                f"  rigid hub radius      {self.hub_radius * 1000:.3f} mm",
                f"  nodal mass floor      {self.min_nodal_mass:.4g} kg",
            ]
        )


FEA_METRIC_FIELDS = rigid.METRIC_FIELDS + [
    "spindle_axis_torque_nm",
    "spindle_reaction_force_n",
    "spindle_motor_angle_rad",
    "spindle_motor_speed_rad_s",
    "max_nodal_displacement_m",
    "max_von_mises_pa",
]


def fea_metric_fields(run) -> list[str]:
    """FEA summary columns; force descent adds the shared actuator telemetry."""
    return FEA_METRIC_FIELDS + (
        rigid.FORCE_METRIC_FIELDS if run.descent_mode == "force" else []
    )


def build_parser() -> argparse.ArgumentParser:
    p = rigid.build_parser(include_drum_gravity=False)
    p.description = __doc__
    # The rigid driver's OBJ switches do not apply to this solid-FEA model.
    for action in list(p._actions):
        if any(flag in {"--mesh", "--mesh-file", "--detector-auto-size",
                        "--detector-radius", "--detector-length"}
               for flag in action.option_strings):
            p._remove_action(action)
        elif "--dt" in action.option_strings:
            action.help = "CRM/FSI step size in seconds (default: 2.5e-4 for FEA)"

    p.add_argument("--fea-node", type=Path, default=None,
                   help="TetGen .node file for the PLA solid mesh (required for direct CLI runs)")
    p.add_argument("--fea-ele", type=Path, default=None,
                   help="TetGen .ele file (default: same prefix as --fea-node)")
    p.add_argument("--use-contact-skin", action="store_true",
                   help="experimental: use a coarse shell skin for CRM contact")
    p.add_argument("--contact-obj", type=Path, default=None,
                   help="coarse welded OBJ for --use-contact-skin (required with that flag)")
    p.add_argument("--pla-youngs-modulus", type=float, default=None,
                   help="PLA Young's modulus in Pa (default: 3.0e9)")
    p.add_argument("--pla-poisson-ratio", type=float, default=None,
                   help="PLA Poisson ratio (default: 0.38)")
    p.add_argument("--pla-density", type=float, default=None,
                   help="PLA density in kg/m^3 (default: shared config, 1240)")
    p.add_argument("--rayleigh-beta", type=float, default=None,
                   help="FEA Rayleigh damping beta in s (default: 0.01)")
    p.add_argument("--hub-radius", type=float, default=None,
                   help="rigid spindle hub radius in m (default: 0.0015)")
    p.add_argument("--hub-axial-half-length", type=float, default=None,
                   help="central axial rigid hub half-length in m; 0 uses the full bore")
    p.add_argument("--min-nodal-mass", type=float, default=None,
                   help="floor FEA nodal masses at this value in kg (default: 1e-6)")
    p.add_argument("--stress-stride", type=int, default=1,
                   help="evaluate von Mises stress on every Nth element at output (default: 1)")
    p.add_argument("--solver", choices=("gmres", "sparse-lu", "sparse-qr"), default="gmres",
                   help="MBS linear solver for the FEA stiffness matrix (default: gmres)")
    p.add_argument("--solver-iterations", type=int, default=20,
                   help="GMRES iterations per MBS step with FEA stiffness (default: 20)")
    p.add_argument("--solver-tolerance", type=float, default=1.0e-3,
                   help="GMRES residual tolerance with FEA stiffness (default: 1e-3)")
    p.add_argument("--num-threads", type=int, default=8,
                   help="Chrono/Eigen CPU threads for FEA (default: 8)")
    p.add_argument("--debug-fea", action="store_true",
                   help="print nodal displacement and extents after every step")
    p.add_argument("--debug-stress", action="store_true",
                   help="print worst stress element and displacement node at output frames")
    p.add_argument("--bce-layers", type=int, default=2,
                   help="flexible BCE layers for FEA/SPH contact (default: 2, best tested interacting setup)")
    p.add_argument("--check-fea-embedded", action="store_true",
                   help="prune SPH particles embedded in the FEA solid at initialization")
    p.add_argument("--initial-gap", type=float, default=0.0,
                   help="raise the initial drum by this distance in m to avoid STEP/SPH overlap")
    p.add_argument("--spin-ramp-time", type=float, default=0.5,
                   help="linearly ramp spin speed over this duration in s (default: 0.5)")
    p.add_argument("--no-initial-solid-velocity", action="store_true",
                   help="start solid nodes at rest instead of with the prescribed spindle velocity")
    p.add_argument("--timestepper", choices=("euler", "hht"), default="euler",
                   help="MBD timestepper for the FEA system (default: euler)")
    p.set_defaults(output_dir=Path("Output_CRM_LunarExcavator_FEA"))
    return p


def make_pla(args: argparse.Namespace) -> PlaParams:
    values = {
        "youngs_modulus": args.pla_youngs_modulus,
        "poisson_ratio": args.pla_poisson_ratio,
        "density": args.pla_density,
        "rayleigh_beta": args.rayleigh_beta,
        "hub_radius": args.hub_radius,
        "hub_axial_half_length": args.hub_axial_half_length,
        "min_nodal_mass": args.min_nodal_mass,
    }
    overrides = {k: v for k, v in values.items() if v is not None}
    return PlaParams(**overrides)


def resolve_fea_mesh(args: argparse.Namespace) -> tuple[Path, Path]:
    if args.fea_node is None:
        raise SystemExit(
            "missing --fea-node; supply a TetGen .node file generated by "
            "`./chrono-env/bin/python step_to_chrono_fea.py ...`"
        )
    node = args.fea_node.resolve()
    ele = args.fea_ele.resolve() if args.fea_ele is not None else node.with_suffix(".ele")
    if not node.is_file():
        raise SystemExit(
            f"missing FEA node file {node}; generate it with "
            "`./chrono-env/bin/python step_to_chrono_fea.py ...`"
        )
    if not ele.is_file():
        raise SystemExit(f"missing FEA element file {ele}")
    if args.use_contact_skin:
        if args.contact_obj is None:
            raise SystemExit("--use-contact-skin requires --contact-obj")
        contact = args.contact_obj.resolve()
        if not contact.is_file():
            raise SystemExit(
                f"missing CRM contact skin {contact}; generate it from the surface STL "
                "with trimesh decimation to about 2000 faces"
            )
    if args.stress_stride < 1:
        raise SystemExit("--stress-stride must be at least 1")
    if args.solver_iterations < 1:
        raise SystemExit("--solver-iterations must be at least 1")
    if args.solver_tolerance <= 0.0:
        raise SystemExit("--solver-tolerance must be positive")
    if args.num_threads < 1:
        raise SystemExit("--num-threads must be at least 1")
    if args.bce_layers < 1:
        raise SystemExit("--bce-layers must be at least 1")
    if args.spin_ramp_time < 0.0:
        raise SystemExit("--spin-ramp-time cannot be negative")
    return node, ele


def create_spindle(chrono, system, motion):
    """Create a spindle body driven by Chrono motor motion functions.

    The spindle is not fixed and is not prescribed by per-step state overrides.
    A ground guide, translating slider, and rotating spindle are connected by
    motor constraints so that descent (global Z) and spin (about the configured
    drum spin axis) follow motion functions consistently.
    """
    z0 = motion.drum_center_z0
    q0 = rigid.initial_chrono_quat(chrono, motion)

    ground = chrono.ChBody()
    ground.SetName("spindle_ground")
    ground.SetFixed(True)
    ground.SetPos(chrono.ChVector3d(0.0, 0.0, z0))
    ground.SetRot(chrono.ChQuaterniond(1.0, 0.0, 0.0, 0.0))
    system.Add(ground)

    slider = chrono.ChBody()
    slider.SetName("spindle_slider")
    slider.SetMass(max(1.0e-3, 1.0e-3 * motion.drum_mass))
    slider.SetInertiaXX(chrono.ChVector3d(1.0e-6, 1.0e-6, 1.0e-6))
    slider.SetFixed(False)
    if hasattr(slider, "SetUseSleeping"):
        slider.SetUseSleeping(False)
    slider.SetPos(chrono.ChVector3d(0.0, 0.0, z0))
    slider.SetRot(chrono.ChQuaterniond(1.0, 0.0, 0.0, 0.0))
    system.Add(slider)

    spindle = chrono.ChBody()
    spindle.SetName("drum_spindle")
    spindle.SetMass(motion.drum_mass)
    spindle.SetInertiaXX(chrono.ChVector3d(*motion.drum_moi))
    spindle.SetFixed(False)
    if hasattr(spindle, "SetUseSleeping"):
        spindle.SetUseSleeping(False)
    spindle.SetPos(chrono.ChVector3d(0.0, 0.0, z0))
    spindle.SetRot(q0)
    system.Add(spindle)

    return {"ground": ground, "slider": slider, "spindle": spindle}


def _axis_quat_align_z(chrono, axis: tuple[float, float, float]):
    """Quaternion rotating local +Z to the given axis."""
    ax, ay, az = axis
    n = math.sqrt(ax * ax + ay * ay + az * az)
    if n == 0.0:
        raise SystemExit("spin axis must be non-zero")
    ax, ay, az = ax / n, ay / n, az / n
    # cross(z, axis)
    cx, cy, cz = -ay, ax, 0.0
    dot_za = az
    if math.sqrt(cx * cx + cy * cy + cz * cz) < 1e-12:
        if dot_za > 0.0:
            return chrono.ChQuaterniond(1.0, 0.0, 0.0, 0.0)
        # 180 deg around X
        return chrono.QuatFromAngleAxis(math.pi, chrono.ChVector3d(1.0, 0.0, 0.0))
    angle = math.atan2(math.sqrt(cx * cx + cy * cy + cz * cz), dot_za)
    return chrono.QuatFromAngleAxis(angle, chrono.ChVector3d(cx, cy, cz))


def _append_const_duration(seq, chrono, value: float, duration: float,
                           continuous: bool = True) -> None:
    if duration <= 0.0:
        return
    # ChFunctionSequence's continuity offset shifts a constant segment so that
    # it starts at the previous segment's end value.  That is correct for the
    # descent ramp/plateau, but a zero-ramp spin step from 0 to omega must NOT
    # be shifted or the motor speed stays at zero forever.
    seq.InsertFunct(chrono.ChFunctionConst(value), duration, 1.0, continuous)


def build_descent_position_function(chrono, motion, t_end: float):
    """Relative Z displacement function: 0 at z0, negative when descending."""
    seq = chrono.ChFunctionSequence()
    z0 = motion.drum_center_z0
    zt = motion.z_target
    settle = max(0.0, motion.settle_time)
    lower_end = settle
    if motion.lower_speed > 1e-12 and z0 > zt:
        lower_end = settle + (z0 - zt) / motion.lower_speed
    lower_end = min(lower_end, t_end)

    if settle > 0.0:
        _append_const_duration(seq, chrono, 0.0, min(settle, t_end))
    if t_end > settle:
        descend_dur = max(0.0, min(lower_end, t_end) - settle)
        if descend_dur > 0.0:
            # displacement = -lower_speed * local_t
            seq.InsertFunct(chrono.ChFunctionRamp(0.0, -motion.lower_speed), descend_dur, 1.0, True)
        remain = t_end - min(lower_end, t_end)
        if remain > 0.0:
            final_disp = min(0.0, zt - z0)
            _append_const_duration(seq, chrono, final_disp, remain)
    # ensure function is defined at least at 0
    if t_end <= 0.0:
        _append_const_duration(seq, chrono, 0.0, 1.0)
    return seq


def build_recorded_position_function(chrono, times, drum_z, z0: float, t_end: float):
    """Position function following a recorded (force-driven CRM) Z trajectory."""
    seq = chrono.ChFunctionSequence()
    if hasattr(seq, "SetClamped"):
        seq.SetClamped(True)
    segments = 0
    first_t = max(0.0, float(times[0]))
    if first_t > 0.0:
        seq.InsertFunct(chrono.ChFunctionConst(float(drum_z[0]) - z0), first_t, 1.0, False)
        segments += 1
    for i in range(len(times) - 1):
        t0, t1 = float(times[i]), float(times[i + 1])
        dt = t1 - t0
        if dt <= 1.0e-12:
            continue
        z0_i, z1_i = float(drum_z[i]) - z0, float(drum_z[i + 1]) - z0
        if abs(z1_i - z0_i) <= 1.0e-15:
            seq.InsertFunct(chrono.ChFunctionConst(z0_i), dt, 1.0, False)
        else:
            seq.InsertFunct(
                chrono.ChFunctionRamp(z0_i, (z1_i - z0_i) / dt), dt, 1.0, False
            )
        segments += 1
    last_t = float(times[-1])
    if t_end > last_t:
        seq.InsertFunct(
            chrono.ChFunctionConst(float(drum_z[-1]) - z0), t_end - last_t, 1.0, True
        )
        segments += 1
    if segments == 0:  # pragma: no cover - degenerate history
        seq.InsertFunct(chrono.ChFunctionConst(float(drum_z[-1]) - z0), 1.0, False)
    return seq


def build_spin_speed_function(chrono, motion, ramp_time: float, t_end: float):
    """Angular speed function about the spindle axis (rad/s).

    ``configure_spindle_drive()`` initializes the revolute motor as
    ``(spindle, slider)`` so Chrono's motor angle is the spindle rotation, not
    its negative.  The speed function therefore uses ``motion.drum_omega``
    directly, matching the rigid CRM and exported ``excavator.pvd`` drum.
    """
    seq = chrono.ChFunctionSequence()
    settle = max(0.0, motion.settle_time)
    omega = motion.drum_omega
    ramp_time = max(0.0, ramp_time)
    ramp_end = settle + ramp_time

    if settle > 0.0:
        _append_const_duration(seq, chrono, 0.0, min(settle, t_end))
    if t_end > settle:
        if ramp_time > 0.0:
            ramp_dur = max(0.0, min(ramp_end, t_end) - settle)
            if ramp_dur > 0.0:
                seq.InsertFunct(chrono.ChFunctionRamp(0.0, omega / ramp_time), ramp_dur, 1.0, True)
            if t_end > ramp_end:
                _append_const_duration(seq, chrono, omega, t_end - ramp_end)
        else:
            _append_const_duration(seq, chrono, omega, t_end - settle, continuous=False)
    if t_end <= 0.0:
        _append_const_duration(seq, chrono, 0.0, 1.0)
    return seq


def configure_spindle_drive(chrono, system, motion, drive, ramp_time: float, t_end: float,
                            *, run=None, position_function=None, descent_mode: str | None = None):
    """Attach spindle descent and spin motors.

    Kinematic mode keeps the original position motor.  Force mode uses a
    ``ChLinkMotorLinearForce`` on the slider, with a hold motor for the settle
    interval and the final target, while the spin motor remains prescribed.
    ``position_function`` lets the one-way replay follow a recorded trajectory.
    """
    ground = drive["ground"]
    slider = drive["slider"]
    spindle = drive["spindle"]
    z0 = motion.drum_center_z0
    q0 = rigid.initial_chrono_quat(chrono, motion)
    mode = descent_mode if descent_mode is not None else getattr(run, "descent_mode", "kinematic")

    if mode == "force":
        force_drive = rigid.create_force_descent(
            chrono, system, slider, motion, run, t_end,
            prismatic=True, ground=ground,
        )
        # Spindle Z is the tracked telemetry/one-way quantity; clamping both
        # bodies keeps it from being recorded below the target after a step.
        force_drive["clamp_bodies"].append(spindle)
        lin = force_drive["force_motor"]
        drive["descent"] = force_drive
    else:
        # Linear motor: slider translates along global Z relative to ground.
        z_frame = chrono.ChFramed(chrono.ChVector3d(0.0, 0.0, z0), chrono.ChQuaterniond(1.0, 0.0, 0.0, 0.0))
        lin = chrono.ChLinkMotorLinearPosition()
        # Chrono measures the motor coordinate from body B (guide) to body A
        # (slider).  Keeping the slider first makes a negative Z position function
        # command the intended downward descent.
        lin.Initialize(slider, ground, z_frame)
        lin.SetGuideConstraint(chrono.ChLinkMotorLinearPosition.GuideConstraint_PRISMATIC)
        lin.SetMotionFunction(
            position_function
            if position_function is not None
            else build_descent_position_function(chrono, motion, max(t_end, motion.t_max, 1.0))
        )
        system.Add(lin)

    # Rotation motor: spindle rotates about configured axis relative to slider.
    # Chrono measures body1 relative to body2, so put the spindle first.  The
    # previous (slider, spindle) order made a negative command rotate the FEA
    # drum positively, opposite to the rigid CRM and ParaView's excavator.pvd.
    axis = spin_axis_tuple(motion)
    q_axis = _axis_quat_align_z(chrono, axis)
    spin_frame = chrono.ChFramed(chrono.ChVector3d(0.0, 0.0, z0), q_axis)
    rot = chrono.ChLinkMotorRotationSpeed()
    rot.Initialize(spindle, slider, spin_frame)
    rot.SetSpindleConstraint(chrono.ChLinkMotorRotationSpeed.SpindleConstraint_REVOLUTE)
    rot.SetSpeedFunction(build_spin_speed_function(chrono, motion, ramp_time, max(t_end, motion.t_max, 1.0)))
    system.Add(rot)

    # Initial kinematics consistent with t=0 motion functions.
    # At t=0, descent displacement is 0 and spin speed is 0 for settle_time >= 0.
    slider.SetPos(chrono.ChVector3d(0.0, 0.0, z0))
    slider.SetPosDt(chrono.ChVector3d(0.0, 0.0, 0.0))
    slider.SetRot(chrono.ChQuaterniond(1.0, 0.0, 0.0, 0.0))
    slider.SetAngVelParent(chrono.ChVector3d(0.0, 0.0, 0.0))

    spindle.SetPos(chrono.ChVector3d(0.0, 0.0, z0))
    spindle.SetPosDt(chrono.ChVector3d(0.0, 0.0, 0.0))
    spindle.SetRot(q0)
    spindle.SetAngVelParent(chrono.ChVector3d(0.0, 0.0, 0.0))

    drive["linear_motor"] = lin
    drive["rotation_motor"] = rot
    return lin, rot


def cast_xyz_nodes(fea, mesh) -> list:
    nodes = []
    for i in range(mesh.GetNumNodes()):
        base = fea.CastToChNodeFEAbase(mesh.GetNode(i))
        node = fea.CastToChNodeFEAxyz(base)
        if node is None:
            raise SystemExit(
                "the FEA mesh contains a node type other than ChNodeFEAxyz; "
                "regenerate it with ChContinuumElastic/TetGen"
            )
        nodes.append(node)
    return nodes


def assign_nodal_masses(fea, mesh, nodes, density: float, min_mass: float) -> tuple[float, int]:
    """Lump element mass to nodes; FromTetGenFile does not do this.

    Chrono's TetGen loader creates ChNodeFEAxyz objects with zero mass.  The
    multibody solver needs a mass matrix, especially once CRM contact loads are
    applied to the surface nodes, so distribute each tetrahedron's mass equally
    to its four nodes.  A small floor is optional because the CAD-derived mesh
    contains a few extremely thin sliver elements.
    """
    mass_by_node = {}
    for i in range(mesh.GetNumElements()):
        element = fea.CastToChElementTetraCorot_4(mesh.GetElement(i))
        if element is None:
            continue
        volume = abs(element.ComputeVolume())
        share = density * volume / 4.0
        for j in range(4):
            node = element.GetTetrahedronNode(j)
            key = rigid.vec_parts(node.GetX0())
            mass_by_node[key] = mass_by_node.get(key, 0.0) + share

    total = 0.0
    floored = 0
    for node in nodes:
        key = rigid.vec_parts(node.GetX0())
        mass = mass_by_node.get(key, 0.0)
        if min_mass > 0.0 and mass < min_mass:
            mass = min_mass
            floored += 1
        node.SetMass(mass)
        total += mass
    return total, floored


def load_fea_drum(chrono, fea, system, motion, node_path: Path, ele_path: Path, pla: PlaParams):
    """Load the transformed tet mesh and return nodes, hub nodes, and material."""
    mesh = fea.ChMesh()
    mesh.SetName("pla_excavator_drum")
    material = fea.ChContinuumElastic(
        pla.youngs_modulus, pla.poisson_ratio, pla.density
    )
    material.SetRayleighDampingBeta(pla.rayleigh_beta)

    q0 = rigid.initial_chrono_quat(chrono, motion)
    pos_transform = chrono.ChVector3d(0.0, 0.0, motion.drum_center_z0)
    rot_transform = chrono.ChMatrix33d(q0)
    fea.ChMeshFileLoader.FromTetGenFile(
        mesh, str(node_path), str(ele_path), material, pos_transform, rot_transform
    )
    if mesh.GetNumNodes() == 0 or mesh.GetNumElements() == 0:
        raise SystemExit("the generated FEA mesh is empty")

    system.Add(mesh)
    nodes = cast_xyz_nodes(fea, mesh)
    total_mass, floored = assign_nodal_masses(
        fea, mesh, nodes, pla.density, pla.min_nodal_mass
    )
    print(
        f"FEA nodal masses: total={total_mass:.6g} kg, "
        f"min={min(node.GetMass() for node in nodes):.6g} kg, "
        f"max={max(node.GetMass() for node in nodes):.6g} kg, "
        f"floored={floored}",
        flush=True,
    )

    q0_tuple = rigid.quat_parts(q0)
    center = rigid.vec_parts(pos_transform)
    hub_nodes = []
    for node in nodes:
        p = rigid.vec_parts(node.GetPos())
        local = rigid.quat_rotate(rigid.quat_conj(q0_tuple),
                                  (p[0] - center[0], p[1] - center[1], p[2] - center[2]))
        radial_ok = math.hypot(local[0], local[1]) <= pla.hub_radius
        axial_ok = (
            pla.hub_axial_half_length is None
            or pla.hub_axial_half_length <= 0.0
            or abs(local[2]) <= pla.hub_axial_half_length
        )
        if radial_ok and axial_ok:
            hub_nodes.append(node)
    if not hub_nodes:
        raise SystemExit(
            f"no FEA nodes found inside the {pla.hub_radius:g} m hub; "
            "check --hub-radius and the generated mesh"
        )
    return mesh, nodes, hub_nodes, material


def nearest_targets(query: np.ndarray, targets: np.ndarray, chunk: int = 256) -> np.ndarray:
    """For each query point, return the index of the nearest target point."""
    result = []
    for start in range(0, len(query), chunk):
        block = query[start:start + chunk]
        squared = ((block[:, None, :] - targets[None, :, :]) ** 2).sum(axis=2)
        result.extend(np.argmin(squared, axis=1).tolist())
    return np.asarray(result, dtype=int)


def create_contact_skin(chrono, fea, system, nodes, contact_path: Path, motion, pla: PlaParams):
    """Create a coarse shell skin for CRM contact and tie it to the solid mesh.

    The tetrahedral mesh is needed for PLA stress, but its thousands of surface
    nodes would create far too many full-mass BCE markers.  A decimated welded
    shell with roughly 1,000 nodes gives a marker count comparable to the SPH
    particle count.  Its nodes are tied to the nearest structural nodes, so SPH
    forces still reach the tetrahedral solid while stress is measured on the
    original fine mesh.
    """
    shell_mesh = fea.ChMesh()
    shell_mesh.SetName("crm_contact_skin")
    elasticity = fea.ChElasticityKirchhoffIsothropic(pla.youngs_modulus, pla.poisson_ratio)
    shell_material = fea.ChMaterialShellKirchhoff(elasticity)
    if hasattr(shell_material, "SetDensity"):
        shell_material.SetDensity(pla.density)
    else:
        # Chrono 11 does not wrap ChMaterialFEA::SetDensity on shell
        # materials.  This contact skin is diagnostic-only, and its carrier
        # node masses are assigned explicitly below, so material density is
        # not needed to keep the node-to-node constraints well conditioned.
        print(
            "Chrono 11 binding note: ChMaterialShellKirchhoff.SetDensity is "
            "unavailable; using explicit contact-skin node masses",
            flush=True,
        )

    q0 = rigid.initial_chrono_quat(chrono, motion)
    center = chrono.ChVector3d(0.0, 0.0, motion.drum_center_z0)
    rot = chrono.ChMatrix33d(q0)
    fea.ChMeshFileLoader.BSTShellFromObjFile(
        shell_mesh, str(contact_path), shell_material, 0.001, center, rot
    )
    if shell_mesh.GetNumNodes() == 0 or shell_mesh.GetNumElements() == 0:
        raise SystemExit("the CRM contact skin OBJ produced an empty shell mesh")

    skin_nodes = cast_xyz_nodes(fea, shell_mesh)
    solid_points = np.array([rigid.vec_parts(n.GetPos()) for n in nodes], dtype=float)
    skin_points = np.array([rigid.vec_parts(n.GetPos()) for n in skin_nodes], dtype=float)
    nearest = nearest_targets(skin_points, solid_points)
    # ChLinkNodeNode enforces coincident positions.  Independent nearest-node
    # mapping can assign multiple skin nodes to the same solid node; snapping
    # those skin nodes onto their targets then collapses shell triangles.  Keep
    # the nearest map where it is unique and remap only collisions to the
    # closest unused solid nodes.
    used = set()
    collisions = []
    for i, target_index in enumerate(nearest):
        target_index = int(target_index)
        if target_index in used:
            collisions.append(i)
        else:
            used.add(target_index)
    if collisions:
        distance_rows = np.linalg.norm(
            skin_points[np.asarray(collisions), None, :] - solid_points[None, :, :], axis=2
        )
        # Resolve the most constrained shell points first.  A greedy unique
        # assignment is sufficient here because the solid mesh is much denser.
        order = sorted(range(len(collisions)), key=lambda j: distance_rows[j].min())
        for j in order:
            skin_index = collisions[j]
            candidates = np.argsort(distance_rows[j])
            target_index = next(int(k) for k in candidates if int(k) not in used)
            nearest[skin_index] = target_index
            used.add(target_index)
    distances = np.linalg.norm(skin_points - solid_points[nearest], axis=1)

    for skin_node, target_index in zip(skin_nodes, nearest):
        # Give the carrier node a mass comparable to the structural node it
        # follows.  A near-zero mass makes the node-to-node constraint
        # ill-conditioned and launches SPH particles out of the domain.
        target = nodes[int(target_index)]
        # Start the carrier exactly on its structural target.  Leaving the
        # coarse-surface offset in place gives the node-node link an initial
        # position error of up to 3.7 mm and produces an impulsive launch.
        skin_node.SetPos(target.GetPos())
        if hasattr(skin_node, "SetPosDt"):
            skin_node.SetPosDt(target.GetPosDt())
        skin_node.SetMass(max(target.GetMass(), 1.0e-8))
        link = fea.ChLinkNodeNode()
        link.Initialize(skin_node, target)
        system.Add(link)

    # Add the mesh only after its node states and masses are finalized so the
    # system's initial state matches what the node-to-node links expect.
    system.Add(shell_mesh)
    print(
        f"CRM contact skin: {len(skin_nodes)} nodes, {shell_mesh.GetNumElements()} shells, "
        f"tied to solid; nearest distance median={np.median(distances):.4g} m, "
        f"max={distances.max():.4g} m",
        flush=True,
    )
    return shell_mesh


def attach_hub_nodes(fea, system, spindle, hub_nodes) -> list:
    """Tie each bore node to the rigid spindle; each link removes 3 DOFs."""
    links = []
    for node in hub_nodes:
        link = fea.ChLinkNodeFrame()
        # No attachment point is supplied: Initialize() captures the current
        # node location in the spindle's local frame.
        link.Initialize(node, spindle)
        system.Add(link)
        links.append(link)
    return links


def spin_ramp_omega(motion, t: float, ramp_time: float) -> float:
    """Instantaneous spin speed for an optional linear spin-up ramp."""
    elapsed = max(0.0, t - motion.settle_time)
    if ramp_time <= 0.0 or elapsed >= ramp_time:
        return motion.drum_omega
    return motion.drum_omega * elapsed / ramp_time


def spin_ramp_angle(motion, t: float, ramp_time: float) -> float:
    """Spin angle whose derivative is ``spin_ramp_omega``."""
    elapsed = max(0.0, t - motion.settle_time)
    if ramp_time <= 0.0:
        return motion.drum_omega * elapsed
    if elapsed >= ramp_time:
        return motion.drum_omega * (elapsed - 0.5 * ramp_time)
    return 0.5 * motion.drum_omega * elapsed * elapsed / ramp_time


def set_solid_initial_velocity(chrono, nodes, motion, t0: float = 0.0, ramp_time: float = 0.0,
                               *, descent_mode: str = "kinematic") -> None:
    """Start every solid node consistent with the prescribed spindle motion.

    Hub nodes are constrained to the spindle; initializing free nodes with the
    same rigid-body velocity avoids an impulsive startup transient.
    """
    omega = spin_ramp_omega(motion, t0, ramp_time)
    axis = motion.drum_spin_axis.upper()
    omega_vec = {
        "X": (omega, 0.0, 0.0),
        "Y": (0.0, omega, 0.0),
        "Z": (0.0, 0.0, omega),
    }[axis]
    center = (0.0, 0.0, motion.drum_z(t0))
    moving = descent_mode == "kinematic" and t0 >= motion.settle_time and center[2] > motion.z_target
    descent = (0.0, 0.0, -motion.lower_speed if moving else 0.0)

    for node in nodes:
        p = rigid.vec_parts(node.GetPos())
        r = tuple(p[k] - center[k] for k in range(3))
        rotational = cross(omega_vec, r)
        velocity = tuple(rotational[k] + descent[k] for k in range(3))
        node.SetPosDt(chrono.ChVector3d(*velocity))
        if hasattr(node, "SetPosDt2"):
            # Centripetal term for constant/linear-ramp angular velocity phases.
            centripetal = cross(omega_vec, rotational)
            node.SetPosDt2(chrono.ChVector3d(*centripetal))


def spin_axis_tuple(motion) -> tuple[float, float, float]:
    axis = motion.drum_spin_axis.upper()
    return {
        "X": (1.0, 0.0, 0.0),
        "Y": (0.0, 1.0, 0.0),
        "Z": (0.0, 0.0, 1.0),
    }[axis]


def cross(a, b) -> tuple[float, float, float]:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def spindle_reaction(links, spindle, motion) -> tuple[float, float]:
    """Return drive-axis torque and total force from all hub-link reactions."""
    center = rigid.vec_parts(spindle.GetPos())
    q = rigid.quat_parts(spindle.GetRot())
    axis = spin_axis_tuple(motion)

    torque = 0.0
    total_force = [0.0, 0.0, 0.0]
    for link in links:
        local_force = rigid.vec_parts(link.GetReactionOnBody())
        force = rigid.quat_rotate(q, local_force)
        point = rigid.vec_parts(link.GetConstrainedNode().GetPos())
        arm = (point[0] - center[0], point[1] - center[1], point[2] - center[2])
        moment = cross(arm, force)
        torque += moment[0] * axis[0] + moment[1] * axis[1] + moment[2] * axis[2]
        for i in range(3):
            total_force[i] += force[i]
    return torque, math.sqrt(sum(v * v for v in total_force))


def sph_positions(fluid, fsi, terrain) -> list:
    """Return only SPH fluid (soil) positions, not boundary/rigid/flex BCE markers."""
    return rigid.particle_positions(fluid, fsi, terrain)


def max_nodal_displacement(nodes) -> float:
    largest = 0.0
    for node in nodes:
        p = rigid.vec_parts(node.GetPos())
        x0 = rigid.vec_parts(node.GetX0())
        d2 = sum((p[i] - x0[i]) ** 2 for i in range(3))
        largest = max(largest, d2)
    return math.sqrt(largest)


def von_mises_pa(stress) -> float:
    """Return von Mises stress (Pa) from a Chrono stress tensor.

    Chrono 11's stock SWIG wrapper omits ``GetEquivalentVonMises()``.  The
    installer applies ``environment/patches/chrono-python-von-mises.patch``,
    which exposes the native C++ formula as ``VonMises()``.  Use the original
    name if a future binding provides it.  Principal-stress reconstruction is
    deliberately avoided: Chrono 11's ``ComputePrincipalStresses()`` returns
    NaNs for hydrostatic/zero tensors, which would corrupt exported stress
    arrays.
    """
    for name in ("VonMises", "GetEquivalentVonMises"):
        native = getattr(stress, name, None)
        if callable(native):
            return float(native())
    raise RuntimeError(
        "this Chrono build does not expose von Mises stress; "
        "apply environment/patches/chrono-python-von-mises.patch"
    )


def max_von_mises(fea, mesh, stride: int) -> float:
    return worst_stress_info(fea, mesh, stride)[1]


def worst_stress_info(fea, mesh, stride: int, min_volume: float = 0.0,
                      stress_values=None) -> tuple[int, float, float, tuple[float, float, float]]:
    """Return index, von Mises stress, volume, and centroid of the hot element.

    ``min_volume`` can exclude sliver tetrahedra whose stress is dominated by a
    near-singular element Jacobian; the promoted mesh has a handful of elements
    below 1e-12 m^3.  If ``stress_values`` is a NumPy array, every visited
    element's von Mises stress is also written into it so callers can reuse the
    scan for VTU export instead of looping over the mesh a second time.
    """
    worst_index = -1
    largest = 0.0
    volume = 0.0
    centroid = (0.0, 0.0, 0.0)
    for i in range(0, mesh.GetNumElements(), stride):
        element = fea.CastToChElementTetraCorot_4(mesh.GetElement(i))
        if element is None:
            if stress_values is not None:
                stress_values[i] = 0.0
            continue
        value = von_mises_pa(element.GetStress())
        if stress_values is not None:
            stress_values[i] = value
        element_volume = abs(element.ComputeVolume())
        if element_volume < min_volume:
            continue
        if value > largest:
            largest = value
            worst_index = i
            volume = element_volume
            points = [rigid.vec_parts(element.GetTetrahedronNode(j).GetPos()) for j in range(4)]
            centroid = tuple(sum(p[k] for p in points) / 4.0 for k in range(3))
    return worst_index, largest, volume, centroid


def worst_disp_info(nodes) -> tuple[int, float, tuple[float, float, float]]:
    """Return index, magnitude, and vector of the largest displacement."""
    worst_index = -1
    largest = 0.0
    vector = (0.0, 0.0, 0.0)
    for i, node in enumerate(nodes):
        p = rigid.vec_parts(node.GetPos())
        x0 = rigid.vec_parts(node.GetX0())
        d = tuple(p[k] - x0[k] for k in range(3))
        mag2 = sum(v * v for v in d)
        if mag2 > largest:
            largest = mag2
            worst_index = i
            vector = d
    return worst_index, math.sqrt(largest), vector


def write_fea_config(path: Path, soil, run, pla: PlaParams, motion, node_path: Path,
                     ele_path: Path, mesh, hub_nodes, capacity_volume: float,
                     particle_mass: float, particle_mass_source: str,
                     spin_ramp_time: float, contact_path: Path | None,
                     bce_layers: int, solver_name: str, solver_iterations: int,
                     solver_tolerance: float, timestepper: str,
                     num_threads: int) -> None:
    config = {
        "model": "Chrono::CRM + FEA solid drum (spindle-driven)",
        "soil": asdict(soil),
        "friction_angle_deg": soil.friction_angle_deg,
        "run": {**asdict(run), "active_box": list(run.active_box)},
        "gravity_m_s2": run.gravity,
        "pla": asdict(pla),
        "spin_ramp_time_s": spin_ramp_time,
        "fea": {
            "node_file": str(node_path),
            "ele_file": str(ele_path),
            "nodes": mesh.GetNumNodes(),
            "elements": mesh.GetNumElements(),
            "hub_nodes": len(hub_nodes),
            "link_constraints": 3 * len(hub_nodes),
            "bce_layers": bce_layers,
            "bce_pattern": "BcePatternMesh2D_INWARD",
            "solver": solver_name,
            "solver_iterations": solver_iterations,
            "solver_tolerance": solver_tolerance,
            "timestepper": timestepper,
            "chrono_eigen_threads": num_threads,
            "contact": (
                "terrain.AddFeaMesh(contact_mesh, False); no rigid drum obstacle"
                if contact_path is not None
                else "terrain.AddFeaMesh(mesh, False); no rigid drum obstacle"
            ),
            "contact_skin_file": str(contact_path) if contact_path is not None else None,
        },
        "geometry": {
            "drum_radius_m": motion.drum_radius,
            "drum_half_length_m": motion.drum_half_length,
            "bed_height_m": motion.bed_height,
            "target_bottom_clearance_m": motion.target_bottom_clearance,
            "spin_axis_global": motion.drum_spin_axis,
            "capacity_volume_m3": capacity_volume,
            "capacity_source": "user override" if run.capacity_volume else "pi*r_i^2*L",
        },
        "particle_mass_kg": particle_mass,
        "particle_mass_source": particle_mass_source,
        "fill_ratio_definition": "excavated SPH mass / (bulk density * nominal cavity volume)",
    }
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2)
        handle.write("\n")


def main() -> int:
    args = build_parser().parse_args()
    # The rigid driver defaults to 5e-4 s; the FEA/SPH system is only stable on
    # the promoted mesh at 2.5e-4 s.  An explicit --dt still wins.
    if args.dt is None:
        args.dt = 2.5e-4
    soil = rigid.make_soil(args)
    run = rigid.make_run_params(args)
    pla = make_pla(args)

    if args.print_soil:
        print(soil.summary())
        return 0

    node_path, ele_path = resolve_fea_mesh(args)
    motion = replace(
        MOTION_DEFAULT,
        t_max=run.duration,
        settle_time=run.settle_time,
        lower_speed=run.descent_speed,
        drum_omega=run.omega,
        gravity=run.gravity,
        bed_height=run.bed_height,
        bed_padding=run.bed_padding,
        drum_spin_axis=run.spin_axis,
        drum_rest_clearance=MOTION_DEFAULT.drum_rest_clearance + args.initial_gap,
    )
    capacity_volume = (
        run.capacity_volume
        if run.capacity_volume is not None
        else math.pi * run.inner_radius**2 * (2.0 * motion.drum_half_length)
    )
    if capacity_volume <= 0.0:
        raise SystemExit("capacity volume must be positive")

    surface_z = motion.floor_z + motion.bed_height
    print(soil.summary(), flush=True)
    print(pla.summary(), flush=True)
    print(
        "\n".join(
            [
                f"CRM+FEA run: spacing={run.spacing:g} m, dt={run.dt:g} s, "
                f"duration={run.duration:g} s, fps={run.fps:g}",
                f"FEA mesh: {node_path.name} / {ele_path.name}",
                f"drum: R={motion.drum_radius:g} m, half-length={motion.drum_half_length:g} m, "
                f"spin={motion.drum_spin_axis.upper()}, omega={run.omega:g} rad/s",
                (
                    f"descent: force {run.descent_force_n:g} N downward "
                    f"(ramp={run.descent_force_ramp_s:g} s), z0={motion.drum_center_z0:.4f} m -> "
                    f"hold at {motion.z_target:.4f} m"
                    if run.descent_mode == "force"
                    else f"descent: {run.descent_speed:g} m/s, z0={motion.drum_center_z0:.4f} m -> "
                    f"{motion.z_target:.4f} m"
                ) + f", bed surface z={surface_z:.4f} m",
                f"active SPH box: {run.active_box}, capacity={capacity_volume:.6g} m^3 "
                f"({capacity_volume * soil.density:.4f} kg)",
                f"gravity: gz={run.gravity:g} m/s^2 on soil and FEA drum",
                f"spin-up ramp: {args.spin_ramp_time:g} s",
            ]
        ),
        flush=True,
    )

    chrono, fsi, veh = rigid.import_crm_modules()
    try:
        import pychrono.fea as fea
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("this PyChrono build does not expose pychrono.fea") from exc

    out_dir = args.output_dir.resolve()
    if not args.no_output:
        out_dir.mkdir(parents=True, exist_ok=True)
    chrono.SetChronoOutputPath(str(out_dir) + "/")

    system = chrono.ChSystemNSC()
    # Chrono uses this setting for its parallel assembly work and Eigen's
    # threaded sparse/dense kernels.  Keep Bullet collision processing serial:
    # the FEA/CRM coupling does not rely on multicore collision detection.
    system.SetNumThreads(args.num_threads, 1, args.num_threads)
    system.SetGravitationalAcceleration(chrono.ChVector3d(0.0, 0.0, run.gravity))
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
    # Barzilai-Borwein and PSOR (used elsewhere in this project) reject systems
    # with FEA stiffness/damping matrices.  Chrono's own FEA tire demo uses a
    # direct sparse solver, so expose the available direct solvers as well as
    # the previously used diagonally preconditioned GMRES fallback.
    if args.solver == "sparse-lu":
        solver = chrono.ChSolverSparseLU()
    elif args.solver == "sparse-qr":
        solver = chrono.ChSolverSparseQR()
    else:
        solver = chrono.ChSolverGMRES()
        solver.SetMaxIterations(args.solver_iterations)
        solver.SetTolerance(args.solver_tolerance)
        solver.EnableDiagonalPreconditioner(True)
    system.SetSolver(solver)

    terrain = veh.CRMTerrain(system, run.spacing)
    terrain.SetVerbose(False)
    terrain.SetGravitationalAcceleration(chrono.ChVector3d(0.0, 0.0, run.gravity))
    terrain.SetStepSizeCFD(run.dt)
    if hasattr(terrain, "SetStepsizeMBD"):
        terrain.SetStepsizeMBD(run.dt)
    terrain.SetActiveDomain(chrono.ChVector3d(*run.active_box))
    if hasattr(terrain, "SetFreeFlowDuration"):
        terrain.SetFreeFlowDuration(run.settle_time)
    elif hasattr(terrain, "SetActiveDomainDelay"):
        terrain.SetActiveDomainDelay(run.settle_time)

    soil.apply(fsi, terrain)
    rigid.configure_sph(fsi, terrain, run)

    drive = create_spindle(chrono, system, motion)
    spindle = drive["spindle"]
    configure_spindle_drive(
        chrono, system, motion, drive, args.spin_ramp_time, run.duration, run=run
    )
    mesh, nodes, hub_nodes, _material = load_fea_drum(
        chrono, fea, system, motion, node_path, ele_path, pla
    )
    if not args.no_initial_solid_velocity:
        set_solid_initial_velocity(
            chrono, nodes, motion, 0.0, args.spin_ramp_time,
            descent_mode=run.descent_mode,
        )
    else:
        # Hub nodes are constrained to the spindle; keep their state consistent.
        for node in hub_nodes:
            node.SetPosDt(chrono.ChVector3d(0.0, 0.0, 0.0))
            if hasattr(node, "SetPosDt2"):
                node.SetPosDt2(chrono.ChVector3d(0.0, 0.0, 0.0))
    contact_mesh = mesh
    contact_path = None
    if args.use_contact_skin:
        contact_path = args.contact_obj.resolve()
        contact_mesh = create_contact_skin(
            chrono, fea, system, nodes, contact_path, motion, pla
        )
    links = attach_hub_nodes(fea, system, spindle, hub_nodes)
    print(
        f"FEA drum: {mesh.GetNumNodes()} nodes, {mesh.GetNumElements()} tets, "
        f"{len(hub_nodes)} hub nodes, {3 * len(links)} spindle constraints",
        flush=True,
    )

    # Two-way contact on the tetrahedral solid's boundary faces.  The FEA tire
    # demo in Chrono uses inward 2-D BCE markers; the default centered pattern
    # places markers on both sides of a thin shell/solid surface and produces
    # grossly excessive contact forces.  The spindle is intentionally NOT
    # registered with AddRigidBody: only the FEA mesh contacts the soil.
    fluid_early = rigid.get_fluid_system(terrain, fsi)
    fluid_early.SetNumBCELayers(args.bce_layers)
    if hasattr(terrain, "SetBcePattern2D"):
        terrain.SetBcePattern2D(fsi.BcePatternMesh2D_INWARD, False)
    terrain.AddFeaMesh(contact_mesh, args.check_fea_embedded)

    box_size = chrono.ChVector3d(
        2.0 * motion.bed_x_half, 2.0 * motion.bed_y_half, motion.bed_height
    )
    box_center = chrono.ChVector3d(0.0, 0.0, motion.floor_z)
    terrain.Construct(box_size, box_center, rigid.side_flags_open_top(fsi))
    terrain.Initialize()

    fluid = rigid.get_fluid_system(terrain, fsi)
    particle_mass, mass_source = rigid.get_particle_mass(fluid, soil, run.spacing)
    # Chrono 11 renamed this diagnostic getter from GetNumFlexBodyMarkers to
    # GetNumFleaMeshMarkers; support both so the count is not silently zero.
    if hasattr(fluid, "GetNumFleaMeshMarkers"):
        flex_markers = fluid.GetNumFleaMeshMarkers()
    elif hasattr(fluid, "GetNumFlexBodyMarkers"):
        flex_markers = fluid.GetNumFlexBodyMarkers()
    else:
        flex_markers = 0
    print(
        f"SPH particles: {terrain.GetNumSPHParticles()}, "
        f"boundary BCE: {terrain.GetNumBoundaryBCEMarkers()}, "
        f"FEA flex BCE: {flex_markers}, "
        f"particle mass: {particle_mass:.6g} kg ({mass_source})",
        flush=True,
    )

    writer = None
    state_file = None
    if not args.no_output:
        write_fea_config(
            out_dir / "config.json", soil, run, pla, motion, node_path, ele_path,
            mesh, hub_nodes, capacity_volume, particle_mass, mass_source,
            args.spin_ramp_time,
            contact_path,
            args.bce_layers, args.solver, args.solver_iterations,
            args.solver_tolerance, args.timestepper, args.num_threads,
        )
        state_file = (out_dir / "summary.csv").open("w", newline="", encoding="utf-8")
        writer = csv.DictWriter(state_file, fieldnames=fea_metric_fields(run))
        writer.writeheader()

    peak_fill = 0.0
    work_j = 0.0
    last_row = {}
    frame = 0
    wall_start = time.perf_counter()
    next_progress = 0.0

    def descent_telemetry(t: float) -> dict:
        if run.descent_mode != "force":
            return {}
        drive_state = drive["descent"]
        state = drive_state["state"]
        actuator = (
            abs(float(drive_state["force_motor"].GetMotorForce()))
            if state == "descend" else 0.0
        )
        vertical = (
            rigid.vec_parts(spindle.GetPosDt())[2]
            if hasattr(spindle, "GetPosDt") else 0.0
        )
        return {
            "descent_mode": run.descent_mode,
            "descent_state": state,
            "actuator_force_n": f"{actuator:.6g}",
            "vertical_speed_m_s": f"{vertical:.6g}",
        }

    def record(t: float, axis_torque: float, reaction_force: float) -> None:
        nonlocal frame, peak_fill, last_row
        positions = sph_positions(fluid, fsi, terrain)
        with_flags = bool(args.save_particles and not args.no_output)
        metrics, flags = rigid.compute_metrics(
            positions, spindle, run, motion, soil, particle_mass,
            capacity_volume, surface_z, with_flags=with_flags,
        )
        peak_fill = max(peak_fill, metrics["fill_ratio_mass"])
        angle = math.degrees(spin_ramp_angle(motion, t, args.spin_ramp_time))
        motor = drive["rotation_motor"]
        motor_angle = motor.GetMotorAngle()
        motor_speed = motor.GetMotorAngleDt()
        stress_index, max_stress, stress_volume, stress_center = worst_stress_info(
            fea, mesh, args.stress_stride
        )
        disp_index, max_disp, disp_vector = worst_disp_info(nodes)
        row = {
            "frame": frame,
            "time_s": f"{t:.3f}",
            "wall_s": f"{time.perf_counter() - wall_start:.3f}",
            "drum_z_m": f"{rigid.vec_parts(spindle.GetPos())[2]:.7f}",
            "spin_deg": f"{angle:.3f}",
            "omega_rad_s": f"{motor_speed:.6g}",
            "sph_particles": metrics["sph_particles"],
            "inside_drum": metrics["inside_drum"],
            "lifted": metrics["lifted"],
            "excavated": metrics["excavated"],
            "fill_ratio_count": f"{metrics['fill_ratio_count']:.6f}",
            "fill_ratio_mass": f"{metrics['fill_ratio_mass']:.6f}",
            "peak_fill_ratio_mass": f"{peak_fill:.6f}",
            "capacity_m3": f"{capacity_volume:.9g}",
            "particle_mass_kg": f"{particle_mass:.9g}",
            "fluid_torque_x_nm": f"{axis_torque:.6g}",
            "input_power_w": f"{max(0.0, -axis_torque * run.omega):.6g}",
            "work_j": f"{work_j:.6g}",
            "specific_energy_j_kg": f"{work_j / max(metrics['excavated_mass_kg'], 1e-12):.6g}",
            "spindle_axis_torque_nm": f"{axis_torque:.6g}",
            "spindle_reaction_force_n": f"{reaction_force:.6g}",
            "spindle_motor_angle_rad": f"{motor_angle:.6g}",
            "spindle_motor_speed_rad_s": f"{motor_speed:.6g}",
            "max_nodal_displacement_m": f"{max_disp:.6g}",
            "max_von_mises_pa": f"{max_stress:.6g}",
        }
        row.update(descent_telemetry(t))
        if writer is not None:
            writer.writerow(row)
            state_file.flush()
        if flags is not None:
            rigid.write_particle_csv(out_dir / f"particles_{frame:06d}.csv", positions, flags)
        last_row = row
        frame += 1
        print(
            f"frame {frame - 1:05d}  t={t:7.3f}s  inside={metrics['inside_drum']:6d}  "
            f"fill={metrics['fill_ratio_mass']:.4f}  peak={peak_fill:.4f}  "
            f"disp={max_disp:.4g}m  vm={max_stress / 1e6:.4g}MPa  "
            f"tau={axis_torque:.4g}Nm",
            flush=True,
        )
        if args.debug_stress:
            print(
                f"  hot element {stress_index}: vm={max_stress / 1e6:.6g} MPa, "
                f"volume={stress_volume:.6g} m^3, "
                f"centroid={tuple(round(v, 5) for v in stress_center)}; "
                f"disp node {disp_index}: |d|={max_disp:.6g} m, "
                f"d={tuple(round(v, 6) for v in disp_vector)}",
                flush=True,
            )

    axis_torque, reaction_force = spindle_reaction(links, spindle, motion)
    if run.descent_mode == "force":
        rigid.update_force_descent(
            drive["descent"], motion, run, 0.0, rigid.vec_parts(spindle.GetPos())[2]
        )
    record(0.0, axis_torque, reaction_force)
    next_output = 1.0 / run.fps

    t = 0.0
    try:
        while t < run.duration - 1.0e-12:
            h = min(run.dt, run.duration - t)
            if run.descent_mode == "force":
                z = rigid.vec_parts(spindle.GetPos())[2]
                vz = (rigid.vec_parts(spindle.GetPosDt())[2]
                      if hasattr(spindle, "GetPosDt") else 0.0)
                rigid.update_force_descent(
                    drive["descent"], motion, run, t, z, velocity=vz, dt=h
                )
            terrain.Synchronize(t)
            terrain.Advance(h)
            t += h
            if run.descent_mode == "force":
                rigid.update_force_descent(
                    drive["descent"], motion, run, t, rigid.vec_parts(spindle.GetPos())[2]
                )

            if args.debug_fea:
                disp = max_nodal_displacement(nodes)
                extent = max(
                    abs(component)
                    for node in nodes
                    for component in rigid.vec_parts(node.GetPos())
                )
                forces = [rigid.vec_parts(node.GetForce()) for node in nodes]
                fmags = [math.sqrt(sum(v * v for v in f)) for f in forces]
                worst = max(range(len(fmags)), key=fmags.__getitem__)
                wpos = rigid.vec_parts(nodes[worst].GetPos())
                center = rigid.vec_parts(spindle.GetPos())
                axis = spin_axis_tuple(motion)
                contact_torque = 0.0
                for node, force in zip(nodes, forces):
                    p = rigid.vec_parts(node.GetPos())
                    arm = tuple(p[k] - center[k] for k in range(3))
                    moment = cross(arm, force)
                    contact_torque += moment[0] * axis[0] + moment[1] * axis[1] + moment[2] * axis[2]
                print(
                    f"  step {frame:05d}: max_disp={disp:.6g} m, max_abs={extent:.6g} m, "
                    f"max_F={fmags[worst]:.6g} N at {tuple(round(v, 5) for v in wpos)}, "
                    f"sum_F={sum(fmags):.6g} N, nodal_tau={contact_torque:.6g} Nm",
                    flush=True,
                )

            axis_torque, reaction_force = spindle_reaction(links, spindle, motion)
            motor_speed = drive["rotation_motor"].GetMotorAngleDt()
            fluid_power = max(0.0, -axis_torque * motor_speed)
            work_j += fluid_power * h

            if t >= next_output - 1.0e-12 or t >= run.duration - 1.0e-12:
                record(t, axis_torque, reaction_force)
                next_output += 1.0 / run.fps

            if t >= next_progress:
                print(f"  t={t:7.3f}s / {run.duration:g}s", flush=True)
                next_progress += 1.0
    finally:
        if state_file is not None:
            state_file.close()

    print(
        "\nCRM+FEA run complete: "
        f"final_fill={last_row.get('fill_ratio_mass')} "
        f"peak_fill={peak_fill:.6f} "
        f"inside={last_row.get('inside_drum')} "
        f"excavated={last_row.get('excavated')} "
        f"max_disp={last_row.get('max_nodal_displacement_m')} m "
        f"max_von_mises={last_row.get('max_von_mises_pa')} Pa "
        f"axis_torque={axis_torque:.4g} Nm "
        f"work={work_j:.4g} J",
        flush=True,
    )
    if not args.no_output:
        print(f"Output: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
