#!/usr/bin/env python3
"""Summarise and plot a completed CRM -> one-way FEA study.

The wizard calls this automatically after a successful run to save
``results_overview.png``.  Run it again with ``--show`` to open an interactive
Matplotlib window with four plots: CRM work, CRM fill ratio, peak von Mises,
and peak deformation.  A shared crosshair follows the pointer: the vertical
line marks the time, and a horizontal line on each plot meets it at that
plot's value.

    ./chrono-env/bin/python view_results.py --output-dir Output_study --show
"""

from __future__ import annotations

import argparse
import csv
from bisect import bisect_left
from pathlib import Path
from typing import Any, Iterable, Sequence


def read_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def values(rows: list[dict[str, str]], key: str, scale: float = 1.0) -> list[float]:
    out: list[float] = []
    for row in rows:
        try:
            out.append(float(row[key]) * scale)
        except (KeyError, ValueError, TypeError):
            continue
    return out


def series_points(rows: list[dict[str, str]], key: str,
                  scale: float = 1.0) -> list[tuple[float, float]]:
    """Return valid ``(time, scaled value)`` pairs ordered by time."""
    x = values(rows, "time_s")
    y = values(rows, key, scale)
    if not x or not y or len(x) != len(y):
        return []
    return sorted(zip(x, y), key=lambda pair: pair[0])


def plot_series(ax: Any, rows: list[dict[str, str]], key: str, label: str,
                *, scale: float = 1.0, color: str | None = None
                ) -> list[tuple[float, float]]:
    points = series_points(rows, key, scale)
    if points:
        ax.plot([t for t, _value in points], [value for _time, value in points],
                label=label, color=color, linewidth=1.6)
    return points


def finish_axis(ax: Any, title: str, xlabel: str = "time (s)") -> None:
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.grid(alpha=0.25)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc="best", fontsize="small")


class IsotimeCursor:
    """Shared crosshair: one time line and one value line per subplot."""

    def __init__(
        self,
        figure: Any,
        axes: Iterable[Any],
        series: Sequence[Sequence[tuple[float, float]]],
        value_labels: Sequence[str],
    ) -> None:
        self.figure = figure
        self.axes = list(axes)
        self.series = [list(points) for points in series]
        self.value_labels = list(value_labels)
        if not (len(self.axes) == len(self.series) == len(self.value_labels)):
            raise ValueError("IsotimeCursor needs one series and label per axis")
        self.times = [[t for t, _value in points] for points in self.series]
        self._overlay: list[list[Any]] = []

        self.vlines = [
            ax.axvline(0.0, color="#666666", linestyle=":", linewidth=1.2,
                       alpha=0.9, visible=False, zorder=10)
            for ax in self.axes
        ]
        self.hlines = [
            ax.axhline(0.0, color="#666666", linestyle="--", linewidth=1.0,
                       alpha=0.9, visible=False, zorder=10)
            for ax in self.axes
        ]
        self.markers = []
        for ax in self.axes:
            marker, = ax.plot([], [], marker="o", markersize=4.5,
                              color="#444444", visible=False, zorder=12)
            self.markers.append(marker)
        self.labels = [
            ax.text(0.97, 0.93, "", transform=ax.transAxes, ha="right", va="top",
                    fontsize="small", color="#444444", visible=False, zorder=13,
                    bbox={"boxstyle": "round,pad=0.18", "facecolor": "white",
                          "edgecolor": "none", "alpha": 0.85})
            for ax in self.axes
        ]
        self._overlay = [
            [vline, hline, marker, label]
            for vline, hline, marker, label in zip(
                self.vlines, self.hlines, self.markers, self.labels)
        ]
        # Animated artists are skipped by normal draws, so the cached plot
        # background never contains the crosshair.  Only the small overlay
        # regions are blitted on each pointer move.
        for artists in self._overlay:
            for artist in artists:
                artist.set_animated(True)
        self._backgrounds: list[tuple[Any, list[Any], Any]] = []
        self._blit_ok = True

        figure.canvas.mpl_connect("draw_event", self._on_draw)
        figure.canvas.mpl_connect("motion_notify_event", self._on_move)
        figure.canvas.mpl_connect("figure_leave_event", self._on_leave)

    @staticmethod
    def _value_at(points: Sequence[tuple[float, float]],
                  times: Sequence[float], time_s: float) -> float | None:
        """Read the plotted value at ``time_s`` (linear interpolation)."""
        if not points:
            return None
        if time_s <= times[0]:
            return points[0][1]
        if time_s >= times[-1]:
            return points[-1][1]
        index = bisect_left(times, time_s)
        if index < len(times) and times[index] == time_s:
            return points[index][1]
        t0, y0 = points[index - 1]
        t1, y1 = points[index]
        if t1 <= t0:
            return y1
        return y0 + (y1 - y0) * (time_s - t0) / (t1 - t0)

    def _on_draw(self, _event: Any) -> None:
        """Cache a clean plot background after each full figure draw."""
        if not self._blit_ok:
            return
        try:
            self._backgrounds = [
                (ax, artists, self.figure.canvas.copy_from_bbox(ax.bbox))
                for ax, artists in zip(self.axes, self._overlay)
                if ax.get_visible()
            ]
        except Exception:
            self._blit_ok = False
            self._backgrounds = []

    def _redraw(self) -> None:
        """Blit only the crosshair regions, or fall back to a full redraw."""
        if not self._blit_ok or not self._backgrounds:
            self.figure.canvas.draw_idle()
            return
        canvas = self.figure.canvas
        try:
            for ax, artists, background in self._backgrounds:
                canvas.restore_region(background)
                for artist in artists:
                    if artist.get_visible():
                        ax.draw_artist(artist)
                canvas.blit(ax.bbox)
        except Exception:
            self._blit_ok = False
            self._backgrounds = []
            canvas.draw_idle()

    def update(self, time_s: float) -> None:
        """Move every crosshair to the same time and matching curve value."""
        for vline, hline, marker, label, points, times, value_label in zip(
            self.vlines, self.hlines, self.markers, self.labels,
            self.series, self.times, self.value_labels,
        ):
            vline.set_xdata((time_s, time_s))
            vline.set_visible(True)
            label.set_text(f"t = {time_s:.3g} s")
            label.set_visible(True)

            value = self._value_at(points, times, time_s)
            if value is None:
                hline.set_visible(False)
                marker.set_visible(False)
            else:
                hline.set_ydata((value, value))
                hline.set_visible(True)
                marker.set_data([time_s], [value])
                marker.set_visible(True)
                label.set_text(f"{value_label}: {value:.3g}\nt = {time_s:.3g} s")
        self._redraw()

    def hide(self) -> None:
        for vline, hline, marker, label in zip(
            self.vlines, self.hlines, self.markers, self.labels,
        ):
            vline.set_visible(False)
            hline.set_visible(False)
            marker.set_visible(False)
            label.set_visible(False)
        self._redraw()

    def _on_move(self, event: Any) -> None:
        if event.inaxes in self.axes and event.xdata is not None:
            self.update(float(event.xdata))

    def _on_leave(self, _event: Any) -> None:
        self.hide()


def build_summary_figure(
    crm_rows: list[dict[str, str]],
    fea_rows: list[dict[str, str]],
    *,
    interactive: bool = False,
) -> tuple[Any, tuple[Any, ...]]:
    """Create the four-panel summary figure; hide panels with no data."""
    import matplotlib.pyplot as plt

    figure, grid = plt.subplots(2, 2, figsize=(10, 7.5), constrained_layout=True)
    work_ax, fill_ax, stress_ax, deformation_ax = tuple(grid.flat)
    axes = (work_ax, fill_ax, stress_ax, deformation_ax)

    panels = (
        (work_ax, crm_rows, "work_j", "CRM work (J)", 1.0, "#1f77b4",
         "Rigid CRM excavation: work"),
        (fill_ax, crm_rows, "fill_ratio_mass", "fill ratio", 1.0, "#ff7f0e",
         "Rigid CRM excavation: fill ratio"),
        (stress_ax, fea_rows, "max_von_mises_pa", "peak von Mises (MPa)", 1.0e-6,
         "#d62728", "One-way CRM -> FEA: peak von Mises"),
        (deformation_ax, fea_rows, "max_deformation_m", "peak deformation (mm)",
         1.0e3, "#2ca02c", "One-way CRM -> FEA: peak deformation"),
    )
    series: list[list[tuple[float, float]]] = []
    value_labels: list[str] = []
    for ax, rows, key, label, scale, color, title in panels:
        points = plot_series(ax, rows, key, label, scale=scale, color=color)
        series.append(points)
        value_labels.append(label)
        if points:
            finish_axis(ax, title)
        else:
            ax.set_visible(False)

    if interactive:
        # Matplotlib holds bound-method callbacks weakly, so keep the cursor
        # alive on the figure for as long as the interactive window exists.
        figure.isotime_cursor = IsotimeCursor(figure, axes, series, value_labels)
    return figure, axes


def print_last(path: Path, rows: list[dict[str, str]]) -> None:
    print(f"{path}: {len(rows)} rows")
    if rows:
        last = rows[-1]
        keep = ("time_s", "work_j", "fill_ratio_mass", "excavated",
                "max_deformation_m", "max_von_mises_pa", "max_von_mises_raw_pa")
        print("  last: " + "  ".join(
            f"{key}={last[key]}" for key in keep if key in last
        ))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, required=True, help="study output directory")
    parser.add_argument("--show", action="store_true", help="open an interactive Matplotlib window")
    args = parser.parse_args()

    out_dir = args.output_dir.expanduser().resolve()
    crm_path = out_dir / "crm" / "summary.csv"
    fea_path = out_dir / "fea" / "summary.csv"
    crm = read_rows(crm_path)
    fea = read_rows(fea_path)
    print_last(crm_path, crm)
    print_last(fea_path, fea)
    if not crm and not fea:
        print(f"No summary CSV files found under {out_dir}")
        return 1

    if not args.show:
        import matplotlib
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figure, _axes = build_summary_figure(crm, fea, interactive=args.show)

    png = out_dir / "results_overview.png"
    figure.savefig(png, dpi=150)
    print(f"Wrote {png}")
    if args.show:
        plt.show()
    else:
        plt.close(figure)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
