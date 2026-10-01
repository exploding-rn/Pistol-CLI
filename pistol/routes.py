"""Static route discovery; dynamic routes and cross-file mounts stay unresolved."""
from pathlib import Path
from .shrimp import Route, run as map_project


def run(project: Path | str | None = None) -> list[Route]:
    return [route for item in map_project(project).python for route in item.routes]

