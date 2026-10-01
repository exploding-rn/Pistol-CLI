from __future__ import annotations

from dataclasses import dataclass, field
import socket

import psutil

from .config import PistolError, read_json


def validate_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise PistolError("Port must be between 1 and 65535.")
    return port


def available(port: int) -> bool:
    validate_port(port)
    # A wildcard bind also detects listeners on interfaces other than localhost.
    for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET6, "::")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                if family == socket.AF_INET6:
                    sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind((host, port))
        except OSError as exc:
            if family == socket.AF_INET6 and exc.errno in {97, 10047, 10049, 49}:
                continue
            return False
    return True


def free_port(excluded: set[int] | None = None) -> int:
    excluded = excluded or set()
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        if port not in excluded and available(port):
            return port
    raise PistolError("Could not find an available TCP port.")


@dataclass
class PortInfo:
    port: int
    available: bool
    listeners: list[dict] = field(default_factory=list)
    chambers: list[str] = field(default_factory=list)
    warning: str | None = None


def inspect(port: int) -> PortInfo:
    from .chamber import list_chambers
    from .fire import is_alive
    validate_port(port)
    result = PortInfo(port, available(port))
    chambers = list_chambers()
    result.chambers = [c.name for c in chambers if c.port == port]
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and (conn.status == psutil.CONN_LISTEN or conn.type == socket.SOCK_DGRAM):
                result.listeners.append({"pid": conn.pid, "host": conn.laddr.ip, "state": conn.status, "protocol": "UDP" if conn.type == socket.SOCK_DGRAM else "TCP", "chamber": None})
    except (psutil.Error, OSError) as exc:
        result.warning = f"Some process details are unavailable: {exc}"
    for chamber in chambers:
        record = read_json(chamber.directory / "process.json", {})
        if is_alive(record):
            try:
                parent = psutil.Process(record["pid"])
                pids = {parent.pid, *(child.pid for child in parent.children(recursive=True))}
                for listener in result.listeners:
                    if listener["pid"] in pids:
                        listener["chamber"] = chamber.name
            except psutil.Error:
                pass
    return result
