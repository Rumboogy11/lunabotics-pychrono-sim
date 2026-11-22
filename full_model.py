#!/usr/bin/env python3
"""Combined report runner: rigid CRM soil response plus FEA drum diagnostic.

This orchestrator runs the production rigid-body CRM driver and the
spindle-driven solid-FEA driver with one shared motion/soil setup, then writes a
combined report.  The FEA stage is included intentionally even though its
stress and fill results are not engineering-validated; see
``crm/FEA_COUPLING.md``.

By default the rigid CRM stage uses the full coarse OBJ at 4 mm SPH spacing and
a spacing-dependent timestep (8e-5 s at 4 mm).
The FEA stage uses the promoted 8k solid mesh at 12 mm spacing with diagnostic
settings (2.5e-4 s timestep, 2 BCE layers, GMRES/diagonal). Coupling has not been
validated and its mechanics diverge in longer runs. Use
``--smoke-test`` for a short end-to-end check of the orchestration itself.
"""

from __future__ import annotations

import argparse
import csv
import json
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "crm"))
from lunar_excavator_crm import suggested_sph_dt  # noqa: E402

COMBINED_FIELDS = [
    "model",
    "status",
    "final_time_s",
    "fill_ratio_mass",
    "peak_fill_ratio_mass",
    "inside_drum",
    "excavated",
    "fluid_torque_x_nm",
    "spindle_axis_torque_nm",
    "work_j",
    "specific_energy_j_kg",
    "max_nodal_displacement_m",
    "max_von_mises_pa",
]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--output-dir", type=Path, default=Path("Output_FullModel"),
                   help="combined output root (default: Output_FullModel)")
    p.add_argument("--duration", type=float, default=None,
                   help="simulated duration in seconds for both stages (default: 20)")
    p.add_argument("--smoke-test", action="store_true",
                   help="short 0.05 s end-to-end orchestration check")

    p.add_argument("--crm-spacing", type=float, default=None,
                   help="rigid CRM SPH spacing in m (default: 0.002)")
    p.add_argument("--crm-dt", type=float, default=None,
                   help="rigid CRM timestep in s (default: spacing-dependent, 4e-5 at 2 mm)")
    p.add_argument("--crm-fps", type=float, default=None,
                   help="rigid CRM summary cadence in Hz (default: 20)")

    p.add_argument("--fea-duration", type=float, default=None,
                   help="FEA duration in s (default: --duration)")
    p.add_argument("--fea-spacing", type=float, default=None,
                   help="FEA SPH spacing in m (default: 0.012)")
    p.add_argument("--fea-dt", type=float, default=None,
                   help="FEA timestep in s (default: 2.5e-4)")
    p.add_argument("--fea-fps", type=float, default=None,
                   help="FEA output cadence in Hz (default: 5)")
    p.add_argument("--fea-stress-stride", type=int, default=None,
                   help="evaluate stress every Nth element (default: 100)")
    p.add_argument("--fea-bce-layers", type=int, default=2,
                   help="FEA flex-BCE layers (default: 2, best tested interacting setup)")
    p.add_argument("--fea-threads", type=int, default=8,
                   help="Chrono/Eigen CPU threads for FEA (default: 8)")

    p.add_argument("--extra-crm", action="append", default=[],
                   help="extra argument passed to crm/run.sh (repeatable)")
    p.add_argument("--extra-fea", action="append", default=[],
                   help="extra argument passed to crm/run_fea.sh (repeatable)")
    p.add_argument("--strict-fea", action="store_true",
                   help="exit non-zero if the FEA stage fails (default: report and continue)")
    p.add_argument("--dry-run", action="store_true",
                   help="print commands and write a dry-run report without simulating")
    return p


def resolve_args(args: argparse.Namespace) -> argparse.Namespace:
    if args.smoke_test:
        args.duration = 0.05 if args.duration is None else args.duration
        args.crm_spacing = 0.012 if args.crm_spacing is None else args.crm_spacing
        args.crm_dt = suggested_sph_dt(args.crm_spacing) if args.crm_dt is None else args.crm_dt
        args.crm_fps = 200.0 if args.crm_fps is None else args.crm_fps
        args.fea_spacing = 0.012 if args.fea_spacing is None else args.fea_spacing
        args.fea_dt = 2.5e-4 if args.fea_dt is None else args.fea_dt
        args.fea_fps = 200.0 if args.fea_fps is None else args.fea_fps
        args.fea_stress_stride = 50 if args.fea_stress_stride is None else args.fea_stress_stride
    else:
        args.duration = 20.0 if args.duration is None else args.duration
        args.crm_spacing = 0.002 if args.crm_spacing is None else args.crm_spacing
        args.crm_dt = suggested_sph_dt(args.crm_spacing) if args.crm_dt is None else args.crm_dt
        args.crm_fps = 20.0 if args.crm_fps is None else args.crm_fps
        args.fea_spacing = 0.012 if args.fea_spacing is None else args.fea_spacing
        args.fea_dt = 2.5e-4 if args.fea_dt is None else args.fea_dt
        args.fea_fps = 5.0 if args.fea_fps is None else args.fea_fps
        args.fea_stress_stride = 100 if args.fea_stress_stride is None else args.fea_stress_stride
    args.fea_duration = args.duration if args.fea_duration is None else args.fea_duration
    return args


def parse_extras(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        result.extend(shlex.split(value))
    return result


def build_commands(args: argparse.Namespace, out_dir: Path) -> tuple[list[str], list[str]]:
    crm_dir = out_dir / "crm"
    fea_dir = out_dir / "fea"
    crm = [
        str(ROOT / "crm" / "run.sh"),
        "--duration", f"{args.duration:g}",
        "--spacing", f"{args.crm_spacing:g}",
        "--dt", f"{args.crm_dt:g}",
        "--fps", f"{args.crm_fps:g}",
        "--output-dir", str(crm_dir),
    ]
    crm.extend(parse_extras(args.extra_crm))

    fea = [
        str(ROOT / "crm" / "run_fea.sh"),
        "--duration", f"{args.fea_duration:g}",
        "--spacing", f"{args.fea_spacing:g}",
        "--dt", f"{args.fea_dt:g}",
        "--fps", f"{args.fea_fps:g}",
        "--stress-stride", str(args.fea_stress_stride),
        "--solver-iterations", "20",
        "--solver-tolerance", "1e-3",
        "--bce-layers", str(args.fea_bce_layers),
        "--num-threads", str(args.fea_threads),
        "--output-dir", str(fea_dir),
    ]
    fea.extend(parse_extras(args.extra_fea))
    return crm, fea


def run_stage(name: str, command: list[str], log_path: Path) -> int:
    print(f"[{name}] command: {shlex.join(command)}", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        print(f"[{name}] log: {log_path}", flush=True)
        proc = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            log.write(line)
            log.flush()
            print(f"[{name}] {line}", end="", flush=True)
        return proc.wait()


def read_last_summary(path: Path) -> dict[str, str] | None:
    if not path.is_file():
        return None
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return rows[-1] if rows else None


def as_number(value: str | None) -> float | str | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def stage_report(name: str, command: list[str], command_dir: Path,
                 returncode: int | None) -> dict[str, Any]:
    summary = read_last_summary(command_dir / "summary.csv")
    config_path = command_dir / "config.json"
    status = "dry-run"
    if returncode is not None:
        status = "completed" if returncode == 0 else "failed"
    return {
        "status": status,
        "returncode": returncode,
        "command": command,
        "log": str(command_dir.parent / f"{name}.log"),
        "output_dir": str(command_dir),
        "summary_csv": str(command_dir / "summary.csv"),
        "config_json": str(config_path) if config_path.is_file() else None,
        "final_metrics": {key: as_number(value) for key, value in (summary or {}).items()},
    }


def write_reports(out_dir: Path, report: dict[str, Any]) -> None:
    with (out_dir / "combined_report.json").open("w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
        handle.write("\n")

    with (out_dir / "combined_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMBINED_FIELDS)
        writer.writeheader()
        for model in ("crm", "fea"):
            metrics = report[model]["final_metrics"]
            final_time = metrics.get("time_s", "")
            if model == "fea" and isinstance(final_time, float):
                final_time = f"{final_time:.3f}"
            writer.writerow({
                "model": model,
                "status": report[model]["status"],
                "final_time_s": final_time,
                "fill_ratio_mass": metrics.get("fill_ratio_mass", ""),
                "peak_fill_ratio_mass": metrics.get("peak_fill_ratio_mass", ""),
                "inside_drum": metrics.get("inside_drum", ""),
                "excavated": metrics.get("excavated", ""),
                "fluid_torque_x_nm": metrics.get("fluid_torque_x_nm", ""),
                "spindle_axis_torque_nm": metrics.get("spindle_axis_torque_nm", ""),
                "work_j": metrics.get("work_j", ""),
                "specific_energy_j_kg": metrics.get("specific_energy_j_kg", ""),
                "max_nodal_displacement_m": metrics.get("max_nodal_displacement_m", ""),
                "max_von_mises_pa": metrics.get("max_von_mises_pa", ""),
            })


def main() -> int:
    args = resolve_args(build_parser().parse_args())
    out_dir = args.output_dir.resolve()
    if out_dir.exists() and not out_dir.is_dir():
        raise SystemExit(f"output path exists and is not a directory: {out_dir}")
    for directory in (out_dir, out_dir / "crm", out_dir / "fea"):
        directory.mkdir(parents=True, exist_ok=True)

    crm_cmd, fea_cmd = build_commands(args, out_dir)
    report: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
            "model": "combined rigid CRM production + spindle-driven FEA diagnostic",
        "requested_duration_s": args.duration,
        "requested_fea_duration_s": args.fea_duration,
        "smoke_test": args.smoke_test,
        "dry_run": args.dry_run,
        "status": "running",
        "notes": [
            "FEA is included intentionally despite not being engineering-validated.",
            "The FEA stage uses 12 mm spacing and unvalidated diagnostic settings; it may crash or diverge.",
            "The rigid CRM stage remains the production source for fill ratio, torque, and specific energy.",
        ],
    }

    if args.dry_run:
        report["status"] = "dry-run"
        report["crm"] = stage_report("crm", crm_cmd, out_dir / "crm", None)
        report["fea"] = stage_report("fea", fea_cmd, out_dir / "fea", None)
        write_reports(out_dir, report)
        print("Dry run complete; no simulation executed.", flush=True)
        print(f"Report: {out_dir / 'combined_report.json'}", flush=True)
        return 0

    crm_code = run_stage("crm", crm_cmd, out_dir / "crm.log")
    report["crm"] = stage_report("crm", crm_cmd, out_dir / "crm", crm_code)
    if crm_code != 0:
        report["status"] = "crm_failed"
        report["fea"] = stage_report("fea", fea_cmd, out_dir / "fea", None)
        report["fea"]["status"] = "not_run"
        write_reports(out_dir, report)
        print(f"CRM stage failed with exit code {crm_code}.", file=sys.stderr, flush=True)
        return crm_code

    fea_code = run_stage("fea", fea_cmd, out_dir / "fea.log")
    report["fea"] = stage_report("fea", fea_cmd, out_dir / "fea", fea_code)
    if fea_code != 0:
        report["status"] = "completed_with_fea_failure"
        print(
            f"FEA stage failed with exit code {fea_code}; combined report still written.",
            file=sys.stderr,
            flush=True,
        )
    else:
        report["status"] = "completed"

    write_reports(out_dir, report)
    print(f"Combined report: {out_dir / 'combined_report.json'}", flush=True)
    print(f"Combined metrics: {out_dir / 'combined_summary.csv'}", flush=True)

    if args.strict_fea and fea_code != 0:
        return fea_code if fea_code > 0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
