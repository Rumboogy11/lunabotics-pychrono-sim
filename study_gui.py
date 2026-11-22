#!/usr/bin/env python3
"""Tkinter GUI consolidating PyChrono excavator study scripts.

Provides an editable, multi-step study builder for the CRM -> one-way FEA
workflow (can go back and change parameters), plus quick tools for exporting
and viewing existing study outputs.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlparse


def _is_chrono_python(current: Path, chrono_py: Path) -> bool:
    """True when *current* is any ``chrono-env/bin/python*`` interpreter.

    The environment ships both ``python`` and ``python3`` as separate files,
    so comparing only with ``bin/python`` misses the common
    ``chrono-env/bin/python3 study_gui.py`` launch path.
    """
    try:
        return current.parent == chrono_py.parent and current.name.startswith("python")
    except AttributeError:
        return False


def _maybe_reexec_for_display():
    """Prefer system Python for the Tk UI when launched from chrono-env.

    chrono-env Python/Tk has no scalable fonts here, so every font family falls
    back to ``fixed`` and the GUI looks like a terminal. System Python/Tk finds
    the normal scalable fonts and proper display scaling. Simulation tools,
    Chrono, and OCP are still executed via chrono-env Python by helpers.
    Set PYCHRONO_GUI_NO_REEXEC=1 to disable.
    """
    if os.environ.get("PYCHRONO_GUI_NO_REEXEC") == "1":
        return
    root = Path(__file__).resolve().parent
    chrono_bin = (root / "chrono-env" / "bin").resolve()
    chrono_py = chrono_bin / "python"
    try:
        cur = Path(sys.executable).resolve()
    except Exception:
        return
    if not _is_chrono_python(cur, chrono_py):
        return

    candidates: list[str] = []
    for p in ("/usr/bin/python3", "/usr/local/bin/python3", shutil.which("python3") or ""):
        if not p:
            continue
        try:
            rp = str(Path(p).resolve())
        except Exception:
            rp = p
        if rp and Path(rp).parent != chrono_bin and rp not in candidates:
            candidates.append(rp)

    for cand in candidates:
        try:
            r = subprocess.run(
                [cand, "-c", "import tkinter"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if r.returncode != 0:
                continue
        except Exception:
            continue
        env = os.environ.copy()
        env["PYCHRONO_GUI_NO_REEXEC"] = "1"
        env.setdefault("PYCHRONO_CHRONO_PYTHON", str(chrono_py))
        os.execve(cand, [cand] + sys.argv, env)


_maybe_reexec_for_display()

import json
import math
import queue
import re
import shlex
import threading
import time
from datetime import datetime
import tkinter as tk
from tkinter import BOTH, DISABLED, END, HORIZONTAL, LEFT, NORMAL, RIGHT, VERTICAL, W, X, Y
from tkinter import BooleanVar, StringVar, Tk, colorchooser, filedialog, messagebox
from tkinter import font as tkfont
from tkinter import ttk

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "crm") not in sys.path:
    sys.path.insert(0, str(ROOT / "crm"))

import study_wizard as wiz  # noqa: E402
import view_study_paraview as pvview  # noqa: E402  (std-lib only; no ParaView import here)
from study_output_preview import (  # noqa: E402
    LINE_WIDTH_DEFAULT,
    LINE_WIDTH_MAX,
    LINE_WIDTH_MIN,
    PARTICLE_SAMPLE_DEFAULT,
    PARTICLE_SAMPLE_MAX,
    PARTICLE_SIZE_DEFAULT,
    PARTICLE_SIZE_MAX,
    PARTICLE_SIZE_MIN,
    OutputPreview,
)


def _info(control: str, impact: str) -> str:
    """Format a consistent Info-button explanation with cost."""
    text = control.strip()
    if impact.strip():
        text += f"\n\n{impact.strip()}"
    return text


PARAM_INFO: dict[str, str] = {
    # Study
    "study_name": _info(
        "Short label for this run. Editing it immediately resets the output directory to Output_<study name> under the project folder; you can still edit the output directory afterwards.",
        "Cost: none for the simulation; it only names files and folders.",
    ),
    "output_dir": _info(
        "Folder for meshes, logs, configs, and results. It is created if needed; use a new folder to preserve earlier runs. Editing the study name resets this field to Output_<study name>.",
        "Cost: a slow or network drive makes every log/CSV write slower; a local empty folder is fastest.",
    ),
    "geometry": _info(
        "Choose where the excavator geometry comes from. supplied/files use the mesh paths below; step converts a STEP CAD assembly first. No geometry is bundled.",
        "Cost: step adds CAD conversion and meshing time before the simulation; supplied/files skip that stage.",
    ),
    "auto_geometry": _info(
        "Measure the selected CAD/mesh and refresh suggested drum, bed, domain, spacing, and mesh values. Note that when this is on, command generation also restores drum radius, half-length, inner radius, and duration to measured/default-consistent values, so manual edits to those fields may be overridden.",
        "Cost: the measurement is quick, but accepting a tighter spacing or larger bed can make the later run much slower; turn this off if you need hand-set dimensions to survive command generation.",
    ),
    # Geometry
    "step_file": _info(
        "CAD input (.step/.stp), used only when Geometry source is step. New models use rotation axis Y in STEP space.",
        "Cost: larger or more detailed CAD files take longer to tessellate, fuse, and mesh.",
    ),
    "crm_obj": _info(
        "Triangulated drum surface used for rigid Chrono::CRM soil contact. Use the full-detail OBJ for production; legacy simplified meshes can close holes in the shell.",
        "Cost: more contact triangles slow the SPH contact search, but a simplified mesh that seals a hole changes the physics and is worse than the extra cost.",
    ),
    "fea_node": _info(
        "TetGen .node file containing the nodes of the solid drum mesh for one-way FEA.",
        "Cost: more nodes mean a larger structural system and a slower FEA solve.",
    ),
    "fea_ele": _info(
        "TetGen .ele file pairing nodes into tetrahedra for the FEA drum mesh.",
        "Cost: more tetrahedra mean more memory and a slower FEA solve.",
    ),
    "cad_axis": _info(
        "Drum axis in the original STEP coordinates. New models default to Y; the converter rotates this axis to local +Z for Chrono.",
        "Cost: none directly, but a wrong axis produces a wrongly oriented drum and a wasted run.",
    ),
    "unit_scale": _info(
        "Multiply STEP coordinates by this factor (0.001 when CAD dimensions are millimetres). Final mesh units must be metres.",
        "Cost: none directly, but a wrong scale can create an enormous domain, far more particles, and a very slow or unstable run.",
    ),
    "mesh_size": _info(
        "Approximate tetrahedral edge length for the FEA solid mesh. Smaller means a finer mesh.",
        "Cost: halving the edge length can increase element count roughly eightfold and greatly slow meshing and FEA.",
    ),
    "surface_target_faces": _info(
        "Starting contact-surface triangle count converted from STEP. The default route doubles it until the exterior fit and TetGen both accept the surface; 8,000 is a modest default. Automatic retries are disabled on the legacy 'after' repair route.",
        "Cost: too few tiles fit badly; very many slow CRM contact and can create sliver tetrahedra that destabilize FEA. On the 'after' route the count is fixed, so set it high enough up front.",
    ),
    "surface_max_faces": _info(
        "Upper bound for the watertight/fit-driven decimation retry used by STEP mesh generation.",
        "Cost: a higher ceiling can improve fit but lengthens meshing and increases the chance of unhealthy slivers.",
    ),
    "surface_fit_tolerance": _info(
        "Maximum allowed deviation in metres between the converted exterior and the raw CAD surface. The default route doubles the face target until this fit and TetGen pass; the achieved error is recorded in the mesh JSON. 0 disables the fit check, and retries do not run on the legacy 'after' repair route.",
        "Cost: a tighter tolerance usually means more triangles and more meshing time; 0 is fastest but skips the check entirely.",
    ),
    "surface_repair_order": _info(
        "'none' unifies fused CAD faces and retries decimation until watertight. 'after' is a legacy MeshFix pass after decimation that can inflate volume; 'before' is accepted but generated studies do not pass it on, so it behaves like 'none'.",
        "Cost: 'after' adds preprocessing time, can inflate the solid, disables fit retries, and cannot be combined with part-aware simplification; 'before' adds no cost over 'none'.",
    ),
    "min_solid_volume": _info(
        "Drop STEP assembly solids smaller than this volume (m^3) before meshing, such as fasteners or debris.",
        "Cost: removing tiny solids usually makes meshing faster and more reliable; setting it too high can delete useful geometry.",
    ),
    "hub_radius": _info(
        "Nodes within this radius of the drum axis are tied rigidly to the spindle in FEA.",
        "Cost: a larger rigid core slightly reduces the flexible system size, but it also changes where stresses can develop.",
    ),
    "part_aware_simplify": _info(
        "Recommended: coarsen only hub/interior surface faces before TetGen. Exterior bucket faces are copied unchanged. The hub is rigid in FEA, so its dense CAD triangulation is mostly wasted solver cost.",
        "Cost: this usually makes FEA much faster without changing the soil-contact surface.",
    ),
    "part_aware_radius": _info(
        "Faces wholly inside this radial distance from the drum axis may be coarsened. Use slightly more than the rigid hub radius. Incompatible with MeshFix order 'after'.",
        "Cost: a larger radius coarsens more of the mesh and speeds FEA, but too large a value can reach geometry you care about.",
    ),
    "part_aware_reduction": _info(
        "Fraction of eligible interior faces removed by topology-preserving, boundary-fixed decimation. 0.75 removed about 74% of eligible faces and 23% of total nodes on the current CAD.",
        "Cost: a higher fraction makes the mesh smaller and faster; excessive reduction can distort the rigid hub region.",
    ),
    "part_aware_min_faces": _info(
        "Skip interior coarsening unless at least this many faces qualify, avoiding changes to already-coarse meshes.",
        "Cost: none when the mesh is simple; it protects small meshes from unnecessary work or distortion.",
    ),
    "drum_radius": _info(
        "Outer crest radius. It sets the initial drum height (bed height + radius) and helps size the bed.",
        "Cost: a larger radius raises the drum and enlarges the bed/domain, which adds SPH particles and slows the CRM run.",
    ),
    "drum_half_length": _info(
        "Half the drum length along its axis, used for the bed box and CRM placement.",
        "Cost: a wider bucket needs more soil and more particles, so the CRM run slows roughly with the added volume.",
    ),
    "inner_radius": _info(
        "Nominal cavity radius used to estimate fill ratio and capacity volume.",
        "Cost: little by itself, but it feeds capacity and detector estimates that affect what is counted as fill.",
    ),
    "capacity_volume": _info(
        "Known internal cavity volume. Leave blank to derive capacity from the drum radii and length.",
        "Cost: none; it changes the denominator of the fill-ratio calculation, not the solver workload.",
    ),
    "detector_auto_size": _info(
        "Automatically size the fill-detector cylinder from the measured drum radius and full length. Uncheck to enter radius and length manually; the 3D adjuster is also available.",
        "Cost: no meaningful solver cost, but a badly sized detector can make the reported fill ratio misleading.",
    ),
    "detector_radius": _info(
        "Manual nominal fill-detector cylinder radius in metres. One SPH particle radius is eroded from it at runtime.",
        "Cost: little directly; a larger cylinder simply includes more particles in the fill count.",
    ),
    "detector_length": _info(
        "Manual nominal fill-detector cylinder full axial length in metres. One SPH particle radius is eroded from each end at runtime.",
        "Cost: little directly; a longer cylinder includes more particles in the fill count.",
    ),
    # Run & domain
    "duration": _info(
        "Total simulated time for the rigid CRM excavation. In kinematic mode a full pass down the bed takes roughly bed_height / descent_speed; in force mode the time depends on how fast the drum actually sinks.",
        "Cost: a longer run is slower and writes more CSV rows; the particle and wrench cadences set how much extra disk space those rows use.",
    ),
    "fea_duration": _info(
        "Length of the one-way FEA replay. The replay stops at the last recorded CRM load frame if the loads file is shorter and prints a notice; it does not hold a stale wrench. FEA replays the rigid soil-wrench CSV, not SPH particle contact.",
        "Cost: each replayed second costs FEA solver time; the stress export stride can dominate that cost if full 3D VTK is on.",
    ),
    "spacing": _info(
        "Initial SPH particle spacing in metres. Smaller means more grains, sharper soil detail, and a slower run; 0.002 m is the default, twice as fine as the old 0.004 m setting.",
        "Cost: particle count grows roughly with the inverse cube of spacing, so halving it can mean about eight times more particles, slower steps, and much larger CSV files.",
    ),
    "d0_multiplier": _info(
        "Smoothing-length factor h = multiplier x spacing. 1.0 is the standard choice for this CRM setup.",
        "Cost: larger values make each particle interact with more neighbours, slowing each step; very small values can make the soil numerically noisy.",
    ),
    "artificial_viscosity": _info(
        "Artificial bilateral SPH viscosity. Raise above 0.5 to damp pressure waves that eject soil at the first drum bite; lower values keep more dynamic response.",
        "Cost: little direct cost; it changes stability and dynamic response more than runtime.",
    ),
    "shifting_ppst_push": _info(
        "PPST particle-shifting push strength. Below 3.0 it reduces outward particle drift; 0 disables the push term.",
        "Cost: little direct cost; it mainly affects particle order, stability, and how cleanly the bed holds together.",
    ),
    "crm_dt": _info(
        "Explicit CRM integration timestep in seconds, or 'auto' for a stable value derived from particle spacing. Unsafe explicit values are rejected unless explicitly allowed.",
        "Cost: this is the main runtime dial. Halving dt roughly doubles the number of solver steps; 'auto' keeps a stable, spacing-aware value.",
    ),
    "crm_fps": _info(
        "How often CRM summary rows are written to summary.csv. Particle CSV extraction happens inside this same record step, so particle output cannot be more frequent than this even if particle FPS is set higher. Soil-wrench CSV has its own cadence.",
        "Cost: higher values add metric rows and particle extraction, so lower values save CPU and I/O; the physics advances at CRM dt, not this cadence.",
    ),
    "fea_fps": _info(
        "Independent FEA summary and export cadence. Lower values speed up the replay; the structural dynamics still advance at FEA dt.",
        "Cost: fewer recorded frames mean fewer stress scans and VTU writes; the solve itself is unchanged.",
    ),
    "loads_fps": _info(
        "Sampling rate in Hz for the soil-wrench CSV used by one-way FEA. The FEA driver interpolates by time, so 20 Hz is usually enough.",
        "Cost: lower values make the CRM solver write less and shrink soil_loads.csv; too low can miss sharp force peaks.",
    ),
    "particle_fps": _info(
        "Particle CSV cadence for ParaView in Hz. It has its own field but is capped by CRM summary FPS, because particle snapshots are written during the summary record step. Setting it to 20 matches one particle file per CRM frame; 5 Hz is usually plenty for a movie.",
        "Cost: this is a major disk-I/O dial. Lower values sharply reduce particle CSV writes; raising it above CRM summary FPS has no effect.",
    ),
    "particle_writers": _info(
        "Number of background processes that write per-frame particle CSV files while the CRM solver keeps running.",
        "Cost: raise only if disk writes are still a bottleneck; each extra writer uses memory and CPU and helps only up to your disk's speed limit.",
    ),
    "fea_dt": _info(
        "Structural integrator timestep for one-way FEA in seconds, independent of CRM dt. Default is 0.002 s.",
        "Cost: smaller values mean more FEA steps and a slower replay; too large can miss the structural vibration.",
    ),
    "bed_height": _info(
        "Depth of the soil bed. The drum starts at bed_height + radius above the ground.",
        "Cost: a deeper bed adds particles and lengthens the descent, so both the solve and the file sizes grow.",
    ),
    "bed_padding": _info(
        "Extra soil around the drum footprint on each side, as a fraction of drum radius. Raising it pushes the reflective BCE walls outward and increases the particle count.",
        "Cost: more padding means more particles, slower steps, and larger particle CSV files.",
    ),
    "active_box": _info(
        "Drum-centred active SPH domain size X,Y,Z in metres (full sizes; X lies along the drum axis only for the default X spin axis, Y is lateral, Z vertical). Defaults to the padded bed plus two excavator diameters laterally and the bed plus drum height plus two diameters vertically, so flung particles are not deactivated.",
        "Cost: a bigger box lets you follow ejected soil but can add particles and slow the run; too small a box silently freezes grains in mid-air.",
    ),
    "settle_time": _info(
        "Time allowed for the bed to settle before excavation motion starts. Longer settling can reduce the first-bite pressure spike that flings soil upward.",
        "Cost: settling is simulated time, so a longer settle makes the run slower, but it can make the first bite more realistic.",
    ),
    "gravity": _info(
        "Signed global vertical acceleration in m/s^2; negative is downward and -9.81 is Earth gravity. Applied to CRM soil and FEA drums.",
        "Cost: none directly; changing it changes soil settling and loads rather than the workload.",
    ),
    "drum_gravity": _info(
        "Rigid CRM only: also apply global gravity to the drum body. Off preserves the older force-descent behaviour; FEA drums always follow global gravity. Kinematic descent overrides the drum pose either way.",
        "Cost: none directly; it changes descent physics and the recorded loads, not the solver workload.",
    ),
    "descent_mode": _info(
        "Kinematic prescribes a constant lowering speed; force applies a downward actuator after settling and holds at the final target, and its FEA replay follows the recorded trajectory. Spin is prescribed in both modes.",
        "Cost: force mode records a real load history and can need a longer duration; kinematic mode is simpler and usually faster to interpret.",
    ),
    "descent_speed": _info(
        "Vertical lowering speed of the drum into the bed in kinematic mode, in m/s. 0 holds constant height. Force descent ignores it except for duration guidance.",
        "Cost: a slower descent needs a longer simulated duration, so it costs more steps; faster descents can excite sharper impact transients.",
    ),
    "descent_force_n": _info(
        "Downward actuator force in newtons after settling, in force mode. Soil reaction always acts on the drum; global gravity adds to it only when 'Apply gravity to CRM drum' is on. Use 0 with drum gravity enabled for gravity-only descent.",
        "Cost: none directly; larger forces change penetration depth, loads, and possibly the duration needed.",
    ),
    "descent_force_ramp_s": _info(
        "Linear ramp in seconds from zero to the descent actuator force in force mode. 0 applies full force immediately after settling.",
        "Cost: none directly; a ramp can smooth the first bite and reduce the pressure spike that flings soil.",
    ),
    "omega": _info(
        "Drum angular speed magnitude about the spin axis in rad/s; about 3.14 rad/s is 30 rpm. The sign comes from Reverse spin.",
        "Cost: none directly; very fast spin can need a smaller timestep for stability, which would slow the run.",
    ),
    "reverse_spin": _info(
        "Flip the drum's rotation direction if the imported CAD spins the wrong way. Signed omega is then written as negative.",
        "Cost: none; it only changes the sign of the spin, not the solver workload.",
    ),
    "spin_axis": _info(
        "Global axis the drum spins about during excavation; X for the reference drum orientation.",
        "Cost: none directly, but choosing the wrong axis makes the drum tumble instead of scoop and wastes a run.",
    ),
    "solver": _info(
        "Linear solver for the one-way FEA replay: pardiso-mkl is the fast default; sparse-lu and sparse-qr are slower fallbacks; gmres is diagnostic only and does not enforce spindle constraints accurately.",
        "Cost: large. The documented 0.2 s case took 28 s with Pardiso versus 309 s with SparseLU. Sparse modes also add library paths in the launcher; gmres is not for engineering results.",
    ),
    "num_threads": _info(
        "Thread count for the Pardiso-MKL FEA solver and for the geometry preview tool's CAD tessellation and fuse. Default 8.",
        "Cost: helps Pardiso up to about 8 threads on a 16-core machine and speeds CAD meshing; Eigen SparseLU/QR factorization stays single-threaded, so extra threads do not help those solvers.",
    ),
    "load_radius_fraction": _info(
        "Inner cutoff for applying the recorded soil wrench in FEA: exterior nodes at a radius of at least this fraction of the drum radius receive the load. 0.95 limits it to a narrow outer band.",
        "Cost: 0.95 maps loads to fewer nodes and is cheaper per step; 0.8 spreads load over more of the rim and costs more. It does not change the recorded wrench or the reported metrics.",
    ),
    "raw_stress_every": _info(
        "Cadence for an extra unfiltered raw peak-von-Mises scan, every Nth recorded frame; 0 checks only the first and last frames. The ordinary filtered stress readout is still computed every frame.",
        "Cost: a higher value means fewer full-mesh scans and a faster replay; it does not change the physics or the filtered max_von_mises_pa column.",
    ),
    "export_workers": _info(
        "Number of worker processes that write ParaView frame files in parallel after a study. Default 4.",
        "Cost: more workers help until the CPU, memory, or disk is saturated; each worker also has a one-time startup cost. The standalone exporter defaults to 1.",
    ),
    # Output & export
    "save_particles": _info(
        "Write CRM particle positions to CSV at the particle output FPS. Needed for ParaView particle spheres and the Output Preview cloud.",
        "Cost: this is the largest disk-I/O item in the CRM stage. Old particle frames are cleared when a run starts, and lower particle FPS cuts writes sharply.",
    ),
    "fea_export_vtk": _info(
        "Write the full 3D FEA stress/deformation animation as per-frame VTU files under fea/vtk/frames/ with fea/vtk/fea.pvd. Off by default; scalar FEA summaries are always written.",
        "Cost: this can dominate FEA runtime and disk because every recorded frame gets a tetrahedral stress scan plus a VTU write. Turning it off retires the previous 3D series but keeps fea/summary.csv.",
    ),
    "fea_vtk_stride": _info(
        "Export every Nth recorded FEA frame to the 3D VTU animation; 1 means every frame, and the final frame is always included. Used only when full 3D FEA export is on.",
        "Cost: a larger stride means fewer VTU files, fewer stress scans, and less disk use; 1 gives the complete animation.",
    ),
    "tools_show_fea_3d": _info(
        "When the Tools tab opens ParaView, load a revalidated multi-frame 3D FEA stress/deformation PVD if one exists. A lone t=0 frame is ignored. On by default; this is the stress overlay only, not the FEA history charts.",
        "Cost: no simulation cost, but ParaView spends time rechecking the PVD and frames. Note that 'Open ParaView after run' always requests this overlay when a usable one exists.",
    ),
    "tools_show_fea_charts": _info(
        "Display one-way FEA scalar stress and deformation history as XY charts in ParaView. Off by default; unchecking also clears an inherited PYCHRONO_FEA_CHARTS setting. Independent of the 3D overlay, and --no-fea suppresses all FEA views.",
        "Cost: no simulation cost; the viewer builds the chart table from fea_summary.csv on demand.",
    ),
    "export_vtk": _info(
        "Run a final export stage that writes crm_particles.pvd, excavator.pvd, crm_animation.pvd/combined.pvd, fea_summary.csv, and a manifest. The combined series contains CRM particles and rigid poses only, never FEA.",
        "Cost: adds an export stage and disk space. CRM particle and excavator frames are regenerated on every export, while existing FEA VTU frames are validated rather than rewritten.",
    ),
    "paraview_stride": _info(
        "Export every Nth CRM frame for both particles and excavator in the post-run export; 1 exports all frames and all series stay time-aligned.",
        "Cost: a higher stride means a faster export and a much smaller PVD/VTP set; the final frame is still included.",
    ),
    "paraview_particle_stride": _info(
        "Export every Nth CRM particle frame independently of the excavator series. 0 follows the general ParaView export stride; values above 1 thin out the much larger particle series.",
        "Cost: particle frames are far larger than rigid poses, so a stride above 1 sharply cuts export time and disk use.",
    ),
    "paraview_particle_render_stride": _info(
        "Render only every Nth particle within each exported frame; 1 keeps all particles. Frame count is unchanged.",
        "Cost: smaller VTP files and faster ParaView loading, with no change to the number of frames.",
    ),
    "open_results": _info(
        "Record the choice to open the output folder when the run finishes. The command-line wizard honours it; the GUI currently saves the setting but does not launch a file manager at the end of its own run.",
        "Cost: no simulation effect, just a file-manager launch where it is honoured.",
    ),
    "open_paraview": _info(
        "Open the preconfigured 3D view in the full ParaView GUI after a successful run; a pvpython render window is the fallback.",
        "Cost: no simulation effect, but it starts a heavy external app and only runs if the study produced a paraview folder.",
    ),
    # Tools tab
    "tools_study_dir": _info(
        "Study/output directory the Tools tab inspects. It starts at the study wizard's output directory and follows edits to that field; you can also point it at the folder containing study_config.json or a previous Output_* run.",
        "Cost: none until a tool runs; a wrong folder makes export or viewing fail with a clear status message.",
    ),
    "tools_stride": _info(
        "Export every Nth CRM frame with the Tools 'Export ParaView VTK/PVD' button; 1 exports all frames and keeps the series time-aligned.",
        "Cost: a higher stride means a faster, smaller manual export; the final frame is still included.",
    ),
    "tools_particle_stride": _info(
        "Export every Nth CRM particle frame with the Tools export button. 0 follows the quick export stride; values above 1 thin out the particle series independently.",
        "Cost: a value above 1 reduces particle export time and disk use without touching the drum poses.",
    ),
    "tools_particle_render_stride": _info(
        "Render only every Nth particle in each frame of the Tools export; 1 keeps all particles. Frame count is unchanged.",
        "Cost: smaller VTP files and lighter ParaView scenes, with no change to frame count.",
    ),
    "tools_camera_orbit": _info(
        "Add a ParaView camera track that automatically orbits the drum while the time animation plays, following the drum centre from the excavator frames. On by default. The viewer primes the track before the first render, so the first frame is already framed and you do not need to step backwards and forwards to wake the camera.",
        "Cost: a tiny keyframe calculation, with no simulation or disk effect unless you record a movie.",
    ),
    "tools_orbit_speed": _info(
        "Camera revolutions per simulation second; positive values orbit one way, negative reverse. 0.1 means one lap every 10 seconds of study time.",
        "Cost: none for the simulation; faster orbits generate more keyframes to stay smooth.",
    ),
    "tools_orbit_radius": _info(
        "Orbit radius in metres around the vertical Z axis. 0 auto-fits the excavator using at least five times its radial extent, 2.4 times its axial extent, or 0.25 m, and lowers the orbit centre by one third of the bounding radius.",
        "Cost: visual only; auto-fit samples the excavator bounds once at the first frame.",
    ),
    "tools_orbit_inclination": _info(
        "Camera elevation above the horizontal orbit plane in degrees, clamped to +/-80 so the camera's up direction stays stable.",
        "Cost: visual only; no simulation or file-size effect.",
    ),
    "tools_orbit_start_angle": _info(
        "Starting camera azimuth in degrees. Leave blank to begin the orbit at the current camera angle.",
        "Cost: visual only; it just shifts the starting point of the orbit.",
    ),
    "tools_orbit_keyframes": _info(
        "Number of camera keyframes holding the orbit path; 0 chooses automatically from orbit speed and animation duration, clamped to 24-360. More keyframes smooth fast orbits.",
        "Cost: a slightly longer ParaView setup for more keyframes; visual only.",
    ),
    "tools_workers": _info(
        "Worker processes used by the Tools export button. Match it to available CPU cores; 4 is a good starting point.",
        "Cost: more workers use more CPU and memory and help only until the disk or cores saturate.",
    ),
}

PARAM_INFO.update({
    "soil_density": _info(
        "Regolith bulk density in kg/m^3: the mass of soil per unit volume, including the air gaps between grains.",
        "Cost: none directly, but denser soil raises contact forces and can demand a smaller timestep for stability.",
    ),
    "soil_youngs_modulus": _info(
        "Soil elastic stiffness in Pa for the CRM contact response: how much the grains resist being squeezed together.",
        "Cost: stiffer soil gives sharper contact forces that may need a smaller timestep, slowing the run.",
    ),
    "soil_poisson_ratio": _info(
        "Soil lateral-to-axial strain ratio: how much the material bulges sideways when compressed.",
        "Cost: barely any direct cost; it shapes the contact response, not the particle count.",
    ),
    "soil_friction": _info(
        "Static friction coefficient (mu_s) at particle contacts: how strongly grains resist sliding past one another. The driver also uses this value for high-inertia friction (mu_2) because the GUI has no separate field for it.",
        "Cost: none directly; it strongly affects the bed's angle of repose, flow, and the torque the drum needs.",
    ),
    "soil_cohesion": _info(
        "Cohesive shear strength in Pa: how strongly grains stick together even without being pressed.",
        "Cost: none directly; it changes excavation loads and the shape of the trench walls.",
    ),
    "soil_grain_diameter": _info(
        "Physical grain diameter in metres used by the rheology model, distinct from the SPH particle spacing.",
        "Cost: none directly; it feeds the rheology rather than changing how many SPH particles are created.",
    ),
})
PARAM_INFO.update({
    "pla_youngs_modulus": _info(
        "Drum material stiffness in Pa for one-way FEA; PLA is about 3 GPa by default.",
        "Cost: a stiffer drum changes deflection and stress and can be harder to solve numerically; no file-size effect.",
    ),
    "pla_poisson_ratio": _info(
        "Drum material lateral-to-axial strain ratio for the FEA solid model; PLA is about 0.38.",
        "Cost: material response only, with a tiny effect on how well the FEA equations condition.",
    ),
    "pla_density": _info(
        "Drum material density in kg/m^3 for FEA; PLA is about 1240 by default.",
        "Cost: sets nodal masses, inertia, and gravity loading in FEA; no disk impact.",
    ),
    "pla_rayleigh_beta": _info(
        "Rayleigh stiffness-proportional damping in seconds to settle structural vibration in FEA.",
        "Cost: little direct cost; too much damping can over-smooth the response you are trying to measure.",
    ),
})

DEFAULT_FILE = ROOT / "study_gui_defaults.json"

# Appearance/startup preferences live in their own file so they never get
# mixed into the study-parameter defaults written by the Defaults tab.
SETTINGS_FILE = ROOT / "study_gui_settings.json"
HEADER_FONT = "StudyHeaderFont"
SECTION_FONT = "StudySectionFont"
DOC_TITLE_FONT = "StudyDocTitleFont"
DOC_HEADING_FONT = "StudyDocHeadingFont"
DOC_SUBHEADING_FONT = "StudyDocSubheadingFont"
DOC_CODE_FONT = "StudyDocCodeFont"
DOC_BOLD_FONT = "StudyDocBoldFont"
DOC_ITALIC_FONT = "StudyDocItalicFont"
DOC_TABLE_HEADER_FONT = "StudyDocTableHeaderFont"
DOC_TABLE_BOLD_FONT = "StudyDocTableBoldFont"
LIGHT_TEXT = "#000000"
DARK_TEXT = "#F2F2F2"
_DOC_INLINE_RE = re.compile(
    r"(\[[^\]\n]+\]\([^)\n]+\)|\*\*[^*\n]+\*\*|\*[^*\n]+\*|`[^`\n]+`)"
)
UI_SCALE_MIN = 0.75
UI_SCALE_MAX = 2.0
UI_SCALE_DEFAULT = 1.0
UI_SCALE_LEGACY_AUTO = 0.0

SHIPPED_SETTINGS: dict[str, object] = {
    "ui_scale": UI_SCALE_DEFAULT,
    "font_size": 10,
    "window_width": 1100,
    "window_height": 1080,
    "text_color": LIGHT_TEXT,
    "dark_mode": False,
    "show_output_preview": True,
    "preview_particle_limit": PARTICLE_SAMPLE_DEFAULT,
    "preview_frame_stride": 1,
    "preview_particle_size": PARTICLE_SIZE_DEFAULT,
    "preview_line_width": LINE_WIDTH_DEFAULT,
}

SETTINGS_INFO: dict[str, str] = {
    "ui_scale": _info(
        "Whole-GUI density factor for widget padding and Tk scaling, from 0.75 to 2.0. 1.0 uses the system density; text size is controlled separately.",
        "Cost: a rendering choice with negligible startup cost; it never changes the simulation or its files.",
    ),
    "font_size": _info(
        "Text size in points from 7 to 24 for labels, entries, buttons, log panels, and the Docs reader. Applied immediately.",
        "Cost: drawing only; larger text can make panes scroll more but has no simulation effect.",
    ),
    "window_width": _info(
        "Window width in pixels from 640 to 3840. Apply now resizes the open window; the saved value is used at the next launch.",
        "Cost: drawing only; a very wide window needs more screen space but does not affect the run.",
    ),
    "window_height": _info(
        "Window height in pixels from 480 to 2160. Apply now resizes the open window; the saved value is used at the next launch.",
        "Cost: drawing only; a very tall window needs more screen space but does not affect the run.",
    ),
    "text_color": _info(
        "Foreground text colour as #RRGGBB; the Pick... button opens the system colour dialog.",
        "Cost: drawing only; an invalid value falls back to the default colour.",
    ),
    "dark_mode": _info(
        "Use a dark interface palette. If the text colour is still the default, it switches to a light shade automatically.",
        "Cost: drawing only; it never changes the simulation or its outputs.",
    ),
    "show_output_preview": _info(
        "Show the lightweight Output Preview tab. It reads study CSVs directly and draws a bounded 3D subsample of particles on a Tk canvas, with no VTK involved.",
        "Cost: when enabled, the tab is built at startup and refreshed after steps; hiding it skips that refresh work.",
    ),
    "preview_particle_limit": _info(
        "Reservoir-sample at most this many SPH particles from each displayed preview frame, from 1 to 250,000. Every row is still read, so the actual particle count is shown. Default 5,000.",
        "Cost: a higher limit makes redraws slower and uses more memory; the CSV rows are read either way.",
    ),
    "preview_frame_stride": _info(
        "Show only every Nth discovered particle CSV frame in the preview slider and Play loop, from 1 to 50. 1 shows all frames.",
        "Cost: a higher stride means fewer frames to step through; no files or simulation results are changed.",
    ),
    "preview_particle_size": _info(
        "Marker diameter in pixels for CRM particle points in the Output Preview, from 1 to 12.",
        "Cost: drawing only; larger dots make a denser-looking cloud, smaller dots resolve individual grains.",
    ),
    "preview_line_width": _info(
        "Line width in pixels for the preview drum/grid and CRM/FEA timeline traces, from 1 to 6. Applied immediately without reloading the study.",
        "Cost: drawing only; there is no simulation or file-size effect.",
    ),
}

GENERATED_DOC_ID = "<generated>"

# Docs tab entries. ``None`` means generated from the GUI's own help tables.
# File paths are relative to ROOT and are re-read on every selection/Refresh.
DOC_ENTRIES: tuple[tuple[str, str | None], ...] = (
    ("Documentation home", "docs/README.md"),
    ("Quick start (beginners)", "docs/quickstart.md"),
    ("Glossary", "docs/glossary.md"),
    ("GUI guide", "docs/gui-guide.md"),
    ("Study wizard (CLI)", "docs/wizard-guide.md"),
    ("Command-line reference", "docs/cli-reference.md"),
    ("Outputs and visualization", "docs/outputs-and-visualization.md"),
    ("Troubleshooting", "docs/troubleshooting.md"),
    ("How it works (in depth)", "docs/how-it-works.md"),
    ("Developer guide", "docs/developer-guide.md"),
    ("GUI parameter reference", None),
    ("Getting started (project README)", "README.md"),
    ("CRM solver", "crm/README.md"),
    ("One-way FEA coupling", "crm/FEA_COUPLING.md"),
    ("Full model notes", "FULL_MODEL.md"),
    ("Environment and install", "environment/INSTALL.md"),
)


def _valid_color(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value.strip()) is not None


def _setting_int(value: object, label: str, low: int, high: int, default: int, strict: bool = False) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        if strict:
            raise ValueError(f"{label} must be a whole number.")
        return default
    if number < low or number > high:
        if strict:
            raise ValueError(f"{label} must be between {low} and {high}.")
        return max(low, min(high, number))
    return number


def _setting_scale(value: object, strict: bool = False) -> float:
    """Validate the GUI scale, treating the old 0 (auto) value as 1.0."""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        if strict:
            raise ValueError("GUI scale must be a number from 0.75 to 2.0.")
        return float(UI_SCALE_DEFAULT)
    if not math.isfinite(number):
        if strict:
            raise ValueError("GUI scale must be a number from 0.75 to 2.0.")
        return float(UI_SCALE_DEFAULT)
    if number == UI_SCALE_LEGACY_AUTO:
        return float(UI_SCALE_DEFAULT)
    if not (UI_SCALE_MIN <= number <= UI_SCALE_MAX):
        if strict:
            raise ValueError("GUI scale must be a number from 0.75 to 2.0.")
        return float(UI_SCALE_DEFAULT)
    return number


def load_ui_settings() -> dict[str, object]:
    """Load appearance preferences, falling back to shipped values per key."""
    values = dict(SHIPPED_SETTINGS)
    if not SETTINGS_FILE.is_file():
        return values
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return values
    if not isinstance(data, dict):
        return values
    values["ui_scale"] = _setting_scale(data.get("ui_scale"))
    values["font_size"] = _setting_int(
        data.get("font_size"), "Text size", 7, 24, int(SHIPPED_SETTINGS["font_size"]))
    values["window_width"] = _setting_int(
        data.get("window_width"), "Window width", 640, 3840, int(SHIPPED_SETTINGS["window_width"]))
    values["window_height"] = _setting_int(
        data.get("window_height"), "Window height", 480, 2160, int(SHIPPED_SETTINGS["window_height"]))
    if _valid_color(data.get("text_color")):
        values["text_color"] = str(data["text_color"]).strip()
    values["dark_mode"] = bool(data.get("dark_mode", False))
    values["show_output_preview"] = bool(data.get("show_output_preview", True))
    values["preview_particle_limit"] = _setting_int(
        data.get("preview_particle_limit"), "Preview particle count", 1, PARTICLE_SAMPLE_MAX,
        int(SHIPPED_SETTINGS["preview_particle_limit"]))
    values["preview_frame_stride"] = _setting_int(
        data.get("preview_frame_stride"), "Preview frame stride", 1, 50,
        int(SHIPPED_SETTINGS["preview_frame_stride"]))
    values["preview_particle_size"] = _setting_int(
        data.get("preview_particle_size"), "Preview particle size",
        PARTICLE_SIZE_MIN, PARTICLE_SIZE_MAX,
        int(SHIPPED_SETTINGS["preview_particle_size"]))
    values["preview_line_width"] = _setting_int(
        data.get("preview_line_width"), "Preview line width",
        LINE_WIDTH_MIN, LINE_WIDTH_MAX,
        int(SHIPPED_SETTINGS["preview_line_width"]))
    return values


def write_ui_settings(values: dict[str, object]) -> None:
    """Persist only validated appearance preferences."""
    clean = {
        "ui_scale": _setting_scale(values.get("ui_scale")),
        "font_size": _setting_int(values.get("font_size"), "Text size", 7, 24, int(SHIPPED_SETTINGS["font_size"])),
        "window_width": _setting_int(values.get("window_width"), "Window width", 640, 3840, int(SHIPPED_SETTINGS["window_width"])),
        "window_height": _setting_int(values.get("window_height"), "Window height", 480, 2160, int(SHIPPED_SETTINGS["window_height"])),
        "text_color": (str(values["text_color"]).strip() if _valid_color(values.get("text_color"))
                       else str(SHIPPED_SETTINGS["text_color"])),
        "dark_mode": bool(values.get("dark_mode", False)),
        "show_output_preview": bool(values.get("show_output_preview", True)),
        "preview_particle_limit": _setting_int(
            values.get("preview_particle_limit"), "Preview particle count", 1, PARTICLE_SAMPLE_MAX,
            int(SHIPPED_SETTINGS["preview_particle_limit"])),
        "preview_frame_stride": _setting_int(
            values.get("preview_frame_stride"), "Preview frame stride", 1, 50,
            int(SHIPPED_SETTINGS["preview_frame_stride"])),
        "preview_particle_size": _setting_int(
            values.get("preview_particle_size"), "Preview particle size",
            PARTICLE_SIZE_MIN, PARTICLE_SIZE_MAX,
            int(SHIPPED_SETTINGS["preview_particle_size"])),
        "preview_line_width": _setting_int(
            values.get("preview_line_width"), "Preview line width",
            LINE_WIDTH_MIN, LINE_WIDTH_MAX,
            int(SHIPPED_SETTINGS["preview_line_width"])),
    }
    SETTINGS_FILE.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def shipped_defaults() -> dict[str, object]:
    """Factory defaults for the GUI; per-run identity fields are excluded."""
    defaults: dict[str, object] = {
        "geometry": "supplied",
        "step_file": str(wiz.DEFAULT_STEP_FILE),
        "crm_obj": str(wiz.DEFAULT_CRM_OBJ or ""),
        "fea_node": str(wiz.DEFAULT_FEA_NODE or ""),
        "fea_ele": str(wiz.DEFAULT_FEA_ELE or ""),
        "cad_axis": "Y",
        "unit_scale": "0.001",
        "mesh_size": "0.012",
        "surface_target_faces": "8000",
        "surface_max_faces": "256000",
        "surface_fit_tolerance": "0.001",
        "surface_repair_order": "none",
        "min_solid_volume": "0.0",
        "hub_radius": "0.0015",
        "part_aware_simplify": True,
        "part_aware_radius": "0.05",
        "part_aware_reduction": "0.75",
        "part_aware_min_faces": "500",
        "drum_radius": "0.061037",
        "drum_half_length": "0.033",
        "inner_radius": "0.055",
        "capacity_volume": "",
        "detector_auto_size": True,
        "detector_radius": "0.061037",
        "detector_length": "0.066",
        "duration": "20",
        "fea_duration": "20",
        "spacing": "0.002",
        "d0_multiplier": "1.0",
        "artificial_viscosity": "0.5",
        "shifting_ppst_push": "3.0",
        "crm_dt": "auto",
        "crm_fps": "20",
        "fea_fps": "20",
        "loads_fps": "20",
        "particle_fps": "5",
        "particle_writers": "4",
        "fea_dt": "0.002",
        "bed_height": "0.06",
        "bed_padding": f"{wiz.BED_PADDING_RADIUS_FRACTION:g}",
        "active_box": "0.631333,0.687407,0.86637",
        "settle_time": "0.001",
        "gravity": "-9.81",
        "drum_gravity": False,
        "descent_mode": "kinematic",
        "descent_speed": "0.0015",
        "descent_force_n": "1.0",
        "descent_force_ramp_s": "0.0",
        "omega": "3.14",
        "reverse_spin": False,
        "spin_axis": "X",
        "solver": "pardiso-mkl",
        "num_threads": "8",
        "load_radius_fraction": "0.95",
        "raw_stress_every": "5",
        "export_workers": "4",
        "save_particles": True,
        "fea_export_vtk": False,
        "fea_vtk_stride": "1",
        "export_vtk": True,
        "paraview_stride": "1",
        "paraview_particle_stride": "0",
        "paraview_particle_render_stride": "1",
        "open_paraview": False,
        "open_results": False,
        "auto_geometry": True,
    }
    defaults.update({f"soil_{k}": f"{v:g}" for k, v in wiz.SOIL_DEFAULTS.items()})
    defaults.update({f"pla_{k}": f"{v:g}" for k, v in wiz.PLA_DEFAULTS.items()})
    return defaults


DEFAULT_KEYS = set(shipped_defaults())

DEFAULT_GROUPS: list[tuple[str, list[tuple[str, str, dict]]]] = [
    ("Geometry", [
        ("Geometry source", "geometry", {"combo": ["supplied", "step", "files"]}),
        ("Auto-scale from CAD/mesh", "auto_geometry", {"check": True}),
        ("STEP file", "step_file", {"width": 60, "browse": "file"}),
        ("CRM surface OBJ", "crm_obj", {"width": 60, "browse": "file"}),
        ("FEA .node", "fea_node", {"width": 60, "browse": "file"}),
        ("FEA .ele", "fea_ele", {"width": 60, "browse": "file"}),
        ("CAD axis (STEP)", "cad_axis", {"combo": ["auto", "X", "Y", "Z"]}),
        ("Unit scale (STEP->m)", "unit_scale", {}),
        ("FEA mesh size (m)", "mesh_size", {}),
        ("Surface target faces", "surface_target_faces", {}),
        ("Surface max faces", "surface_max_faces", {}),
        ("Exterior fit tolerance (m)", "surface_fit_tolerance", {}),
        ("MeshFix order", "surface_repair_order", {"combo": ["none", "before", "after"]}),
        ("Min solid volume (m^3)", "min_solid_volume", {}),
        ("Hub radius (m)", "hub_radius", {}),
        ("Coarsen hub/interior mesh", "part_aware_simplify", {"check": True}),
        ("Interior coarsen radius (m)", "part_aware_radius", {}),
        ("Interior reduction", "part_aware_reduction", {}),
        ("Interior min faces", "part_aware_min_faces", {}),
        ("Drum radius (m)", "drum_radius", {}),
        ("Drum half-length (m)", "drum_half_length", {}),
        ("Inner/cavity radius (m)", "inner_radius", {}),
        ("Capacity volume override (m^3)", "capacity_volume", {}),
        ("Auto-size fill detector", "detector_auto_size", {"check": True}),
        ("Detector cylinder radius (m)", "detector_radius", {}),
        ("Detector cylinder length (m)", "detector_length", {}),
    ]),
    ("Run & Domain", [
        ("CRM duration (s)", "duration", {}),
        ("FEA duration (s)", "fea_duration", {}),
        ("SPH spacing (m)", "spacing", {}),
        ("SPH d0 multiplier", "d0_multiplier", {}),
        ("Artificial viscosity", "artificial_viscosity", {}),
        ("PPST shifting push", "shifting_ppst_push", {}),
        ("CRM dt (s or 'auto')", "crm_dt", {}),
        ("CRM summary FPS", "crm_fps", {}),
        ("Particle CSV FPS", "particle_fps", {}),
        ("FEA summary FPS", "fea_fps", {}),
        ("Soil loads FPS", "loads_fps", {}),
        ("Particle writer processes", "particle_writers", {}),
        ("FEA dt (s)", "fea_dt", {}),
        ("Bed height (m)", "bed_height", {}),
        ("Bed padding (fraction of radius)", "bed_padding", {}),
        ("Active box X,Y,Z (m)", "active_box", {}),
        ("Settle time (s)", "settle_time", {}),
        ("Gravity Z (m/s^2)", "gravity", {}),
        ("Apply gravity to CRM drum", "drum_gravity", {"check": True}),
        ("Descent mode", "descent_mode", {"combo": ["kinematic", "force"]}),
        ("Descent speed (m/s; kinematic)", "descent_speed", {}),
        ("Descent force (N; force mode)", "descent_force_n", {}),
        ("Descent force ramp (s; force mode)", "descent_force_ramp_s", {}),
        ("Spin rate |omega| (rad/s)", "omega", {}),
        ("Reverse spin", "reverse_spin", {"check": True}),
        ("Spin axis", "spin_axis", {"combo": ["X", "Y", "Z"]}),
        ("FEA solver", "solver", {"combo": ["pardiso-mkl", "sparse-lu", "sparse-qr", "gmres"]}),
        ("FEA threads", "num_threads", {}),
        ("Load node radius fraction", "load_radius_fraction", {}),
        ("Raw stress interval (frames)", "raw_stress_every", {}),
    ]),
    ("Soil (CRM)", [
        (label, f"soil_{key}", {}) for key, (label, _lim) in wiz.SOIL_FIELDS.items()
    ]),
    ("FEA drum material (PLA)", [
        (label, f"pla_{key}", {}) for key, (label, _lim) in wiz.PLA_FIELDS.items()
    ]),
    ("Output & Export", [
        ("Save CRM particle CSV frames", "save_particles", {"check": True}),
        ("Export full 3D FEA stress animation", "fea_export_vtk", {"check": True}),
        ("FEA VTU frame stride", "fea_vtk_stride", {}),
        ("Export ParaView datasets after run", "export_vtk", {"check": True}),
        ("ParaView export stride (frames)", "paraview_stride", {}),
        ("ParaView particle stride (0 = same)", "paraview_particle_stride", {}),
        ("ParaView particle render stride", "paraview_particle_render_stride", {}),
        ("ParaView export workers", "export_workers", {}),
        ("Open results folder after run", "open_results", {"check": True}),
        ("Open ParaView after run", "open_paraview", {"check": True}),
    ]),
]


def coerce_default_value(key: str, value, template=None) -> object:
    """Normalize a saved/edited default to the type used by the GUI vars."""
    if template is None:
        template = shipped_defaults().get(key)
    if isinstance(template, bool):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
    return "" if value is None else str(value)


def load_saved_defaults() -> dict[str, object]:
    if not DEFAULT_FILE.is_file():
        return {}
    try:
        data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    templates = shipped_defaults()
    return {
        key: coerce_default_value(key, value, templates.get(key))
        for key, value in data.items()
        if key in DEFAULT_KEYS
    }


def write_saved_defaults(values: dict[str, object]) -> None:
    templates = shipped_defaults()
    clean = {
        key: coerce_default_value(key, value, templates.get(key))
        for key, value in values.items()
        if key in DEFAULT_KEYS
    }
    DEFAULT_FILE.write_text(json.dumps(clean, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def py_exec() -> str:
    env_py = os.environ.get("PYCHRONO_CHRONO_PYTHON")
    if env_py and Path(env_py).is_file():
        return env_py
    cand = ROOT / "chrono-env" / "bin" / "python"
    return str(cand) if cand.is_file() else sys.executable


def read_detector_result(path: Path | None) -> dict | None:
    """Read a detector-sizer result file; return None if absent or incomplete."""
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    try:
        return {
            "radius": float(data["radius"]),
            "length": float(data["length"]),
            "dirty": bool(data.get("dirty", True)),
        }
    except (KeyError, TypeError, ValueError):
        return None


def build_detector_sizer_command(
        *, python: str, root: Path, step_file: Path | None,
        surface_file: Path | None, fea_node: Path | None,
        radius: float, length: float, spacing: float, cad_axis: str,
        unit_scale: float, result_json: Path, threads: int = 0,
        show: bool = True) -> list[str]:
    """Build the live 3D adjuster command without launching it."""
    cmd = [python, str(root / "detector_size_tool.py")]
    if step_file is not None and step_file.is_file():
        cmd.extend(["--step", str(step_file)])
    if surface_file is not None and surface_file.is_file():
        cmd.extend(["--surface", str(surface_file)])
    if fea_node is not None and fea_node.is_file():
        cmd.extend(["--fea-node", str(fea_node)])
    cmd.extend([
        "--radius", f"{radius:g}",
        "--length", f"{length:g}",
        "--spacing", f"{spacing:g}",
        "--source-axis", str(cad_axis or "Y"),
        "--unit-scale", f"{unit_scale:g}",
        "--result-json", str(result_json),
    ])
    if threads > 0:
        cmd.extend(["--threads", str(threads)])
    if show:
        cmd.append("--show")
    return cmd


class ScrollFrame(ttk.Frame):
    """A frame with a vertical scrollbar that also responds to the mouse wheel.

    Wheel events in Tk are delivered to the widget under the pointer, not to
    the scrollable canvas.  Bind a private bindtag over the whole subtree so
    hovering over labels, entries, buttons, etc. scrolls the page too.  Widgets
    with their own wheel scrolling (Text/Listbox/Treeview) keep their native
    behaviour.
    """

    def __init__(self, parent, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0, yscrollincrement=36)
        self.vsb = ttk.Scrollbar(self, orient=VERTICAL, command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.vsb.pack(side=RIGHT, fill=Y)

        self._wheel_tag = f"StudyGuiScroll{id(self)}"
        self.bind_class(self._wheel_tag, "<MouseWheel>", self._on_mousewheel)
        self.bind_class(self._wheel_tag, "<Button-4>", self._on_button4)
        self.bind_class(self._wheel_tag, "<Button-5>", self._on_button5)
        self._bind_wheel_tree(self)
        # Page builders add their widgets after ScrollFrame.__init__ returns.
        self._bind_after = self.after_idle(self._bind_wheel_tree_idle)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _bind_wheel_tree_idle(self) -> None:
        self._bind_after = None
        self._bind_wheel_tree(self)

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        after = getattr(self, "_bind_after", None)
        if after is not None:
            try:
                self.after_cancel(after)
            except Exception:
                pass
            self._bind_after = None

    def _bind_wheel_tree(self, widget) -> None:
        """Install the scroll bindtag on widget and every descendant."""
        tags = list(widget.bindtags())
        if self._wheel_tag not in tags:
            # After widget-specific bindings, before class bindings.
            tags.insert(1, self._wheel_tag)
            widget.bindtags(tuple(tags))
        for child in widget.winfo_children():
            self._bind_wheel_tree(child)

    @staticmethod
    def _owns_own_wheel(widget) -> bool:
        return isinstance(widget, (tk.Text, tk.Listbox, ttk.Treeview))

    def _scroll_units(self, amount: int) -> str:
        """Scroll by wheel units, clamped to the content bounds.

        Tk normally clamps yview, but some wheel bindings deliver duplicate or
        oversized deltas.  Computing the target explicitly keeps the view from
        drifting above the first row (or below the last row).
        """
        region = self.canvas.cget("scrollregion")
        if not region:
            return "break"
        try:
            _, top, _, bottom = (float(part) for part in region.split())
        except (TypeError, ValueError):
            return "break"
        span = bottom - top
        if span <= 0.0:
            return "break"

        visible = self.canvas.winfo_height() / span
        if visible >= 1.0:  # content fits; nothing to scroll
            self.canvas.yview_moveto(0.0)
            return "break"

        first, _last = self.canvas.yview()
        max_first = max(0.0, 1.0 - visible)
        step = amount * 36.0 / span
        target = min(max_first, max(0.0, first + step))
        self.canvas.yview_moveto(target)
        # Stop class/toplevel bindings from also handling this wheel event.
        return "break"

    def _on_button4(self, event) -> str | None:
        if self._owns_own_wheel(event.widget):
            return None
        return self._scroll_units(-1)

    def _on_button5(self, event) -> str | None:
        if self._owns_own_wheel(event.widget):
            return None
        return self._scroll_units(1)

    def _on_mousewheel(self, event) -> str | None:
        if self._owns_own_wheel(event.widget):
            return None
        delta = int(getattr(event, "delta", 0) or 0)
        if not delta:
            return None
        # Windows/macOS normally use +/-120; some setups use +/-1.
        steps = -int(delta / 120) if abs(delta) >= 120 else -delta
        return self._scroll_units(steps or (-1 if delta > 0 else 1))


class StudyGUI:
    def __init__(self, root: Tk):
        self.root = root
        root.title("PyChrono Excavator Study GUI")
        self.settings = load_ui_settings()
        self._base_theme = ttk.Style().theme_use()
        self.settings_vars: dict[str, StringVar | BooleanVar] = {}
        self.text_widgets: list[tk.Text] = []
        self.ui_palette: dict[str, str] = {}
        self._system_scaling = self._tk_scaling()
        self._apply_ui_settings(self.settings)
        self.proc: subprocess.Popen | None | str = None
        self.log_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        self.tools_queue: "queue.Queue[tuple[str, object]]" = queue.Queue()
        # Stop ends the whole pipeline; skip terminates only the active step.
        self._stop_requested = threading.Event()
        self._skip_requested = threading.Event()
        self.vars: dict[str, StringVar | BooleanVar] = {}
        self.default_vars: dict[str, StringVar | BooleanVar] = {}
        self.detector_widgets: dict[str, object] = {}
        self._build_vars()
        self._build_ui()
        self._sync_detector_fields()
        self._bind_particle_estimate()
        self._update_particle_estimate()
        self.root.after(100, self._drain_log)
        self.root.after(100, self._drain_tools_log)

    def _tk_scaling(self) -> float:
        """Read Tk's system pixel-per-point scaling, falling back to 1.0."""
        try:
            return float(self.root.tk.call("tk", "scaling"))
        except (tk.TclError, TypeError, ValueError):
            return 1.0

    def _v(self, key: str, value, is_bool: bool = False, target=None):
        store = target if target is not None else self.vars
        if is_bool:
            store[key] = BooleanVar(value=bool(value))
        else:
            store[key] = StringVar(value="" if value is None else str(value))
        return store[key]

    def _wizard_output_dir(self) -> str:
        """Output directory currently configured in the study wizard."""
        return self._g("output_dir").strip() or str(ROOT)

    def _on_study_name_changed(self, *_):
        """Keep the default output folder aligned with the study name."""
        name = self._g("study_name").strip()
        self.vars["output_dir"].set(str(ROOT / f"Output_{name}"))

    def _on_output_dir_changed(self, *_):
        """Mirror the wizard output directory into Tools and Output Preview."""
        value = self._wizard_output_dir()
        tools = getattr(self, "tools_study_dir", None)
        if tools is not None and tools.get() != value:
            tools.set(value)
        preview = getattr(self, "output_preview", None)
        if preview is None or preview.dir_var.get() == value:
            return
        preview.set_directory(value)
        self._schedule_preview_refresh()

    def _schedule_preview_refresh(self) -> None:
        """Debounce the CSV read while the output directory is being typed."""
        pending = getattr(self, "_preview_refresh_after", None)
        if pending is not None:
            try:
                self.root.after_cancel(pending)
            except tk.TclError:
                pass
        self._preview_refresh_after = self.root.after(300, self._refresh_output_preview)

    def _build_vars(self):
        name = datetime.now().strftime("study_%Y%m%d_%H%M%S")
        self._v("study_name", name)
        self._v("output_dir", str(ROOT / f"Output_{name}"))
        self.vars["study_name"].trace_add("write", self._on_study_name_changed)
        self.vars["output_dir"].trace_add("write", self._on_output_dir_changed)
        defaults = shipped_defaults()
        defaults.update(load_saved_defaults())
        for key, value in defaults.items():
            self._v(key, value, is_bool=isinstance(value, bool), target=self.default_vars)
        for key, var in self.default_vars.items():
            self._v(key, var.get(), is_bool=isinstance(var, BooleanVar))
        self.particle_estimate_var = StringVar(value="Estimated initial SPH particles: --")

    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill=BOTH, expand=True)
        self.notebook = nb
        self.study_tab = ttk.Frame(nb)
        self.defaults_tab = ttk.Frame(nb)
        self.tools_tab = ttk.Frame(nb)
        self.preview_tab = ttk.Frame(nb)
        self.docs_tab = ttk.Frame(nb)
        self.settings_tab = ttk.Frame(nb)
        nb.add(self.study_tab, text="Study Wizard")
        nb.add(self.defaults_tab, text="Defaults")
        nb.add(self.tools_tab, text="Tools / View Results")
        nb.add(self.docs_tab, text="Docs")
        nb.add(self.settings_tab, text="Settings")
        self._build_study_tab()
        self._build_defaults_tab()
        self._build_tools_tab()
        self._build_docs_tab()
        self._build_settings_tab()
        self.output_preview = OutputPreview(
            self.preview_tab, self.root, self._initial_preview_dir(),
            palette=lambda: self.ui_palette,
            particle_limit=int(self.settings["preview_particle_limit"]),
            frame_stride=int(self.settings["preview_frame_stride"]),
            limits_callback=self._on_preview_limits_change,
            particle_size=int(self.settings["preview_particle_size"]),
            line_width=int(self.settings["preview_line_width"]),
            style_callback=self._on_preview_style_change,
        )
        self._apply_ui_settings(self.settings)

    def _initial_preview_dir(self) -> str:
        """Start the output preview on the wizard's output directory."""
        return self._wizard_output_dir()

    def _refresh_output_preview(self) -> None:
        preview = getattr(self, "output_preview", None)
        if preview is None or not bool(self.settings.get("show_output_preview", True)):
            return
        preview.refresh()

    def _on_preview_limits_change(self, particle_limit: int, frame_stride: int) -> None:
        """Persist display limits edited directly in the Output Preview tab."""
        self.settings = dict(self.settings)
        self.settings["preview_particle_limit"] = int(particle_limit)
        self.settings["preview_frame_stride"] = int(frame_stride)
        for key, value in (("preview_particle_limit", particle_limit),
                           ("preview_frame_stride", frame_stride)):
            var = self.settings_vars.get(key)
            if var is not None:
                var.set(str(value))
        write_ui_settings(self.settings)
        self.output_preview.refresh()

    def _on_preview_style_change(self, particle_size: int, line_width: int) -> None:
        """Persist marker/line sizes edited in the Output Preview tab."""
        self.settings = dict(self.settings)
        self.settings["preview_particle_size"] = int(particle_size)
        self.settings["preview_line_width"] = int(line_width)
        for key, value in (("preview_particle_size", particle_size),
                           ("preview_line_width", line_width)):
            var = self.settings_vars.get(key)
            if var is not None:
                var.set(str(value))
        write_ui_settings(self.settings)

    def _apply_preview_limits(self, values: dict[str, object]) -> None:
        preview = getattr(self, "output_preview", None)
        if preview is None:
            return
        particle_limit = _setting_int(
            values.get("preview_particle_limit"), "Preview particle count", 1, PARTICLE_SAMPLE_MAX,
            int(SHIPPED_SETTINGS["preview_particle_limit"]))
        frame_stride = _setting_int(
            values.get("preview_frame_stride"), "Preview frame stride", 1, 50,
            int(SHIPPED_SETTINGS["preview_frame_stride"]))
        if preview.set_display_limits(particle_limit, frame_stride):
            preview.refresh()

    def _apply_preview_style(self, values: dict[str, object]) -> None:
        preview = getattr(self, "output_preview", None)
        if preview is None:
            return
        particle_size = _setting_int(
            values.get("preview_particle_size"), "Preview particle size",
            PARTICLE_SIZE_MIN, PARTICLE_SIZE_MAX,
            int(SHIPPED_SETTINGS["preview_particle_size"]))
        line_width = _setting_int(
            values.get("preview_line_width"), "Preview line width",
            LINE_WIDTH_MIN, LINE_WIDTH_MAX,
            int(SHIPPED_SETTINGS["preview_line_width"]))
        preview.set_display_style(particle_size, line_width)

    def _open_output_preview(self) -> None:
        self.settings_vars["show_output_preview"].set(True)
        self._apply_settings_now()
        self.notebook.select(self.preview_tab)
        if getattr(self, "output_preview", None) is not None:
            tools = getattr(self, "tools_study_dir", None)
            target = tools.get().strip() if tools is not None else ""
            self.output_preview.set_directory(target or self._wizard_output_dir())
            self.output_preview.refresh()

    def _build_study_tab(self):
        top = ttk.Frame(self.study_tab)
        top.pack(fill=BOTH, expand=True)
        self.step_nb = ttk.Notebook(top)
        self.step_nb.pack(fill=BOTH, expand=True, padx=6, pady=6)
        self.pages = []
        self._page_study()
        self._page_geometry()
        self._page_run()
        self._page_materials()
        self._page_export()
        self._page_review()
        nav = ttk.Frame(self.study_tab)
        nav.pack(fill=X, padx=8, pady=4)
        ttk.Button(nav, text="< Back", command=self._back).pack(side=LEFT)
        ttk.Button(nav, text="Next >", command=self._next).pack(side=LEFT, padx=4)
        ttk.Button(nav, text="Apply CAD/mesh defaults", command=self._apply_geo_defaults).pack(side=LEFT, padx=12)
        ttk.Button(nav, text="Load study_config.json", command=self._load_config).pack(side=LEFT)
        ttk.Button(nav, text="Save config/commands (dry run)", command=self._save_only).pack(side=RIGHT)
        ttk.Button(nav, text="Run one-way FEA only (existing CRM)",
                   command=self._run_oneway_fea_only).pack(side=RIGHT, padx=6)
        ttk.Button(nav, text="Run study", command=self._run_study).pack(side=RIGHT, padx=6)

    def _add_page(self, title: str):
        frame = ScrollFrame(self.step_nb)
        self.step_nb.add(frame, text=title)
        self.pages.append(frame.inner)
        return frame.inner

    def _show_info(self, title: str, text: str):
        messagebox.showinfo(title=f"{title} - info", message=text)

    def _row(self, parent, r, label, key, width=40, browse=None, combo=None, check=False,
             help_text=None, variables=None, widget_out=None, command=None):
        fields = variables if variables is not None else self.vars
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky=W, padx=6, pady=3)
        if check:
            w = ttk.Checkbutton(parent, variable=fields[key], command=command)
            w.grid(row=r, column=1, sticky=W, padx=6, pady=3)
        elif combo:
            w = ttk.Combobox(parent, textvariable=fields[key], values=combo, width=max(12, width-2), state="readonly")
            w.grid(row=r, column=1, sticky=W, padx=6, pady=3)
        else:
            w = ttk.Entry(parent, textvariable=fields[key], width=width)
            w.grid(row=r, column=1, sticky=W, padx=6, pady=3)
            if browse:
                def cmd(bt=browse, k=key, f=fields):
                    if bt == "dir":
                        p = filedialog.askdirectory(initialdir=f[k].get() or str(ROOT))
                    else:
                        p = filedialog.askopenfilename(initialdir=str(ROOT))
                    if p:
                        f[k].set(p)
                ttk.Button(parent, text="Browse", command=cmd).grid(row=r, column=2, sticky=W)
        if widget_out is not None:
            widget_out[key] = w
        info_text = PARAM_INFO.get(key) or help_text or "No additional information is available for this setting."
        ttk.Button(
            parent, text="Info", width=6,
            command=lambda t=label, i=info_text: self._show_info(t, i),
        ).grid(row=r, column=3, sticky=W, padx=6, pady=3)
        return r + 1

    def _page_study(self):
        p = self._add_page("1. Study")
        r = 0
        ttk.Label(p, text="Study identity and output location. You can navigate back and edit any step before running.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        r = self._row(p, r, "Study name", "study_name", help_text="Used for default output folder naming.")
        r = self._row(p, r, "Output directory", "output_dir", width=60, browse="dir", help_text="All configs, logs, meshes, and results go here.")
        r = self._row(p, r, "Geometry source", "geometry", combo=["supplied", "step", "files"], help_text="supplied/files=mesh paths below, step=convert CAD; no geometry is bundled.")
        r = self._row(p, r, "Auto-scale from CAD/mesh", "auto_geometry", check=True, help_text="Derive bed/spacing/box defaults from drum size when possible.")
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        ttk.Label(p, text="Tip: use 'Apply CAD/mesh defaults' after choosing geometry to refresh suggested dimensions."
                  ).grid(row=r, column=0, columnspan=4, sticky=W, padx=6)

    def _page_geometry(self):
        p = self._add_page("2. Geometry")
        r = 0
        ttk.Label(p, text="Geometry and mesh inputs (used according to geometry source).",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        r = self._row(p, r, "STEP file", "step_file", width=60, browse="file", help_text="CAD input for geometry=step.")
        r = self._row(p, r, "CRM surface OBJ", "crm_obj", width=60, browse="file", help_text="Rigid CRM contact surface (geometry=files/supplied).")
        r = self._row(p, r, "FEA .node", "fea_node", width=60, browse="file")
        r = self._row(p, r, "FEA .ele", "fea_ele", width=60, browse="file")
        r = self._row(p, r, "CAD axis (STEP)", "cad_axis", combo=["auto", "X", "Y", "Z"])
        r = self._row(p, r, "Unit scale (STEP->m)", "unit_scale", help_text="0.001 for mm CAD.")
        r = self._row(p, r, "FEA mesh size (m)", "mesh_size")
        r = self._row(p, r, "Surface target faces", "surface_target_faces")
        r = self._row(p, r, "Surface max faces", "surface_max_faces")
        r = self._row(p, r, "Exterior fit tolerance (m)", "surface_fit_tolerance",
                      help_text="0 disables; otherwise the face target rises until this exterior fit passes.")
        r = self._row(p, r, "MeshFix order", "surface_repair_order", combo=["none", "before", "after"])
        r = self._row(p, r, "Min solid volume (m^3)", "min_solid_volume")
        r = self._row(p, r, "Hub radius (m)", "hub_radius")
        r = self._row(p, r, "Coarsen hub/interior mesh", "part_aware_simplify", check=True)
        r = self._row(p, r, "Interior coarsen radius (m)", "part_aware_radius")
        r = self._row(p, r, "Interior reduction", "part_aware_reduction")
        r = self._row(p, r, "Interior min faces", "part_aware_min_faces")
        r = self._row(p, r, "Drum radius (m)", "drum_radius")
        r = self._row(p, r, "Drum half-length (m)", "drum_half_length")
        r = self._row(p, r, "Inner/cavity radius (m)", "inner_radius")
        r = self._row(p, r, "Capacity volume override (m^3)", "capacity_volume", help_text="Blank = derive from geometry.")
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        ttk.Label(p, text="Fill-detector cylinder", font=SECTION_FONT).grid(
            row=r, column=0, sticky=W, padx=6); r += 1
        r = self._row(p, r, "Auto-size fill detector", "detector_auto_size", check=True,
                      command=self._sync_detector_fields,
                      widget_out=self.detector_widgets)
        r = self._row(p, r, "Detector cylinder radius (m)", "detector_radius",
                      help_text="Used when auto-size is unchecked.",
                      widget_out=self.detector_widgets)
        r = self._row(p, r, "Detector cylinder length (m)", "detector_length",
                      help_text="Full axial length; used when auto-size is unchecked.",
                      widget_out=self.detector_widgets)
        ttk.Button(
            p, text="Open live 3D detector sizer", command=self._tool_detector_sizer,
        ).grid(row=r, column=1, sticky=W, padx=6, pady=4)
        ttk.Label(p, text="Shows the STEP/drum at 50% opacity with the detector cylinder; "
                          "slider changes update the fields live.",
                  foreground="#666666").grid(row=r, column=2, columnspan=2, sticky=W, padx=6)
        r += 1

    def _page_run(self):
        p = self._add_page("3. Run & Domain")
        r = 0
        ttk.Label(p, text="Simulation timing, motion, SPH/domain, and solver settings.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        r = self._row(p, r, "CRM duration (s)", "duration")
        r = self._row(p, r, "FEA duration (s)", "fea_duration")
        r = self._row(p, r, "SPH spacing (m)", "spacing", help_text="Smaller = higher resolution/more particles.")
        r = self._row(p, r, "SPH d0 multiplier", "d0_multiplier")
        r = self._row(p, r, "Artificial viscosity", "artificial_viscosity")
        r = self._row(p, r, "PPST shifting push", "shifting_ppst_push")
        r = self._row(p, r, "CRM dt (s or 'auto')", "crm_dt")
        r = self._row(p, r, "CRM summary FPS", "crm_fps")
        r = self._row(p, r, "Particle CSV FPS", "particle_fps")
        r = self._row(p, r, "FEA summary FPS", "fea_fps")
        r = self._row(p, r, "Soil loads FPS", "loads_fps")
        r = self._row(p, r, "FEA dt (s)", "fea_dt")
        r = self._row(p, r, "Bed height (m)", "bed_height")
        r = self._row(p, r, "Bed padding (fraction of radius)", "bed_padding")
        ttk.Label(p, textvariable=self.particle_estimate_var, wraplength=560,
                  justify=LEFT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=(0, 8))
        r += 1
        r = self._row(p, r, "Active box X,Y,Z (m)", "active_box")
        r = self._row(p, r, "Settle time (s)", "settle_time")
        r = self._row(p, r, "Gravity Z (m/s^2)", "gravity")
        r = self._row(p, r, "Apply gravity to CRM drum", "drum_gravity", check=True)
        r = self._row(p, r, "Descent mode", "descent_mode", combo=["kinematic", "force"])
        r = self._row(p, r, "Descent speed (m/s; kinematic)", "descent_speed")
        r = self._row(p, r, "Descent force (N; force mode)", "descent_force_n")
        r = self._row(p, r, "Descent force ramp (s; force mode)", "descent_force_ramp_s")
        r = self._row(p, r, "Spin rate |omega| (rad/s)", "omega")
        r = self._row(p, r, "Reverse spin", "reverse_spin", check=True)
        r = self._row(p, r, "Spin axis", "spin_axis", combo=["X", "Y", "Z"])
        r = self._row(p, r, "FEA solver", "solver", combo=["pardiso-mkl", "sparse-lu", "sparse-qr", "gmres"])
        r = self._row(p, r, "FEA threads", "num_threads")

    def _page_materials(self):
        p = self._add_page("4. Soil & Material")
        r = 0
        ttk.Label(p, text="Soil and PLA drum material parameters.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        ttk.Label(p, text="Soil (CRM)", font=SECTION_FONT).grid(row=r, column=0, sticky=W, padx=6, pady=4); r += 1
        for k, (label, _lim) in wiz.SOIL_FIELDS.items():
            r = self._row(p, r, label, f"soil_{k}")
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        ttk.Label(p, text="FEA drum material (PLA defaults)", font=SECTION_FONT).grid(row=r, column=0, sticky=W, padx=6, pady=4); r += 1
        for k, (label, _lim) in wiz.PLA_FIELDS.items():
            r = self._row(p, r, label, f"pla_{k}")

    def _page_export(self):
        p = self._add_page("5. Output & Export")
        r = 0
        ttk.Label(p, text="Saved outputs and ParaView/VTK export (added as final wizard step when enabled).",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8); r += 1
        r = self._row(p, r, "Save CRM particle CSV frames", "save_particles", check=True)
        r = self._row(p, r, "Export full 3D FEA stress animation", "fea_export_vtk", check=True,
                      help_text="Writes per-frame stress/deformation VTU files. Expensive; off by default.")
        r = self._row(p, r, "FEA VTU frame stride", "fea_vtk_stride",
                      help_text="1 = every recorded frame; higher values sample frames. Final frame is included.")
        r = self._row(p, r, "Export ParaView datasets after run", "export_vtk", check=True,
                      help_text="Creates separate particle and excavator series + combined PVD.")
        r = self._row(p, r, "ParaView export stride (frames)", "paraview_stride")
        r = self._row(p, r, "ParaView particle stride (0 = same)", "paraview_particle_stride")
        r = self._row(p, r, "ParaView particle render stride", "paraview_particle_render_stride")
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        r = self._row(p, r, "Open results folder after run", "open_results", check=True)
        r = self._row(p, r, "Open ParaView after run", "open_paraview", check=True)
        ttk.Label(p, text="ParaView note: run 'pvpython view_study_paraview.py <study>/paraview' for a preconfigured 3D view\n"
                          "(particles as spheres and a separate excavator). One-way FEA history charts are hidden\n"
                          "by default; add --show-fea-charts to display them. Enable the 3D FEA stress option\n"
                          "above and the Tools toggle 'Load 3D FEA stress' for a usable multi-frame deformation animation.",
                  justify=LEFT).grid(row=r, column=0, columnspan=4, sticky=W, padx=6, pady=8)

    def _page_review(self):
        p = ttk.Frame(self.step_nb)
        self.step_nb.add(p, text="6. Review & Run")
        self.pages.append(p)
        top = ttk.Frame(p)
        top.pack(fill=X, padx=6, pady=6)
        ttk.Label(top, text="Review generated configuration/commands. Go back to any step to change parameters. "
                            "Skip current step bypasses one command and continues the run.",
                  font=HEADER_FONT).pack(side=LEFT)
        ttk.Button(top, text="Refresh preview", command=self._refresh_preview).pack(side=RIGHT)
        paned = ttk.Panedwindow(p, orient=VERTICAL)
        paned.pack(fill=BOTH, expand=True, padx=6, pady=4)
        self.cmd_preview_frame = ttk.Frame(paned)
        self.log_frame = ttk.Frame(paned)
        paned.add(self.cmd_preview_frame, weight=1)
        paned.add(self.log_frame, weight=2)
        self.cmd_text = self._text_widget(self.cmd_preview_frame, height=16)
        self.log_text = self._text_widget(self.log_frame, height=18)
        runbar = ttk.Frame(p)
        runbar.pack(fill=X, padx=6, pady=4)
        ttk.Button(runbar, text="Skip current step", command=self._skip_step).pack(side=LEFT)
        ttk.Button(runbar, text="Stop running process", command=self._stop).pack(side=LEFT)
        self.status_var = StringVar(value="Ready.")
        ttk.Label(runbar, textvariable=self.status_var).pack(side=LEFT, padx=12)

    def _text_widget(self, parent, height=12):
        f = ttk.Frame(parent)
        f.pack(fill=BOTH, expand=True)
        txt = tk.Text(f, height=height, wrap="none")
        self.text_widgets.append(txt)
        vs = ttk.Scrollbar(f, orient=VERTICAL, command=txt.yview)
        hs = ttk.Scrollbar(f, orient=HORIZONTAL, command=txt.xview)
        txt.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        txt.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        return txt

    def _build_docs_tab(self):
        pane = ttk.Panedwindow(self.docs_tab, orient=HORIZONTAL)
        pane.pack(fill=BOTH, expand=True, padx=6, pady=6)

        left = ttk.Frame(pane, width=220)
        right = ttk.Frame(pane)
        pane.add(left, weight=0)
        pane.add(right, weight=1)

        ttk.Label(left, text="Documentation", font=HEADER_FONT).pack(
            anchor="w", padx=8, pady=(6, 4))
        self.docs_tree = ttk.Treeview(left, show="tree", selectmode="browse")
        self.docs_tree.pack(fill=BOTH, expand=True, padx=4, pady=(0, 6))
        self.docs_tree.bind("<<TreeviewSelect>>", self._show_doc)

        toolbar = ttk.Frame(right)
        toolbar.pack(fill=X, padx=4, pady=(4, 2))
        self.doc_path_var = StringVar(value="")
        ttk.Label(toolbar, textvariable=self.doc_path_var, foreground="#777777").pack(
            side=LEFT, fill=X, expand=True, padx=4)
        self.doc_search_var = StringVar(value="")
        search_entry = ttk.Entry(toolbar, textvariable=self.doc_search_var, width=22)
        search_entry.pack(side=RIGHT, padx=4)
        search_entry.bind("<Return>", self._find_doc_text)
        ttk.Button(toolbar, text="Find", command=self._find_doc_text).pack(side=RIGHT)
        ttk.Button(toolbar, text="Refresh", command=self._show_doc).pack(side=RIGHT, padx=4)

        frame = ttk.Frame(right)
        frame.pack(fill=BOTH, expand=True, padx=4, pady=(0, 4))
        self.doc_text = tk.Text(frame, wrap="word", height=20, spacing1=1, spacing3=1)
        self.text_widgets.append(self.doc_text)
        self._doc_link_tags: dict[str, str] = {}
        self._doc_heading_lines: list[tuple[str, str, int]] = []
        self._doc_embedded: list[tk.Misc] = []
        self._doc_tables: list[ttk.Treeview] = []
        self._current_doc_path: Path | None = None
        vs = ttk.Scrollbar(frame, orient=VERTICAL, command=self.doc_text.yview)
        self.doc_text.configure(yscrollcommand=vs.set)
        self.doc_text.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self._configure_doc_tags()
        self.doc_text.configure(state=DISABLED)

        first = None
        for index, (title, relative) in enumerate(DOC_ENTRIES):
            doc_id = GENERATED_DOC_ID if relative is None else relative
            iid = f"doc_{index}"
            self.docs_tree.insert("", "end", iid=iid, text=title, values=(doc_id,))
            if first is None:
                first = iid
        if first is not None:
            self.docs_tree.selection_set(first)

    def _configure_doc_tags(self) -> None:
        """Apply readable colors and fonts to the Docs tab text tags."""
        doc_text = getattr(self, "doc_text", None)
        if doc_text is None:
            return

        dark = bool(self.settings.get("dark_mode"))
        palette = getattr(self, "ui_palette", {})
        text = str(palette.get("text") or (DARK_TEXT if dark else LIGHT_TEXT))
        field = str(palette.get("field") or ("#2D2D30" if dark else "#FFFFFF"))
        frame = str(palette.get("frame") or ("#252526" if dark else "#F4F4F4"))
        code_bg = "#252526" if dark else "#F2F4F7"
        quote_bg = "#2B2B2C" if dark else "#F7F4E8"
        title_fg = "#D2A8FF" if dark else "#5B2A86"
        heading_fg = "#79C0FF" if dark else "#0B5394"
        sub_fg = "#7EE787" if dark else "#1B7F3A"
        link_fg = "#8AB4F8" if dark else "#1456A6"
        muted = "#B8B8B8" if dark else "#5A5A5A"
        self._doc_palette = {
            "text": text, "field": field, "frame": frame,
            "code_bg": code_bg, "quote_bg": quote_bg,
            "title": title_fg, "heading": heading_fg, "sub": sub_fg,
            "link": link_fg, "muted": muted,
        }

        tags: dict[str, dict[str, object]] = {
            "doc_title": {
                "font": DOC_TITLE_FONT, "foreground": title_fg,
                "spacing1": 2, "spacing3": 10,
            },
            "doc_heading": {
                "font": DOC_HEADING_FONT, "foreground": heading_fg,
                "spacing1": 12, "spacing3": 5,
            },
            "doc_subheading": {
                "font": DOC_SUBHEADING_FONT, "foreground": sub_fg,
                "spacing1": 10, "spacing3": 3,
            },
            "doc_bold": {"font": DOC_BOLD_FONT, "foreground": text},
            "doc_italic": {"font": DOC_ITALIC_FONT, "foreground": text},
            "doc_inline_code": {
                "font": DOC_CODE_FONT, "background": code_bg,
                "foreground": sub_fg,
            },
            "doc_link": {"foreground": link_fg, "underline": 1},
            "doc_code": {
                "font": DOC_CODE_FONT, "background": code_bg,
                "foreground": text, "lmargin1": 18, "lmargin2": 18,
                "rmargin": 12, "spacing1": 2, "spacing3": 2,
            },
            "doc_code_box": {
                "font": DOC_CODE_FONT, "background": code_bg,
                "foreground": text, "lmargin1": 18, "lmargin2": 18,
                "rmargin": 12, "spacing1": 0, "spacing3": 0,
                "relief": "groove", "borderwidth": 1,
            },
            "doc_table": {
                "font": DOC_CODE_FONT, "foreground": text,
                "lmargin1": 10, "spacing1": 0, "spacing3": 0,
            },
            "doc_table_header": {
                "font": DOC_TABLE_HEADER_FONT, "foreground": heading_fg,
                "lmargin1": 10, "spacing1": 4, "spacing3": 2,
            },
            "doc_table_bold": {"font": DOC_TABLE_BOLD_FONT, "foreground": text},
            "doc_table_separator": {
                "font": DOC_CODE_FONT, "foreground": muted, "lmargin1": 10,
            },
            "doc_bullet": {"lmargin1": 16, "lmargin2": 28, "spacing3": 2},
            "doc_quote": {
                "background": quote_bg, "foreground": text,
                "lmargin1": 18, "lmargin2": 18, "rmargin": 10,
                "spacing1": 4, "spacing3": 4,
            },
            "doc_quote_bar": {"foreground": sub_fg, "lmargin1": 18},
            "doc_rule": {"foreground": muted, "spacing1": 6, "spacing3": 6},
            "doc_search": {"background": "#FFD400", "foreground": "#000000"},
        }
        for name, options in tags.items():
            doc_text.tag_configure(name, **options)
        for depth in range(5):
            indent = 10 + 12 * depth
            doc_text.tag_configure(
                f"doc_indent_{depth}",
                lmargin1=indent, lmargin2=indent + 12, spacing3=2,
            )
        for tag_name in getattr(self, "_doc_link_tags", {}).values():
            doc_text.tag_configure(tag_name, foreground=link_fg, underline=1)
        try:
            doc_text.tag_raise("doc_search")
        except tk.TclError:
            pass

        style = ttk.Style()
        row_height = max(20, int(self.settings.get("font_size", 10)) + 9)
        try:
            style.configure(
                "DocTable.Treeview",
                background=field, fieldbackground=field, foreground=text,
                borderwidth=1, relief="groove", font=DOC_CODE_FONT,
                rowheight=row_height,
            )
            style.configure(
                "DocTable.Treeview.Heading",
                background=frame, foreground=heading_fg,
                font=DOC_TABLE_HEADER_FONT,
            )
            style.map(
                "DocTable.Treeview.Heading",
                background=[("active", frame)],
            )
        except tk.TclError:
            pass

    def _clear_doc_embeds(self) -> None:
        """Destroy embedded Docs-tab widgets from the previous render."""
        for widget in getattr(self, "_doc_embedded", []):
            try:
                widget.destroy()
            except tk.TclError:
                pass
        self._doc_embedded = []
        self._doc_tables = []

    def _doc_link_tag(self, target: str) -> str:
        """Return a stable Tk tag for a Markdown link target."""
        mapping = getattr(self, "_doc_link_tags", None)
        if mapping is None:
            mapping = self._doc_link_tags = {}
        if target not in mapping:
            tag_name = f"doc_link_{len(mapping)}"
            mapping[target] = tag_name
            self.doc_text.tag_configure(
                tag_name,
                foreground=self._doc_palette.get("link", "#1456A6"),
                underline=1,
            )
            self.doc_text.tag_bind(
                tag_name, "<Button-1>",
                lambda _event, value=target: self._on_doc_link_click(value),
            )
            self.doc_text.tag_bind(
                tag_name, "<Enter>", lambda _event: self.doc_text.configure(cursor="hand2")
            )
            self.doc_text.tag_bind(
                tag_name, "<Leave>", lambda _event: self.doc_text.configure(cursor="")
            )
        return mapping[target]

    def _on_doc_link_click(self, target: str) -> str:
        self._doc_follow_link(target)
        return "break"

    def _doc_link_target(self, raw: str) -> tuple[str, str, str]:
        """Classify a Markdown target as url, anchor, file, or none."""
        target = raw.strip()
        if not target:
            return "none", "", ""
        if target.startswith("#"):
            return "anchor", unquote(target[1:]), ""

        parsed = urlparse(target)
        if parsed.scheme in {"http", "https", "mailto", "tel", "ftp"}:
            return "url", target, ""
        if parsed.scheme and parsed.scheme != "file":
            return "url", target, ""

        path_part = unquote(parsed.path or target)
        base = getattr(self, "_current_doc_path", None)
        candidate = (Path(path_part) if parsed.scheme == "file"
                     else (base.parent if base else ROOT) / path_part)
        try:
            resolved = candidate.resolve()
            resolved.relative_to(ROOT.resolve())
        except (OSError, RuntimeError, ValueError):
            return "url", target, ""
        return "file", str(resolved), parsed.fragment

    def _doc_follow_link(self, raw: str) -> None:
        """Open a Docs-tab link: registered pages in-tab, web links in browser."""
        kind, value, fragment = self._doc_link_target(raw)
        status = getattr(self, "status_var", None)
        if kind == "url":
            try:
                webbrowser.open(value)
                message = f"Docs: opened '{value}' in your browser."
            except Exception as exc:
                message = f"Docs: could not open '{value}': {exc}"
            if status is not None:
                status.set(message)
            return
        if kind == "anchor":
            if not self._doc_scroll_to_heading(value):
                self._set_doc_status(f"Docs: heading '{value}' not found.")
            return
        if kind != "file":
            return

        path = Path(value)
        for index, (_title, relative) in enumerate(DOC_ENTRIES):
            if relative and (ROOT / relative).resolve() == path:
                self.docs_tree.selection_set(f"doc_{index}")
                self._show_doc()
                if fragment:
                    self._doc_scroll_to_heading(fragment)
                return
        self._set_doc_status(
            f"Docs: '{path.name}' is not registered in the Docs tab."
        )

    def _set_doc_status(self, message: str) -> None:
        status = getattr(self, "status_var", None)
        if status is not None:
            status.set(message)

    @classmethod
    def _doc_heading_slug(cls, text: str) -> str:
        """Return the GitHub-style anchor for a heading or link fragment."""
        clean = cls._clean_doc_inline(unquote(text)).strip().lower()
        clean = re.sub(r"[^\w\s-]", "", clean)
        return re.sub(r"\s+", "-", clean).strip("-")

    def _doc_scroll_to_heading(self, fragment: str) -> bool:
        """Scroll to a heading anchor recorded while rendering the current page."""
        needle = self._doc_heading_slug(fragment)
        if not needle:
            return False
        for slug, title, line_no in getattr(self, "_doc_heading_lines", []):
            if slug == needle or (len(needle) > 3 and needle in slug):
                self.doc_text.see(f"{line_no}.0")
                self._set_doc_status(f"Docs: jumped to '{title}'.")
                return True
        self._set_doc_status(f"Docs: heading '{fragment}' not found.")
        return False

    @staticmethod
    def _clean_doc_inline(line: str) -> str:
        """Return the visible text of an inline Markdown line."""
        line = re.sub(r"\*\*(.+?)\*\*", r"\1", line)
        line = re.sub(r"\*([^*]+)\*", r"\1", line)
        line = re.sub(r"`([^`]+)`", r"\1", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        return line

    def _insert_doc_inline(self, text: str, base_tags: tuple[str, ...] = ()) -> None:
        """Insert text with styled bold, italic, inline code, and links."""
        last = 0
        for match in _DOC_INLINE_RE.finditer(text):
            if match.start() > last:
                self.doc_text.insert(END, text[last:match.start()], base_tags)
            token = match.group(0)
            if token.startswith("["):
                link = re.fullmatch(r"\[([^\]]+)\]\(([^)]+)\)", token)
                if link:
                    label, target = link.groups()
                    self.doc_text.insert(
                        END, label, base_tags + (self._doc_link_tag(target),)
                    )
            elif token.startswith("**"):
                tag = ("doc_table_bold" if any(t.startswith("doc_table") for t in base_tags)
                       else "doc_bold")
                self.doc_text.insert(END, token[2:-2], base_tags + (tag,))
            elif token.startswith("`"):
                self.doc_text.insert(END, token[1:-1], base_tags + ("doc_inline_code",))
            else:
                self.doc_text.insert(END, token[1:-1], base_tags + ("doc_italic",))
            last = match.end()
        if last < len(text):
            self.doc_text.insert(END, text[last:], base_tags)

    @staticmethod
    def _doc_table_cells(line: str) -> list[str]:
        """Split one Markdown table row into trimmed cell strings."""
        stripped = line.strip()
        if stripped.startswith("|"):
            stripped = stripped[1:]
        if stripped.endswith("|"):
            stripped = stripped[:-1]
        return [cell.strip() for cell in stripped.split("|")]

    @classmethod
    def _is_doc_table_separator(cls, line: str) -> bool:
        cells = cls._doc_table_cells(line)
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)

    def _render_doc_table(self, lines: list[str], start: int) -> int:
        """Insert a real, scrollable table widget and return the next line index."""
        header = [cell.strip() for cell in self._doc_table_cells(lines[start])]
        rows: list[list[str]] = []
        index = start + 2
        while index < len(lines) and "|" in lines[index] and lines[index].strip().startswith("|"):
            rows.append([cell.strip() for cell in self._doc_table_cells(lines[index])])
            index += 1
        if not header:
            return index

        container = ttk.Frame(self.doc_text)
        tree = ttk.Treeview(
            container,
            columns=[f"col{i}" for i in range(len(header))],
            show="headings",
            height=max(1, len(rows)),
            selectmode="none",
            style="DocTable.Treeview",
        )
        xscroll = ttk.Scrollbar(container, orient=HORIZONTAL, command=tree.xview)
        tree.configure(xscrollcommand=xscroll.set)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky="nsew")
        xscroll.grid(row=1, column=0, sticky="ew")

        try:
            measure = tkfont.nametofont(DOC_CODE_FONT)
        except tk.TclError:
            measure = tkfont.nametofont("TkFixedFont")
        widths: list[int] = []
        for col, title in enumerate(header):
            cells = [title] + [row[col] for row in rows if col < len(row)]
            plain = [self._clean_doc_inline(cell) for cell in cells]
            longest = max(plain, key=len) if plain else ""
            try:
                pixels = measure.measure(longest)
            except tk.TclError:
                pixels = len(longest) * 8
            widths.append(max(70, min(2200, pixels + 24)))

        link_targets: dict[tuple[str, str], str] = {}
        cell_links: dict[tuple[str, str], tuple[str, list[tuple[int, int, str]]]] = {}
        for col, title in enumerate(header):
            column = f"col{col}"
            tree.heading(column, text=self._clean_doc_inline(title), anchor=W)
            tree.column(column, width=widths[col], minwidth=60, anchor=W, stretch=False)

        for row in rows:
            item = tree.insert("", END)
            cleaned: list[str] = []
            for col, cell in enumerate(row):
                plain = self._clean_doc_inline(cell)
                cleaned.append(plain)
                spans: list[tuple[int, int, str]] = []
                for link in re.finditer(r"\[([^\]]+)\]\(([^)]+)\)", cell):
                    start = len(self._clean_doc_inline(cell[:link.start()]))
                    end = start + len(self._clean_doc_inline(link.group(1)))
                    spans.append((start, end, link.group(2)))
                if spans:
                    key = (item, f"#{col + 1}")
                    cell_links[key] = (plain, spans)
                    link_targets[key] = spans[0][2]
            tree.item(item, values=cleaned)

        def link_at(event, widget=tree, cells=cell_links):
            item = widget.identify_row(event.y)
            column = widget.identify_column(event.x)
            meta = cells.get((item, column))
            if not meta:
                return None
            plain, spans = meta
            bbox = widget.bbox(item, column)
            if not bbox:
                return None
            offset = event.x - bbox[0] - 3
            for start, end, target in spans:
                left = measure.measure(plain[:start]) - 3
                right = measure.measure(plain[:end]) + 3
                if left <= offset <= right:
                    return target
            return None

        def on_click(event):
            target = link_at(event)
            if target:
                self._doc_follow_link(target)
                return "break"
            return None

        def on_motion(event, widget=tree):
            widget.configure(cursor="hand2" if link_at(event) else "")

        tree.bind("<Button-1>", on_click)
        tree.bind("<Motion>", on_motion)
        tree._doc_table_links = link_targets
        tree._doc_table_cell_links = cell_links

        try:
            available = max(360, self.doc_text.winfo_width() - 40)
        except tk.TclError:
            available = 760
        container.configure(width=min(available, 1020))
        self.doc_text.window_create(
            END, window=container, padx=4, pady=2, stretch=False
        )
        self.doc_text.insert(END, "\n")
        self._doc_embedded.append(container)
        self._doc_tables.append(tree)
        return index

    def _render_doc_markdown(self, body: str) -> None:
        self._clear_doc_embeds()
        lines = body.splitlines()
        self._doc_heading_lines = []
        in_code = False
        index = 0
        while index < len(lines):
            raw = lines[index]
            if raw.strip().startswith("```"):
                self.doc_text.insert(END, "\n", "doc_code_box")
                in_code = not in_code
                index += 1
                continue
            if in_code:
                self.doc_text.insert(END, raw + "\n", "doc_code_box")
                index += 1
                continue

            if (raw.strip().startswith("|") and index + 1 < len(lines)
                    and self._is_doc_table_separator(lines[index + 1])):
                index = self._render_doc_table(lines, index)
                continue

            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                self.doc_text.insert(END, "\n")
                index += 1
                continue

            heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if heading:
                level = len(heading.group(1))
                tag = "doc_title" if level == 1 else (
                    "doc_heading" if level == 2 else "doc_subheading"
                )
                line_no = int(self.doc_text.index(END).split(".", 1)[0])
                title = self._clean_doc_inline(heading.group(2))
                self._doc_heading_lines.append((self._doc_heading_slug(title), title, line_no))
                self._insert_doc_inline(heading.group(2), (tag,))
                self.doc_text.insert(END, "\n")
                index += 1
                continue
            if re.fullmatch(r"[-*_]\s*[-*_]\s*[-*_][-*_ ]*", stripped):
                self.doc_text.insert(END, "─" * 60 + "\n", "doc_rule")
                index += 1
                continue

            bullet = re.match(r"^(\s*)[-*+]\s+(.*)$", line)
            if bullet:
                depth = min(4, len(bullet.group(1).expandtabs(2)) // 2)
                tag = f"doc_indent_{depth}"
                self.doc_text.insert(END, "• ", tag)
                self._insert_doc_inline(bullet.group(2), (tag,))
                self.doc_text.insert(END, "\n")
                index += 1
                continue
            number = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
            if number:
                depth = min(4, len(number.group(1).expandtabs(2)) // 2)
                tag = f"doc_indent_{depth}"
                self.doc_text.insert(END, f"{number.group(2)}. ", tag)
                self._insert_doc_inline(number.group(3), (tag,))
                self.doc_text.insert(END, "\n")
                index += 1
                continue
            if stripped.startswith(">"):
                self.doc_text.insert(END, "│ ", "doc_quote_bar")
                self._insert_doc_inline(stripped.lstrip("> ").strip(), ("doc_quote",))
                self.doc_text.insert(END, "\n")
                index += 1
                continue

            self._insert_doc_inline(line)
            self.doc_text.insert(END, "\n")
            index += 1
        if in_code:
            self.doc_text.insert(END, "\n", "doc_code_box")

    def _generated_doc_text(self) -> str:
        lines = [
            "# GUI parameter reference",
            "",
            "These are the help strings shown throughout the study wizard and the Settings tab. "
            "Each entry explains what the value controls and notes runtime, disk, or display "
            "cost where it matters.",
            "",
            "## Study parameters",
            "",
        ]
        for key, info in PARAM_INFO.items():
            lines.extend([f"### `{key}`", info, ""])
        lines.extend(["## Appearance settings", ""])
        for key, info in SETTINGS_INFO.items():
            lines.extend([f"### `{key}`", info, ""])
        return "\n".join(lines)

    def _show_doc(self, *_):
        selection = self.docs_tree.selection()
        if not selection:
            return
        values = self.docs_tree.item(selection[0], "values")
        doc_id = values[0] if values else ""
        if doc_id == GENERATED_DOC_ID:
            body = self._generated_doc_text()
            label = "Generated from the GUI help tables"
            self._current_doc_path = None
        else:
            path = ROOT / doc_id
            self._current_doc_path = path
            if not path.is_file():
                messagebox.showerror("Docs", f"Documentation file not found:\n{path}")
                return
            try:
                body = path.read_text(encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Docs", f"Could not read {path}:\n{exc}")
                return
            label = str(path)

        self.doc_text.configure(state=NORMAL)
        self.doc_text.delete("1.0", END)
        self._render_doc_markdown(body)
        self.doc_text.configure(state=DISABLED)
        self.doc_text.yview_moveto(0.0)
        self.doc_path_var.set(label)
        status = getattr(self, "status_var", None)
        if status is not None:
            status.set(f"Viewing {label}")

    def _find_doc_text(self, *_):
        needle = self.doc_search_var.get().strip()
        if not needle:
            return
        self.doc_text.tag_remove("doc_search", "1.0", END)
        pos = self.doc_text.search(needle, "insert", nocase=1, stopindex=END)
        wrapped = False
        if not pos:
            pos = self.doc_text.search(needle, "1.0", nocase=1, stopindex=END)
            wrapped = bool(pos)
        status = getattr(self, "status_var", None)
        if not pos:
            if status is not None:
                status.set(f"Docs: no match for '{needle}'.")
            return
        end = f"{pos}+{len(needle)}c"
        self.doc_text.tag_add("doc_search", pos, end)
        self.doc_text.mark_set("insert", end)
        self.doc_text.see(pos)
        if status is not None:
            suffix = " (wrapped)" if wrapped else ""
            status.set(f"Docs: found '{needle}'{suffix}.")

    def _back(self):
        i = self.step_nb.index("current")
        if i > 0:
            self.step_nb.select(i - 1)

    def _next(self):
        i = self.step_nb.index("current")
        if i < self.step_nb.index("end") - 1:
            self.step_nb.select(i + 1)
            if self.step_nb.index("current") == self.step_nb.index("end") - 1:
                self._refresh_preview()

    def _build_tools_tab(self):
        outer = ScrollFrame(self.tools_tab)
        outer.pack(fill=BOTH, expand=True)
        p = outer.inner
        r = 0
        ttk.Label(p, text="Run/view tools for an existing study directory.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=8, pady=10); r += 1
        self.tools_study_dir = StringVar(value=self._wizard_output_dir())
        ttk.Label(p, text="Study/output directory").grid(row=r, column=0, sticky=W, padx=8, pady=4)
        ttk.Entry(p, textvariable=self.tools_study_dir, width=70).grid(row=r, column=1, sticky=W, padx=4)
        ttk.Button(p, text="Browse", command=self._browse_tools_dir).grid(row=r, column=2, sticky=W)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Study/output directory", PARAM_INFO["tools_study_dir"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1

        actions = [
            ("Export ParaView VTK/PVD (separate particles & excavator)", self._tool_export),
            ("Open ParaView 3D view (pvpython, spheres + FEA stress)", self._tool_paraview),
            ("Open interactive result plots", self._tool_results),
            ("Open lightweight CRM/FEA output preview", self._open_output_preview),
            ("Preview STEP mesh (full-run mesher + comparison)", self._tool_mesh_preview),
            ("View geometry comparison (STEP vs converted)", self._tool_geometry),
            ("Live 3D fill-detector size adjuster", self._tool_detector_sizer),
            ("Open study folder", self._tool_open_folder),
        ]
        for text, cmd in actions:
            ttk.Button(p, text=text, command=cmd, width=54).grid(row=r, column=0, columnspan=2, sticky=W, padx=8, pady=4); r += 1

        # Viewing an already-validated FEA PVD should work straight from the
        # ParaView button.  Exporting those VTU frames remains opt-in elsewhere.
        self.tools_show_fea_3d = BooleanVar(value=True)
        ttk.Checkbutton(p, text="Load 3D FEA stress/deformation in ParaView (if valid)",
                        variable=self.tools_show_fea_3d).grid(
                            row=r, column=0, columnspan=2, sticky=W, padx=8, pady=4)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Load 3D FEA stress", PARAM_INFO["tools_show_fea_3d"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        # One-way FEA history charts are deliberately opt-in: the default false
        # state also clears PYCHRONO_FEA_CHARTS inherited from the environment.
        self.tools_show_fea_charts = BooleanVar(value=False)
        ttk.Checkbutton(p, text="Show one-way FEA scalar/deformation history charts",
                        variable=self.tools_show_fea_charts).grid(
                            row=r, column=0, columnspan=2, sticky=W, padx=8, pady=4)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Show FEA history charts",
                                                   PARAM_INFO["tools_show_fea_charts"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1

        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        ttk.Label(p, text="ParaView notes:", font=SECTION_FONT).grid(row=r, column=0, sticky=W, padx=8); r += 1
        notes = (
            "• The 'Open ParaView 3D view' button launches the full ParaView GUI and keeps it open.\n"
            "• It forces QT_QPA_PLATFORM=xcb (X11/XWayland) because ParaView 6.2 can abort on Wayland.\n"
            "• Command line: QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR=<study>/paraview paraview --script=view_study_paraview.py\n"
            "  (passing args after '--' may not work with some ParaView builds).\n"
            "• pvpython fallback: pvpython view_study_paraview.py <study>/paraview stays open until its render window is closed.\n"
            "• 'Preview STEP mesh' runs the current Geometry settings through the exact full-run mesher into geometry_checks/, then opens the STEP-vs-mesh comparison tool. Active study outputs are untouched.\n"
            "• Particles are rendered as 3D sphere point sprites (glyph fallback). Excavator is a separate source.\n"
            "• The camera-orbit track is primed before the first render: the first frame is framed immediately, with no manual step back/forward and no VTK view-up warning.\n"
            "• One-way FEA scalar/deformation XY charts are hidden by default; check 'Show one-way FEA scalar/deformation history charts' to opt in (or pass --show-fea-charts to the viewer). A validated per-frame stress/deformation PVD is loaded by default; uncheck 'Load 3D FEA stress' to skip it.\n"
            "• The lightweight Output Preview tab reads CRM/FEA CSVs directly and draws a bounded 3D point cloud with editable particle/frame strides."
        )
        ttk.Label(p, text=notes, justify=LEFT).grid(row=r, column=0, columnspan=4, sticky=W, padx=8, pady=4); r += 1

        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=8); r += 1
        ttk.Label(p, text="Quick export stride (every N CRM frames)").grid(row=r, column=0, sticky=W, padx=8, pady=4)
        self.tools_stride = StringVar(value="1")
        ttk.Entry(p, textvariable=self.tools_stride, width=8).grid(row=r, column=1, sticky=W)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Quick export stride", PARAM_INFO["tools_stride"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        ttk.Label(p, text="Particle frame stride (0 = same as quick)").grid(row=r, column=0, sticky=W, padx=8, pady=4)
        self.tools_particle_stride = StringVar(value="0")
        ttk.Entry(p, textvariable=self.tools_particle_stride, width=8).grid(row=r, column=1, sticky=W)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Particle frame stride", PARAM_INFO["tools_particle_stride"])).grid(
                        row=r, column=3, sticky=W, padx=6)
        r += 1
        ttk.Label(p, text="Particle render stride (every N particles/frame)").grid(row=r, column=0, sticky=W, padx=8, pady=4)
        self.tools_particle_render_stride = StringVar(value="1")
        ttk.Entry(p, textvariable=self.tools_particle_render_stride, width=8).grid(row=r, column=1, sticky=W)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Particle render stride", PARAM_INFO["tools_particle_render_stride"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        self.tools_camera_orbit = BooleanVar(value=True)
        ttk.Checkbutton(p, text="Auto camera orbit around drum",
                        variable=self.tools_camera_orbit).grid(
                            row=r, column=0, columnspan=2, sticky=W, padx=8, pady=4)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Auto camera orbit", PARAM_INFO["tools_camera_orbit"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        for label, attr, default, info_key in (
            ("Orbit speed (revolutions/s)", "tools_orbit_speed", "0.10", "tools_orbit_speed"),
            ("Orbit radius (m, 0 = auto-fit)", "tools_orbit_radius", "0", "tools_orbit_radius"),
            ("Orbit inclination (deg)", "tools_orbit_inclination", "20", "tools_orbit_inclination"),
            ("Orbit start angle (deg, blank = current)", "tools_orbit_start_angle", "", "tools_orbit_start_angle"),
            ("Orbit keyframes (0 = automatic)", "tools_orbit_keyframes", "0", "tools_orbit_keyframes"),
        ):
            setattr(self, attr, StringVar(value=default))
            ttk.Label(p, text=label).grid(row=r, column=0, sticky=W, padx=8, pady=4)
            ttk.Entry(p, textvariable=getattr(self, attr), width=12).grid(row=r, column=1, sticky=W)
            ttk.Button(p, text="Info", width=6,
                       command=lambda key=info_key, lbl=label: self._show_info(lbl, PARAM_INFO[key])).grid(
                           row=r, column=3, sticky=W, padx=6)
            r += 1
        ttk.Label(p, text="Export workers (parallel frame writes)").grid(row=r, column=0, sticky=W, padx=8, pady=4)
        self.tools_workers = StringVar(value=self._g("export_workers") or "4")
        ttk.Entry(p, textvariable=self.tools_workers, width=8).grid(row=r, column=1, sticky=W)
        ttk.Button(p, text="Info", width=6,
                   command=lambda: self._show_info("Export workers", PARAM_INFO["tools_workers"])).grid(
                       row=r, column=3, sticky=W, padx=6)
        r += 1
        logf = ttk.Frame(p)
        logf.grid(row=r, column=0, columnspan=4, sticky="nsew", padx=8, pady=8)
        p.rowconfigure(r, weight=1)
        p.columnconfigure(1, weight=1)
        self.tools_log = tk.Text(logf, height=14, wrap="none")
        self.text_widgets.append(self.tools_log)
        vs = ttk.Scrollbar(logf, orient=VERTICAL, command=self.tools_log.yview)
        hs = ttk.Scrollbar(logf, orient=HORIZONTAL, command=self.tools_log.xview)
        self.tools_log.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tools_log.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        logf.rowconfigure(0, weight=1)
        logf.columnconfigure(0, weight=1)

    def _browse_tools_dir(self):
        p = filedialog.askdirectory(initialdir=self.tools_study_dir.get() or str(ROOT))
        if p:
            self.tools_study_dir.set(p)

    # ---- persistent defaults ----
    def _build_defaults_tab(self):
        outer = ScrollFrame(self.defaults_tab)
        outer.pack(fill=BOTH, expand=True)
        p = outer.inner
        r = 0
        ttk.Label(p, text="Persistent starting values for new studies.",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=4, sticky=W, padx=8, pady=8)
        r += 1
        ttk.Label(p, text=f"Saved in {DEFAULT_FILE}. Study name and output directory are generated for each run."
                  ).grid(row=r, column=0, columnspan=4, sticky=W, padx=8)
        r += 1
        bar = ttk.Frame(p)
        bar.grid(row=r, column=0, columnspan=4, sticky=W, padx=8, pady=8)
        for text, cmd in (
            ("Save defaults", self._save_defaults),
            ("Apply to current study", self._apply_defaults_to_study),
            ("Copy current study values", self._copy_study_to_defaults),
            ("Reset to shipped defaults", self._reset_defaults),
        ):
            ttk.Button(bar, text=text, command=cmd).pack(side=LEFT, padx=(0, 6))
        r += 1
        ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=4)
        r += 1
        for section, fields in DEFAULT_GROUPS:
            ttk.Label(p, text=section, font=SECTION_FONT).grid(
                row=r, column=0, columnspan=4, sticky=W, padx=8, pady=(8, 4))
            r += 1
            for label, key, kwargs in fields:
                r = self._row(p, r, label, key, variables=self.default_vars, **kwargs)
            ttk.Separator(p).grid(row=r, column=0, columnspan=4, sticky="ew", pady=6)
            r += 1

    def _collect_default_values(self) -> dict[str, object]:
        templates = shipped_defaults()
        return {
            key: coerce_default_value(key, var.get(), templates.get(key))
            for key, var in self.default_vars.items()
        }

    def _save_defaults(self):
        try:
            write_saved_defaults(self._collect_default_values())
            self.status_var.set(f"Saved GUI defaults to {DEFAULT_FILE}")
            messagebox.showinfo("Defaults", f"Saved GUI defaults to:\n{DEFAULT_FILE}")
        except Exception as e:
            messagebox.showerror("Defaults", str(e))

    def _apply_defaults_to_study(self):
        try:
            templates = shipped_defaults()
            for key, var in self.default_vars.items():
                target = self.vars.get(key)
                if target is None:
                    continue
                value = coerce_default_value(key, var.get(), templates.get(key))
                if isinstance(target, BooleanVar):
                    target.set(bool(value))
                else:
                    target.set(str(value))
            if self.step_nb.index("current") == len(self.pages) - 1:
                self._refresh_preview()
            self.status_var.set("Applied default values to the current study fields.")
        except Exception as e:
            messagebox.showerror("Defaults", str(e))

    def _copy_study_to_defaults(self):
        try:
            templates = shipped_defaults()
            for key, target in self.default_vars.items():
                source = self.vars.get(key)
                if source is None:
                    continue
                value = coerce_default_value(key, source.get(), templates.get(key))
                if isinstance(target, BooleanVar):
                    target.set(bool(value))
                else:
                    target.set(str(value))
            self.status_var.set("Copied current study values into Defaults (click Save defaults to keep them).")
        except Exception as e:
            messagebox.showerror("Defaults", str(e))

    def _reset_defaults(self):
        if not messagebox.askyesno(
            "Defaults",
            "Reset all saved defaults to shipped values?\n\n"
            f"This removes {DEFAULT_FILE} and restores the Defaults tab, but does not change "
            "the current study fields until you click 'Apply to current study'.",
        ):
            return
        try:
            if DEFAULT_FILE.exists():
                DEFAULT_FILE.unlink()
            for key, value in shipped_defaults().items():
                var = self.default_vars.get(key)
                if var is None:
                    continue
                if isinstance(var, BooleanVar):
                    var.set(bool(value))
                else:
                    var.set(str(value))
            self.status_var.set("Defaults reset to shipped values. Use 'Apply to current study' if desired.")
        except Exception as e:
            messagebox.showerror("Defaults", str(e))

    # ---- appearance settings ----
    def _build_settings_tab(self):
        outer = ScrollFrame(self.settings_tab)
        outer.pack(fill=BOTH, expand=True)
        p = outer.inner
        r = 0
        ttk.Label(p, text="Appearance and startup preferences (kept separate from study defaults).",
                  font=HEADER_FONT).grid(row=r, column=0, columnspan=5, sticky=W, padx=8, pady=8); r += 1
        ttk.Label(p, text=f"Saved in {SETTINGS_FILE}.",
                  font=SECTION_FONT).grid(row=r, column=0, columnspan=5, sticky=W, padx=8); r += 1

        values = self.settings
        self.settings_vars = {
            "ui_scale": StringVar(value=str(values["ui_scale"])),
            "font_size": StringVar(value=str(values["font_size"])),
            "window_width": StringVar(value=str(values["window_width"])),
            "window_height": StringVar(value=str(values["window_height"])),
            "text_color": StringVar(value=str(values["text_color"])),
            "dark_mode": BooleanVar(value=bool(values["dark_mode"])),
            "show_output_preview": BooleanVar(value=bool(values["show_output_preview"])),
            "preview_particle_limit": StringVar(value=str(values["preview_particle_limit"])),
            "preview_frame_stride": StringVar(value=str(values["preview_frame_stride"])),
            "preview_particle_size": StringVar(value=str(values["preview_particle_size"])),
            "preview_line_width": StringVar(value=str(values["preview_line_width"])),
        }
        r = self._settings_row(p, r, "GUI scale", "ui_scale", kind="scale")
        r = self._settings_row(p, r, "Text size", "font_size", kind="size")
        r = self._settings_row(p, r, "Window width (px)", "window_width")
        r = self._settings_row(p, r, "Window height (px)", "window_height")
        r = self._settings_row(p, r, "Text color", "text_color", kind="color")
        r = self._settings_row(p, r, "Dark mode", "dark_mode", kind="check")
        r = self._settings_row(p, r, "Show output preview tab", "show_output_preview",
                               kind="check", command=self._apply_settings_now)
        r = self._settings_row(p, r, "Preview particles to sample", "preview_particle_limit")
        r = self._settings_row(p, r, "Preview frame stride (files)", "preview_frame_stride")
        r = self._settings_row(p, r, "Preview particle size (px)", "preview_particle_size")
        r = self._settings_row(p, r, "Preview line width (px)", "preview_line_width")
        ttk.Separator(p).grid(row=r, column=0, columnspan=5, sticky="ew", pady=8); r += 1
        bar = ttk.Frame(p)
        bar.grid(row=r, column=0, columnspan=5, sticky=W, padx=8, pady=6); r += 1
        for text, cmd in (
            ("Apply now", self._apply_settings_now),
            ("Save settings", self._save_settings),
            ("Reset to defaults", self._reset_settings),
        ):
            ttk.Button(bar, text=text, command=cmd).pack(side=LEFT, padx=(0, 6))
        ttk.Label(
            p,
            text="Apply now changes this session only; Save settings also keeps the values for the next launch. "
                 "Text size changes fonts immediately; GUI scale changes widget density and spacing.",
            justify=LEFT,
        ).grid(row=r, column=0, columnspan=5, sticky=W, padx=8, pady=(2, 8)); r += 1
        self._refresh_color_preview()

    def _settings_row(self, parent, r, label, key, kind="entry", command=None):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky=W, padx=8, pady=4)
        var = self.settings_vars[key]
        if kind == "check":
            callback = command or self._on_dark_mode_toggle
            ttk.Checkbutton(parent, variable=var,
                            command=callback).grid(row=r, column=1, sticky=W, padx=4)
        elif kind == "size" and hasattr(ttk, "Spinbox"):
            ttk.Spinbox(parent, from_=7, to=24, width=8,
                        textvariable=var).grid(row=r, column=1, sticky=W, padx=4)
        elif kind == "scale" and hasattr(ttk, "Spinbox"):
            ttk.Spinbox(parent, from_=UI_SCALE_MIN, to=UI_SCALE_MAX,
                        increment=0.05, width=8,
                        textvariable=var).grid(row=r, column=1, sticky=W, padx=4)
        else:
            ttk.Entry(parent, textvariable=var, width=12).grid(row=r, column=1, sticky=W, padx=4)
        if kind == "color":
            self.color_preview = ttk.Label(parent, text="", width=12, relief="sunken")
            self.color_preview.grid(row=r, column=2, sticky=W, padx=4)
            ttk.Button(parent, text="Pick...", width=8,
                       command=self._pick_text_color).grid(row=r, column=3, sticky=W, padx=4)
            var.trace_add("write", lambda *_: self._refresh_color_preview())
        ttk.Button(parent, text="Info", width=6,
                   command=lambda l=label, k=key: self._show_info(l, SETTINGS_INFO.get(k, ""))).grid(
                       row=r, column=4, sticky=W, padx=6)
        return r + 1

    def _refresh_color_preview(self):
        preview = getattr(self, "color_preview", None)
        if preview is None:
            return
        color = self.settings_vars["text_color"].get().strip()
        if not _valid_color(color):
            preview.configure(text="invalid", background="#777777", foreground=DARK_TEXT)
            return
        dark = bool(self.settings_vars["dark_mode"].get())
        preview.configure(text=color, background=color,
                          foreground=DARK_TEXT if dark else LIGHT_TEXT)

    def _pick_text_color(self):
        chosen = colorchooser.askcolor(
            color=self.settings_vars["text_color"].get(), title="Choose text color")
        if chosen and chosen[1]:
            self.settings_vars["text_color"].set(chosen[1])
            self._refresh_color_preview()

    def _on_dark_mode_toggle(self):
        dark = bool(self.settings_vars["dark_mode"].get())
        current = self.settings_vars["text_color"].get().strip().lower()
        if current in {LIGHT_TEXT.lower(), DARK_TEXT.lower()}:
            self.settings_vars["text_color"].set(DARK_TEXT if dark else LIGHT_TEXT)
        self._refresh_color_preview()
        self._apply_settings_now()

    def _collect_settings_from_vars(self, strict: bool = False) -> dict[str, object]:
        color = self.settings_vars["text_color"].get().strip()
        if not _valid_color(color):
            if strict:
                raise ValueError("Text color must be a hex color such as #336699.")
            color = str(self.settings.get("text_color", SHIPPED_SETTINGS["text_color"]))
        return {
            "ui_scale": _setting_scale(self.settings_vars["ui_scale"].get(), strict=strict),
            "font_size": _setting_int(self.settings_vars["font_size"].get(), "Text size",
                                      7, 24, int(self.settings["font_size"]), strict=strict),
            "window_width": _setting_int(self.settings_vars["window_width"].get(), "Window width",
                                         640, 3840, int(self.settings["window_width"]), strict=strict),
            "window_height": _setting_int(self.settings_vars["window_height"].get(), "Window height",
                                          480, 2160, int(self.settings["window_height"]), strict=strict),
            "text_color": color,
            "dark_mode": bool(self.settings_vars["dark_mode"].get()),
            "show_output_preview": bool(self.settings_vars["show_output_preview"].get()),
            "preview_particle_limit": _setting_int(
                self.settings_vars["preview_particle_limit"].get(), "Preview particle count",
                1, PARTICLE_SAMPLE_MAX, int(self.settings["preview_particle_limit"]), strict=strict),
            "preview_frame_stride": _setting_int(
                self.settings_vars["preview_frame_stride"].get(), "Preview frame stride",
                1, 50, int(self.settings["preview_frame_stride"]), strict=strict),
            "preview_particle_size": _setting_int(
                self.settings_vars["preview_particle_size"].get(), "Preview particle size",
                PARTICLE_SIZE_MIN, PARTICLE_SIZE_MAX,
                int(self.settings["preview_particle_size"]), strict=strict),
            "preview_line_width": _setting_int(
                self.settings_vars["preview_line_width"].get(), "Preview line width",
                LINE_WIDTH_MIN, LINE_WIDTH_MAX,
                int(self.settings["preview_line_width"]), strict=strict),
        }

    def _sync_settings_vars(self, values: dict[str, object]) -> None:
        if not self.settings_vars:
            return
        for key in ("ui_scale", "font_size", "window_width", "window_height", "text_color",
                    "preview_particle_limit", "preview_frame_stride",
                    "preview_particle_size", "preview_line_width"):
            self.settings_vars[key].set(str(values[key]))
        self.settings_vars["dark_mode"].set(bool(values["dark_mode"]))
        self.settings_vars["show_output_preview"].set(bool(values["show_output_preview"]))

    def _walk_widgets(self, widget=None):
        if widget is None:
            widget = self.root
        yield widget
        try:
            children = widget.winfo_children()
        except tk.TclError:
            return
        for child in children:
            yield from self._walk_widgets(child)

    def _apply_ui_settings(self, values: dict[str, object] | None = None) -> None:
        """Apply appearance preferences to the live Tk widgets."""
        values = values or self.settings
        scale = _setting_scale(values.get("ui_scale"))
        base_size = _setting_int(values.get("font_size"), "Text size", 7, 24, 10)
        # Font size is a literal point size: changing Text size must always be
        # visible.  GUI scale controls density (tk scaling + ttk padding)
        # instead of being folded into the font metrics.
        size = base_size
        system_scale = getattr(self, "_system_scaling", 1.0)
        effective_scale = system_scale * scale
        try:
            self.root.tk.call("tk", "scaling", effective_scale)
        except tk.TclError:
            pass
        pad_x = max(2, int(round(8 * scale)))
        pad_y = max(2, int(round(4 * scale)))
        width = _setting_int(values.get("window_width"), "Window width", 640, 3840, 1100)
        height = _setting_int(values.get("window_height"), "Window height", 480, 2160, 1080)
        color = str(values.get("text_color") or LIGHT_TEXT)
        if not _valid_color(color):
            color = str(SHIPPED_SETTINGS["text_color"])
        dark = bool(values.get("dark_mode"))

        self.root.geometry(f"{width}x{height}")

        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                     "TkHeadingFont", "TkFixedFont"):
            try:
                tkfont.nametofont(name).configure(size=size)
            except tk.TclError:
                pass
        # Use Tk's generic family names.  Passing "TkDefaultFont"/"TkFixedFont"
        # as a *family* makes fontconfig fall back to a font that ignores the
        # configured size and renders poorly.
        try:
            default_family = tkfont.nametofont("TkDefaultFont").cget("family")
            fixed_family = tkfont.nametofont("TkFixedFont").cget("family")
        except tk.TclError:
            default_family, fixed_family = "sans-serif", "monospace"
        for name, font_size, family, weight, slant in (
            (HEADER_FONT, size, default_family, "bold", "roman"),
            (SECTION_FONT, max(7, size - 1), default_family, "bold", "roman"),
            (DOC_TITLE_FONT, size + 5, default_family, "bold", "roman"),
            (DOC_HEADING_FONT, size + 2, default_family, "bold", "roman"),
            (DOC_SUBHEADING_FONT, size + 1, default_family, "bold", "roman"),
            (DOC_CODE_FONT, size, fixed_family, "normal", "roman"),
            (DOC_BOLD_FONT, size, default_family, "bold", "roman"),
            (DOC_ITALIC_FONT, size, default_family, "normal", "italic"),
            (DOC_TABLE_HEADER_FONT, size, fixed_family, "bold", "roman"),
            (DOC_TABLE_BOLD_FONT, size, fixed_family, "bold", "roman"),
        ):
            try:
                font = tkfont.nametofont(name)
            except tk.TclError:
                font = tkfont.Font(name=name, exists=False, family=family,
                                   size=font_size, weight=weight, slant=slant)
            font.configure(family=family, size=font_size, weight=weight, slant=slant)

        style = ttk.Style()
        theme = "clam" if dark and "clam" in style.theme_names() else self._base_theme
        if theme in style.theme_names() and style.theme_use() != theme:
            style.theme_use(theme)

        bg = "#1E1E1E" if dark else "#FFFFFF"
        frame = "#252526" if dark else "#F4F4F4"
        field = "#2D2D30" if dark else "#FFFFFF"
        button = "#3A3F4B" if dark else "#E6E6E6"
        self.ui_palette = {
            "bg": bg, "frame": frame, "field": field,
            "text": color, "button": button,
        }
        try:
            # A named font reference keeps ttk in sync with TkDefaultFont;
            # ("TkDefaultFont", size) is a literal family, not the named font.
            style.configure(".", background=bg, foreground=color, font="TkDefaultFont")
            for cls in ("TFrame", "TLabelframe", "TLabelframe.Label", "TLabel",
                        "TButton", "TCheckbutton", "TRadiobutton", "TScale"):
                style.configure(cls, background=bg, foreground=color)
            style.configure("TButton", background=button, foreground=color,
                            padding=(pad_x, pad_y))
            style.configure("TNotebook", background=frame)
            style.configure("TNotebook.Tab", background=frame, foreground=color,
                            padding=(pad_x, pad_y))
            style.map("TNotebook.Tab", background=[("selected", field)],
                      foreground=[("selected", color)])
            for cls in ("TEntry", "TCombobox", "TSpinbox"):
                style.configure(cls, fieldbackground=field, foreground=color,
                                insertcolor=color, arrowcolor=color)
            style.configure("TCombobox", background=button,
                            selectbackground=button, selectforeground=color)
            # "clam" maps readonly comboboxes to a light gray fieldbackground,
            # which overrides the configured value for selectors like Geometry source.
            style.map("TCombobox",
                      fieldbackground=[("readonly", "focus", field),
                                       ("readonly", field),
                                       ("disabled", frame)],
                      foreground=[("readonly", "focus", color),
                                  ("readonly", color),
                                  ("disabled", frame)],
                      background=[("active", button), ("pressed", button)],
                      bordercolor=[("focus", button)])
            style.configure("Treeview", background=field, fieldbackground=field,
                            foreground=color)
            style.map("Treeview", background=[("selected", button)],
                      foreground=[("selected", color)])
        except tk.TclError:
            pass  # Some platform themes do not allow every style option.

        # ttk creates the combobox popdown as a plain Tk Listbox, so the ttk
        # style above cannot color it.  Set option defaults for future popdowns.
        for pattern, value in (
            ("*ComboboxPopdown*background", bg),
            ("*ComboboxPopdown*Listbox*background", field),
            ("*ComboboxPopdown*Listbox*foreground", color),
            ("*ComboboxPopdown*Listbox*selectBackground", button),
            ("*ComboboxPopdown*Listbox*selectForeground", color),
            ("*ComboboxPopdown*Listbox*activeBackground", button),
            ("*ComboboxPopdown*Listbox*highlightBackground", bg),
        ):
            self.root.option_add(pattern, value)

        self.root.configure(background=bg)
        self._configure_doc_tags()
        for widget in self._walk_widgets():
            try:
                wclass = widget.winfo_class()
                if wclass == "Text":
                    body_font = ("TkDefaultFont" if widget is getattr(self, "doc_text", None)
                                 else "TkFixedFont")
                    widget.configure(background=field, foreground=color,
                                     insertbackground=color, font=body_font)
                elif wclass == "Canvas":
                    widget.configure(background=bg)
                elif wclass == "TCombobox":
                    self._style_combobox_popdown(widget, bg, field, button, color)
            except tk.TclError:
                continue

        self._apply_preview_limits(values)
        self._apply_preview_style(values)
        self._apply_preview_visibility(bool(values.get("show_output_preview", True)))

    def _style_combobox_popdown(self, widget, bg: str, field: str,
                               button: str, color: str) -> None:
        """Recolor an already-created ttk combobox popdown, if it exists."""
        popdown = f"{widget}.popdown"
        try:
            if not self.root.tk.call("winfo", "exists", popdown):
                return
            self.root.tk.call(popdown, "configure", "-background", bg)
            listbox = f"{popdown}.f.l"
            if self.root.tk.call("winfo", "exists", listbox):
                for option, value in (
                    ("background", field),
                    ("foreground", color),
                    ("selectbackground", button),
                    ("selectforeground", color),
                    ("activebackground", button),
                    ("highlightbackground", bg),
                ):
                    self.root.tk.call(listbox, "configure", f"-{option}", value)
        except tk.TclError:
            pass

    def _apply_preview_visibility(self, show: bool) -> None:
        notebook = getattr(self, "notebook", None)
        tab = getattr(self, "preview_tab", None)
        if notebook is None or tab is None:
            return
        tabs = tuple(notebook.tabs())
        managed = str(tab) in tabs
        visible = managed and notebook.tab(tab, "state") != "hidden"
        if show and not visible:
            index = notebook.index(self.docs_tab)
            notebook.insert(index, tab, text="Output Preview")
            notebook.tab(tab, state="normal")
            preview = getattr(self, "output_preview", None)
            if preview is not None:
                preview.set_visible(True)
                self.root.after(0, preview.refresh)
        elif not show and visible:
            preview = getattr(self, "output_preview", None)
            if preview is not None:
                preview.set_visible(False)
            notebook.hide(tab)

    def _apply_settings_now(self):
        try:
            values = self._collect_settings_from_vars(strict=True)
        except ValueError as e:
            messagebox.showerror("Settings", str(e))
            return
        self.settings = values
        self._sync_settings_vars(values)
        self._apply_ui_settings(values)
        self._refresh_color_preview()
        self.status_var.set("Appearance settings applied (not saved yet).")

    def _save_settings(self):
        if not self._apply_settings_from_ui():
            return
        try:
            write_ui_settings(self.settings)
            self.status_var.set(f"Saved GUI appearance settings to {SETTINGS_FILE}")
            messagebox.showinfo("Settings", f"Saved GUI appearance settings to:\n{SETTINGS_FILE}")
        except Exception as e:
            messagebox.showerror("Settings", str(e))

    def _apply_settings_from_ui(self) -> bool:
        try:
            values = self._collect_settings_from_vars(strict=True)
        except ValueError as e:
            messagebox.showerror("Settings", str(e))
            return False
        self.settings = values
        self._sync_settings_vars(values)
        self._apply_ui_settings(values)
        self._refresh_color_preview()
        return True

    def _reset_settings(self):
        if not messagebox.askyesno(
            "Settings",
            "Reset GUI scale, text size, window size, text color, and dark mode to shipped defaults?",
        ):
            return
        values = dict(SHIPPED_SETTINGS)
        self.settings = values
        self._sync_settings_vars(values)
        self._apply_ui_settings(values)
        self._refresh_color_preview()
        try:
            write_ui_settings(values)
            self.status_var.set("Appearance settings reset to shipped defaults.")
        except Exception as e:
            messagebox.showerror("Settings", str(e))

    # ---- config/build helpers ----
    def _g(self, key: str) -> str:
        v = self.vars[key]
        return v.get() if isinstance(v, StringVar) else ("1" if v.get() else "0")

    def _gb(self, key: str) -> bool:
        v = self.vars[key]
        return bool(v.get()) if isinstance(v, BooleanVar) else v.get().strip().lower() in {"1", "true", "yes", "y"}

    def _sync_detector_fields(self, *_):
        """Enable the manual size fields only when auto sizing is disabled."""
        state = NORMAL if not self._gb("detector_auto_size") else DISABLED
        for key in ("detector_radius", "detector_length"):
            widget = getattr(self, "detector_widgets", {}).get(key)
            if widget is not None:
                widget.configure(state=state)

    def _bind_particle_estimate(self) -> None:
        for key in ("drum_radius", "drum_half_length", "spin_axis",
                    "bed_height", "bed_padding", "spacing"):
            var = self.vars.get(key)
            if var is not None:
                var.trace_add("write", self._update_particle_estimate)

    def _update_particle_estimate(self, *_):
        label = getattr(self, "particle_estimate_var", None)
        if label is None:
            return
        try:
            count = wiz.estimate_sph_particles(
                drum_radius=float(self._g("drum_radius")),
                drum_half_length=float(self._g("drum_half_length")),
                bed_height=float(self._g("bed_height")),
                spacing=float(self._g("spacing")),
                spin_axis=self._g("spin_axis") or "X",
                bed_padding=float(self._g("bed_padding")),
            )
        except (ValueError, TypeError, ZeroDivisionError, OverflowError):
            label.set("Estimated initial SPH particles: -- "
                      "(enter positive bed height, spacing, and drum dimensions)")
            return
        label.set(f"Estimated initial SPH particles: ~{count:,}  "
                  f"(padded bed volume / spacing^3; Chrono's lattice count may differ)")

    def _parse_active_box(self, s: str):
        parts = [p.strip() for p in s.replace(";", ",").split(",") if p.strip()]
        if len(parts) != 3:
            raise ValueError("Active box must be three comma-separated numbers X,Y,Z")
        vals = tuple(float(x) for x in parts)
        if min(vals) <= 0:
            raise ValueError("Active box values must be positive")
        return vals

    def _mesh_settings(self) -> dict:
        """Parse the current Geometry-page values used by STEP meshing.

        This is deliberately side-effect free so the mesh-preview tool can run
        the exact same converter command without creating or touching an
        active study output directory.
        """
        step_raw = self._g("step_file").strip()
        step_file = Path(step_raw).expanduser().resolve() if step_raw else None
        cad_axis = self._g("cad_axis") or "Y"
        if cad_axis == "auto" and step_file and step_file.is_file():
            cad_axis = wiz.detect_cad_axis(step_file)
        elif cad_axis == "auto":
            cad_axis = "Y"

        unit_scale = float(self._g("unit_scale") or 0.001)
        mesh_size = float(self._g("mesh_size") or 0.012)
        surface_faces = int(float(self._g("surface_target_faces") or 8000))
        surface_max_faces = int(float(self._g("surface_max_faces") or 256000))
        surface_fit_tolerance = float(self._g("surface_fit_tolerance") or 0.001)
        if surface_fit_tolerance < 0.0:
            raise ValueError("Exterior fit tolerance must be non-negative")
        surface_repair_order = self._g("surface_repair_order") or "none"
        min_solid_volume = float(self._g("min_solid_volume") or 0.0)
        hub_radius = float(self._g("hub_radius") or 0.0015)
        part_aware_simplify = self._gb("part_aware_simplify")
        part_aware_radius = float(self._g("part_aware_radius") or 0.05)
        part_aware_reduction = float(self._g("part_aware_reduction") or 0.75)
        part_aware_min_faces = max(0, int(float(self._g("part_aware_min_faces") or 500)))
        if part_aware_radius <= 0.0:
            raise ValueError("Interior coarsening radius must be positive")
        if not 0.0 <= part_aware_reduction < 1.0:
            raise ValueError("Interior reduction must be in [0, 1)")
        if part_aware_simplify and surface_repair_order == "after":
            raise ValueError(
                "Interior coarsening is incompatible with MeshFix order 'after'; "
                "use 'none' (recommended) or 'before'."
            )
        return {
            "geometry": self._g("geometry"),
            "step_file": step_file,
            "cad_axis": cad_axis,
            "unit_scale": unit_scale,
            "mesh_size": mesh_size,
            "surface_faces": surface_faces,
            "surface_max_faces": surface_max_faces,
            "surface_fit_tolerance": surface_fit_tolerance,
            "surface_repair_order": surface_repair_order,
            "min_solid_volume": min_solid_volume,
            "hub_radius": hub_radius,
            "part_aware_simplify": part_aware_simplify,
            "part_aware_radius": part_aware_radius,
            "part_aware_reduction": part_aware_reduction,
            "part_aware_min_faces": part_aware_min_faces,
        }

    def _mesh_command(self, prefix: Path, step_file: Path, mesh: dict) -> list[str]:
        """Build the exact STEP converter command used by a full study run."""
        cmd = [
            str(ROOT / "chrono-env" / "bin" / "python"),
            str(ROOT / "step_to_chrono_fea.py"),
            str(step_file),
            "--out-prefix", str(prefix),
            "--unit-scale", f"{mesh['unit_scale']:g}",
            "--mesh-size", f"{mesh['mesh_size']:g}",
            "--surface-target-faces", str(mesh["surface_faces"]),
            "--surface-max-faces", str(mesh["surface_max_faces"]),
            "--surface-fit-tolerance", f"{mesh['surface_fit_tolerance']:g}",
            "--min-solid-volume", f"{mesh['min_solid_volume']:g}",
            "--hub-radius", f"{mesh['hub_radius']:g}",
            "--axis", mesh["cad_axis"],
            # Keep TetGen's default radius-edge/dihedral quality bounds;
            # pYQ disables them and can create FEA-destabilizing slivers.
            "--time-stages",
        ]
        if mesh["part_aware_simplify"]:
            cmd.extend([
                "--part-aware-simplify",
                "--part-aware-radius", f"{mesh['part_aware_radius']:g}",
                "--part-aware-reduction", f"{mesh['part_aware_reduction']:g}",
                "--part-aware-min-faces", str(mesh["part_aware_min_faces"]),
            ])
        if mesh["surface_repair_order"] == "after":
            cmd.append("--repair-after-decimation")
        return cmd

    def _collect_cfg(self) -> dict:
        mesh = self._mesh_settings()
        geo = mesh["geometry"]
        out_dir = Path(self._g("output_dir")).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        step_file = mesh["step_file"]
        crm_obj = Path(self._g("crm_obj")).expanduser().resolve()
        fea_node = Path(self._g("fea_node")).expanduser().resolve()
        fea_ele = Path(self._g("fea_ele")).expanduser().resolve()
        cad_axis = mesh["cad_axis"]
        unit_scale = mesh["unit_scale"]
        mesh_size = mesh["mesh_size"]
        surface_faces = mesh["surface_faces"]
        surface_max_faces = mesh["surface_max_faces"]
        surface_fit_tolerance = mesh["surface_fit_tolerance"]
        surface_repair_order = mesh["surface_repair_order"]
        min_solid_volume = mesh["min_solid_volume"]
        hub_radius = mesh["hub_radius"]
        part_aware_simplify = mesh["part_aware_simplify"]
        part_aware_radius = mesh["part_aware_radius"]
        part_aware_reduction = mesh["part_aware_reduction"]
        part_aware_min_faces = mesh["part_aware_min_faces"]
        drum_radius = float(self._g("drum_radius") or 0.061)
        drum_half_length = float(self._g("drum_half_length") or 0.033)
        inner_radius = float(self._g("inner_radius") or 0.055)
        cap_raw = self._g("capacity_volume").strip()
        capacity_volume = float(cap_raw) if cap_raw else None
        detector_auto_size = self._gb("detector_auto_size")
        detector_radius = float(self._g("detector_radius") or drum_radius)
        detector_length = float(self._g("detector_length") or 2.0 * drum_half_length)
        if detector_radius <= 0.0 or detector_length <= 0.0:
            raise ValueError("Detector radius and length must be positive")

        mesh_steps = []
        if geo == "step":
            if not step_file or not step_file.is_file():
                raise ValueError(f"STEP file not found: {step_file}")
            mesh_dir = out_dir / "mesh"
            mesh_dir.mkdir(parents=True, exist_ok=True)
            prefix = mesh_dir / f"{step_file.stem}_fea"
            fea_node = Path(f"{prefix}.node")
            fea_ele = Path(f"{prefix}.ele")
            surface_stl = Path(f"{prefix}_surface.stl")
            crm_obj = Path(f"{prefix}_surface.obj")
            cmd = self._mesh_command(prefix, step_file, mesh)
            mesh_steps.append({"name": "generate-FEA-mesh", "command": cmd})
            mesh_steps.append({
                "name": "make-CRM-surface-OBJ",
                "command": [str(ROOT / "chrono-env" / "bin" / "python"),
                            str(ROOT / "stl_to_obj.py"), str(surface_stl)],
            })
        elif geo == "files":
            if not crm_obj.is_file():
                raise ValueError(f"CRM OBJ not found: {crm_obj}")
            if not fea_node.is_file():
                raise ValueError(f"FEA .node not found: {fea_node}")
            if not fea_ele.is_file():
                raise ValueError(f"FEA .ele not found: {fea_ele}")

        duration = float(self._g("duration"))
        fea_duration = float(self._g("fea_duration"))
        spacing = float(self._g("spacing"))
        d0_multiplier = float(self._g("d0_multiplier"))
        artificial_viscosity = float(self._g("artificial_viscosity") or 0.5)
        shifting_ppst_push = float(self._g("shifting_ppst_push") or 3.0)
        crm_dt_raw = self._g("crm_dt").strip().lower()
        if crm_dt_raw in {"", "auto"}:
            crm_dt = wiz.suggested_sph_dt(spacing, d0_multiplier)
        else:
            crm_dt = float(crm_dt_raw)
        crm_fps = float(self._g("crm_fps"))
        fea_fps = float(self._g("fea_fps"))
        loads_fps = float(self._g("loads_fps") or 20)
        particle_fps = float(self._g("particle_fps") or 5)
        particle_writers = max(1, int(float(self._g("particle_writers") or 4)))
        fea_dt = float(self._g("fea_dt"))
        bed_height = float(self._g("bed_height"))
        bed_padding = float(self._g("bed_padding") or wiz.BED_PADDING_RADIUS_FRACTION)
        if bed_padding < 0.0:
            raise ValueError("Bed padding fraction must be non-negative")
        active_box = self._parse_active_box(self._g("active_box"))
        settle_time = float(self._g("settle_time"))
        gravity = float(self._g("gravity"))
        if not math.isfinite(gravity):
            raise ValueError("Gravity Z must be finite")
        drum_gravity = self._gb("drum_gravity")
        descent_mode = self._g("descent_mode").strip().lower()
        if descent_mode not in {"kinematic", "force"}:
            raise ValueError("Descent mode must be kinematic or force")
        descent_speed = float(self._g("descent_speed"))
        descent_force_n = float(self._g("descent_force_n"))
        descent_force_ramp_s = float(self._g("descent_force_ramp_s"))
        if descent_mode == "force":
            if descent_force_n < 0.0:
                raise ValueError("Descent force must be non-negative")
            if descent_force_ramp_s < 0.0:
                raise ValueError("Descent force ramp cannot be negative")
        omega_mag = float(self._g("omega"))
        reverse_spin = self._gb("reverse_spin")
        omega = -abs(omega_mag) if reverse_spin else abs(omega_mag)
        spin_axis = self._g("spin_axis") or "X"
        solver = self._g("solver") or "pardiso-mkl"
        num_threads = int(float(self._g("num_threads") or 8))
        load_radius_fraction = float(self._g("load_radius_fraction") or 0.95)
        raw_stress_every = max(1, int(float(self._g("raw_stress_every") or 5)))
        export_workers = max(1, int(float(self._g("export_workers") or 4)))

        soil = {k: float(self._g(f"soil_{k}")) for k in wiz.SOIL_DEFAULTS}
        pla = {k: float(self._g(f"pla_{k}")) for k in wiz.PLA_DEFAULTS}

        cfg = {
            "study_name": self._g("study_name"),
            "output_dir": out_dir,
            "geometry": geo,
            "step_file": step_file,
            "crm_obj": crm_obj,
            "fea_node": fea_node,
            "fea_ele": fea_ele,
            "mesh_steps": mesh_steps,
            "geometry_defaults": wiz.geometry_defaults(None, auto=False),
            "auto_geometry": self._gb("auto_geometry"),
            "cad_axis": cad_axis,
            "unit_scale": unit_scale,
            "mesh_size": mesh_size,
            "surface_faces": surface_faces,
            "surface_max_faces": surface_max_faces,
            "surface_fit_tolerance": surface_fit_tolerance,
            "surface_repair_order": surface_repair_order,
            "hub_radius": hub_radius,
            "part_aware_simplify": bool(part_aware_simplify),
            "part_aware_radius": part_aware_radius,
            "part_aware_reduction": part_aware_reduction,
            "part_aware_min_faces": part_aware_min_faces,
            "drum_radius": drum_radius,
            "drum_half_length": drum_half_length,
            "min_solid_volume": min_solid_volume,
            "duration": duration,
            "fea_duration": fea_duration,
            "spacing": spacing,
            "d0_multiplier": d0_multiplier,
            "artificial_viscosity": artificial_viscosity,
            "shifting_ppst_push": shifting_ppst_push,
            "crm_dt": crm_dt,
            "auto_crm_dt": wiz.suggested_sph_dt(spacing, d0_multiplier),
            "crm_fps": crm_fps,
            "fea_fps": fea_fps,
            "loads_fps": loads_fps,
            "particle_fps": particle_fps,
            "particle_writers": particle_writers,
            "bed_height": bed_height,
            "bed_padding": bed_padding,
            "active_box": active_box,
            "settle_time": settle_time,
            "gravity": gravity,
            "drum_gravity": drum_gravity,
            "descent_mode": descent_mode,
            "descent_speed": descent_speed,
            "descent_force_n": descent_force_n,
            "descent_force_ramp_s": descent_force_ramp_s,
            "omega": omega,
            "reverse_spin": reverse_spin,
            "spin_axis": spin_axis,
            "inner_radius": inner_radius,
            "capacity_volume": capacity_volume,
            "detector_auto_size": detector_auto_size,
            "detector_radius": detector_radius,
            "detector_length": detector_length,
            "soil": soil,
            "fea_dt": fea_dt,
            "solver": solver,
            "num_threads": num_threads,
            "load_radius_fraction": load_radius_fraction,
            "raw_stress_every": raw_stress_every,
            "export_workers": export_workers,
            "pla": pla,
            "save_particles": self._gb("save_particles"),
            "fea_export_vtk": self._gb("fea_export_vtk"),
            "fea_vtk_stride": max(1, int(float(self._g("fea_vtk_stride") or 1))),
            "export_vtk": self._gb("export_vtk"),
            "paraview_stride": max(1, int(float(self._g("paraview_stride") or 1))),
            "paraview_particle_stride": max(0, int(float(self._g("paraview_particle_stride") or 0))),
            "paraview_particle_render_stride": max(1, int(float(self._g("paraview_particle_render_stride") or 1))),
            "export_workers": export_workers,
            "open_results": self._gb("open_results"),
            "open_paraview": self._gb("open_paraview"),
            "no_open": False,
            "dry_run": False,
        }
        return cfg

    def _chrono_env_python(self) -> str | None:
        cand = ROOT / "chrono-env" / "bin" / "python"
        return str(cand) if cand.is_file() else None

    def _measure_step_via_chrono_env(self, step_file: Path, axis: str, unit_scale: float):
        """Measure STEP geometry using chrono-env Python (for OCP), if available."""
        py = self._chrono_env_python()
        if not py:
            return None
        code = r"""
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
step = Path(sys.argv[2])
axis = sys.argv[3]
unit_scale = float(sys.argv[4])
sys.path.insert(0, str(root))
sys.path.insert(0, str(root / 'crm'))
import study_wizard as wiz
if axis == 'auto':
    axis = wiz.detect_cad_axis(step)
m = wiz.measure_geometry(step, is_step=True, axis=axis, unit_scale=unit_scale)
print(json.dumps({'axis': axis, 'measured': m}, default=float))
"""
        try:
            r = subprocess.run(
                [py, "-c", code, str(ROOT), str(step_file), axis, str(unit_scale)],
                cwd=ROOT, capture_output=True, text=True, timeout=180,
            )
            if r.returncode == 0 and r.stdout.strip():
                data = json.loads(r.stdout.strip().splitlines()[-1])
                if isinstance(data, dict):
                    return data
        except Exception:
            return None
        return None

    def _apply_geo_defaults(self):
        try:
            geo = self._g("geometry")
            cad_axis = self._g("cad_axis") or "Y"
            unit_scale = float(self._g("unit_scale") or 0.001)
            measured = None
            if geo == "step":
                step_file = Path(self._g("step_file")).expanduser().resolve()
                if not step_file.is_file():
                    messagebox.showwarning("Geometry", f"STEP file not found:\n{step_file}")
                    return
                # Try in-process first (works when running under chrono-env or if OCP is installed)
                if cad_axis == "auto":
                    try:
                        cad_axis = wiz.detect_cad_axis(step_file)
                        self.vars["cad_axis"].set(cad_axis)
                    except Exception:
                        pass
                if step_file.is_file():
                    try:
                        measured = wiz.measure_geometry(
                            step_file, is_step=True, axis=cad_axis, unit_scale=unit_scale
                        )
                    except Exception:
                        measured = None
                # Fallback: use chrono-env Python for OCP/STEP measurement (allows GUI to run
                # under system Python for proper display scaling while using project deps).
                if not measured:
                    data = self._measure_step_via_chrono_env(step_file, cad_axis, unit_scale)
                    if isinstance(data, dict):
                        if data.get("axis"):
                            cad_axis = str(data["axis"])
                            self.vars["cad_axis"].set(cad_axis)
                        measured = data.get("measured")
                if not measured:
                    try:
                        import OCP  # noqa: F401
                        ocp_ok = True
                    except Exception:
                        ocp_ok = False
                    chrono_py = self._chrono_env_python()
                    if not ocp_ok and not chrono_py:
                        messagebox.showwarning(
                            "Geometry",
                            "Could not measure STEP geometry because OCP is not available in the "
                            "current Python and chrono-env/bin/python was not found.\n\n"
                            f"STEP file: {step_file}\n"
                            f"Python: {sys.executable}\n\n"
                            "Install/use the project chrono-env (with OCP/cadquery-ocp) or run with a Python that has OCP."
                        )
                    elif not ocp_ok and chrono_py:
                        messagebox.showwarning(
                            "Geometry",
                            "Could not measure STEP geometry. OCP is not available in the current "
                            "Python and the chrono-env fallback also failed.\n\n"
                            f"STEP file: {step_file}\n"
                            f"GUI Python: {sys.executable}\n"
                            f"chrono-env: {chrono_py}\n"
                            f"CAD axis: {cad_axis}, unit scale: {unit_scale:g}"
                        )
                    else:
                        messagebox.showwarning(
                            "Geometry",
                            "Could not measure STEP geometry (file may be unreadable or unsupported).\n\n"
                            f"STEP file: {step_file}\n"
                            f"CAD axis: {cad_axis}, unit scale: {unit_scale:g}"
                        )
                    return
            else:
                crm_obj = Path(self._g("crm_obj")).expanduser().resolve()
                fea_node = Path(self._g("fea_node")).expanduser().resolve()
                if not crm_obj.is_file():
                    messagebox.showwarning("Geometry", f"CRM surface OBJ not found:\n{crm_obj}")
                    return
                try:
                    measured = wiz.measure_geometry(
                        crm_obj, is_step=False, axis="Z", unit_scale=1.0,
                        metadata=wiz.read_mesh_metadata(fea_node)
                    )
                except Exception:
                    measured = None
                if not measured:
                    messagebox.showwarning(
                        "Geometry",
                        "Could not measure mesh geometry (file may be unreadable or dependencies "
                        "such as trimesh may be unavailable in this Python).\n\n"
                        f"Mesh: {crm_obj}\nPython: {sys.executable}"
                    )
                    return
            gd = wiz.geometry_defaults(measured, auto=True)
            for k in ["mesh_size", "surface_target_faces", "surface_fit_tolerance",
                      "min_solid_volume", "hub_radius",
                      "drum_radius", "drum_half_length", "inner_radius", "bed_height",
                      "descent_speed", "spacing"]:
                if k in gd and k in self.vars:
                    self.vars[k].set(f"{gd[k]:g}")
            if "active_box" in gd:
                self.vars["active_box"].set(",".join(f"{v:g}" for v in gd["active_box"]))
            if self._gb("detector_auto_size"):
                self.vars["detector_radius"].set(f"{gd['drum_radius']:g}")
                self.vars["detector_length"].set(f"{2.0 * gd['drum_half_length']:g}")
            self.vars["fea_duration"].set(self.vars["duration"].get())
            self.status_var.set("Applied CAD/mesh-derived defaults (you can still edit them).")
            messagebox.showinfo("Geometry", "Applied geometry-derived defaults. Review and adjust any field before running.")
        except Exception as e:
            messagebox.showerror("Geometry", str(e))

    def _load_config(self):
        path = filedialog.askopenfilename(initialdir=str(ROOT), filetypes=[("JSON", "*.json"), ("All", "*")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self._apply_config(data)
            self.status_var.set(f"Loaded config: {path}")
        except Exception as e:
            messagebox.showerror("Load config", str(e))

    def _apply_config(self, data: dict):
        def setv(k, v):
            if k in self.vars and v is not None:
                if isinstance(self.vars[k], BooleanVar):
                    self.vars[k].set(bool(v))
                else:
                    self.vars[k].set(str(v))
        for k in ["study_name", "geometry", "cad_axis", "surface_repair_order", "spin_axis", "solver", "crm_dt", "descent_mode"]:
            setv(k, data.get(k))
        for k in ["unit_scale", "mesh_size", "surface_target_faces", "surface_faces", "surface_max_faces",
                  "surface_fit_tolerance", "min_solid_volume", "hub_radius",
                  "part_aware_radius", "part_aware_reduction", "part_aware_min_faces",
                  "drum_radius", "drum_half_length", "inner_radius", "capacity_volume",
                  "detector_radius", "detector_length", "duration",
                  "fea_duration", "spacing", "d0_multiplier", "artificial_viscosity",
                   "shifting_ppst_push", "crm_fps", "fea_fps", "fea_dt",
                  "bed_height", "bed_padding", "settle_time", "gravity", "descent_speed", "descent_force_n", "descent_force_ramp_s", "omega", "num_threads",
                  "load_radius_fraction", "raw_stress_every", "export_workers",
                  "fea_vtk_stride", "paraview_stride", "paraview_particle_stride",
                  "paraview_particle_render_stride"]:
            setv(k, data.get(k))
        for k in ["reverse_spin", "drum_gravity", "save_particles", "fea_export_vtk", "export_vtk", "open_results", "open_paraview", "auto_geometry",
                  "detector_auto_size", "part_aware_simplify"]:
            if k in data:
                setv(k, data.get(k))
        for k, v in {"output_dir": data.get("output_dir"), "step_file": data.get("step_file"),
                     "crm_obj": data.get("crm_obj"), "fea_node": data.get("fea_node"),
                     "fea_ele": data.get("fea_ele")}.items():
            if v:
                setv(k, v)
        self._sync_detector_fields()
        ab = data.get("active_box")
        if isinstance(ab, (list, tuple)) and len(ab) == 3:
            setv("active_box", ",".join(f"{float(x):g}" for x in ab))
        soil = data.get("soil") or {}
        for k in wiz.SOIL_DEFAULTS:
            if k in soil:
                setv(f"soil_{k}", soil[k])
        pla = data.get("pla") or {}
        for k in wiz.PLA_DEFAULTS:
            if k in pla:
                setv(f"pla_{k}", pla[k])

    def _refresh_preview(self):
        try:
            cfg = self._collect_cfg()
            steps = wiz.build_commands(cfg)
            wiz.write_reproducibility_files(cfg, steps)
            estimate = wiz.estimate_sph_particles(
                drum_radius=cfg["drum_radius"],
                drum_half_length=cfg["drum_half_length"],
                bed_height=cfg["bed_height"],
                spacing=cfg["spacing"],
                spin_axis=cfg["spin_axis"],
                bed_padding=cfg["bed_padding"],
            )
            lines = [
                f"Output: {cfg['output_dir']}",
                f"Config: {cfg['output_dir'] / 'study_config.json'}",
                f"Estimated initial SPH particles: ~{estimate:,} "
                "(padded bed volume / spacing^3; approximate)",
                "",
            ]
            for s in steps:
                lines.append(f"[{s['name']}]")
                lines.append(shlex.join(s['command']))
                lines.append("")
            self._set_text(self.cmd_text, "\n".join(lines))
            self.status_var.set("Preview/config updated. You can still go back and edit before running.")
        except Exception as e:
            messagebox.showerror("Preview", str(e))

    def _save_only(self):
        try:
            self._refresh_preview()
            messagebox.showinfo("Saved", "study_config.json and commands.sh were written to the output directory.")
        except Exception as e:
            messagebox.showerror("Save", str(e))

    # ---- run ----
    def _run_active(self) -> bool:
        """True while a pipeline thread owns a process or is starting one."""
        return self.proc is not None

    def _run_study(self):
        if self._run_active():
            messagebox.showwarning("Run", "A process is already running.")
            return
        try:
            cfg = self._collect_cfg()
            steps = wiz.build_commands(cfg)
            wiz.write_reproducibility_files(cfg, steps)
            self._refresh_preview()
            self._start_run(cfg, steps, "Running study...")
        except Exception as e:
            messagebox.showerror("Run", str(e))

    def _run_oneway_fea_only(self):
        """Run just the one-way FEA step against an existing CRM study folder."""
        if self._run_active():
            messagebox.showwarning("Run", "A process is already running.")
            return
        try:
            cfg = self._collect_cfg()
            out_dir = Path(cfg["output_dir"]).expanduser().resolve()
            loads = out_dir / "crm" / "soil_loads.csv"
            if not loads.is_file():
                raise ValueError(
                    f"No CRM load history at {loads}. Set Output directory to a project "
                    "that already contains crm/soil_loads.csv."
                )
            if not messagebox.askyesno(
                "One-way FEA only",
                f"Run only the one-way FEA replay using CRM data in:\n{out_dir}\n\n"
                "Mesh generation and the rigid-CRM stage will be skipped.\n"
                "CRM-derived motion settings are read from crm/config.json when present.",
            ):
                return
            steps = wiz.build_oneway_fea_only_commands(cfg)
            wiz.write_reproducibility_files(
                cfg, steps,
                config_name="oneway_fea_config.json",
                commands_name="oneway_fea_commands.sh",
            )
            lines = [
                f"Output: {out_dir}",
                f"Config: {out_dir / 'oneway_fea_config.json'}",
                f"Commands: {out_dir / 'oneway_fea_commands.sh'}",
                "",
            ]
            for step in steps:
                lines.append(f"[{step['name']}]")
                lines.append(shlex.join(step["command"]))
                lines.append("")
            self._set_text(self.cmd_text, "\n".join(lines))
            self._start_run(cfg, steps, "Running one-way FEA only (existing CRM data)...")
        except Exception as e:
            messagebox.showerror("One-way FEA only", str(e))

    def _start_run(self, cfg, steps, status: str):
        run_dir = str(Path(cfg["output_dir"]).expanduser().resolve())
        if hasattr(self, "tools_study_dir"):
            self.tools_study_dir.set(run_dir)
        if hasattr(self, "output_preview"):
            self.output_preview.set_directory(run_dir)
        self._set_text(self.log_text, "")
        self.step_nb.select(len(self.pages) - 1)
        self._stop_requested.clear()
        self._skip_requested.clear()
        self.proc = "starting"  # marker until the worker publishes the Popen
        self.status_var.set(status)
        threading.Thread(target=self._run_thread, args=(cfg, steps), daemon=True).start()

    def _run_thread(self, cfg, steps):
        try:
            started = time.perf_counter()
            for step in steps:
                if self._stop_requested.is_set():
                    self.log_queue.put(("line", "\n[stop requested; aborting]\n"))
                    self.log_queue.put(("done", "stopped"))
                    return
                # A skip applies to the step that was active when requested.
                self._skip_requested.clear()
                self.log_queue.put(("line", f"\n[{step['name']}] {shlex.join(step['command'])}\n"))
                log_path = Path(step["log"])
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with log_path.open("w", encoding="utf-8") as log:
                    # Give every step its own session/process group.  The CRM and
                    # export drivers spawn worker processes that inherit stdout;
                    # on skip/stop we need to signal the whole group so those
                    # writers die and _run_thread's stdout read gets EOF.
                    popen_kwargs = {"start_new_session": True} if os.name != "nt" else {}
                    proc = subprocess.Popen(step["command"], cwd=ROOT, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, bufsize=1,
                                            **popen_kwargs)
                    self.proc = proc
                    assert proc.stdout is not None
                    try:
                        for line in proc.stdout:
                            log.write(line)
                            log.flush()
                            self.log_queue.put(("line", line))
                    finally:
                        proc.stdout.close()
                    rc = proc.wait()
                if self._stop_requested.is_set():
                    self.log_queue.put(("line", f"[{step['name']}] stopped by user\n"))
                    self.log_queue.put(("done", "stopped"))
                    return
                if self._skip_requested.is_set():
                    self.log_queue.put(("line", f"[{step['name']}] skipped by user\n"))
                    self.log_queue.put(("step_skipped", step["name"]))
                    continue
                if rc != 0:
                    self.log_queue.put(("line", f"[{step['name']}] FAILED (exit {rc})\n"))
                    self.log_queue.put(("done", f"failed:{step['name']}"))
                    return
                self.log_queue.put(("line", f"[{step['name']}] complete\n"))
                self.log_queue.put(("step_done", step["name"]))
            # post results (capture summary text)
            self.log_queue.put(("line", f"\nTotal wall time: {time.perf_counter()-started:.1f} s\n"))
            self.log_queue.put(("done", "ok"))
        except Exception as e:
            self.log_queue.put(("line", f"\nERROR: {e}\n"))
            self.log_queue.put(("done", "error"))

    def _step_process_alive(self, proc: subprocess.Popen) -> bool:
        """True while the step (or any process holding its stdout) is alive.

        A step's parent can exit while its worker children keep the inherited
        stdout pipe open and block the run thread.  ``poll()`` only sees the
        parent, so on POSIX check the whole process group instead.
        """
        if os.name != "nt":
            try:
                os.killpg(proc.pid, 0)
                return True
            except (ProcessLookupError, PermissionError, OSError):
                return False
        return proc.poll() is None

    def _terminate_step_process(self, proc: subprocess.Popen) -> None:
        """Terminate a step and its descendants, not just the parent PID.

        Each step runs in its own session (see ``_run_thread``), so on POSIX the
        step and every worker it spawned share a process group.  Signalling only
        the parent leaves CRM/export worker processes holding the inherited
        stdout pipe open; the run thread then blocks in ``for line in stdout``
        and never advances to the next stage.  Killing the group closes the
        pipe so the pipeline can continue.
        """
        try:
            if os.name != "nt":
                os.killpg(proc.pid, signal.SIGTERM)
                return
        except (ProcessLookupError, PermissionError, OSError):
            # Group already gone (or unsupported): fall back to the direct PID.
            pass
        try:
            proc.terminate()
        except Exception:
            pass

    def _stop(self):
        """Terminate the active command and abort the remaining steps."""
        if self.proc is None:
            return
        self._stop_requested.set()
        p = self.proc
        if isinstance(p, subprocess.Popen) and self._step_process_alive(p):
            self._terminate_step_process(p)
        self.log_queue.put(("line", "\n[stop requested]\n"))

    def _skip_step(self):
        """Terminate only the active command; the remaining steps still run."""
        p = self.proc
        if not isinstance(p, subprocess.Popen) or not self._step_process_alive(p):
            messagebox.showinfo("Skip", "No step is currently running.")
            return
        self._skip_requested.set()
        self._terminate_step_process(p)
        self.log_queue.put(("line", "\n[skip requested; continuing with next step]\n"))

    def _drain_log(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "line":
                    self.log_text.insert(END, str(payload))
                    self.log_text.see(END)
                elif kind == "step_done":
                    self._refresh_output_preview()
                elif kind == "step_skipped":
                    self.status_var.set(f"Skipped {payload}; continuing with next step.")
                elif kind == "done":
                    status = str(payload)
                    self.proc = None
                    if status == "ok":
                        self.status_var.set("Study completed. See output folder and ParaView export if enabled.")
                        try:
                            cfg = self._collect_cfg()
                            # print summary paths in log
                            out_dir = Path(cfg["output_dir"]).expanduser().resolve()
                            self.log_text.insert(END, f"\nResults folder: {out_dir}\n")
                            if (out_dir / "paraview").is_dir():
                                self.log_text.insert(
                                    END,
                                    f"ParaView 3D: QT_QPA_PLATFORM=xcb PYCHRONO_EXPORT_DIR={out_dir/'paraview'} "
                                    f"paraview --script={ROOT/'view_study_paraview.py'}\n",
                                )
                                if cfg.get("open_paraview"):
                                    show_fea_charts = bool(
                                        getattr(self, "tools_show_fea_charts", None)
                                        and self.tools_show_fea_charts.get()
                                    )
                                    if wiz.launch_paraview_viewer(
                                        ROOT / "view_study_paraview.py",
                                        out_dir / "paraview",
                                        out_dir,
                                        show_fea_charts=show_fea_charts,
                                    ):
                                        self.log_text.insert(END, "Opened ParaView visualization.\n")
                                    else:
                                        self.log_text.insert(
                                            END,
                                            "Could not launch ParaView automatically; run the command above.\n",
                                        )
                            self.log_text.see(END)
                            self._refresh_output_preview()
                        except Exception:
                            pass
                    elif status == "stopped":
                        self.status_var.set("Study stopped by user. Check logs.")
                    else:
                        self.status_var.set(f"Study stopped ({status}). Check logs.")
        except queue.Empty:
            pass
        self.root.after(120, self._drain_log)

    def _drain_tools_log(self):
        try:
            while True:
                kind, payload = self.tools_queue.get_nowait()
                if kind == "line":
                    text = str(payload)
                    if not text.endswith("\n"):
                        text += "\n"
                    self.tools_log.insert(END, text)
                    self.tools_log.see(END)
                elif kind == "done":
                    if isinstance(payload, tuple) and len(payload) == 2:
                        label, result = payload
                        if isinstance(result, int):
                            self._tool_log(f"[{label} exited {result}]")
                            if label == "export":
                                self.status_var.set(
                                    f"ParaView export exited {result}. "
                                    "Refreshing output preview."
                                )
                                try:
                                    self._refresh_output_preview()
                                except Exception:
                                    pass
                            elif label == "mesh-preview":
                                if result == 0:
                                    self.status_var.set(
                                        "Mesh preview complete; opening STEP-vs-mesh comparison."
                                    )
                                    try:
                                        self._launch_mesh_comparison()
                                    except Exception as exc:
                                        self._tool_log(f"[comparison error] {exc}")
                                else:
                                    self.status_var.set("Mesh preview failed. See Tools log.")
                        else:
                            self._tool_log(f"[{label} {result}]")
                    else:
                        self._tool_log(str(payload))
        except queue.Empty:
            pass
        self.root.after(120, self._drain_tools_log)

    # ---- tools ----
    def _tool_log(self, msg: str):
        if hasattr(self, "tools_log"):
            self.tools_log.insert(END, msg + "\n")
            self.tools_log.see(END)

    def _run_tool_cmd(self, cmd, cwd=None, stream: bool = False, label: str = "export"):
        self._tool_log("$ " + shlex.join(cmd))
        try:
            p = subprocess.Popen(cmd, cwd=cwd or ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if stream:
                # Keep the GUI responsive while a long tool streams progress.
                # Completion is reported through tools_queue, not this return.
                threading.Thread(target=self._watch_tool_process,
                                 args=(p, label), daemon=True).start()
                return None
            out, _ = p.communicate(timeout=600)
            if out:
                self.tools_log.insert(END, out[-8000:] + ("\n" if not out.endswith("\n") else ""))
                self.tools_log.see(END)
            self._tool_log(f"[exit {p.returncode}]")
            return p.returncode
        except Exception as e:
            self._tool_log(f"[error] {e}")
            return 1

    def _tool_export(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        stride = max(1, int(float(self.tools_stride.get() or 1)))
        particle_stride = max(0, int(float(self.tools_particle_stride.get() or 0)))
        particle_render_stride = max(1, int(float(self.tools_particle_render_stride.get() or 1)))
        workers = max(1, int(float(self.tools_workers.get() or 4)))
        cmd = [py_exec(), str(ROOT / "export_study_vtk.py"), "--study-dir", str(d),
               "--stride", str(stride), "--workers", str(workers),
               "--particle-stride", str(particle_stride),
               "--particle-render-stride", str(particle_render_stride)]
        self._tool_log(f"# exporting {workers} frame worker process(es) "
                       f"(stride {stride}, particle frame stride "
                       f"{particle_stride or stride}, particle render stride "
                       f"{particle_render_stride}, columnar CSV parsing)")
        self._run_tool_cmd(cmd, stream=True)

    def _watch_tool_process(self, proc: subprocess.Popen, label: str):
        """Stream a long-lived tool's output into the tools log."""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                self.tools_queue.put(("line", line))
            self.tools_queue.put(("done", (label, proc.wait())))
        except Exception as exc:
            self.tools_queue.put(("done", (label, f"log error: {exc}")))

    def _tool_detector_sizer(self):
        """Open the live 3D STEP + detector-cylinder size adjuster."""
        proc = getattr(self, "_detector_sizer_proc", None)
        if proc is not None and proc.poll() is None:
            self._tool_log("# Detector sizer is already running.")
            return
        try:
            mesh = self._mesh_settings()
            step_file = mesh.get("step_file")
            if step_file is not None and not step_file.is_file():
                step_file = None
            crm_obj = Path(self._g("crm_obj")).expanduser().resolve()
            surface_file = crm_obj if crm_obj.is_file() else None
            fea_node = Path(self._g("fea_node")).expanduser().resolve()
            if not fea_node.is_file():
                fea_node = None
            if step_file is None and surface_file is None and fea_node is None:
                raise ValueError(
                    "3D sizer needs a STEP file, CRM surface OBJ, or FEA .node with "
                    "a recorded surface"
                )

            if self._gb("detector_auto_size"):
                # Show the cylinder that will actually be used in auto mode.
                radius = float(self._g("drum_radius"))
                length = 2.0 * float(self._g("drum_half_length"))
            else:
                radius = float(self._g("detector_radius")
                               or self._g("drum_radius"))
                length = float(self._g("detector_length")
                               or 2.0 * float(self._g("drum_half_length")))
            spacing = float(self._g("spacing"))
            if radius <= 0.0 or length <= 0.0 or spacing <= 0.0:
                raise ValueError("Detector radius, length, and SPH spacing must be positive")
        except Exception as exc:
            self._tool_log(f"[detector sizer error] {exc}")
            return

        scratch = Path(tempfile.mkdtemp(prefix="detector_sizer_"))
        result_path = scratch / "detector_size.json"
        cmd = build_detector_sizer_command(
            python=py_exec(),
            root=ROOT,
            step_file=step_file,
            surface_file=surface_file,
            fea_node=fea_node,
            radius=radius,
            length=length,
            spacing=spacing,
            cad_axis=str(mesh.get("cad_axis") or "Y"),
            unit_scale=float(mesh.get("unit_scale") or 0.001),
            result_json=result_path,
            show=True,
        )
        self._tool_log("$ " + shlex.join(cmd))
        env = os.environ.copy()
        env.setdefault("QT_QPA_PLATFORM", "xcb")
        try:
            self._detector_sizer_proc = subprocess.Popen(cmd, cwd=ROOT, env=env)
        except Exception as exc:
            self._tool_log(f"[detector sizer error] {exc}")
            return
        self._detector_result_json = result_path
        self._detector_last_values = None
        self._tool_log("# 3D detector sizer opened; slider values update the GUI live.")
        self.root.after(250, self._poll_detector_sizer)

    def _poll_detector_sizer(self):
        proc = getattr(self, "_detector_sizer_proc", None)
        if proc is None:
            return
        result = read_detector_result(getattr(self, "_detector_result_json", None))
        values = None
        if result is not None and result["dirty"]:
            values = (result["radius"], result["length"])
        if values is not None and values != getattr(self, "_detector_last_values", None):
            self.vars["detector_auto_size"].set(False)
            self.vars["detector_radius"].set(f"{values[0]:.6g}")
            self.vars["detector_length"].set(f"{values[1]:.6g}")
            self._sync_detector_fields()
            self._detector_last_values = values
            self.status_var.set(
                f"Detector cylinder: R={values[0]:.5g} m, L={values[1]:.5g} m"
            )
        rc = proc.poll()
        if rc is None:
            self.root.after(250, self._poll_detector_sizer)
            return
        self._detector_sizer_proc = None
        self._tool_log(f"# 3D detector sizer closed (exit {rc}).")
        if values is not None:
            self.status_var.set(
                f"Detector cylinder set to R={values[0]:.5g} m, L={values[1]:.5g} m"
            )

    def _tool_mesh_preview(self):
        """Run the full-run STEP mesher into a scratch dir, then compare it to CAD."""
        try:
            mesh = self._mesh_settings()
        except Exception as exc:
            self._tool_log(f"[mesh preview error] {exc}")
            return
        if mesh["geometry"] != "step":
            self._tool_log("# Mesh preview needs Geometry source = 'step' on the Geometry page.")
            return
        step_file = mesh["step_file"]
        if not step_file or not step_file.is_file():
            self._tool_log(f"[mesh preview error] STEP file not found: {step_file}")
            return

        preview_root = ROOT / "geometry_checks" / "mesh_preview"
        prefix = preview_root / f"{step_file.stem}_fea"
        prefix.parent.mkdir(parents=True, exist_ok=True)
        self._mesh_preview_step = step_file
        self._mesh_preview_node = Path(f"{prefix}.node")
        self._mesh_preview_axis = mesh["cad_axis"]

        cmd = self._mesh_command(prefix, step_file, mesh)
        self._tool_log("# Mesh preview uses the exact full-run STEP mesher; "
                       f"outputs go to {prefix.parent}")
        self._run_tool_cmd(cmd, cwd=ROOT, stream=True, label="mesh-preview")

    def _launch_mesh_comparison(self):
        step_file = getattr(self, "_mesh_preview_step", None)
        node_file = getattr(self, "_mesh_preview_node", None)
        if not step_file or not node_file:
            self._tool_log("[comparison error] no mesh preview result to compare")
            return
        if not node_file.is_file():
            self._tool_log(f"[comparison error] mesher did not write {node_file}")
            return
        threads = max(0, int(float(self._g("num_threads") or 8)))
        cmd = [py_exec(), str(ROOT / "view_geometry.py"),
               "--step", str(step_file),
               "--fea-node", str(node_file),
               "--source-axis", str(getattr(self, "_mesh_preview_axis", "Y")),
               "--threads", str(threads),
               "--show"]
        self._tool_log("$ " + shlex.join(cmd))
        try:
            subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL,
                             stderr=subprocess.STDOUT)
            self._tool_log("[launched STEP-vs-mesh comparison]")
        except Exception as exc:
            self._tool_log(f"[comparison error] {exc}")

    def _tool_paraview(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        pdir = d / "paraview" if (d / "paraview").is_dir() else d
        script = str(ROOT / "view_study_paraview.py")
        show_fea_3d = bool(getattr(self, "tools_show_fea_3d", None) and self.tools_show_fea_3d.get())
        show_fea_charts = bool(getattr(self, "tools_show_fea_charts", None)
                               and self.tools_show_fea_charts.get())
        env = os.environ.copy()
        if show_fea_charts:
            env["PYCHRONO_FEA_CHARTS"] = "1"
        else:
            # An unchecked GUI toggle is authoritative: do not let an inherited
            # shell setting silently enable the one-way history charts.
            env.pop("PYCHRONO_FEA_CHARTS", None)
        env["PYCHRONO_EXPORT_DIR"] = str(pdir)
        env["PYCHRONO_STUDY_DIR"] = str(d)
        # ParaView's --script runner does not define __file__; the viewer uses
        # these to locate its local fea_summary helper.
        env["PYCHRONO_SCRIPT_DIR"] = str(ROOT)
        env["PYCHRONO_ROOT"] = str(ROOT)
        chart_state = "enabled by the Tools chart toggle" if show_fea_charts else \
                      "hidden by default (check the Tools chart toggle to opt in)"
        self._tool_log(f"# one-way FEA scalar/deformation history charts: {chart_state}; "
                       "--no-fea suppresses all FEA views")

        # Make invalid/cancelled 3D FEA exports explicit before the viewer opens.
        manifest_path = pdir / "export_manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if not isinstance(manifest, dict):
                manifest = {}
        except (OSError, json.JSONDecodeError, UnicodeError):
            manifest = {}
        if manifest:
            if manifest.get("has_fea_summary"):
                self._tool_log("# FEA scalar history: available from fea_summary.csv "
                               "(displayed only with the chart opt-in)")
            else:
                self._tool_log("# FEA scalar history: no summary found")
        # The manifest can be stale.  Let the viewer validate the actual PVD
        # and frame files so a usable partial run still loads from the GUI.
        fea_pvd, fea_status = pvview.find_valid_fea_pvd(pdir, manifest)
        if fea_pvd is not None:
            self._tool_log(f"# 3D FEA VTK: loadable source found ({fea_status})")
        elif show_fea_3d:
            self._tool_log(f"# 3D FEA VTK requested but unavailable ({fea_status}); "
                           "scalar charts and CRM still load")
        elif manifest:
            self._tool_log(f"# 3D FEA VTK: unavailable ({fea_status}); "
                           "scalar charts and CRM still load")

        def env_number(name: str, text: str, default: float, low: float | None = None,
                       high: float | None = None):
            try:
                value = float(text)
            except (TypeError, ValueError):
                value = default
            if low is not None:
                value = max(low, value)
            if high is not None:
                value = min(high, value)
            env[name] = f"{value:g}"

        env["PYCHRONO_CAMERA_ORBIT"] = "1" if self.tools_camera_orbit.get() else "0"
        env["PYCHRONO_WITH_FEA"] = "1" if show_fea_3d else "0"
        if show_fea_3d:
            self._tool_log("# 3D FEA stress overlay requested; a validated multi-frame PVD will load")
        env_number("PYCHRONO_ORBIT_SPEED", self.tools_orbit_speed.get(), 0.10)
        env_number("PYCHRONO_ORBIT_RADIUS", self.tools_orbit_radius.get(), 0.0, low=0.0)
        env_number("PYCHRONO_ORBIT_INCLINATION", self.tools_orbit_inclination.get(), 20.0,
                   low=-80.0, high=80.0)
        start_angle = self.tools_orbit_start_angle.get().strip()
        if start_angle:
            env_number("PYCHRONO_ORBIT_START_ANGLE", start_angle, 0.0)
        else:
            env.pop("PYCHRONO_ORBIT_START_ANGLE", None)
        env_number("PYCHRONO_ORBIT_KEYFRAMES", self.tools_orbit_keyframes.get(), 0, low=0)
        # ParaView 6.2 can abort on Wayland; X11/XWayland is the stable backend here.
        env["QT_QPA_PLATFORM"] = os.environ.get("PYCHRONO_PARAVIEW_QT_PLATFORM", "xcb")

        # The full ParaView GUI stays open after --script finishes; pvpython
        # needs the viewer's Interact() loop to avoid exiting immediately.
        paraview_bin = shutil.which("paraview")
        if paraview_bin:
            env["PYCHRONO_PARAVIEW_GUI"] = "1"
            cmd = [paraview_bin, f"--script={script}"]
            self._tool_log("$ " + shlex.join(cmd))
            self._tool_log(f"# PYCHRONO_EXPORT_DIR={pdir} QT_QPA_PLATFORM={env['QT_QPA_PLATFORM']}")
            try:
                proc = subprocess.Popen(
                    cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1,
                    start_new_session=True,
                )
                self._tool_log("[launched ParaView GUI; close its window to exit]")
                threading.Thread(target=self._watch_tool_process, args=(proc, "ParaView"), daemon=True).start()
                return
            except Exception as exc:
                self._tool_log(f"[paraview launch failed: {exc}] trying pvpython...")

        env.pop("PYCHRONO_PARAVIEW_GUI", None)
        pvpython_bin = shutil.which("pvpython") or "pvpython"
        cmd = [pvpython_bin, script, str(pdir)]
        if show_fea_3d:
            cmd.append("--with-fea")
        if show_fea_charts:
            cmd.append("--show-fea-charts")
        self._tool_log("$ " + shlex.join(cmd))
        try:
            proc = subprocess.Popen(
                cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                start_new_session=True,
            )
            self._tool_log("[launched pvpython render window; close it to exit]")
            threading.Thread(target=self._watch_tool_process, args=(proc, "pvpython"), daemon=True).start()
        except Exception as exc:
            self._tool_log(f"[error launching viewer: {exc}]")

    def _tool_results(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        cmd = [py_exec(), str(ROOT / "view_results.py"), "--output-dir", str(d), "--show"]
        self._tool_log("$ " + shlex.join(cmd))
        try:
            subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._tool_log("[launched interactive results plot]")
        except Exception as e:
            self._tool_log(f"[error] {e}")

    def _tool_geometry(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        threads = max(0, int(float(self._g("num_threads") or 8)))
        cmd = [py_exec(), str(ROOT / "view_geometry.py"), "--study-dir", str(d),
               "--threads", str(threads)]
        self._tool_log("$ " + shlex.join(cmd))
        self._tool_log(f"# OCC parallel tessellation/fuse enabled ({threads or 'all'} threads)")
        try:
            subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._tool_log("[launched geometry view]")
        except Exception as e:
            self._tool_log(f"[error] {e}")

    def _tool_open_folder(self):
        d = Path(self.tools_study_dir.get()).expanduser()
        wiz.open_path(d)
        self._tool_log(f"[open folder] {d}")

    @staticmethod
    def _set_text(widget, text):
        widget.delete("1.0", END)
        widget.insert("1.0", text)


def main():
    root = Tk()
    # Create app (applies scaling/fonts/theme) before showing UI
    app = StudyGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
