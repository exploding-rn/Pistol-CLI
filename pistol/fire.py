"""Foreground process supervision, log capture and identity-checked stopping."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import codecs
import logging
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import threading
import time
import uuid

import psutil

from .chamber import Chamber, current
from .config import PistolError, atomic_json, project_state, read_json, state_lock
from .env import read_dotenv
from .utils.project import detect, entrypoint_file, root_path, validate_entrypoint

logger = logging.getLogger(__name__)


def is_alive(record: dict) -> bool:
    if not record or record.get("status") != "running":
        return False
    try:
        process = psutil.Process(int(record["pid"]))
        return abs(process.create_time() - float(record["create_time"])) < 0.01 and process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, KeyError, TypeError, ValueError):
        return False
    except psutil.AccessDenied:
        # Identity is uncertain: protect it from duplicate launches and cleanup.
        return True


def record_path(selected: Chamber | None, project: Path) -> Path:
    return (selected.directory if selected else project_state(project)) / "process.json"


def project_python(project: Path, selected: Chamber | None = None) -> str:
    candidate = selected.python if selected else project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(candidate) if candidate.is_file() else sys.executable


def command_for(project: Path, selected: Chamber | None = None, command: list[str] | None = None) -> list[str]:
    info = detect(project)
    entry = list(command if command is not None else selected.entrypoint if selected else info.entrypoint)
    if not entry:
        raise PistolError("No entrypoint detected. Create a chamber with --entrypoint main.py or --entrypoint '-m uvicorn app:app --port {port}'.")
    runtime = selected.runtime if selected else info.runtime
    port = selected.port if selected else os.environ.get("PORT", "8000")
    entry = [part.replace("{port}", str(port)) for part in entry]
    source = validate_entrypoint(project, entry)
    if command is not None:
        if Path(entry[0]).name.lower() in {"python", "python.exe", "python3"}:
            entry[0] = project_python(project, selected)
        return entry
    if runtime == "python":
        if Path(entry[0]).name.lower() in {"python", "python.exe", "python3"}:
            entry.pop(0)
        return [project_python(project, selected), *entry]
    if runtime == "node" and source and source.suffix.lower() in {".js", ".mjs", ".cjs", ".ts"} and Path(entry[0]).suffix.lower() == source.suffix.lower():
        return ["node", *entry]
    return entry


def environment(project: Path, selected: Chamber | None, extra: dict[str, str] | None = None) -> dict[str, str]:
    # OS environment wins over .env; explicit chamber and magazine settings win last.
    result = {**read_dotenv(project / ".env"), **os.environ}
    state = selected.directory if selected else project_state(project)
    for folder in ("temp", "cache", "state", "logs"):
        (state / folder).mkdir(parents=True, exist_ok=True)
    result.update(selected.env if selected else {})
    result.update(extra or {})
    result.update({"PISTOL_STATE_DIR": str(state / "state"), "PISTOL_CACHE_DIR": str(state / "cache"), "PISTOL_LOG_DIR": str(state / "logs"), "TEMP": str(state / "temp"), "TMP": str(state / "temp"), "TMPDIR": str(state / "temp"), "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    if selected:
        result.update({"PISTOL_CHAMBER": selected.name, "PORT": str(selected.port), "HOST": "127.0.0.1"})
    python = Path(project_python(project, selected))
    local_python = project / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if selected and selected.python.is_file() or not selected and local_python.is_file():
        result["VIRTUAL_ENV"] = str(python.parent.parent)
        result["PATH"] = str(python.parent) + os.pathsep + result.get("PATH", "")
        result.pop("PYTHONHOME", None)
        result.pop("PYTHONPATH", None)
    return result


def resolve_executable(command: list[str], project: Path, env: dict[str, str]) -> list[str]:
    """Windows CreateProcess does not search PATHEXT or the child's PATH."""
    command = list(command)
    first = Path(command[0])
    if not first.is_absolute() and first.parent != Path("."):
        command[0] = str((project / first).resolve())
    else:
        command[0] = shutil.which(command[0], path=env.get("PATH")) or command[0]
    resolved = Path(command[0])
    if not (resolved.is_file() if resolved.is_absolute() or resolved.parent != Path(".") else shutil.which(command[0], path=env.get("PATH"))):
        name = Path(command[0]).name.lower()
        label = "Python executable" if name.startswith(("python", "py.exe")) else "Node.js executable" if name.startswith("node") else "Rust compiler" if name.startswith("rustc") else "Runtime executable"
        raise PistolError(f"{label} not found: {command[0]}")
    if os.name == "nt" and resolved.suffix.lower() in {".cmd", ".bat"}:
        # npm's Windows shim otherwise introduces cmd.exe argument interpretation.
        script = resolved.parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
        node = shutil.which("node", path=env.get("PATH"))
        if resolved.stem.lower() == "npm" and script.is_file() and node:
            return [node, str(script), *command[1:]]
        raise PistolError("This executable is a Windows batch wrapper. Configure an explicit cmd.exe command if you intend to run a shell, or use the underlying executable/script.")
    return command


def _prepare_rust(project: Path, selected: Chamber | None, entry: list[str], env: dict[str, str]) -> list[str]:
    source = entrypoint_file(project, entry)
    if not source or source.suffix.lower() != ".rs" or Path(entry[0]).suffix.lower() != ".rs":
        return entry
    compiler = shutil.which("rustc", path=env.get("PATH"))
    if not compiler:
        raise PistolError("Rust compiler not found: rustc")
    target = (selected.directory if selected else project_state(project)) / "temp" / (f"rust-{uuid.uuid4().hex}" + (".exe" if os.name == "nt" else ""))
    try:
        built = subprocess.run([compiler, str(source), "-o", str(target)], cwd=project, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PistolError(f"Rust compilation could not start: {exc}") from exc
    if built.returncode:
        raise PistolError("Rust compilation failed: " + (built.stderr or built.stdout).strip()[-2000:])
    if not target.is_file():
        raise PistolError(f"Rust executable not found after compilation: {target}")
    return [str(target), *entry[1:]]


@dataclass
class RunningProcess:
    process: subprocess.Popen
    record: dict
    state_file: Path
    reader: threading.Thread
    output_errors: list[str] = field(default_factory=list)

    def finish(self) -> int:
        code = self.process.wait()
        self.reader.join(timeout=3)
        self.record.update(status="exited", exit_code=code, ended=datetime.now(timezone.utc).isoformat())
        if self.output_errors:
            self.record["output_errors"] = self.output_errors
        with state_lock():
            stored = read_json(self.state_file, {})
            if stored.get("run_id") == self.record["run_id"]:
                atomic_json(self.state_file, self.record)
        return code


def launch(project: Path | str | None = None, *, selected: Chamber | None = None, command: list[str] | None = None, extra_env: dict[str, str] | None = None, echo: bool = True) -> RunningProcess:
    root = Path(selected.project) if selected else root_path(project)
    if not root.is_dir():
        raise PistolError(f"Project directory is missing: {root}")
    cmd = command_for(root, selected, command)
    if selected and selected.runtime == "python" and not selected.python.is_file():
        raise PistolError(f"Python executable not found: {selected.python}. Run pistol medic --chamber {selected.name}.")
    state_file = record_path(selected, root)
    env = environment(root, selected, extra_env)
    if (selected.runtime if selected else detect(root).runtime) == "rust" and command is None:
        cmd = _prepare_rust(root, selected, cmd, env)
    cmd = resolve_executable(cmd, root, env)
    log_file = state_file.parent / "logs" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.log"
    with state_lock():
        if is_alive(read_json(state_file, {})):
            raise PistolError("This project/chamber already has a running Pistol process.")
        if selected:
            from .ports import available
            if not available(selected.port):
                raise PistolError(f"Port {selected.port} is occupied; use pistol detective --port {selected.port}.")
        log_stream = log_file.open("wb")
        try:
            process = subprocess.Popen(cmd, cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=None, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0, start_new_session=os.name != "nt")
        except FileNotFoundError as exc:
            log_stream.close()
            raise PistolError(f"Runtime executable not found: {cmd[0]}") from exc
        except BaseException:
            log_stream.close()
            raise
        try:
            created = psutil.Process(process.pid).create_time()
            record = {"schema": 1, "run_id": uuid.uuid4().hex, "pid": process.pid, "create_time": created, "started": datetime.now(timezone.utc).isoformat(), "cwd": str(root), "command": cmd, "log_file": str(log_file), "port": selected.port if selected else None, "chamber": selected.name if selected else None, "status": "running", "exit_code": None}
            atomic_json(state_file, record)
        except BaseException:
            process.terminate()
            process.wait(timeout=5)
            log_stream.close()
            raise
    errors: list[str] = []

    def copy_output():
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            assert process.stdout is not None
            while chunk := process.stdout.read1(4096):
                log_stream.write(chunk)
                log_stream.flush()
                if echo:
                    try:
                        sys.stdout.write(decoder.decode(chunk))
                        sys.stdout.flush()
                    except (OSError, UnicodeError):
                        pass
        except OSError as exc:
            errors.append(str(exc))
            logger.error("Unable to capture process output: %s", exc)
        finally:
            log_stream.close()
            if process.stdout:
                process.stdout.close()

    reader = threading.Thread(target=copy_output, name=f"pistol-log-{process.pid}", daemon=True)
    reader.start()
    logger.info("Started PID %s for %s; log %s", process.pid, root, log_file)
    return RunningProcess(process, record, state_file, reader, errors)


def stop_record(record: dict, timeout: float = 5) -> bool:
    if not is_alive(record):
        return False
    try:
        parent = psutil.Process(record["pid"])
        if abs(parent.create_time() - record["create_time"]) >= 0.01:
            raise PistolError("PID was reused; refusing to stop it.")
        children = parent.children(recursive=True)
        try:
            if os.name == "nt":
                os.kill(parent.pid, signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(parent.pid, signal.SIGTERM)
        except OSError:
            parent.terminate()
        _, alive = psutil.wait_procs([parent, *children], timeout=timeout)
        for process in alive:
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
        _, alive = psutil.wait_procs(alive, timeout=2)
        for process in alive:
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        return True
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied as exc:
        raise PistolError("Windows denied permission to stop this process.") from exc


def stop(selected: Chamber | None = None, project: Path | str | None = None) -> bool:
    path = record_path(selected, Path(selected.project) if selected else root_path(project))
    record = read_json(path, {})
    stopped = stop_record(record)
    if record and not is_alive(record):
        with state_lock():
            stored = read_json(path, {})
            if stored.get("run_id") == record.get("run_id"):
                stored.update(status="stopped" if stopped else "stale", ended=datetime.now(timezone.utc).isoformat())
                atomic_json(path, stored)
    return stopped


def supervise(processes: list[RunningProcess]) -> int:
    """Wait for all children. If one fails, stop peers and return its failure."""
    try:
        while True:
            codes = [item.process.poll() for item in processes]
            failure = next((code for code in codes if code not in (None, 0)), None)
            if failure is not None:
                return failure if 0 < failure < 126 else 1
            if all(code is not None for code in codes):
                return 0
            time.sleep(0.1)
    except KeyboardInterrupt:
        return 130
    finally:
        for item in processes:
            if item.process.poll() is None:
                stop_record(item.record)
            item.finish()


def run(project: Path | str | None = None, *, name: str | None = None, magazine: str | None = None) -> int:
    from .chamber import get
    from .magazine import get as get_magazine, environment as magazine_environment
    processes = []
    try:
        if magazine:
            group = get_magazine(magazine)
            if not group.chambers:
                raise PistolError("Magazine has no chambers. Add one with pistol mag NAME --add CHAMBER.")
            extra = magazine_environment(group)
            for member in group.chambers:
                processes.append(launch(selected=get(member), extra_env=extra))
        else:
            selected = get(name) if name else current(project)
            processes.append(launch(project, selected=selected))
    except BaseException:
        for item in processes:
            stop_record(item.record)
            item.finish()
        raise
    return supervise(processes)
