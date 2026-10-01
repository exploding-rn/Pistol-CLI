from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re

from . import chamber
from .config import PistolError, atomic_json, data_dir, read_json, state_lock, valid_name


@dataclass
class Magazine:
    name: str
    chambers: list[str] = field(default_factory=list)
    schema: int = 1


def prefix(name: str) -> str:
    return "PISTOL_" + re.sub(r"[^A-Z0-9_]", "_", valid_name(name).upper())


def get(name: str) -> Magazine:
    name = valid_name(name)
    value = read_json(data_dir() / "magazines" / f"{name}.json")
    if value is None:
        raise PistolError(f"Magazine '{name}' does not exist.")
    try:
        item = Magazine(**value)
        if item.name != name or item.schema != 1 or not isinstance(item.chambers, list) or not all(isinstance(n, str) for n in item.chambers):
            raise ValueError("invalid magazine fields")
        return item
    except (TypeError, ValueError) as exc:
        raise PistolError(f"Invalid magazine metadata for '{name}': {exc}") from exc


def save(item: Magazine):
    atomic_json(data_dir() / "magazines" / f"{valid_name(item.name)}.json", asdict(item))


def create(name: str) -> Magazine:
    name = valid_name(name)
    with state_lock():
        if (data_dir() / "magazines" / f"{name}.json").exists():
            raise PistolError(f"Magazine '{name}' already exists.")
        item = Magazine(name)
        save(item)
        return item


def add(name: str, chamber_name: str) -> Magazine:
    with state_lock():
        item, selected = get(name), chamber.get(chamber_name)
        if selected.name not in item.chambers:
            if any(prefix(n) == prefix(selected.name) for n in item.chambers):
                raise PistolError("These chamber names collide after environment variable normalization.")
            item.chambers.append(selected.name)
            save(item)
        return item


def remove(name: str, chamber_name: str) -> Magazine:
    with state_lock():
        item = get(name)
        chamber_name = valid_name(chamber_name)
        if chamber_name in item.chambers:
            item.chambers.remove(chamber_name)
            save(item)
        return item


def list_magazines() -> list[Magazine]:
    return [get(path.stem) for path in sorted((data_dir() / "magazines").glob("*.json"))]


def environment(item: Magazine) -> dict[str, str]:
    result = {}
    ports = set()
    for name in item.chambers:
        selected = chamber.get(name)
        key = prefix(name)
        if f"{key}_HOST" in result or selected.port in ports:
            raise PistolError("Magazine contains colliding names or ports.")
        ports.add(selected.port)
        result[f"{key}_HOST"] = "127.0.0.1"
        result[f"{key}_PORT"] = str(selected.port)
    return result

