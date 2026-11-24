"""Regression suite for the pychrono study pipeline.

The repository root and the tests directory are added to ``sys.path`` so the
suite works with ``python -m unittest discover`` from the repository root,
``python -m unittest discover -s tests``, and direct test-file execution.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parent

for _path in (str(ROOT), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
