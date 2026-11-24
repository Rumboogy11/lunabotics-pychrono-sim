#!/usr/bin/env python3
"""Shared runtime progress labels for the CRM and FEA drivers.

Concurrent studies stream two solvers into one GUI log, so every frame and
coarse time-progress line names the stage that produced it.  The formatting
lives here to keep the labels identical and to allow tests without importing
the Chrono-dependent drivers.
"""

from __future__ import annotations


def frame_status(stage: str, frame: int, time_s: float, detail: str) -> str:
    """Format one stage-labelled frame status line."""
    return f"{stage} frame {frame:05d}  t={time_s:7.3f}s  {detail}"


def time_progress(stage: str, time_s: float, duration_s: float) -> str:
    """Format one stage-labelled coarse time-progress line."""
    return f"  {stage} t={time_s:7.3f}s / {duration_s:g}s"
