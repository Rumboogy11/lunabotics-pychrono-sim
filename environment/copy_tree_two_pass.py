#!/usr/bin/env python3
"""Copy directory trees onto filesystems with unreliable symlink support.

The project directory is on an NTFS mount.  ntfs3 can create symlinks, but
conda environments contain many relative symlinks whose targets are not valid
at creation time, so normal copy tools and conda linkers fail.  This helper
copies regular files first, then replaces every symlink with a real copy of
its resolved target.  The destination is self-contained and contains no
symlinks.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def prepare_destination(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def copy_dereferenced(src: Path, dst: Path, exclude: set[str], stack: set[str]) -> None:
    src = src.resolve()
    marker = str(src)
    if marker in stack:
        return
    stack.add(marker)
    dst.mkdir(parents=True, exist_ok=True)

    for root, dirs, files in os.walk(src, followlinks=False):
        root_path = Path(root)
        rel_root = root_path.relative_to(src)
        out_root = dst if rel_root == Path(".") else dst / rel_root
        out_root.mkdir(parents=True, exist_ok=True)

        for name in sorted(dirs):
            if name in exclude:
                continue
            src_path = root_path / name
            if src_path.is_symlink():
                copy_link(src_path, out_root / name, exclude, stack)
            else:
                (out_root / name).mkdir(parents=True, exist_ok=True)

        for name in sorted(files):
            if name in exclude:
                continue
            src_path = root_path / name
            dst_path = out_root / name
            if src_path.is_symlink():
                copy_link(src_path, dst_path, exclude, stack)
            else:
                shutil.copy2(src_path, dst_path)

    stack.remove(marker)


def copy_link(src_path: Path, dst_path: Path, exclude: set[str], stack: set[str]) -> None:
    target = src_path.resolve(strict=False)
    if not target.exists():
        return
    prepare_destination(dst_path)
    if target.is_dir():
        copy_dereferenced(target, dst_path, exclude, stack)
    elif target.is_file():
        shutil.copy2(target, dst_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", type=Path, help="Source directories")
    parser.add_argument("-d", "--destination", required=True, type=Path, help="Destination prefix")
    parser.add_argument("--exclude", action="append", default=[], help="Directory/file name to exclude")
    args = parser.parse_args()

    exclude = set(args.exclude)
    for source in args.sources:
        if not source.is_dir():
            print(f"Not a directory: {source}", file=sys.stderr)
            return 1
        copy_dereferenced(source, args.destination, exclude, set())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
