"""Environment inspection. Values are omitted unless explicitly requested."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import re

from .utils.project import root_path


def read_dotenv(path: Path) -> dict[str, str]:
    """Read simple KEY=value files without evaluating commands or expanding values."""
    if not path.is_file():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$", line)
        if not match:
            continue
        key, value = match.groups()
        value = value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.rfind(quote)
            value = value[1:end] if end > 0 else value[1:]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


@dataclass
class Variable:
    name: str
    sources: list[str]
    values: dict[str, str] | None = None


@dataclass
class EnvironmentReport:
    variables: list[Variable]
    missing_from_dotenv: list[str]
    unconfigured_references: list[str]
    warnings: list[str] = field(default_factory=list)


def run(project: Path | str | None = None, *, reveal: bool = False, chamber_vars: dict[str, str] | None = None) -> EnvironmentReport:
    from .shrimp import run as map_project
    from .chamber import current
    root = root_path(project)
    if chamber_vars is None:
        selected = current(root)
        chamber_vars = selected.env if selected else {}
        if selected:
            chamber_vars = {**chamber_vars, "PORT": str(selected.port), "PISTOL_CHAMBER": selected.name}
    dotenv, example = read_dotenv(root / ".env"), read_dotenv(root / ".env.example")
    mapped = map_project(root)
    referenced = {key for item in mapped.python for key in item.env}
    sources = {".env": dotenv, ".env.example": example, "process": os.environ, "chamber": chamber_vars}
    relevant = set(dotenv) | set(example) | referenced | set(chamber_vars)
    relevant |= {key for key in os.environ if key.startswith("PISTOL_")}
    variables = []
    for key in sorted(relevant):
        origins = [label for label, values in sources.items() if key in values]
        if key in referenced:
            origins.append("source code")
        variables.append(Variable(key, origins, {label: values[key] for label, values in sources.items() if key in values} if reveal else None))
    # os.environ provides case-insensitive lookup on Windows; preserve that behavior.
    missing = [key for key in referenced if key not in dotenv and key not in os.environ and key not in chamber_vars]
    return EnvironmentReport(variables, sorted(set(example) - set(dotenv)), sorted(missing), mapped.warnings)
