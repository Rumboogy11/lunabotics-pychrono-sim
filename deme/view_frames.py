#!/usr/bin/env python3
"""Lightweight playback viewer for DEME LunarExcavator frames.

This avoids ParaView entirely.  Grain positions come from ``spheres_*.csv``.
The drum pose is taken from the actual ``drum_*.vtk`` frames by default.  The
source OBJ is selected automatically by matching its vertex count to the VTK
frames (production runs use the full coarse geometry), and the fitted pose is
drawn on the chosen display mesh; an analytic fallback is available with
``--drum-source analytic``.  The retaining-wall box comes from ``domain_bounds``.
The Tk backend is used by default so the window stays open without importing Qt.

Usage:
    ./view_frames.py --output-dir Output_DEME_LunarExcavator --stride 8
    MPLBACKEND=Agg ./view_frames.py --output-dir ... --no-render
"""

from __future__ import annotations

import argparse
import csv
import glob
import math
import os
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lunar_excavator_config import DEFAULT, OBJ_COARSE, OBJ_DEME, OBJ_MEDIUM  # noqa: E402

# Candidate pose-source meshes, in preference order.  The VTK frames preserve
# the solver mesh's vertex order, so the point count identifies which OBJ to use.
MESH_SOURCES = (("coarse", OBJ_COARSE), ("medium", OBJ_MEDIUM), ("deme", OBJ_DEME))


def natural_key(text: str):
    return [int(c) if c.isdigit() else c for c in re.split(r"(\d+)", text)]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path(os.environ.get("LUNAR_OUTPUT_DIR", "Output_DEME_LunarExcavator")),
        help="frame directory (or set LUNAR_OUTPUT_DIR)",
    )
    p.add_argument("--fps", type=float, default=None, help="output fps used when summary.csv is absent")
    p.add_argument("--stride", type=int, default=8, help="plot every Nth grain (default: 8)")
    p.add_argument("--interval", type=float, default=100.0, help="nominal ms between frames")
    p.add_argument("--start", type=int, default=0, help="first frame index in the sorted list")
    p.add_argument("--stop", type=int, default=None, help="stop before this frame index")
    p.add_argument("--point-size", type=float, default=6.0, help="scatter marker size")
    p.add_argument(
        "--mesh",
        choices=("coarse", "medium", "deme"),
        default="coarse",
        help="visualization OBJ (default: full coarse; 'deme' is the legacy 1200-face simplification)",
    )
    p.add_argument(
        "--drum-source",
        choices=("auto", "vtk", "analytic"),
        default="auto",
        help=(
            "drum pose source: auto uses VTK only when its spin axis matches the config "
            "(default); vtk forces the recorded pose; analytic always uses the config"
        ),
    )
    p.add_argument("--drum-stride", type=int, default=1, help="plot every Nth drum face (default: 1)")
    p.add_argument("--no-drum", action="store_true", help="hide the drum mesh")
    p.add_argument("--no-walls", action="store_true", help="hide the retaining-wall box")
    p.add_argument("--no-repeat", action="store_true", help="stop at the last frame instead of looping")
    p.add_argument("--no-render", action="store_true", help="build the scene and exit (headless check)")
    p.add_argument("--save", type=Path, default=None, help="save the selected frame to a PNG and exit")
    return p


def find_sphere_frames(output_dir: Path) -> tuple[list[Path], list[int]]:
    paths = [Path(f) for f in glob.glob(str(output_dir / "spheres_*.csv")) if "merged" not in f]
    if not paths:
        raise SystemExit(f"No spheres_*.csv frames found in {output_dir}. Run the simulation first.")
    paths.sort(key=lambda q: natural_key(q.name))
    ids = []
    for path in paths:
        digits = re.search(r"(\d+)\.csv$", path.name)
        if not digits:
            raise SystemExit(f"Cannot read frame number from {path.name}")
        ids.append(int(digits.group(1)))
    return paths, ids


def load_times(output_dir: Path, frame_ids: list[int], fallback_fps: float) -> np.ndarray:
    """Prefer exact times from summary.csv; otherwise use frame_id / fps."""
    times: dict[int, float] = {}
    summary = output_dir / "summary.csv"
    if summary.exists():
        try:
            with summary.open(newline="", encoding="utf-8") as f:
                reader = csv.reader(f)
                header = next(reader, [])
                lower = [h.strip().lower() for h in header]
                if "frame" in lower and "time" in lower:
                    fi, ti = lower.index("frame"), lower.index("time")
                    for row in reader:
                        if len(row) > max(fi, ti):
                            times[int(float(row[fi]))] = float(row[ti])
        except (OSError, ValueError):
            times = {}
    return np.array([times.get(fid, fid / fallback_fps) for fid in frame_ids], dtype=float)


def load_particle_counts(output_dir: Path) -> dict[int, int]:
    """Read frame -> particle count from summary.csv, when available."""
    counts: dict[int, int] = {}
    summary = output_dir / "summary.csv"
    if not summary.exists():
        return counts
    try:
        with summary.open(newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            header = [h.strip().lower() for h in next(reader, [])]
            if "frame" not in header or "particles" not in header:
                return counts
            fi, pi = header.index("frame"), header.index("particles")
            for row in reader:
                if len(row) > max(fi, pi):
                    counts[int(float(row[fi]))] = int(float(row[pi]))
    except (OSError, ValueError):
        return {}
    return counts


def filter_current_frames(
    output_dir: Path, paths: list[Path], frame_ids: list[int]
) -> tuple[list[Path], list[int], int]:
    """Drop frames that do not match the current particle count.

    An interrupted/restarted output directory can contain frames from two
    different configurations.  Mixing them makes the drum appear to change spin
    axis halfway through playback.
    """
    counts = load_particle_counts(output_dir)
    if not counts:
        return paths, frame_ids, 0
    expected = DEFAULT.n_particles
    keep = [i for i, fid in enumerate(frame_ids) if counts.get(fid) == expected]
    if len(keep) == len(frame_ids):
        return paths, frame_ids, 0
    if not keep:
        print(
            f"Warning: no frame has the current {expected} particles; showing all "
            f"{len(frame_ids)} frames anyway (output may be stale).",
            file=sys.stderr,
        )
        return paths, frame_ids, 0
    skipped = len(frame_ids) - len(keep)
    print(
        f"Warning: skipped {skipped} stale frame(s) with a particle count other than "
        f"{expected} (current config).",
        file=sys.stderr,
    )
    return [paths[i] for i in keep], [frame_ids[i] for i in keep], skipped


def load_grains(path: Path, stride: int) -> np.ndarray:
    data = np.loadtxt(path, delimiter=",", skiprows=1, usecols=(0, 1, 2, 3, 4))
    if data.size == 0:
        return np.empty((0, 5), dtype=float)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    return data[::stride]


def load_obj(path: Path) -> tuple[np.ndarray, np.ndarray]:
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.startswith("v "):
                vertices.append([float(x) for x in line.split()[1:4]])
            elif line.startswith("f "):
                idx = [int(tok.split("/")[0]) - 1 for tok in line.split()[1:4]]
                faces.append(idx)
    if not vertices or not faces:
        raise SystemExit(f"OBJ {path} has no vertices/faces")
    return np.asarray(vertices, dtype=float), np.asarray(faces, dtype=int)


def find_drum_frames(output_dir: Path) -> dict[int, Path]:
    out: dict[int, Path] = {}
    for f in glob.glob(str(output_dir / "drum_*.vtk")):
        match = re.search(r"drum_(\d+)\.vtk$", Path(f).name)
        if match:
            out[int(match.group(1))] = Path(f)
    return out


def load_drum_vtk(path: Path) -> np.ndarray:
    """Read POINTS from a legacy ASCII VTK drum frame (solver mesh vertex order)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^POINTS\s+(\d+)\s+\w+\s*$", text, re.MULTILINE)
    if not match:
        raise SystemExit(f"No POINTS block in {path}")
    n = int(match.group(1))
    points = np.fromstring(text[match.end() :], sep=" ", count=n * 3)
    if points.size != n * 3:
        raise SystemExit(f"Truncated POINTS block in {path}")
    return points.reshape(n, 3)


def choose_pose_source(vtk_path: Path) -> tuple[np.ndarray, str]:
    """Return the source vertices and mesh name matching a drum VTK frame."""
    n_points = len(load_drum_vtk(vtk_path))
    for name, path in MESH_SOURCES:
        if not path.exists():
            continue
        vertices, _ = load_obj(path)
        if len(vertices) == n_points:
            return vertices, name
    names = ", ".join(f"{name}={len(load_obj(path)[0])}" for name, path in MESH_SOURCES if path.exists())
    raise SystemExit(
        f"No drum OBJ matches {n_points} VTK points ({names or 'no mesh files found'}); "
        "regenerate the frames with the matching --mesh setting."
    )


def fit_rigid_transform(source: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Kabsch: return R, t such that source @ R.T + t ~= target."""
    if source.shape != target.shape:
        raise SystemExit(f"drum VTK has {len(target)} points; visualization source has {len(source)}")
    sc, tc = source.mean(axis=0), target.mean(axis=0)
    h = (source - sc).T @ (target - tc)
    u, _, vt = np.linalg.svd(h)
    r = vt.T @ u.T
    if np.linalg.det(r) < 0.0:
        vt[-1, :] *= -1.0
        r = vt.T @ u.T
    return r, tc - r @ sc


def rotation_axis(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray | None, float]:
    """Axis and angle of the relative rotation b @ a.T (sign of axis is arbitrary)."""
    d = b @ a.T
    v = np.array([d[2, 1] - d[1, 2], d[0, 2] - d[2, 0], d[1, 0] - d[0, 1]])
    n = float(np.linalg.norm(v))
    angle = float(np.arctan2(n, (np.trace(d) - 1.0) / 2.0))
    if n < 1.0e-6:
        return None, angle
    return v / n, angle


def infer_spin_axis(
    source: np.ndarray,
    drum_paths: dict[int, Path],
    frame_ids: list[int],
    times: np.ndarray,
) -> np.ndarray | None:
    """Infer the recorded drum spin axis from a pair of early VTK frames."""
    time_by_id = dict(zip(frame_ids, times.tolist()))
    candidates = [fid for fid in frame_ids if fid in drum_paths][:40]
    if len(candidates) < 2:
        return None

    rotations: dict[int, np.ndarray] = {}
    for fid in candidates:
        try:
            rotations[fid], _ = fit_rigid_transform(source, load_drum_vtk(drum_paths[fid]))
        except SystemExit:
            return None

    best: tuple[float, np.ndarray | None] = (float("inf"), None)
    for i, fid_a in enumerate(candidates):
        for fid_b in candidates[i + 1 :]:
            dt = time_by_id[fid_b] - time_by_id[fid_a]
            expected_angle = DEFAULT.drum_omega * dt
            if expected_angle < 0.15:
                continue
            # Prefer a quarter-ish turn; large angles can wrap near zero.
            score = abs(expected_angle - 0.8)
            if score < best[0]:
                axis, _ = rotation_axis(rotations[fid_a], rotations[fid_b])
                best = (score, axis)
    return best[1]


def quat_mul_xyzw(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return np.array(
        [
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz,
        ],
        dtype=float,
    )


def quat_rotate(q: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    v = q[:3]
    w = q[3]
    t = 2.0 * np.cross(v, vectors)
    return vectors + w * t + np.cross(v, t)


def drum_vertices(t: float, base: np.ndarray, p) -> np.ndarray:
    axis = {
        "X": np.array([1.0, 0.0, 0.0]),
        "Y": np.array([0.0, 1.0, 0.0]),
        "Z": np.array([0.0, 0.0, 1.0]),
    }[p.drum_spin_axis.upper()]
    theta = p.drum_omega * max(0.0, t - p.settle_time)
    half = 0.5 * theta
    q_spin = np.array([axis[0] * math.sin(half), axis[1] * math.sin(half), axis[2] * math.sin(half), math.cos(half)])
    q = quat_mul_xyzw(q_spin, np.asarray(p.initial_quaternion_xyzw(), dtype=float))
    out = quat_rotate(q, base)
    out[:, 2] += p.drum_z(t)
    return out


def wall_polygons(p) -> list[np.ndarray]:
    (xlo, xhi), (ylo, yhi), (zlo, zhi) = p.domain_bounds
    return [
        np.array([[xlo, ylo, zlo], [xhi, ylo, zlo], [xhi, yhi, zlo], [xlo, yhi, zlo]]),
        np.array([[xlo, ylo, zlo], [xlo, ylo, zhi], [xlo, yhi, zhi], [xlo, yhi, zlo]]),
        np.array([[xhi, ylo, zlo], [xhi, ylo, zhi], [xhi, yhi, zhi], [xhi, yhi, zlo]]),
        np.array([[xlo, ylo, zlo], [xlo, ylo, zhi], [xhi, ylo, zhi], [xhi, ylo, zlo]]),
        np.array([[xlo, yhi, zlo], [xlo, yhi, zhi], [xhi, yhi, zhi], [xhi, yhi, zlo]]),
    ]


def main() -> int:
    args = build_parser().parse_args()
    for name in ("stride", "drum_stride"):
        if getattr(args, name) < 1:
            raise SystemExit(f"--{name.replace('_', '-')} must be >= 1")

    backend = "Agg" if (args.no_render or args.save is not None) else os.environ.get("MPLBACKEND", "TkAgg")
    os.environ["MPLBACKEND"] = backend

    import matplotlib

    matplotlib.use(backend, force=True)
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    output_dir = args.output_dir.resolve()
    paths, frame_ids = find_sphere_frames(output_dir)
    paths, frame_ids, skipped = filter_current_frames(output_dir, paths, frame_ids)
    fps = args.fps if args.fps is not None else DEFAULT.fps
    times = load_times(output_dir, frame_ids, fps)

    start = max(0, args.start)
    stop = len(paths) if args.stop is None else min(len(paths), args.stop)
    if start >= stop:
        raise SystemExit(f"No frames selected: start={start}, stop={stop}, available={len(paths)}")

    display_vertices = display_faces = transform_source = None
    pose_mesh_name = "none"
    drum_paths: dict[int, Path] = {}
    drum_mode = args.drum_source
    if not args.no_drum:
        mesh_path = {"deme": OBJ_DEME, "coarse": OBJ_COARSE, "medium": OBJ_MEDIUM}[args.mesh]
        if not mesh_path.exists():
            raise SystemExit(f"missing {mesh_path}; run `python3 stl_to_obj.py` first")
        display_vertices, display_faces = load_obj(mesh_path)
        display_faces = display_faces[:: args.drum_stride]

        if drum_mode in ("auto", "vtk"):
            drum_paths = find_drum_frames(output_dir)
            if not drum_paths:
                print("Warning: no drum_*.vtk frames found; falling back to analytic drum pose.", file=sys.stderr)
                drum_mode = "analytic"
            else:
                transform_source, pose_mesh_name = choose_pose_source(drum_paths[min(drum_paths)])
                if drum_mode == "auto":
                    recorded_axis = infer_spin_axis(transform_source, drum_paths, frame_ids, times)
                    expected_axis = {
                        "X": np.array([1.0, 0.0, 0.0]),
                        "Y": np.array([0.0, 1.0, 0.0]),
                        "Z": np.array([0.0, 0.0, 1.0]),
                    }[DEFAULT.drum_spin_axis.upper()]
                    if recorded_axis is not None and abs(float(np.dot(recorded_axis, expected_axis))) < 0.9:
                        print(
                            "Warning: recorded drum frames spin about "
                            f"({recorded_axis[0]:+.2f},{recorded_axis[1]:+.2f},{recorded_axis[2]:+.2f}), "
                            f"not the configured {DEFAULT.drum_spin_axis} axis; using analytic pose instead. "
                            "Regenerate the output to see the solver's motion.",
                            file=sys.stderr,
                        )
                        drum_mode = "analytic"
                        drum_paths = {}
                    else:
                        drum_mode = "vtk"

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")
    fig.canvas.manager.set_window_title("LunarExcavator DEME frames")

    if not args.no_walls:
        walls = Poly3DCollection(wall_polygons(DEFAULT), facecolors="0.75", edgecolors="0.35", linewidths=0.5, alpha=0.12)
        ax.add_collection3d(walls)

    drum_collection = None
    if display_vertices is not None:
        drum_collection = Poly3DCollection([], facecolors="0.45", edgecolors="none", alpha=0.65)
        ax.add_collection3d(drum_collection)

    first = load_grains(paths[start], args.stride)
    zmin, zmax = 0.0, DEFAULT.drum_center_z0 + DEFAULT.drum_radius
    scatter = ax.scatter(
        first[:, 0],
        first[:, 1],
        first[:, 2],
        s=args.point_size,
        c=first[:, 2],
        cmap="viridis",
        vmin=zmin,
        vmax=zmax,
    )

    (xlo, xhi), (ylo, yhi), (zlo, zhi) = DEFAULT.domain_bounds
    margin = 0.006
    ax.set_xlim(xlo - margin, xhi + margin)
    ax.set_ylim(ylo - margin, yhi + margin)
    ax.set_zlim(zlo, zhi + margin)
    ax.set_box_aspect((xhi - xlo, yhi - ylo, zhi - zlo))
    ax.set_xlabel("x [m]")
    ax.set_ylabel("y [m]")
    ax.set_zlabel("z [m]")
    ax.view_init(elev=22, azim=-58)
    title = ax.set_title("")

    state = {"index": start, "paused": False}

    def update(index: int):
        state["index"] = index
        grains = load_grains(paths[index], args.stride)
        scatter._offsets3d = (grains[:, 0], grains[:, 1], grains[:, 2])
        scatter.set_array(grains[:, 2])
        drum_z = DEFAULT.drum_z(times[index])
        if drum_collection is not None:
            if drum_mode == "vtk" and frame_ids[index] in drum_paths:
                vtk_points = load_drum_vtk(drum_paths[frame_ids[index]])
                rot, trans = fit_rigid_transform(transform_source, vtk_points)
                verts = display_vertices @ rot.T + trans
                drum_z = float(trans[2])
            else:
                verts = drum_vertices(times[index], display_vertices, DEFAULT)
            drum_collection.set_verts(verts[display_faces])
        title.set_text(
            f"frame {frame_ids[index]}  t={times[index]:.3f} s  "
            f"drum z≈{drum_z:.4f} m  pose={drum_mode}:{pose_mesh_name}  "
            f"grains shown {len(grains)}/{len(grains) * max(1, args.stride)}"
        )
        return (scatter, title) if drum_collection is None else (scatter, drum_collection, title)

    update(start)
    fig.tight_layout()

    if args.save is not None:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(args.save, dpi=150)
        print(f"Saved frame {frame_ids[start]} (t={times[start]:.3f} s) to {args.save}", flush=True)
        return 0

    if args.no_render:
        print(
            f"Prepared {stop - start} frames from {output_dir} "
            f"({skipped} stale frame(s) skipped, drum pose={drum_mode}:{pose_mesh_name})\n"
            f"frame {frame_ids[start]}  t={times[start]:.3f} s  "
            f"grains shown {len(first)}  backend={backend}",
            flush=True,
        )
        return 0

    anim = FuncAnimation(
        fig,
        update,
        frames=range(start, stop),
        interval=max(1.0, args.interval),
        blit=False,
        repeat=not args.no_repeat,
    )

    def on_key(event) -> None:
        if event.key == " ":
            if state["paused"]:
                anim.event_source.start()
                state["paused"] = False
            else:
                anim.event_source.stop()
                state["paused"] = True
        elif event.key in ("left", "right"):
            if not state["paused"]:
                anim.event_source.stop()
                state["paused"] = True
            step = -1 if event.key == "left" else 1
            nxt = (state["index"] + step - start) % (stop - start) + start
            update(nxt)
            fig.canvas.draw_idle()

    fig.canvas.mpl_connect("key_press_event", on_key)
    print(
        f"Showing {stop - start} frames from {output_dir} (backend={backend}).\n"
        "Space = pause/resume, Left/Right = step frame, close window to exit.",
        flush=True,
    )
    plt.show()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
