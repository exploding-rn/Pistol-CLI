"""Allowlisted cleanup. Never follow links/junctions or remove the project root."""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import time

from .chamber import list_chambers
from .config import PistolError, project_state, read_json
from .fire import is_alive
from .utils.files import IGNORED, is_link, safe_remove
from .utils.project import root_path

CACHES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".vite", ".parcel-cache"}


@dataclass
class CleanupTarget:
    path: str
    boundary: str
    reason: str


@dataclass
class CleanupResult:
    removed: list[str] = field(default_factory=list)
    bytes_freed: int = 0
    skipped: list[str] = field(default_factory=list)


def plan(project: Path | str | None = None, *, deep: bool = False) -> list[CleanupTarget]:
    root = root_path(project)
    result = []
    deadline = time.monotonic() + 10
    visited = 0
    for folder, directories, _ in os.walk(root, followlinks=False):
        visited += 1
        if visited > 10000 or time.monotonic() > deadline:
            raise PistolError("Cleanup scan exceeded its 10-second/10,000-directory limit; select a smaller project.")
        keep = []
        for name in directories:
            target = Path(folder) / name
            if is_link(target):
                continue
            if name in CACHES:
                result.append(CleanupTarget(str(target), str(root), "project cache"))
            elif name == ".next":
                cached = target / "cache"
                if cached.is_dir() and not is_link(cached):
                    result.append(CleanupTarget(str(cached), str(root), "Next.js cache"))
            elif name not in IGNORED:
                keep.append(name)
        directories[:] = keep
    if deep:
        for name in (".venv", "node_modules", "target", "build", "dist"):
            target = root / name
            if target.is_dir() and not is_link(target):
                result.append(CleanupTarget(str(target), str(root), "deep cleanup: dependencies/build output"))
    states = [project_state(root)] + [c.directory for c in list_chambers() if Path(c.project).resolve() == root]
    for state in states:
        if is_alive(read_json(state / "process.json", {})):
            continue
        for name in ("temp", "cache"):
            target = state / name
            if target.is_dir() and not is_link(target):
                result.append(CleanupTarget(str(target), str(state), "Pistol disposable runtime"))
        for log in (state / "logs").glob("*.log"):
            if not is_link(log) and log.stat().st_mtime < time.time() - 7 * 86400:
                result.append(CleanupTarget(str(log), str(state), "Pistol log older than 7 days"))
    return result


def run(project: Path | str | None = None, *, deep: bool = False) -> CleanupResult:
    """Explicit library cleanup. CLI requires confirmation when deep=True."""
    result = CleanupResult()
    for target in plan(project, deep=deep):
        try:
            result.bytes_freed += safe_remove(Path(target.path), Path(target.boundary))
            result.removed.append(target.path)
        except (PistolError, OSError) as exc:
            result.skipped.append(f"{target.path}: {exc}")
    return result

