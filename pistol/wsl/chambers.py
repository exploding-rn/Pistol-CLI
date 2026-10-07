"""Read-only adapter over existing chamber metadata, with Windows path translation."""
from pathlib import Path, PureWindowsPath

from ..chamber import list_chambers
from ..config import read_json
from ..fire import is_alive


def windows_path(value):
    path = PureWindowsPath(value)
    if len(path.drive) == 2 and path.drive[1] == ":":
        return Path("/mnt") / path.drive[0].lower() / Path(*path.parts[1:])
    return None


def listing(config):
    result = [{"name": c.name, "status": "running" if is_alive(read_json(c.directory / "process.json", {})) else c.status,
               "port": c.port, "entrypoint": " ".join(c.entrypoint), "path": c.project, "source": "Linux"}
              for c in list_chambers()]
    for item in config.get("windows_chambers", []):
        path = windows_path(item["path"])
        if path and path.is_dir():
            result.append({**item, "path": str(path), "entrypoint": " ".join(item["entrypoint"]),
                           "source": "Windows (snapshot at console launch)"})
    return {"chambers": result, "message": "Linux chambers use Pistol's existing registry. Accessible Windows projects are included from launch metadata; Windows runtime environments are not Linux environments."}
