from __future__ import annotations

from dataclasses import dataclass, field

import psutil

from .chamber import list_chambers
from .config import PistolError, read_json
from .fire import is_alive
from .ports import inspect


@dataclass
class ProcessInfo:
    port: int
    protocol: str
    state: str
    pid: int | None
    name: str | None = None
    executable: str | None = None
    command: list[str] = field(default_factory=list)
    parent_pid: int | None = None
    parent_name: str | None = None
    cwd: str | None = None
    chamber: str | None = None
    create_time: float | None = None
    warning: str | None = None


def inspect_port(port: int) -> list[ProcessInfo]:
    result = []
    inspection = inspect(port)
    if inspection.warning and not inspection.listeners:
        raise PistolError(inspection.warning)
    for listener in inspection.listeners:
        item = ProcessInfo(port, listener["protocol"], listener["state"], listener["pid"])
        if item.pid is not None:
            try:
                process = psutil.Process(item.pid)
                attrs = process.as_dict(attrs=["name", "exe", "cmdline", "ppid", "cwd", "create_time"], ad_value=None)
                item.name, item.executable, item.command = attrs["name"], attrs["exe"], attrs["cmdline"] or []
                item.parent_pid, item.cwd, item.create_time = attrs["ppid"], attrs["cwd"], attrs["create_time"]
                if item.parent_pid:
                    try:
                        item.parent_name = psutil.Process(item.parent_pid).name()
                    except psutil.Error:
                        pass
                if any(value is None for value in attrs.values()):
                    item.warning = "Some details are restricted by the operating system."
                for chamber in list_chambers():
                    record = read_json(chamber.directory / "process.json", {})
                    if is_alive(record):
                        try:
                            owner = psutil.Process(record["pid"])
                            if item.pid == owner.pid or any(child.pid == item.pid for child in owner.children(recursive=True)):
                                item.chamber = chamber.name
                        except psutil.Error:
                            pass
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                item.warning = str(exc)
        result.append(item)
    return result


def terminate_port(port: int) -> list[int]:
    """Explicit operation: terminate only owners observed during this invocation."""
    terminated = []
    for item in inspect_port(port):
        if item.pid is None or item.create_time is None or item.pid in terminated:
            continue
        try:
            process = psutil.Process(item.pid)
            if abs(process.create_time() - item.create_time) >= 0.01:
                continue
            process.terminate()
            terminated.append(item.pid)
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as exc:
            raise PistolError(f"Permission denied terminating PID {item.pid}.") from exc
    return terminated
