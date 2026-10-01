"""Portable polling watcher with debounce and controlled child restarts."""
from __future__ import annotations

from pathlib import Path
import time

from .chamber import current
from .config import PistolError
from .fire import launch, stop_record
from .utils.files import scan
from .utils.project import root_path


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    inventory = scan(root)
    if inventory.warnings:
        raise PistolError("Watcher cannot obtain a complete bounded snapshot: " + "; ".join(inventory.warnings))
    result = {}
    for path in inventory.files:
        if path.suffix in {".pyc", ".pyo", ".log", ".tmp"}:
            continue
        try:
            info = path.stat()
            result[str(path)] = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            pass
    return result


def run(project: Path | str | None = None, *, command: list[str] | None = None, interval: float = 0.5, debounce: float = 0.4) -> int:
    if interval < 0.1 or debounce < 0:
        raise PistolError("Watch interval must be >= 0.1 seconds and debounce >= 0.")
    root = root_path(project)
    selected = current(root)
    previous = snapshot(root)
    child = launch(root, selected=selected, command=command)
    pending = None
    finished = False
    try:
        while True:
            time.sleep(interval)
            latest = snapshot(root)
            if latest != previous:
                pending = time.monotonic()
                previous = latest
            if child.process.poll() is not None and not finished:
                code = child.finish()
                print(f"[watch] Process exited ({code}); waiting for changes.", flush=True)
                finished = True
            if pending is not None and time.monotonic() - pending >= debounce:
                print("[watch] Files changed; restarting.", flush=True)
                if not finished:
                    stop_record(child.record)
                    child.finish()
                child = launch(root, selected=selected, command=command)
                finished = False
                pending = None
    except KeyboardInterrupt:
        return 130
    finally:
        if not finished:
            stop_record(child.record)
            child.finish()

