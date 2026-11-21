#!/usr/bin/env python3
"""Small helpers for turning one-way FEA scalar summaries into viewer tables.

The structural driver writes per-frame deformation VTU files only when the
full 3D FEA stress animation is explicitly enabled; that export dominated
runtime.  ``fea/summary.csv`` always records peak stress, deformation, load,
and work histories.  This module derives a compact, ParaView-friendly CSV with
useful units (MPa/mm) without importing NumPy, VTK, or ParaView.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any


# Source column -> derived column, scale.  Keep the derived names stable: the
# ParaView viewer selects series by these names.
DERIVED_FIELDS: tuple[tuple[str, str, float], ...] = (
    ("frame", "frame", 1.0),
    ("time_s", "time_s", 1.0),
    ("max_von_mises_pa", "max_von_mises_MPa", 1.0e-6),
    ("max_von_mises_raw_pa", "max_von_mises_raw_MPa", 1.0e-6),
    ("max_deformation_m", "max_deformation_mm", 1.0e3),
    ("applied_force_n", "applied_force_n", 1.0),
    ("applied_torque_nm", "applied_torque_nm", 1.0),
    ("applied_axis_torque_nm", "applied_axis_torque_nm", 1.0),
    ("input_power_w", "input_power_w", 1.0),
    ("work_j", "work_j", 1.0),
    ("drum_z_m", "drum_z_m", 1.0),
    ("spin_deg", "spin_deg", 1.0),
    ("hot_element_volume_m3", "hot_element_volume_m3", 1.0),
)

# Fields used to decide whether a CSV really is an FEA summary.
REQUIRED_FIELDS = ("time_s",)

KNOWN_SERIES = (
    "max_von_mises_MPa",
    "max_von_mises_raw_MPa",
    "max_deformation_mm",
    "applied_force_n",
    "applied_torque_nm",
    "applied_axis_torque_nm",
    "input_power_w",
    "work_j",
    "max_von_mises_pa",
    "max_von_mises_raw_pa",
    "max_deformation_m",
)

# Preferred chart series, in display order.  Derived names come first; raw
# fallbacks keep older, un-regenerated study directories viewable.
PREFERRED_SERIES: tuple[tuple[str, ...], ...] = (
    ("max_von_mises_MPa", "max_von_mises_pa"),
    ("max_deformation_mm", "max_deformation_m"),
    ("applied_force_n",),
    ("applied_torque_nm",),
    ("work_j",),
    ("input_power_w",),
)


def _finite_float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: float) -> str:
    """Compact numeric formatting without trailing noise."""
    return f"{value:.9g}"


def read_summary_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a small FEA summary CSV; return (header, rows)."""
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            header = list(reader.fieldnames or [])
            rows = [row for row in reader]
    except (OSError, csv.Error, UnicodeError):
        return [], []
    return header, rows


def is_summary_table(path: Path | None) -> bool:
    """True when *path* has a time column and at least one FEA scalar series."""
    if not path or not path.is_file():
        return False
    header, rows = read_summary_rows(path)
    if not rows or not all(name in header for name in REQUIRED_FIELDS):
        return False
    return any(name in header for name in KNOWN_SERIES)


def write_fea_summary_csv(source: Path, destination: Path) -> bool:
    """Derive a compact MPa/mm table from an FEA ``summary.csv``.

    Returns False when the source is missing/unusable, so callers can leave an
    older table in place or fall back to the raw CSV.
    """
    header, rows = read_summary_rows(source)
    if not rows or not all(name in header for name in REQUIRED_FIELDS):
        return False

    fields = [(src, dst, scale) for src, dst, scale in DERIVED_FIELDS if src in header]
    if not any(dst in KNOWN_SERIES for _src, dst, _scale in fields):
        return False

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow([dst for _src, dst, _scale in fields])
            for row in rows:
                time = _finite_float(row.get("time_s"))
                if time is None:
                    continue
                values: list[str] = []
                for src, _dst, scale in fields:
                    number = _finite_float(row.get(src))
                    values.append("" if number is None else _fmt(number * scale))
                writer.writerow(values)
    except (OSError, csv.Error, ValueError):
        return False
    return True


def _manifest_paths(manifest: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    series = manifest.get("series")
    if isinstance(series, dict):
        for key in ("fea_summary", "fea_summary_source"):
            value = series.get(key)
            if value:
                paths.append(Path(str(value)))
    value = manifest.get("fea_summary")
    if value:
        paths.append(Path(str(value)))
    study_dir = manifest.get("study_dir")
    if study_dir:
        paths.append(Path(str(study_dir)) / "fea" / "summary.csv")
    return paths


def find_fea_summary_csv(export_dir: Path, manifest: dict[str, Any] | None = None) -> Path | None:
    """Find a usable FEA scalar table without arbitrary directory searches."""
    manifest = manifest if isinstance(manifest, dict) else {}
    candidates: list[Path] = [
        export_dir / "fea_summary.csv",
        export_dir / "fea" / "summary.csv",
        export_dir.parent / "fea" / "summary.csv",
    ]
    candidates.extend(_manifest_paths(manifest))
    checked: set[Path] = set()
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else (export_dir / candidate)
        try:
            path = path.resolve()
        except OSError:
            pass
        if path in checked:
            continue
        checked.add(path)
        if is_summary_table(path):
            return path
    return None


def find_raw_fea_summary(export_dir: Path, manifest: dict[str, Any] | None = None) -> Path | None:
    """Locate the driver-written ``fea/summary.csv`` for derivation."""
    manifest = manifest if isinstance(manifest, dict) else {}
    candidates: list[Path] = []
    series = manifest.get("series")
    if isinstance(series, dict) and series.get("fea_summary_source"):
        candidates.append(Path(str(series["fea_summary_source"])))
    candidates.extend([
        export_dir.parent / "fea" / "summary.csv",
        export_dir / "fea" / "summary.csv",
    ])
    study_dir = manifest.get("study_dir")
    if study_dir:
        candidates.append(Path(str(study_dir)) / "fea" / "summary.csv")
    checked: set[Path] = set()
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else (export_dir / candidate)
        try:
            path = path.resolve()
        except OSError:
            pass
        if path in checked:
            continue
        checked.add(path)
        if is_summary_table(path):
            return path
    return None


def ensure_fea_summary_csv(export_dir: Path, manifest: dict[str, Any] | None = None) -> Path | None:
    """Return a ParaView-ready FEA table, deriving it in export_dir if needed.

    This keeps existing study directories viewable: the viewer can create the
    tiny derived table on demand instead of requiring a fresh exporter run.
    """
    existing = find_fea_summary_csv(export_dir, manifest)
    raw = find_raw_fea_summary(export_dir, manifest)
    if raw is None:
        return existing
    derived = export_dir / "fea_summary.csv"
    if existing is not None and existing.resolve() == derived.resolve():
        try:
            if existing.stat().st_mtime >= raw.stat().st_mtime:
                return existing
        except OSError:
            return existing
    if write_fea_summary_csv(raw, derived):
        return derived
    return existing


def choose_fea_series(available: list[str] | tuple[str, ...],
                      requested: str | None = None) -> list[str]:
    """Pick sensible chart series, or a user-provided comma-separated subset."""
    names = {str(name).strip() for name in available if str(name).strip()}
    if requested:
        wanted = [part.strip() for part in str(requested).split(",") if part.strip()]
        return [name for name in wanted if name in names]
    chosen: list[str] = []
    for alternatives in PREFERRED_SERIES:
        for name in alternatives:
            if name in names:
                chosen.append(name)
                break
    return chosen


def load_manifest(path: Path) -> dict[str, Any]:
    """Read an export manifest, tolerating a missing/corrupt file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


__all__ = [
    "DERIVED_FIELDS",
    "KNOWN_SERIES",
    "PREFERRED_SERIES",
    "choose_fea_series",
    "ensure_fea_summary_csv",
    "find_fea_summary_csv",
    "find_raw_fea_summary",
    "is_summary_table",
    "load_manifest",
    "read_summary_rows",
    "write_fea_summary_csv",
]
