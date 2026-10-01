"""Deterministic checks that do not import or execute project source."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib

from . import chamber
from .config import read_json
from .env import run as inspect_env
from .fire import is_alive, project_python, record_path
from .ports import inspect as inspect_port
from .shrimp import run as map_project
from .utils.project import detect, git_state


@dataclass
class Check:
    level: str
    message: str
    detail: str = ""


@dataclass
class Report:
    project: str
    checks: list[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(check.level == "error" for check in self.checks)

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dependency_names(root: Path) -> tuple[list[str], list[str]]:
    """Only unconditional declarations are conclusive without a resolver."""
    declarations, notes = [], []
    metadata = root / "pyproject.toml"
    if metadata.is_file():
        payload = tomllib.loads(metadata.read_text(encoding="utf-8"))
        declarations.extend(payload.get("project", {}).get("dependencies", []))
    requirements = root / "requirements.txt"
    if requirements.is_file():
        declarations.extend(requirements.read_text(encoding="utf-8-sig", errors="replace").splitlines())
    names = []
    for declaration in declarations:
        if not isinstance(declaration, str):
            notes.append("Invalid dependency declaration")
            continue
        declaration = declaration.strip()
        if not declaration or declaration.startswith("#"):
            continue
        if declaration.startswith("-") or ";" in declaration or "://" in declaration or " @ " in declaration:
            notes.append("Conditional, nested, URL or option-based dependency needs installer validation")
            continue
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9_.-]*)(?:\[.*?\])?(?:\s*[<>=!~].*|\s*(?:#.*)?)$", declaration)
        if match:
            names.append(match.group(1))
        else:
            notes.append("A dependency declaration could not be statically resolved")
    return sorted(set(names)), sorted(set(notes))


def run(project: Path | str | None = None, *, chamber_name: str | None = None) -> Report:
    selected = chamber.get(chamber_name) if chamber_name else chamber.current(project)
    info = detect(selected.project if selected else project)
    runtime = selected.runtime if selected else info.runtime
    report = Report(str(info.root))
    add = lambda level, message, detail="": report.checks.append(Check(level, message, detail))
    if not info.root.is_dir():
        add("error", "Project directory is missing", str(info.root))
        return report
    python = project_python(info.root, selected)
    add("ok", f"Pistol Python {sys.version.split()[0]}", sys.executable)
    target_venv = selected.python if selected else info.root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if runtime == "python":
        add("ok" if target_venv.is_file() else "error" if selected else "warning", "Virtual environment exists" if target_venv.is_file() else "No project virtual environment", str(target_venv))
        add("info", "Project Python", python)
        if os.environ.get("VIRTUAL_ENV") and Path(os.environ["VIRTUAL_ENV"]).resolve() != target_venv.parent.parent.resolve():
            add("warning", "The terminal virtual environment differs from the project environment")
    if info.dependency_file:
        add("ok", f"Dependency file: {info.dependency_file}")
    else:
        add("warning", "No dependency file found")
    declarations = []
    try:
        declarations, notes = dependency_names(info.root)
        for note in notes:
            add("warning", note)
        if (info.root / "pyproject.toml").exists():
            payload = tomllib.loads((info.root / "pyproject.toml").read_text(encoding="utf-8"))
            add("ok", "pyproject.toml parses correctly")
            metadata = payload.get("project", {})
            if metadata and not metadata.get("name"):
                add("error", "[project] is missing package name")
    except (tomllib.TOMLDecodeError, TypeError, AttributeError) as exc:
        add("error", "Invalid pyproject.toml or dependency metadata", str(exc))
    mapped = map_project(info.root)
    for item in mapped.python:
        for error in item.errors:
            add("error", f"Python syntax/read error: {item.path}", error)
    if not any(item.errors for item in mapped.python):
        add("ok", f"Parsed {len(mapped.python)} Python files without syntax errors")
    for warning in mapped.warnings:
        add("warning", warning)
    if runtime == "python":
        script = "import importlib.metadata as m,json,sys; print(json.dumps({'version':sys.version.split()[0],'packages':{d.metadata['Name'].lower().replace('_','-').replace('.','-'):d.version for d in m.distributions() if d.metadata['Name']},'modules':list(m.packages_distributions()),'stdlib':list(sys.stdlib_module_names)}))"
        try:
            probe = subprocess.run([python, "-I", "-c", script], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            if probe.returncode:
                add("error", "Project Python could not be inspected", probe.stderr[-1000:])
            else:
                installed = json.loads(probe.stdout)
                add("ok", f"Project runtime: Python {installed['version']}")
                missing = [name for name in declarations if _normal(name) not in installed["packages"]]
                add("error" if missing else "ok", "Missing dependencies: " + ", ".join(missing) if missing else "Declared unconditional dependencies are installed")
                local = {Path(path).parts[0] for path in mapped.files}
                local |= {Path(path).stem for path in mapped.files if "/" not in path}
                local |= {Path(path).parts[1] for path in mapped.files if path.startswith("src/") and len(Path(path).parts) > 1}
                known = set(installed["modules"]) | set(installed["stdlib"]) | local
                unresolved = sorted({module.split(".")[0] for item in mapped.python for module in item.imports if module and not module.startswith(".") and module.split(".")[0] not in known})
                if unresolved:
                    add("warning", "Imports not found in installed metadata: " + ", ".join(unresolved[:20]), "May be optional/test dependencies or namespace packages; project code was not imported.")
            check = subprocess.run([python, "-I", "-m", "pip", "check"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
            if check.returncode:
                add("warning" if "No module named pip" in check.stderr else "error", "Installed dependency consistency check failed", (check.stdout + check.stderr).strip()[:1500])
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
            add("error", "Could not inspect project Python", str(exc))
    entry = selected.entrypoint if selected else info.entrypoint
    if entry:
        first = entry[1] if entry[0] in {"python", "python.exe", "python3"} and len(entry) > 1 else entry[0]
        if first.endswith(".py") and not (info.root / first).is_file():
            add("error", "Configured entrypoint is missing", first)
        else:
            add("ok", "Entrypoint: " + " ".join(entry))
    else:
        add("warning", "No runnable entrypoint detected (normal for a library)")
    environment = inspect_env(info.root, chamber_vars={**selected.env, "PORT": str(selected.port), "HOST": "127.0.0.1", "PISTOL_CHAMBER": selected.name} if selected else None)
    if (info.root / ".env").exists():
        add("ok", ".env exists; values are hidden")
    if environment.missing_from_dotenv:
        add("warning", f".env.example contains {len(environment.missing_from_dotenv)} keys missing from .env", ", ".join(environment.missing_from_dotenv))
    if environment.unconfigured_references:
        add("warning", "Source references unconfigured environment names", ", ".join(environment.unconfigured_references))
    record = read_json(record_path(selected, info.root), {})
    if selected:
        add("info", f"Chamber: {selected.name} ({selected.status})")
        if selected.status != "ready":
            add("error", "Chamber setup is incomplete; reset or repair it")
        port = inspect_port(selected.port)
        if not port.available:
            from .detective import inspect_port as owners
            owned = any(owner.chamber == selected.name for owner in owners(selected.port))
            add("ok" if owned else "error", f"PORT {selected.port} is {'in use by this chamber' if owned else 'already occupied'}")
        else:
            add("ok", f"PORT {selected.port} is available")
    if record.get("status") == "running" and not is_alive(record):
        add("warning", "Stale Pistol process record found; medic can archive it")
    git = git_state(info.root)
    if git["available"]:
        add("info", f"Git branch {git['branch']}; {git['modified']} changed files")
    else:
        add("info", "Git repository not detected, or Git is unavailable")
    return report
