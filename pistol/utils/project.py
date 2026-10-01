from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import subprocess
import tomllib

from pistol.config import PistolError

SCRIPT_RUNTIMES = {".py": "python", ".pyw": "python", ".js": "node", ".mjs": "node", ".cjs": "node", ".ts": "node", ".rs": "rust"}
RUNNERS = {"python": "python", "python.exe": "python", "python3": "python", "python3.exe": "python", "py": "python", "py.exe": "python", "node": "node", "node.exe": "node", "npm": "node", "npm.cmd": "node", "cargo": "rust", "cargo.exe": "rust", "rustc": "rust", "rustc.exe": "rust"}


def _script_token(entrypoint: list[str] | None) -> str | None:
    if not entrypoint:
        return None
    first = entrypoint[0]
    if first.startswith("-"):
        return None
    if Path(first).name.lower() in RUNNERS:
        if len(entrypoint) < 2 or entrypoint[1].startswith("-"):
            return None
        return entrypoint[1]
    return first


def runtime_from_entrypoint(entrypoint: list[str] | None) -> str | None:
    """An explicit script takes priority over any project-wide marker."""
    token = _script_token(entrypoint)
    if token:
        runtime = SCRIPT_RUNTIMES.get(Path(token).suffix.lower())
        if runtime:
            return runtime
    if entrypoint:
        if entrypoint[0] == "-m":
            return "python"
        return RUNNERS.get(Path(entrypoint[0]).name.lower())
    return None


def entrypoint_file(root: Path, entrypoint: list[str] | None) -> Path | None:
    """Return the configured source file, if this is a file-style entrypoint."""
    token = _script_token(entrypoint)
    if not token:
        return None
    path = Path(token)
    if path.suffix.lower() not in SCRIPT_RUNTIMES and not path.is_absolute() and len(path.parts) == 1 and "\\" not in token and "/" not in token:
        return None  # A bare executable such as npm or uvicorn.
    return (root / path).resolve()


def validate_entrypoint(root: Path, entrypoint: list[str] | None) -> Path | None:
    source = entrypoint_file(root, entrypoint)
    if source is not None and not source.is_file():
        raise PistolError(f"Entrypoint not found: {source}")
    return source


@dataclass
class Project:
    root: Path
    name: str
    language: str
    runtime: str
    entrypoint: list[str]
    dependency_file: str | None
    frameworks: list[str] = field(default_factory=list)
    config_files: list[str] = field(default_factory=list)


def root_path(path: Path | str | None = None) -> Path:
    """Use the requested directory, or the nearest project marker from cwd."""
    current = Path(path or Path.cwd()).resolve()
    if path is not None:
        return current
    home = Path.home().resolve()
    for candidate in (current, *current.parents):
        if candidate == home and candidate != current:
            break
        if candidate == candidate.parent:
            break
        if any((candidate / marker).exists() for marker in ("pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", ".git", "manage.py")):
            return candidate
    return current


def detect(path: Path | str | None = None) -> Project:
    root = root_path(path)
    name = root.name
    dependency = next((f for f in ("pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod") if (root / f).is_file()), None)
    entry = next(([f] for f in ("main.py", "app.py", "server.py", "run.py") if (root / f).is_file()), [])
    runtime = "python"
    language = "Python"
    frameworks = []
    contents = ""
    if (root / "pyproject.toml").is_file():
        contents = (root / "pyproject.toml").read_text(encoding="utf-8")
        try:
            metadata = tomllib.loads(contents).get("project", {})
            if isinstance(metadata, dict) and isinstance(metadata.get("name", name), str):
                name = metadata.get("name", name)
        except tomllib.TOMLDecodeError:
            pass
    if (root / "requirements.txt").is_file():
        contents += (root / "requirements.txt").read_text(encoding="utf-8-sig", errors="replace")
    if (root / "manage.py").is_file() and not entry:
        entry = ["manage.py", "runserver", "127.0.0.1:{port}"]
        frameworks.append("Django")
    if not entry and (root / "package.json").is_file() and not (root / "pyproject.toml").exists():
        runtime, language = "node", "JavaScript/TypeScript"
        try:
            metadata = json.loads((root / "package.json").read_text(encoding="utf-8-sig"))
            if not isinstance(metadata, dict):
                raise ValueError("package.json must be an object")
            name = metadata.get("name", name)
            scripts = metadata.get("scripts", {})
            script = next((s for s in ("dev", "start") if isinstance(scripts, dict) and s in scripts), None)
            entry = ["npm", "run", script] if script else []
            contents += json.dumps(metadata.get("dependencies", {}))
        except ValueError:
            pass
    if not entry and (root / "Cargo.toml").is_file() and not (root / "pyproject.toml").exists() and not (root / "package.json").exists():
        runtime, language, entry = "rust", "Rust", ["cargo", "run"]
    for needle, label in (("fastapi", "FastAPI"), ("flask", "Flask"), ("django", "Django"), ("next", "Next.js"), ("react", "React")):
        if needle in contents.lower() and label not in frameworks:
            frameworks.append(label)
    configs = [f for f in ("pyproject.toml", "requirements.txt", "package.json", "tsconfig.json", "setup.cfg", "setup.py", ".env", ".env.example", "pytest.ini", "ruff.toml") if (root / f).exists()]
    return Project(root, name, language, runtime, entry, dependency, frameworks, configs)


def git_state(root: Path) -> dict:
    try:
        branch = subprocess.run(["git", "-C", str(root), "branch", "--show-current"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5)
        if branch.returncode:
            return {"available": False}
        status = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "-z", "--untracked-files=normal"], capture_output=True, timeout=5)
        entries = status.stdout.decode("utf-8", errors="replace").split("\0")
        count, skip = 0, False
        for entry in entries:
            if skip:
                skip = False
                continue
            if entry:
                count += 1
                skip = "R" in entry[:2] or "C" in entry[:2]
        return {"available": True, "branch": branch.stdout.strip() or "(detached)", "modified": count}
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False}
