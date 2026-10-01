"""Explicit, reviewable repairs. No source rewriting or implicit downloads."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import venv

from . import chamber
from .config import PistolError, atomic_json, read_json, state_lock
from .fire import is_alive, project_python, record_path
from .ports import available, free_port
from .utils.project import detect, root_path, validate_entrypoint


@dataclass
class Repair:
    kind: str
    description: str
    target: str


def plan(project: Path | str | None = None, *, chamber_name: str | None = None, install_deps: bool = False) -> list[Repair]:
    selected = chamber.get(chamber_name) if chamber_name else chamber.current(project)
    root = Path(selected.project) if selected else root_path(project)
    result = []
    source_missing = False
    if selected:
        try:
            validate_entrypoint(root, selected.entrypoint)
        except PistolError:
            source_missing = True
    target = selected.directory / "venv" if selected else root / ".venv"
    interpreter = target / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    runtime = selected.runtime if selected else detect(root).runtime
    if not interpreter.is_file() and runtime == "python" and not source_missing:
        result.append(Repair("venv", "Create/complete the missing Python virtual environment", str(target)))
    if (root / ".env.example").is_file() and not (root / ".env").exists():
        result.append(Repair("dotenv", "Copy .env.example to a new .env", str(root / ".env")))
    path = record_path(selected, root)
    record = read_json(path, {})
    if record.get("status") == "running" and not is_alive(record):
        result.append(Repair("stale", "Archive the stale Pistol PID record (retain failure evidence)", str(path)))
    if selected and not available(selected.port) and not is_alive(record):
        result.append(Repair("port", f"Assign an available port instead of {selected.port}", selected.name))
    if selected and selected.status != "ready" and not source_missing:
        result.append(Repair("ready", "Verify runtime directories and complete chamber metadata", selected.name))
    if install_deps:
        if runtime != "python":
            raise PistolError("--install-deps currently supports Python projects. Use your runtime's package manager for this project.")
        dependency = root / "requirements.txt"
        if dependency.is_file():
            result.append(Repair("install", "Install requirements.txt using pip (may access the network and execute package build code)", str(dependency)))
        elif (root / "pyproject.toml").is_file():
            result.append(Repair("install-project", "Install this project editable using pip (may access the network and execute package build code)", str(root)))
        else:
            raise PistolError("No requirements.txt or pyproject.toml found for dependency installation.")
    return result


def run(project: Path | str | None = None, *, chamber_name: str | None = None, install_deps: bool = False) -> list[Repair]:
    """Apply repairs. Calling this library function explicitly authorizes the plan."""
    selected = chamber.get(chamber_name) if chamber_name else chamber.current(project)
    root = Path(selected.project) if selected else root_path(project)
    repairs = plan(root, chamber_name=selected.name if selected else None, install_deps=install_deps)
    if is_alive(read_json(record_path(selected, root), {})) and any(r.kind in {"venv", "install", "install-project", "ready"} for r in repairs):
        raise PistolError("Stop the Pistol process before changing its environment.")
    completed = []
    for repair in repairs:
        if repair.kind == "venv":
            venv.EnvBuilder(with_pip=True).create(repair.target)
        elif repair.kind == "dotenv":
            try:
                with (root / ".env").open("xb") as stream:
                    stream.write((root / ".env.example").read_bytes())
            except FileExistsError:
                continue
        elif repair.kind == "stale":
            with state_lock():
                path = Path(repair.target)
                record = read_json(path, {})
                if record.get("status") == "running" and not is_alive(record):
                    atomic_json(path.parent / "last-process.json", record)
                    path.unlink()
        elif repair.kind in {"port", "ready"}:
            with state_lock():
                item = chamber.get(repair.target)
                chamber._assert_stopped(item)
                if repair.kind == "port":
                    item.port = free_port({c.port for c in chamber.list_chambers()})
                else:
                    validate_entrypoint(Path(item.project), item.entrypoint)
                    if item.runtime == "python" and not item.python.is_file():
                        raise PistolError("Chamber Python is still missing.")
                    for folder in ("logs", "state", "cache", "temp"):
                        (item.directory / folder).mkdir(exist_ok=True)
                    item.status = "ready"
                chamber.save(item)
                selected = item
        elif repair.kind in {"install", "install-project"}:
            arguments = ["-r", repair.target] if repair.kind == "install" else ["-e", repair.target]
            result = subprocess.run([project_python(root, selected), "-m", "pip", "install", *arguments], cwd=root)
            if result.returncode:
                raise PistolError(f"pip failed with exit code {result.returncode}; prior repairs remain applied.")
        completed.append(repair)
    return completed
