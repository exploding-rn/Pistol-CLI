from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import shutil
import stat
import time

from pistol.config import PistolError

IGNORED = {".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".pistol", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".next"}


def is_link(path: Path) -> bool:
    """Include Windows junctions and other reparse points, even on Python 3.11."""
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


@dataclass
class Scan:
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def scan(root: Path, *, limit: int = 10000, seconds: float = 10) -> Scan:
    root = root.resolve()
    result = Scan()
    deadline = time.monotonic() + seconds
    pending = [root]
    seen = 0
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    seen += 1
                    if seen > limit or time.monotonic() > deadline:
                        result.warnings.append(f"Scan truncated at {limit} entries / {seconds:g} seconds.")
                        return result
                    path = Path(entry.path)
                    if is_link(path):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in IGNORED and not entry.name.endswith(".egg-info"):
                            pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        result.files.append(path)
        except OSError as exc:
            result.warnings.append(f"Cannot inspect {folder}: {exc}")
    result.files.sort()
    return result


def safe_remove(path: Path, boundary: Path) -> int:
    """Remove only a strict descendant, rejecting every reparse point first."""
    boundary = boundary.resolve()
    absolute = Path(os.path.abspath(path))
    if not absolute.is_relative_to(boundary) or absolute == boundary:
        raise PistolError(f"Refusing cleanup outside the allowed directory: {path}")
    relative = absolute.relative_to(boundary)
    check = boundary
    for part in relative.parts:
        check = check / part
        if is_link(check):
            raise PistolError(f"Refusing cleanup through a link or junction: {check}")
    if not absolute.resolve().is_relative_to(boundary):
        raise PistolError(f"Cleanup path escapes its boundary: {path}")
    size = 0
    if absolute.is_dir():
        for folder, dirs, files in os.walk(absolute, followlinks=False):
            for name in dirs + files:
                child = Path(folder) / name
                if is_link(child):
                    raise PistolError(f"Refusing cleanup of directory containing a link: {child}")
                if child.is_file():
                    size += child.stat().st_size
        shutil.rmtree(absolute)
    else:
        size = absolute.stat().st_size
        absolute.unlink()
    return size

