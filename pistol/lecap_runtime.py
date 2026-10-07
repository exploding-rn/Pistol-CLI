"""Identity-checked lifecycle for the installation's detached LECAP worker."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import http.client
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid

import psutil

from .config import PistolError, atomic_json, data_dir, read_json

WORKER_MODULE = "pistol.lecap_worker"


def directory() -> Path:
    return data_dir() / "runtime" / "lecap"


def log_path() -> Path:
    return data_dir() / "logs" / "lecap.log"


@contextmanager
def lock(name: str, timeout: float = 25):
    """Separate lifecycle/worker locks avoid blocking certificate state locks."""
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    with (root / name).open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise PistolError("LECAP worker/lifecycle is already busy; retry or inspect pistol lecap status.") from None
                time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def read_record(name: str) -> dict:
    # Windows cannot replace a destination while another process has it open.
    # Serialize short record reads/writes, independently of lifecycle locks.
    with lock("records.lock"):
        return read_json(directory() / name, {})


def write_record(name: str, value: dict) -> None:
    with lock("records.lock"):
        atomic_json(directory() / name, value)


def remove_record(name: str) -> None:
    with lock("records.lock"):
        (directory() / name).unlink(missing_ok=True)


def worker_arguments(instance: str, http_port: int, https_port: int) -> list[str]:
    return ["-I", "-m", WORKER_MODULE, "--instance", instance,
            "--http-port", str(http_port), "--https-port", str(https_port)]


def identity(instance: str, http_port: int, https_port: int) -> dict:
    process = psutil.Process()
    return {"schema": 1, "instance": instance, "pid": process.pid,
            "create_time": process.create_time(), "exe": process.exe(),
            "command": process.cmdline(), "requested_http_port": http_port,
            "requested_https_port": https_port,
            "started": datetime.now(timezone.utc).isoformat()}


def owned_process(record: dict):
    """An uncertain identity raises; a missing/reused/unrelated PID returns None."""
    try:
        instance = record["instance"]
        if record.get("schema") != 1 or uuid.UUID(instance).hex != instance:
            return None
        expected = worker_arguments(instance, record["requested_http_port"], record["requested_https_port"])
        if record["command"][1:] != expected:
            return None
        process = psutil.Process(int(record["pid"]))
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        if abs(process.create_time() - float(record["create_time"])) >= .001:
            return None
        known_executables = {os.path.normcase(os.path.realpath(p)) for p in (sys.executable, getattr(sys, "_base_executable", sys.executable))}
        executable = os.path.normcase(os.path.realpath(process.exe()))
        if executable not in known_executables or executable != os.path.normcase(os.path.realpath(record["exe"])):
            return None
        if process.cmdline() != record["command"]:
            return None
        return process
    except (KeyError, TypeError, ValueError, AttributeError, psutil.NoSuchProcess):
        return None
    except psutil.AccessDenied as exc:
        raise PistolError("Cannot verify the stored LECAP process identity. No process was stopped; use the account/privileges that started it.") from exc


def signatures(accesses) -> list[str]:
    from .lecap_proxy import fingerprint
    return [fingerprint(access) for access in accesses]


def _inspect() -> dict:
    from .lecap import routes
    from .lecap_proxy import request
    record = read_record("state.json")
    process = owned_process(record) if record else None
    result = {"active": False, "running": process is not None, "pid": process.pid if process else None,
              "http_port": None, "https_port": None, "needs_restart": False,
              "urls": [], "log": str(log_path())}
    if not process:
        if record.get("status") in {"stopped", "failed"}:
            result["reason"] = record.get("reason", "stopped")
            result["needs_restart"] = record.get("needs_restart", False)
        elif record:
            remove_record("state.json")
            result["reason"] = "Stale or unrelated process state removed; no process was signaled."
        return result
    result.update({key: record.get(key) for key in ("http_port", "https_port", "urls")})
    if record.get("status") != "ready":
        result["reason"] = record.get("reason", "Worker is starting or stopping")
        return result
    try:
        actual = {(c.laddr.ip, c.laddr.port) for c in process.net_connections(kind="tcp") if c.status == psutil.CONN_LISTEN}
        listeners = record.get("listeners", [])
        listening = bool(listeners) and all((entry["address"], entry["port"]) in actual for entry in listeners)
        healthy = listening
        if healthy:
            for route in record.get("routes", []):
                status_code, headers = request(route["name"], "127.0.0.1", record["http_port"],
                                               path="/.well-known/pistol-lecap", timeout=1)
                if status_code != 200 or headers.get("X-Pistol-LECAP") != route["fingerprint"]:
                    healthy = False
                    break
        result["active"] = healthy
        if not healthy:
            result["reason"] = "Verified worker is not serving all recorded listeners/routes; stop and start it."
    except (OSError, http.client.HTTPException, psutil.Error) as exc:
        result["reason"] = f"Cannot verify the worker listeners: {exc}"
    try:
        result["needs_restart"] = signatures(routes()) != record.get("signatures")
    except PistolError:
        result["needs_restart"] = True
    return result


def status() -> dict:
    with lock("lifecycle.lock"):
        return _inspect()


def describe_conflict(address: str, port: int, error: OSError) -> str:
    try:
        for connection in psutil.net_connections(kind="tcp"):
            if connection.status == psutil.CONN_LISTEN and connection.laddr.port == port and connection.laddr.ip in {address, "0.0.0.0", "::"}:
                pid = connection.pid
                try:
                    name = psutil.Process(pid).name() if pid else "unknown process"
                except psutil.Error:
                    name = "unknown process"
                return f"Cannot start LECAP. Port {port} is already owned by PID {pid or 'unknown'} ({name}). No process was stopped."
    except psutil.Error:
        pass
    return f"Cannot start LECAP on {address}:{port}: {error}. Free the port or use an elevated terminal if permission was denied."


def check_ports(accesses, http_port: int, https_port: int):
    addresses = {"127.0.0.1"} | {a.values["lan_address"] for a in accesses if a.values["lan"] and a.values["lan_address"]}
    ports = {http_port} | ({https_port} if any(a.values["https"] for a in accesses) else set())
    for address in sorted(addresses):
        for port in sorted(ports - {0}):
            with socket.socket() as sock:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    sock.bind((address, port))
                except OSError as exc:
                    raise PistolError(describe_conflict(address, port, exc)) from exc


def _launch(instance: str, http_port: int, https_port: int):
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
               "stderr": subprocess.DEVNULL, "close_fds": True, "cwd": str(directory())}
    if os.name == "nt":
        # The venv redirector starts another python.exe without forwarding flags.
        # DETACHED_PROCESS lets that interpreter allocate a visible console.
        # CREATE_NO_WINDOW keeps both processes windowless and independent of
        # the caller's console/control events. Do not combine it with DETACHED_PROCESS
        # (Windows would ignore CREATE_NO_WINDOW). No process-group flag is needed.
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        options["start_new_session"] = True
    # No pipes or inherited console handles keep the launcher/PowerShell attached.
    return subprocess.Popen([sys.executable, *worker_arguments(instance, http_port, https_port)], **options)


def _stop(record: dict, timeout: float = 15) -> None:
    process = owned_process(record)
    if process is None:
        return
    write_record("stop.json", {"instance": record["instance"]})
    deadline = time.monotonic() + timeout
    try:
        try:
            process.wait(timeout=timeout)
        except psutil.AccessDenied:
            # Windows may allow identity queries but deny a SYNCHRONIZE handle
            # (e.g. a worker started elevated). The stop record still works.
            # is_running also checks creation time, so PID reuse means it exited.
            while process.is_running():
                if time.monotonic() >= deadline:
                    raise psutil.TimeoutExpired(timeout, pid=process.pid) from None
                time.sleep(.05)
    except psutil.TimeoutExpired as exc:
        raise PistolError(f"LECAP PID {process.pid} did not finish shutdown; it remains tracked. See {log_path()}.") from exc


def control(action: str = "toggle", *, http_port: int = 80, https_port: int = 443, startup_timeout: float = 20) -> dict:
    """Serialize toggles. Port overrides are library-only for isolated tests."""
    from .lecap import routes
    if action not in {"toggle", "start", "stop"}:
        raise PistolError("LECAP action must be start, stop or toggle.")
    with lock("lifecycle.lock", timeout=startup_timeout + 20):
        current = _inspect()
        record = read_record("state.json")
        if current["running"]:
            if action == "start":
                return {**current, "action": "already_active"}
            _stop(record)
            return {**_inspect(), "action": "stopped"}
        if action == "stop":
            return {**current, "action": "already_stopped"}
        # A surviving worker whose state was removed/corrupted still holds this lock.
        with lock("worker.lock", timeout=0):
            pass
        accesses = routes()
        if not accesses:
            raise PistolError("No enabled proxy routes. Configure a subdomain or --proxy true first.")
        check_ports(accesses, http_port, https_port)
        instance = uuid.uuid4().hex
        parent = psutil.Process()
        write_record("launch.json", {"instance": instance, "parent_pid": parent.pid, "parent_created": parent.create_time()})
        for name in ("state.json", "ack.json", "stop.json"):
            remove_record(name)
        child = None
        try:
            child = _launch(instance, http_port, https_port)
            deadline = time.monotonic() + startup_timeout
            while time.monotonic() < deadline:
                record = read_record("state.json")
                if record.get("instance") == instance:
                    if record.get("status") == "failed":
                        raise PistolError(f"Cannot start LECAP: {record.get('reason')}. Log: {log_path()}")
                    if record.get("status") == "ready":
                        ready = _inspect()
                        if ready["active"]:
                            write_record("ack.json", {"instance": instance})
                            return {**ready, "action": "started"}
                if child.poll() is not None:
                    raise PistolError(f"LECAP worker exited before startup was confirmed. Log: {log_path()}")
                time.sleep(.05)
            raise PistolError(f"LECAP did not become ready within {startup_timeout:g}s. Log: {log_path()}")
        except BaseException:
            write_record("stop.json", {"instance": instance})
            remove_record("launch.json")
            record = read_record("state.json")
            if record.get("instance") == instance:
                _stop(record)
            if child is not None:
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    # A slow startup observes cancellation before acknowledging readiness.
                    pass
            raise
