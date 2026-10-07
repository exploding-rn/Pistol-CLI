"""Standalone stdlib-only bootstrap sent to python3 over wsl.exe stdin.

This file must not import Pistol: the distribution may not have it installed yet.
"""
import base64
import hashlib
import io
import importlib.util
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
import zipfile


def read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def write(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def ticks(pid):
    # comm can itself contain spaces and parentheses.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def alive(state):
    try:
        pid = int(state["pid"])
        if pid <= 1 or ticks(pid) != state["start_ticks"]:
            return False
        args = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        return b"pistol.wsl.server" in args and state["instance"].encode() in args
    except (KeyError, OSError, ValueError):
        return False


def stop_process(state):
    if not alive(state):
        return
    os.kill(state["pid"], signal.SIGTERM)
    for _ in range(100):
        if not alive(state):
            return
        time.sleep(0.1)
    raise RuntimeError("Console did not finish stopping. Inspect ~/.local/share/Pistol/wsl-console/agent.log.")


def execute(request):
    import fcntl
    if sys.version_info < (3, 11):
        raise RuntimeError("Pistol WSL Console requires Python 3.11+ inside this distro.")
    if os.getuid() == 0:
        raise RuntimeError("The distro's default user is root. Configure a normal default Linux user before starting the console.")
    root = Path.home() / ".local/share/Pistol/wsl-console"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    state_path = root / "state.json"
    with (root / "agent.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read(state_path)
        running = alive(state)
        action = request["action"]
        if action == "status":
            return {"state": state if running else None}
        if action == "stop":
            if running and state["instance"] != request["instance"]:
                raise RuntimeError("Console identity changed; run status and try again.")
            stop_process(state)
            state_path.unlink(missing_ok=True)
            return {"stopped": True}
        if action != "start":
            raise ValueError("Unknown agent action.")
        if running:
            if not state.get("port"):
                raise RuntimeError("A console is still starting. Try again shortly, or use pistol wsl stop.")
            return {"state": state, "reused": True}
        archive = base64.b64decode(request["archive"], validate=True)
        release = root / "releases" / hashlib.sha256(archive).hexdigest()[:24]
        release.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            for name in bundle.namelist():
                path = release / name
                if not path.resolve().is_relative_to(release.resolve()):
                    raise ValueError("Invalid agent archive path.")
            bundle.extractall(release)
        python = root / "venv/bin/python"
        log_path = root / "agent.log"
        if log_path.exists() and log_path.stat().st_size > 2_000_000:
            log_path.replace(root / "agent.previous.log")
        with log_path.open("ab", buffering=0) as log:
            if not python.exists():
                created = subprocess.run([sys.executable, "-m", "venv", str(root / "venv")],
                                         stdout=log, stderr=log, timeout=60)
                if created.returncode and not (python.exists() and importlib.util.find_spec("pip")):
                    raise RuntimeError("Could not create the agent venv. Install python3-venv in your distro "
                                       "(Ubuntu: sudo apt install python3-venv), then retry. See " + str(log_path))
            check = subprocess.run([str(python), "-c", "import aiohttp, psutil; assert tuple(map(int, aiohttp.__version__.split('.')[:3])) >= (3,14,4)"],
                                   stdout=log, stderr=log)
            if check.returncode:
                has_pip = subprocess.run([str(python), "-c", "import pip"], stdout=log, stderr=log).returncode == 0
                if not has_pip and not importlib.util.find_spec("pip"):
                    raise RuntimeError("Install python3-venv in your distro (Ubuntu: sudo apt install python3-venv), "
                                       "then run: python3 -m venv " + str(root / "venv"))
                installer = [str(python), "-m", "pip"] if has_pip else [sys.executable, "-m", "pip", "--python", str(python)]
                installed = subprocess.run([*installer, "install", "--disable-pip-version-check",
                                            "aiohttp>=3.14.4,<4", "psutil>=5.9,<8"], stdout=log, stderr=log, timeout=150)
                if installed.returncode:
                    raise RuntimeError("Agent dependencies could not be installed. Check the distro's network/pip configuration and " + str(log_path))
            instance = secrets.token_hex(16)
            state = {"distro": request["distro"], "version": request["version"], "instance": instance,
                     "token": secrets.token_urlsafe(32), "port": 0}
            config = root / "launch.json"
            write(config, {**state, "ports": request["ports"], "state_path": str(state_path),
                           "windows_chambers": request.get("chambers", [])})
            environment = os.environ.copy()
            # Windows state locations must never become Linux state locations.
            for key in ("LOCALAPPDATA", "APPDATA", "PYTHONHOME"):
                environment.pop(key, None)
            environment["PYTHONPATH"] = str(release)
            process = subprocess.Popen([str(python), "-m", "pistol.wsl.server", "--config", str(config),
                                        "--instance", instance], cwd=Path.home(), env=environment,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
            state.update(pid=process.pid, start_ticks=ticks(process.pid))
            # The server waits for this record before publishing readiness.
            write(state_path, state)
            for _ in range(150):
                ready = read(state_path)
                if ready.get("instance") == instance and ready.get("port") and alive(ready):
                    return {"state": ready, "reused": False}
                if process.poll() is not None:
                    raise RuntimeError("The WSL console exited during startup. See " + str(log_path))
                time.sleep(0.1)
            stop_process(state)
            raise RuntimeError("The agent did not become ready. See " + str(log_path))


if __name__ == "__main__":
    os.umask(0o077)
    try:
        print(json.dumps(execute(json.load(sys.stdin))))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
