#!/usr/bin/env python3
"""Summarise and plot a completed CRM -> one-way FEA study.

The wizard calls this automatically after a successful run to save
``results_overview.png``.  Run it again with ``--show`` to open an interactive
Matplotlib window:

    ./chrono-env/bin/python view_results.py --output-dir Output_study --show
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Any


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


def add_series(ax: Any, rows: list[dict[str, str]], key: str, label: str,
               *, scale: float = 1.0, color: str | None = None) -> bool:
    x = values(rows, "time_s")
    y = values(rows, key, scale)
    if not x or not y or len(x) != len(y):
        return False
    ax.plot(x, y, label=label, color=color, linewidth=1.6)
    return True


def finish_axis(ax: Any, title: str, xlabel: str = "time (s)") -> None:
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.grid(alpha=0.25)
    handles, labels = ax.get_legend_handles_labels()
    if handles:
        ax.legend(loc="best", fontsize="small")


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
    parser = argparse.ArgumentParser(description=__doc__)
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

    figure, axes = plt.subplots(2, 1, figsize=(9, 8), constrained_layout=True)
    crm_ok = add_series(axes[0], crm, "work_j", "CRM work (J)", color="#1f77b4")
    crm_ok |= add_series(axes[0], crm, "fill_ratio_mass", "fill ratio (mass)", color="#ff7f0e")
    if crm_ok:
        finish_axis(axes[0], "Rigid CRM excavation")
    else:
        axes[0].set_visible(False)

    fea_ok = add_series(axes[1], fea, "max_von_mises_pa", "peak von Mises (MPa)",
                        scale=1.0e-6, color="#d62728")
    fea_ok |= add_series(axes[1], fea, "max_deformation_m", "peak deformation (mm)",
                         scale=1.0e3, color="#2ca02c")
    if fea_ok:
        finish_axis(axes[1], "One-way CRM -> FEA response")
    else:
        axes[1].set_visible(False)

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
