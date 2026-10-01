from pathlib import Path
from .chamber import current
from .shrimp import run as map_project
from .utils.project import detect, git_state


def run(project: Path | str | None = None) -> dict:
    info = detect(project)
    selected = current(info.root)
    mapped = map_project(info.root)
    git = git_state(info.root)
    frameworks = sorted(set(info.frameworks + [framework for item in mapped.python for framework in item.frameworks]))
    return {"name": info.name, "root": str(info.root), "language": info.language, "runtime": selected.runtime if selected else info.runtime, "entrypoint": selected.entrypoint if selected else info.entrypoint, "frameworks": frameworks, "git_branch": git.get("branch"), "modified_files": git.get("modified"), "chamber": selected.name if selected else None, "port": selected.port if selected else None, "dependency_file": info.dependency_file, "config_files": info.config_files, "routes": sum(len(item.routes) for item in mapped.python), "warnings": mapped.warnings}

