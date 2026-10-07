"""Static Python requirement discovery and environment synchronization."""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass, field
from importlib import metadata
import os
from pathlib import Path
import re
import subprocess
import sys
import sysconfig
import tomllib
from typing import Iterable

from . import chamber
from .config import PistolError


# Import name -> PyPI/distribution name.
#
# Python imports and package names are unfortunately not always identical.
PACKAGE_MAP: dict[str, str] = {
    "flask": "Flask",
    "flask_cors": "flask-cors",
    "dotenv": "python-dotenv",
    "requests": "requests",
    "discord": "discord.py",
    "yaml": "PyYAML",
    "PIL": "Pillow",
    "cv2": "opencv-python",
    "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn",
    "dateutil": "python-dateutil",
    "Crypto": "pycryptodome",
    "OpenSSL": "pyOpenSSL",
    "serial": "pyserial",
    "gi": "PyGObject",
    "dotenv": "python-dotenv",
    "psutil": "psutil",
    "rich": "rich",
    "watchdog": "watchdog",
    "numpy": "numpy",
    "pandas": "pandas",
    "matplotlib": "matplotlib",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    "starlette": "starlette",
    "pydantic": "pydantic",
    "sqlalchemy": "SQLAlchemy",
    "aiohttp": "aiohttp",
    "httpx": "httpx",
    "jinja2": "Jinja2",
    "werkzeug": "Werkzeug",
}


IGNORE_DIR_NAMES = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    "ENV",
    "__pycache__",
    "node_modules",
    "site-packages",
    "dist",
    "build",
    "target",
    ".idea",
    ".vscode",
    ".tox",
    ".nox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".pistol",
}


PROJECT_MARKERS = (
    "pyproject.toml",
    "requirements.txt",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "manage.py",
    "setup.py",
    "setup.cfg",
    ".git",
)


@dataclass(slots=True)
class ImportFinding:
    import_name: str
    package_name: str | None
    installed: bool
    version: str | None = None
    files: list[str] = field(default_factory=list)


@dataclass(slots=True)
class PythonInfo:
    running: str
    chamber: str | None
    declared: str | None
    python_version_file: str | None
    static_minimum: str | None


@dataclass(slots=True)
class RequirementReport:
    project: str
    standard_library: list[str]
    local_modules: list[str]
    third_party: list[ImportFinding]
    unresolved: list[str]
    python: PythonInfo
    requirements_file: str | None = None

    @property
    def missing(self) -> list[ImportFinding]:
        return [
            item
            for item in self.third_party
            if not item.installed
        ]


def _resolve_project(project: Path | str | None) -> Path:
    """Resolve a useful project root without climbing above the user's home."""

    start = Path(project).expanduser() if project else Path.cwd()
    start = start.resolve()

    if start.is_file():
        start = start.parent

    home = Path.home().resolve()

    current = start

    while True:
        # A marker in the user's home does not make every unmarked descendant
        # part of a home-wide project. Scan the requested folder in that case.
        if current == home and current != start:
            break

        if any((current / marker).exists() for marker in PROJECT_MARKERS):
            return current

        # Do not climb above the user's home.
        if current == home:
            break

        if current.parent == current:
            break

        # If we're inside the user's home, stop before climbing out of it.
        try:
            current.relative_to(home)
        except ValueError:
            break

        current = current.parent

    return start


def _is_virtualenv_directory(path: Path) -> bool:
    """Detect virtual environments even when they have weird custom names."""

    try:
        if (path / "pyvenv.cfg").is_file():
            return True

        if (
            (path / "Scripts" / "python.exe").is_file()
            and (path / "Lib" / "site-packages").is_dir()
        ):
            return True

        if (
            (path / "bin" / "python").is_file()
            and any((path / "lib").glob("python*/site-packages"))
        ):
            return True

    except OSError:
        pass

    return False


def _should_skip_directory(path: Path) -> bool:
    if path.name.lower() in IGNORE_DIR_NAMES or path.name.lower().endswith(".egg-info"):
        return True

    if path.is_symlink():
        return True

    if _is_virtualenv_directory(path):
        return True

    try:
        # Windows junction/reparse point protection.
        stat = path.stat()

        reparse_flag = getattr(
            stat,
            "st_file_attributes",
            0,
        ) & getattr(
            __import__("stat"),
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0,
        )

        if reparse_flag:
            return True

    except OSError:
        return True

    return False


def _python_files(project: Path) -> Iterable[Path]:
    """Yield Python source files without wandering into generated/runtime trees."""

    stack = [project]

    while stack:
        directory = stack.pop()

        try:
            children = list(directory.iterdir())
        except (OSError, PermissionError):
            continue

        for child in children:
            if child.is_dir():
                if not _should_skip_directory(child):
                    stack.append(child)

            elif child.is_file() and child.suffix.lower() in {".py", ".pyw"}:
                yield child


def _local_modules(project: Path) -> set[str]:
    """Find import names that belong to the current project."""

    names: set[str] = set()

    roots = [project]

    src = project / "src"
    if src.is_dir():
        roots.append(src)

    for root in roots:
        try:
            children = list(root.iterdir())
        except OSError:
            continue

        for child in children:
            if child.is_file() and child.suffix.lower() in {".py", ".pyw"}:
                names.add(child.stem)

            elif child.is_dir() and (child / "__init__.py").is_file():
                names.add(child.name)

    return names


def _analyze_python_file(path: Path) -> tuple[set[str], tuple[int, int]]:
    """Read and walk each AST once for imports and a conservative syntax floor."""
    try:
        source = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        tree = ast.parse(
            source,
            filename=str(path),
        )

    except (OSError, SyntaxError, UnicodeError):
        return set(), (3, 7)

    imports: set[str] = set()
    minimum = (3, 7)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".", 1)[0])

        elif isinstance(node, ast.ImportFrom):
            if node.level:
                # Relative import = project-local.
                continue

            if node.module:
                imports.add(node.module.split(".", 1)[0])

        if isinstance(node, ast.NamedExpr):
            minimum = _max_version(minimum, (3, 8))
        if hasattr(ast, "Match") and isinstance(node, ast.Match):
            minimum = _max_version(minimum, (3, 10))
        if hasattr(ast, "TryStar") and isinstance(node, ast.TryStar):
            minimum = _max_version(minimum, (3, 11))
        if hasattr(ast, "TypeAlias") and isinstance(node, ast.TypeAlias):
            minimum = _max_version(minimum, (3, 12))

    return imports, minimum


def _extract_imports(path: Path) -> set[str]:
    return _analyze_python_file(path)[0]


def _scan_source(project: Path) -> tuple[dict[str, set[str]], str | None]:
    result: dict[str, set[str]] = {}
    minimum = (3, 7)
    found_python = False

    for path in _python_files(project):
        found_python = True
        relative = str(path.relative_to(project))
        imports, file_minimum = _analyze_python_file(path)
        minimum = _max_version(minimum, file_minimum)

        for import_name in imports:
            result.setdefault(import_name, set()).add(relative)

    return result, (f">={minimum[0]}.{minimum[1]}" if found_python else None)


def _scan_import_files(project: Path) -> dict[str, set[str]]:
    return _scan_source(project)[0]


def _distribution_for_import(import_name: str, distribution_map: dict[str, list[str]] | None = None) -> str | None:
    if import_name in PACKAGE_MAP:
        return PACKAGE_MAP[import_name]

    # If a package is already installed, Python metadata can often tell us
    # which distribution provides the import.
    try:
        mapping = distribution_map if distribution_map is not None else metadata.packages_distributions()

        distributions = mapping.get(import_name)

        if distributions:
            return distributions[0]

    except Exception:
        pass

    # Exact-name imports are safe enough for a small set of very common
    # packages. Unknown imports are intentionally NOT guessed.
    common_same_name = {
        "numpy",
        "pandas",
        "matplotlib",
        "requests",
        "fastapi",
        "uvicorn",
        "aiohttp",
        "httpx",
        "psutil",
        "rich",
        "watchdog",
    }

    if import_name in common_same_name:
        return import_name

    return None


def _distribution_version(package_name: str) -> str | None:
    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def _chamber_python(project: Path) -> Path | None:
    try:
        selected = chamber.current(project)
    except Exception:
        selected = None

    if not selected:
        return None

    base = Path(selected.directory) / "venv"

    if os.name == "nt":
        candidate = base / "Scripts" / "python.exe"
    else:
        candidate = base / "bin" / "python"

    return candidate if candidate.exists() else None


def _metadata_version_with_python(
    python: Path,
    package_name: str,
) -> str | None:
    script = (
        "from importlib import metadata;"
        "import sys;"
        "name=sys.argv[1];"
        "\ntry:\n"
        " print(metadata.version(name))\n"
        "except metadata.PackageNotFoundError:\n"
        " raise SystemExit(44)"
    )

    try:
        completed = subprocess.run(
            [str(python), "-c", script, package_name],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    value = completed.stdout.strip()
    return value or None


def _declared_python(project: Path) -> str | None:
    pyproject = project / "pyproject.toml"

    if not pyproject.is_file():
        return None

    try:
        data = tomllib.loads(
            pyproject.read_text(
                encoding="utf-8",
            )
        )
    except (OSError, ValueError):
        return None

    project_table = data.get("project")

    if isinstance(project_table, dict):
        value = project_table.get("requires-python")

        if isinstance(value, str):
            return value

    return None


def _python_version_file(project: Path) -> str | None:
    path = project / ".python-version"

    if not path.is_file():
        return None

    try:
        value = path.read_text(
            encoding="utf-8",
        ).strip()
    except OSError:
        return None

    return value or None


def _version_tuple(value: str) -> tuple[int, int]:
    match = re.match(r"^(\d+)\.(\d+)", value)

    if not match:
        return (3, 0)

    return int(match.group(1)), int(match.group(2))


def _max_version(
    left: tuple[int, int],
    right: tuple[int, int],
) -> tuple[int, int]:
    return left if left >= right else right


def _static_minimum(project: Path) -> str | None:
    """
    Conservative syntax-based lower bound.

    This intentionally reports only syntax we can confidently associate
    with a minimum Python version. It does NOT claim the project's exact
    required version.
    """

    return _scan_source(project)[1]


def _python_version(python: Path) -> str | None:
    try:
        completed = subprocess.run(
            [
                str(python),
                "-c",
                "import sys; print('.'.join(map(str, sys.version_info[:3])))",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if completed.returncode != 0:
        return None

    return completed.stdout.strip() or None


def _requirements_name(line: str) -> str | None:
    line = line.strip()

    if not line:
        return None

    if line.startswith("#"):
        return None

    if line.startswith(("-", "--")):
        return None

    # Strip environment markers.
    line = line.split(";", 1)[0].strip()

    # Strip extras.
    line = re.sub(r"\[[^\]]+\]", "", line)

    # Pull out distribution name before version operators / URLs.
    match = re.match(
        r"^([A-Za-z0-9_.-]+)",
        line,
    )

    if not match:
        return None

    return match.group(1)


def scan(
    project: Path | str | None = None,
) -> RequirementReport:
    root = _resolve_project(project)

    import_files, static_minimum = _scan_source(root)
    local = _local_modules(root)

    stdlib: list[str] = []
    unresolved: list[str] = []
    third_party: list[ImportFinding] = []

    chamber_python = _chamber_python(root)
    distribution_map: dict[str, list[str]] | None = None
    version_cache: dict[str, str | None] = {}

    for import_name in sorted(
        import_files,
        key=str.lower,
    ):
        if import_name in sys.stdlib_module_names:
            stdlib.append(import_name)
            continue

        if import_name in local:
            continue

        if import_name not in PACKAGE_MAP and distribution_map is None:
            try:
                distribution_map = metadata.packages_distributions()
            except Exception:
                distribution_map = {}

        package_name = _distribution_for_import(import_name, distribution_map)

        if package_name is None:
            unresolved.append(import_name)
            continue

        if package_name not in version_cache:
            if chamber_python:
                version_cache[package_name] = _metadata_version_with_python(
                    chamber_python,
                    package_name,
                )
            else:
                version_cache[package_name] = _distribution_version(
                    package_name,
                )
        version = version_cache[package_name]

        third_party.append(
            ImportFinding(
                import_name=import_name,
                package_name=package_name,
                installed=version is not None,
                version=version,
                files=sorted(import_files[import_name]),
            )
        )

    chamber_version = (
        _python_version(chamber_python)
        if chamber_python
        else None
    )

    python_info = PythonInfo(
        running=".".join(
            map(
                str,
                sys.version_info[:3],
            )
        ),
        chamber=chamber_version,
        declared=_declared_python(root),
        python_version_file=_python_version_file(root),
        static_minimum=static_minimum,
    )

    requirements = root / "requirements.txt"

    return RequirementReport(
        project=str(root),
        standard_library=sorted(
            stdlib,
            key=str.lower,
        ),
        local_modules=sorted(
            local,
            key=str.lower,
        ),
        third_party=sorted(
            third_party,
            key=lambda item: (
                item.package_name or item.import_name
            ).lower(),
        ),
        unresolved=sorted(
            unresolved,
            key=str.lower,
        ),
        python=python_info,
        requirements_file=(
            str(requirements)
            if requirements.exists()
            else None
        ),
    )


def write(
    project: Path | str | None = None,
) -> Path:
    """
    Safely add detected known packages to requirements.txt.

    Existing requirements, comments, pins, options and custom indexes are
    preserved. Pistol only appends packages that are missing.
    """

    report = scan(project)
    root = Path(report.project)

    path = root / "requirements.txt"

    existing_lines: list[str] = []

    if path.exists():
        existing_lines = path.read_text(
            encoding="utf-8",
            errors="replace",
        ).splitlines()

    existing_names = {
        name.lower().replace("_", "-")
        for line in existing_lines
        if (name := _requirements_name(line))
    }

    additions: list[str] = []

    for item in report.third_party:
        if not item.package_name:
            continue

        normalized = (
            item.package_name
            .lower()
            .replace("_", "-")
        )

        if normalized not in existing_names:
            additions.append(item.package_name)
            existing_names.add(normalized)

    output = list(existing_lines)

    if output and additions and output[-1].strip():
        output.append("")

    output.extend(additions)

    path.write_text(
        "\n".join(output).rstrip() + "\n",
        encoding="utf-8",
    )

    return path


def install(
    project: Path | str | None = None,
) -> list[str]:
    root = _resolve_project(project)

    selected = chamber.current(root)

    if not selected:
        raise PistolError(
            "No active chamber is linked to this project. "
            "Create/select a Python chamber before using pistol req --install."
        )

    if selected.runtime != "python":
        raise PistolError(
            f"Chamber {selected.name} uses runtime {selected.runtime!r}; "
            "requirements installation requires a Python chamber."
        )

    python = _chamber_python(root)

    if python is None:
        raise PistolError(
            f"Python executable was not found for chamber {selected.name}. "
            "Run pistol medic or recreate/reset the chamber."
        )

    report = scan(root)

    missing = [
        item.package_name
        for item in report.third_party
        if item.package_name and not item.installed
    ]

    if not missing:
        return []

    command = [
        str(python),
        "-m",
        "pip",
        "install",
        *missing,
    ]

    completed = subprocess.run(
        command,
        cwd=root,
        check=False,
    )

    if completed.returncode != 0:
        raise PistolError(
            f"pip failed with exit code {completed.returncode}."
        )

    return missing


def sync(
    project: Path | str | None = None,
) -> tuple[Path, list[str], RequirementReport]:
    requirements = write(project)
    installed = install(project)
    report = scan(project)

    return requirements, installed, report


def run(
    project: Path | str | None = None,
) -> RequirementReport:
    return scan(project)


def to_dict(report: RequirementReport) -> dict:
    return asdict(report)
