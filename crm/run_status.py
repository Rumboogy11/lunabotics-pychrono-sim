#!/usr/bin/env python3
"""Shared runtime progress labels for the CRM and FEA drivers.

Concurrent studies stream two solvers into one GUI log, so every frame and
coarse time-progress line names the stage that produced it in square brackets.
The formatting lives here to keep the labels identical and to allow tests
without importing the Chrono-dependent drivers.
"""

from __future__ import annotations


def frame_status(stage: str, frame: int, time_s: float, detail: str) -> str:
    """Format one stage-labelled frame status line."""
    return f"[{stage}] frame {frame:05d}  t={time_s:7.3f}s  {detail}"


def time_progress(stage: str, time_s: float, duration_s: float) -> str:
    """Format one stage-labelled coarse time-progress line."""
    return f"  [{stage}] t={time_s:7.3f}s / {duration_s:g}s"


def integer_progress_times(time_s: float, next_integer_s: float) -> tuple[list[float], float]:
    """Return integer-second stamps reached by ``time_s`` and the next target.

    The one-way replay only moves between output times, so it can cross several
    whole seconds in one step.  Emitting one short progress line per integer
    keeps its console cadence aligned with the rigid-CRM driver.
    """
    due: list[float] = []
    while next_integer_s <= time_s + 1.0e-12:
        due.append(next_integer_s)
        next_integer_s += 1.0
    return due, next_integer_s
