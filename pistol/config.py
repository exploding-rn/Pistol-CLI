"""User configuration and crash-safe state, outside the source checkout."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Iterator


class PistolError(Exception):
    """An actionable user-facing failure."""


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "Pistol" if base else Path.home() / ("AppData/Local/Pistol" if os.name == "nt" else ".local/share/Pistol")


def config_dir() -> Path:
    base = os.environ.get("APPDATA")
    return Path(base) / "Pistol" if base else Path.home() / ("AppData/Roaming/Pistol" if os.name == "nt" else ".config/Pistol")


def valid_name(name: str) -> str:
    if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name):
        raise PistolError("Names must contain 1–64 letters, digits, hyphens or underscores, starting with a letter or digit.")
    name = name.lower()
    if name in {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}:
        raise PistolError("This name is reserved by Windows.")
    return name


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            result = json.load(stream)
        if isinstance(default, dict) and not isinstance(result, dict):
            raise PistolError(f"Expected a JSON object in {path}. The file was left intact.")
        return result
    except FileNotFoundError:
        return default
    except (ValueError, UnicodeError) as exc:
        raise PistolError(f"Invalid JSON in {path}: {exc}. The file was left intact.") from exc


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextlib.contextmanager
def state_lock(timeout: float = 15) -> Iterator[None]:
    """Serialize cross-process read/modify/write operations without a daemon."""
    path = data_dir() / "state.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise PistolError("Pistol state is busy; try again shortly.") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def settings() -> dict:
    value = read_json(config_dir() / "config.json", {})
    if not isinstance(value, dict):
        raise PistolError("Pistol config.json must contain an object.")
    return value


def project_state(project: Path) -> Path:
    key = hashlib.sha256(os.path.normcase(str(project.resolve())).encode("utf-8")).hexdigest()[:24]
    return data_dir() / "projects" / key
