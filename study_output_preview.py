#!/usr/bin/env python3
"""Lightweight CSV-only CRM/FEA output preview for the study GUI.

The preview intentionally avoids VTK/PVD conversion and heavy 3D rendering.
It reads the files written by the study directly:

  * ``crm/particles_*.csv`` for a subsampled Y-Z particle scatter,
  * ``crm/summary.csv`` and ``fea/summary.csv`` for scalar timelines.

Particles are drawn as 1-2 pixel points on a Tk canvas instead of 3D spheres,
and each particle frame is reservoir-sampled to a small fixed limit.
"""

from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path
from typing import Callable, Iterable

import tkinter as tk
from tkinter import BooleanVar, IntVar, StringVar, filedialog, ttk

PARTICLE_LIMIT = 1200
FRAME_DELAY_MS = 250

SUMMARY_FIELDS = ("frame", "time_s", "drum_z_m", "spin_deg")

PARTICLE_COLORS = {
    "soil": "#9E7B5A",
    "inside": "#4FC3F7",
    "excavated": "#FF7043",
}

CRM_SERIES = (
    ("fill_ratio_mass", "fill ratio", "#42A5F5", 1.0, ""),
    ("excavated", "excavated count", "#FFA726", 1.0, ""),
    ("fluid_torque_x_nm", "torque X (Nm)", "#66BB6A", 1.0, ""),
)

FEA_SERIES = (
    ("max_von_mises_pa", "von Mises (MPa)", "#EF5350", 1.0e-6, " MPa"),
    ("max_deformation_m", "deformation (mm)", "#29B6F6", 1.0e3, " mm"),
)


def _to_float(value: object) -> float | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_int(value: object) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


def read_rows(path: Path) -> list[dict[str, str]]:
    """Read a small study CSV into dictionaries; return [] when unavailable."""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return []
            return [row for row in reader]
    except (OSError, csv.Error, UnicodeError):
        return []


def load_summary(path: Path) -> list[dict[str, float]]:
    """Load a summary CSV, converting known numeric columns to floats."""
    rows: list[dict[str, float]] = []
    for raw in read_rows(path):
        row: dict[str, float] = {}
        for key in SUMMARY_FIELDS:
            value = _to_float(raw.get(key))
            if value is not None:
                row[key] = value
        for key, value in raw.items():
            if key in row or not key:
                continue
            number = _to_float(value)
            if number is not None:
                row[key] = number
        if row:
            rows.append(row)
    return rows


def load_config(out_dir: Path) -> dict[str, object]:
    """Read the study config written by the wizard (flat JSON)."""
    for name in ("study_config.json", "crm/config.json"):
        path = out_dir / name
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def frame_number_from_path(path: Path) -> int:
    suffix = path.stem.rsplit("_", 1)[-1]
    try:
        return int(suffix)
    except ValueError:
        return -1


def discover_particle_frames(out_dir: Path) -> list[Path]:
    crm_dir = out_dir / "crm"
    try:
        paths = list(crm_dir.glob("particles_*.csv"))
    except OSError:
        return []
    return sorted(paths, key=frame_number_from_path)


def load_particle_sample(path: Path, limit: int = PARTICLE_LIMIT) -> tuple[list[tuple[float, float, float, int, int]], int]:
    """Reservoir-sample one particle CSV frame with bounded memory.

    Returns ``(sample, total_rows)``.  Each sample item is
    ``(x, y, z, inside_drum, excavated)`` in world metres / integer flags.
    """
    sample: list[tuple[float, float, float, int, int]] = []
    total = 0
    rng = random.Random(20261120)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                return [], 0
            for raw in reader:
                x = _to_float(raw.get("X"))
                y = _to_float(raw.get("Y"))
                z = _to_float(raw.get("Z"))
                if x is None or y is None or z is None:
                    continue
                inside = _to_int(raw.get("inside_drum")) or 0
                excavated = _to_int(raw.get("excavated")) or 0
                item = (x, y, z, inside, excavated)
                total += 1
                if len(sample) < limit:
                    sample.append(item)
                else:
                    slot = rng.randint(1, total)
                    if slot <= limit:
                        sample[slot - 1] = item
    except (OSError, csv.Error, UnicodeError):
        return [], 0
    return sample, total


def _nice_step(span: float) -> float:
    if span <= 0:
        return 1.0
    rough = span / 4.0
    power = 10.0 ** math.floor(math.log10(rough))
    for factor in (1.0, 2.0, 5.0, 10.0):
        if rough <= factor * power:
            return factor * power
    return 10.0 * power


def _time_of(rows: list[dict[str, float]], index: int, fallback_fps: float) -> float:
    if 0 <= index < len(rows):
        value = rows[index].get("time_s")
        if value is not None:
            return float(value)
    return index / fallback_fps if fallback_fps > 0 else float(index)


class OutputPreview:
    """Embedded, low-cost preview widgets bound to one output directory."""

    def __init__(
        self,
        parent: ttk.Frame,
        root: tk.Tk,
        initial_dir: str,
        palette: Callable[[], dict[str, str]],
    ) -> None:
        self.parent = parent
        self.root = root
        self.palette = palette
        self.dir_var = StringVar(value=initial_dir)
        self.show_particles = BooleanVar(value=True)
        self.show_crm = BooleanVar(value=True)
        self.show_fea = BooleanVar(value=True)
        self.playing = BooleanVar(value=False)
        self.frame_var = IntVar(value=0)

        self.frames: list[Path] = []
        self.frame_numbers: list[int] = []
        self.frame_lookup: dict[int, dict[str, float]] = {}
        self.crm_rows: list[dict[str, float]] = []
        self.fea_rows: list[dict[str, float]] = []
        self.config: dict[str, object] = {}
        self.particles: list[tuple[float, float, float, int, int]] = []
        self.particle_total = 0
        self.loaded_frame = -1
        self.current_frame = 0
        self.current_time = 0.0
        self._after_id: str | None = None

        self._build()

    # ---- construction ----
    def _build(self) -> None:
        controls = ttk.Frame(self.parent)
        controls.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(controls, text="Output directory", font="StudySectionFont").grid(
            row=0, column=0, sticky="w", padx=(2, 6), pady=3)
        ttk.Entry(controls, textvariable=self.dir_var, width=58).grid(
            row=0, column=1, sticky="w", padx=4)
        ttk.Button(controls, text="Browse", command=self._browse).grid(
            row=0, column=2, sticky="w", padx=3)
        ttk.Button(controls, text="Refresh", command=self.refresh).grid(
            row=0, column=3, sticky="w", padx=3)

        layers = ttk.Frame(self.parent)
        layers.pack(fill="x", padx=6, pady=2)
        ttk.Checkbutton(layers, text="CRM particles", variable=self.show_particles,
                        command=self._on_layer_toggle).pack(side="left", padx=(2, 10))
        ttk.Checkbutton(layers, text="CRM timeline", variable=self.show_crm,
                        command=self._on_layer_toggle).pack(side="left", padx=10)
        ttk.Checkbutton(layers, text="FEA timeline", variable=self.show_fea,
                        command=self._on_layer_toggle).pack(side="left", padx=10)
        self.play_button = ttk.Button(layers, text="Play", width=8,
                                      command=self._toggle_play)
        self.play_button.pack(side="left", padx=(16, 6))
        self.frame_scale = ttk.Scale(layers, from_=0, to=1, orient="horizontal",
                                     variable=self.frame_var, command=self._on_scale)
        self.frame_scale.pack(side="left", fill="x", expand=True, padx=4)
        self.frame_label = ttk.Label(layers, text="no particle frames", width=24)
        self.frame_label.pack(side="left", padx=(6, 2))

        pane = ttk.Panedwindow(self.parent, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=6, pady=4)

        particle_frame = ttk.Frame(pane, width=520)
        pane.add(particle_frame, weight=3)
        ttk.Label(particle_frame, text="CRM particle cross-section (Y-Z, points only)",
                  font="StudySectionFont").pack(anchor="w", padx=6, pady=(4, 2))
        self.particle_canvas = tk.Canvas(particle_frame, highlightthickness=0)
        self.particle_canvas.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        self.particle_canvas.bind("<Configure>", lambda _e: self._draw_particles())

        timeline_frame = ttk.Frame(pane, width=440)
        pane.add(timeline_frame, weight=2)
        ttk.Label(timeline_frame, text="Scalar histories (CSV, no conversion)",
                  font="StudySectionFont").pack(anchor="w", padx=6, pady=(4, 2))
        self.crm_canvas = tk.Canvas(timeline_frame, height=150, highlightthickness=0)
        self.crm_canvas.pack(fill="both", expand=True, padx=6, pady=(2, 2))
        self.crm_canvas.bind("<Configure>", lambda _e: self._draw_timeline(
            self.crm_canvas, "CRM", self.crm_rows, CRM_SERIES, self.show_crm.get()))
        self.fea_canvas = tk.Canvas(timeline_frame, height=150, highlightthickness=0)
        self.fea_canvas.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        self.fea_canvas.bind("<Configure>", lambda _e: self._draw_timeline(
            self.fea_canvas, "FEA", self.fea_rows, FEA_SERIES, self.show_fea.get()))

        self.status_var = StringVar(value="Choose a study directory, then click Refresh.")
        ttk.Label(self.parent, textvariable=self.status_var, anchor="w").pack(
            fill="x", padx=8, pady=(0, 6))

    # ---- public API ----
    def set_directory(self, path: str | Path) -> None:
        self.dir_var.set(str(path))

    def refresh(self) -> None:
        out_dir = Path(self.dir_var.get().strip()).expanduser()
        self.frames = discover_particle_frames(out_dir)
        self.frame_numbers = [frame_number_from_path(path) for path in self.frames]
        self.crm_rows = load_summary(out_dir / "crm" / "summary.csv")
        self.frame_lookup = {
            int(row["frame"]): row for row in self.crm_rows if "frame" in row
        }
        self.fea_rows = load_summary(out_dir / "fea" / "summary.csv")
        self.config = load_config(out_dir)
        self.loaded_frame = -1

        maximum = max(0, len(self.frames) - 1)
        self.frame_scale.configure(to=maximum)
        self.current_frame = min(self.current_frame, maximum)
        self.frame_var.set(self.current_frame)
        self._update_frame_label()
        self._draw_all()

        details = [
            f"{len(self.frames)} particle frames",
            f"{len(self.crm_rows)} CRM rows",
            f"{len(self.fea_rows)} FEA rows",
        ]
        self.status_var.set(f"{out_dir} | " + ", ".join(details))

    def set_visible(self, visible: bool) -> None:
        if not visible and self.playing:
            self._stop_play()

    # ---- interactions ----
    def _browse(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self.dir_var.get() or str(Path.cwd()),
            title="Study output directory")
        if chosen:
            self.dir_var.set(chosen)
            self.refresh()

    def _on_layer_toggle(self) -> None:
        self._draw_all()

    def _on_scale(self, _value: str) -> None:
        self.current_frame = int(self.frame_var.get())
        self._update_frame_label()
        self._draw_particles()
        self._draw_timelines()

    def _toggle_play(self) -> None:
        if self.playing.get():
            self._stop_play()
        else:
            if not self.frames:
                self.status_var.set("No particle frames to play; check the output directory.")
                return
            self.playing.set(True)
            self.play_button.configure(text="Pause")
            self._advance()

    def _stop_play(self) -> None:
        self.playing.set(False)
        self.play_button.configure(text="Play")
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _advance(self) -> None:
        if not self.playing.get() or not self.frames:
            self._after_id = None
            return
        next_frame = (self.current_frame + 1) % len(self.frames)
        self.frame_var.set(next_frame)
        self.current_frame = next_frame
        self._update_frame_label()
        self._draw_particles()
        self._draw_timelines()
        self._after_id = self.root.after(FRAME_DELAY_MS, self._advance)

    def _update_frame_label(self) -> None:
        fps = _to_float(self.config.get("crm_fps")) or 20.0
        frame_id = (self.frame_numbers[self.current_frame]
                    if self.current_frame < len(self.frame_numbers) else self.current_frame)
        row = self.frame_lookup.get(frame_id)
        if row is not None and "time_s" in row:
            self.current_time = float(row["time_s"])
        else:
            self.current_time = _time_of(self.crm_rows, frame_id, fps)
        if self.frames:
            self.frame_label.configure(
                text=f"file {self.current_frame + 1}/{len(self.frames)}  "
                     f"frame {frame_id}  t={self.current_time:.2f}s")
        else:
            self.frame_label.configure(text="no particle frames")

    # ---- drawing ----
    def _draw_all(self) -> None:
        self._draw_particles()
        self._draw_timelines()

    def _draw_timelines(self) -> None:
        self._draw_timeline(self.crm_canvas, "CRM", self.crm_rows,
                            CRM_SERIES, self.show_crm.get())
        self._draw_timeline(self.fea_canvas, "FEA", self.fea_rows,
                            FEA_SERIES, self.show_fea.get())

    def _palette(self) -> dict[str, str]:
        fallback = {
            "bg": "#FFFFFF", "frame": "#F4F4F4", "field": "#FFFFFF",
            "text": "#000000", "button": "#E6E6E6",
        }
        try:
            fallback.update(self.palette())
        except Exception:
            pass
        return fallback

    def _message(self, canvas: tk.Canvas, text: str) -> None:
        palette = self._palette()
        canvas.delete("all")
        canvas.configure(background=palette["field"])
        canvas.create_text(10, 12, anchor="nw", text=text,
                           fill=palette["text"], font="TkDefaultFont")

    def _draw_particles(self) -> None:
        canvas = getattr(self, "particle_canvas", None)
        if canvas is None:
            return
        palette = self._palette()
        canvas.configure(background=palette["field"])
        canvas.delete("all")
        width = canvas.winfo_width()
        height = canvas.winfo_height()
        if width < 20 or height < 20:
            return
        if not self.show_particles.get():
            self._message(canvas, "CRM particle layer hidden. Enable 'CRM particles' above.")
            return
        if not self.frames:
            self._message(canvas, "No crm/particles_*.csv frames found.\n"
                                  "Enable 'Save CRM particle CSV frames' for a new study.")
            return

        frame_index = min(self.current_frame, len(self.frames) - 1)
        if frame_index != self.loaded_frame:
            self.particles, self.particle_total = load_particle_sample(
                self.frames[frame_index], PARTICLE_LIMIT)
            self.loaded_frame = frame_index

        radius = _to_float(self.config.get("drum_radius")) or 0.06
        frame_id = (self.frame_numbers[frame_index]
                    if frame_index < len(self.frame_numbers) else frame_index)
        summary_row = self.frame_lookup.get(frame_id)
        if summary_row is None and 0 <= frame_id < len(self.crm_rows):
            summary_row = self.crm_rows[frame_id]
        center_z = float(summary_row.get("drum_z_m", 0.0)) if summary_row else 0.0
        if center_z == 0.0:
            bed = _to_float(self.config.get("bed_height")) or 0.0
            center_z = bed + radius

        ys = [point[1] for point in self.particles] + [-radius, radius]
        zs = [point[2] for point in self.particles] + [
            center_z - radius, center_z + radius]
        ymin, ymax = min(ys), max(ys)
        zmin, zmax = min(zs), max(zs)
        center_y = (ymin + ymax) / 2.0
        center_z_view = (zmin + zmax) / 2.0
        span = max(ymax - ymin, zmax - zmin, 4.0 * radius) * 1.10
        ymin, ymax = center_y - span / 2.0, center_y + span / 2.0
        zmin, zmax = center_z_view - span / 2.0, center_z_view + span / 2.0

        pad_x, pad_y = 34, 26
        plot_w, plot_h = max(1, width - 2 * pad_x), max(1, height - 2 * pad_y)
        scale = min(plot_w / (ymax - ymin), plot_h / (zmax - zmin))
        origin_x = (width - (ymax - ymin) * scale) / 2.0
        origin_y = (height - (zmax - zmin) * scale) / 2.0

        def px(y: float) -> float:
            return origin_x + (y - ymin) * scale

        def py(z: float) -> float:
            return height - origin_y - (z - zmin) * scale

        # Grid and scale labels.
        step = _nice_step(span)
        grid = palette["frame"]
        y_tick = math.ceil(ymin / step) * step
        while y_tick <= ymax:
            x = px(y_tick)
            canvas.create_line(x, origin_y, x, height - origin_y, fill=grid)
            canvas.create_text(x, height - origin_y + 3, anchor="n",
                               text=f"{y_tick:g}", fill=palette["text"],
                               font="TkSmallCaptionFont")
            y_tick += step
        z_tick = math.ceil(zmin / step) * step
        while z_tick <= zmax:
            y = py(z_tick)
            canvas.create_line(origin_x, y, width - origin_x, y, fill=grid)
            canvas.create_text(origin_x - 3, y, anchor="e", text=f"{z_tick:g}",
                               fill=palette["text"], font="TkSmallCaptionFont")
            z_tick += step

        # Bed surface and drum envelope.
        bed = _to_float(self.config.get("bed_height"))
        if bed is not None and zmin <= bed <= zmax:
            y = py(bed)
            canvas.create_line(origin_x, y, width - origin_x, y,
                               fill="#8D6E63", dash=(4, 3))

        def draw_circle(r: float, color: str, dashed: bool = False) -> None:
            x0, y0 = px(-r), py(center_z + r)
            x1, y1 = px(r), py(center_z - r)
            options = {"outline": color, "width": 1}
            if dashed:
                options["dash"] = (3, 2)
            canvas.create_oval(x0, y0, x1, y1, **options)

        draw_circle(radius, "#FFD54F")
        inner = _to_float(self.config.get("inner_radius"))
        if inner is not None and 0 < inner < radius:
            draw_circle(inner, "#90A4AE", dashed=True)

        spin = _to_float(summary_row.get("spin_deg") if summary_row else None) or 0.0
        theta = math.radians(spin)
        canvas.create_line(px(0.0), py(center_z),
                           px(radius * math.cos(theta)),
                           py(center_z + radius * math.sin(theta)),
                           fill="#FFD54F", width=2)

        # Subsampled soil points (never 3D glyphs).
        for x, y, z, inside, excavated in self.particles:
            color = PARTICLE_COLORS["excavated"] if excavated else (
                PARTICLE_COLORS["inside"] if inside else PARTICLE_COLORS["soil"])
            sx, sy = px(y), py(z)
            canvas.create_oval(sx - 1, sy - 1, sx + 1, sy + 1,
                               fill=color, outline="")

        canvas.create_text(8, 8, anchor="nw",
                           text=f"showing {len(self.particles)} of {self.particle_total} particles",
                           fill=palette["text"], font="TkSmallCaptionFont")
        canvas.create_text(width - 8, height - 8, anchor="se",
                           text="Y horizontal, Z vertical (m)",
                           fill=palette["text"], font="TkSmallCaptionFont")

    def _draw_timeline(
        self,
        canvas: tk.Canvas,
        label: str,
        rows: list[dict[str, float]],
        series: tuple[tuple[str, str, str, float, str], ...],
        visible: bool,
    ) -> None:
        palette = self._palette()
        canvas.configure(background=palette["field"])
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 20 or height < 20:
            return
        if not visible:
            self._message(canvas, f"{label} timeline hidden. Enable '{label} timeline' above.")
            return
        if not rows:
            self._message(canvas, f"No {label.lower()} summary.csv rows available.")
            return

        fps = _to_float(self.config.get("crm_fps")) or 20.0
        times = [row.get("time_s", index / fps) for index, row in enumerate(rows)]
        t_min, t_max = min(times), max(times)
        if t_max <= t_min:
            t_max = t_min + 1.0
        left, right = 42, width - 10
        top, bottom = 28, height - 22
        plot_w, plot_h = max(1, right - left), max(1, bottom - top)

        for fraction in (0.0, 0.5, 1.0):
            y = top + plot_h * fraction
            canvas.create_line(left, y, right, y, fill=palette["frame"])
        canvas.create_text(left, 8, anchor="nw", text=f"{label} history",
                           fill=palette["text"], font="TkSmallCaptionFont")
        for fraction in (0.0, 1.0):
            x = left + plot_w * fraction
            value = t_min + (t_max - t_min) * fraction
            canvas.create_text(x, bottom + 3, anchor="n" if fraction == 0 else "ne",
                               text=f"{value:.1f}s", fill=palette["text"],
                               font="TkSmallCaptionFont")

        marker_x = left + plot_w * max(0.0, min(1.0,
                                               (self.current_time - t_min) / (t_max - t_min)))
        canvas.create_line(marker_x, top, marker_x, bottom,
                           fill="#FFD54F", dash=(3, 2))

        legend_x = left + 4
        for key, title, color, scale, suffix in series:
            pairs: list[tuple[float, float]] = []
            for time_s, row in zip(times, rows):
                raw = row.get(key)
                if raw is None:
                    continue
                pairs.append((time_s, float(raw) * scale))
            if len(pairs) < 2:
                continue
            low = min(value for _time, value in pairs)
            high = max(value for _time, value in pairs)
            if high <= low:
                high = low + 1.0
            points: list[float] = []
            for time_s, value in pairs:
                x = left + plot_w * (time_s - t_min) / (t_max - t_min)
                y = bottom - plot_h * (value - low) / (high - low)
                points.extend((x, y))
            canvas.create_line(*points, fill=color, width=1)
            current = min(pairs, key=lambda pair: abs(pair[0] - self.current_time))[1]
            canvas.create_text(legend_x, height - 8, anchor="sw",
                               text=f"{title}: {current:.3g}{suffix}",
                               fill=color, font="TkSmallCaptionFont")
            legend_x += max(120, int(len(title) * 6 + 70))

        if legend_x <= left + 4:
            canvas.create_text(left + 4, height - 8, anchor="sw",
                               text="no plotted columns",
                               fill=palette["text"], font="TkSmallCaptionFont")
