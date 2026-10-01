from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import venv

from .config import PistolError, atomic_json, config_dir, data_dir, read_json, settings, state_lock, valid_name
from .utils.files import is_link, safe_remove
from .utils.project import detect, root_path, runtime_from_entrypoint, validate_entrypoint


@dataclass
class Chamber:
    name: str
    project: str
    port: int
    runtime: str
    entrypoint: list[str]
    created: str
    status: str = "ready"
    env: dict[str, str] = field(default_factory=dict)
    schema: int = 1

    @property
    def directory(self) -> Path:
        return data_dir() / "chambers" / valid_name(self.name)

    @property
    def python(self) -> Path:
        return self.directory / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def get(name: str) -> Chamber:
    name = valid_name(name)
    folder = data_dir() / "chambers" / name
    if folder.exists() and is_link(folder):
        raise PistolError(f"Chamber directory is a link or junction: {folder}")
    payload = read_json(folder / "chamber.json")
    if payload is None:
        raise PistolError(f"Chamber '{name}' does not exist.")
    try:
        item = Chamber(**payload)
        from .ports import validate_port
        validate_port(item.port)
        if item.name != name or item.schema != 1 or not Path(item.project).is_absolute():
            raise ValueError("invalid name, schema, or project")
        if item.runtime not in {"python", "node", "rust", "command"} or not isinstance(item.entrypoint, list) or not all(isinstance(x, str) for x in item.entrypoint):
            raise ValueError("invalid runtime or entrypoint")
        if not isinstance(item.env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in item.env.items()):
            raise ValueError("invalid environment")
        return item
    except (TypeError, ValueError) as exc:
        raise PistolError(f"Invalid chamber metadata: {folder / 'chamber.json'}: {exc}") from exc


def list_chambers() -> list[Chamber]:
    root = data_dir() / "chambers"
    if not root.exists():
        return []
    return [get(path.name) for path in sorted(root.iterdir()) if path.is_dir() and (path / "chamber.json").is_file()]


def save(item: Chamber) -> None:
    atomic_json(item.directory / "chamber.json", asdict(item))


def create(name: str, project: Path | str | None = None, port: int | None = None, *, runtime: str | None = None, entrypoint: list[str] | None = None, env: dict[str, str] | None = None, create_venv: bool = True) -> Chamber:
    from .ports import available, free_port, validate_port
    name = valid_name(name)
    info = detect(project)
    if not info.root.is_dir():
        raise PistolError(f"Project directory does not exist: {info.root}")
    configured_entrypoint = list(entrypoint) if entrypoint is not None else info.entrypoint
    if entrypoint is not None:
        validate_entrypoint(info.root, configured_entrypoint)
    runtime = runtime or runtime_from_entrypoint(entrypoint) or info.runtime
    if runtime not in {"python", "node", "rust", "command"}:
        raise PistolError("Runtime must be python, node, rust, or command.")
    with state_lock(timeout=120):
        directory = data_dir() / "chambers" / name
        if directory.exists():
            raise PistolError(f"Chamber '{name}' already exists (or has an incomplete creation).")
        reserved = {c.port for c in list_chambers()}
        port = validate_port(port) if port is not None else free_port(reserved)
        if port in reserved or not available(port):
            raise PistolError(f"Port {port} is already occupied or assigned to another chamber.")
        item = Chamber(name, str(info.root), port, runtime, configured_entrypoint, datetime.now(timezone.utc).isoformat(), env=env or {})
        directory.mkdir(parents=True)
        try:
            for child in ("logs", "temp", "cache", "state"):
                (directory / child).mkdir()
            item.status = "creating"
            save(item)
            if runtime == "python" and create_venv:
                venv.EnvBuilder(with_pip=True).create(directory / "venv")
            item.status = "ready"
            save(item)
        except BaseException:
            item.status = "incomplete"
            save(item)
            raise
        return item


def current(project: Path | str | None = None) -> Chamber | None:
    root = root_path(project)
    candidates = [c for c in list_chambers() if Path(c.project).resolve() == root]
    active = settings().get("active_chamber")
    selected = next((c for c in candidates if c.name == active), None)
    return selected or (candidates[0] if len(candidates) == 1 else None)


def switch(name: str) -> Chamber:
    with state_lock():
        item = get(name)
        payload = settings()
        payload["active_chamber"] = item.name
        atomic_json(config_dir() / "config.json", payload)
    return item


def _assert_stopped(item: Chamber):
    from .fire import is_alive
    record = read_json(item.directory / "process.json", {})
    if is_alive(record):
        raise PistolError(f"Stop chamber '{item.name}' first: pistol fire {item.name} --stop")


def delete(name: str) -> None:
    """Explicit library deletion; the CLI asks for confirmation before calling."""
    from .magazine import list_magazines, save as save_magazine
    with state_lock():
        item = get(name)
        _assert_stopped(item)
        for magazine in list_magazines():
            if item.name in magazine.chambers:
                magazine.chambers.remove(item.name)
                save_magazine(magazine)
        payload = settings()
        if payload.get("active_chamber") == item.name:
            payload.pop("active_chamber")
            atomic_json(config_dir() / "config.json", payload)
        safe_remove(item.directory, data_dir() / "chambers")


def reset(name: str) -> Chamber:
    """Recreate the venv and disposable runtime; preserve config and logs."""
    with state_lock(timeout=120):
        item = get(name)
        _assert_stopped(item)
        validate_entrypoint(Path(item.project), item.entrypoint)
        item.status = "incomplete"
        save(item)
        for child in ("venv", "temp", "cache", "state", "process.json"):
            target = item.directory / child
            if target.exists():
                safe_remove(target, item.directory)
        for child in ("temp", "cache", "state"):
            (item.directory / child).mkdir()
        if item.runtime == "python":
            venv.EnvBuilder(with_pip=True).create(item.directory / "venv")
        item.status = "ready"
        save(item)
        return item
