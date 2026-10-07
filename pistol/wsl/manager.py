"""Windows orchestration; all WSL calls use argument arrays and bounded timeouts."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import json
import logging
from pathlib import Path
import re
import subprocess
import time
import urllib.request
import zipfile

from ..config import PistolError, atomic_json, data_dir, read_json, state_lock
from ..ports import available, free_port


@dataclass
class Distro:
    name: str
    state: str
    version: int
    default: bool = False


def decode_output(raw: bytes) -> str:
    return raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else
                      "utf-16-le" if b"\0" in raw else "utf-8", errors="replace").lstrip("\ufeff")


def parse_distros(raw: bytes | str) -> list[Distro]:
    text = decode_output(raw) if isinstance(raw, bytes) else raw.replace("\0", "")
    result = []
    for line in text.splitlines():
        match = re.match(r"^\s*(\*)?\s*(.+)\s{2,}(\S.*?)\s{2,}([12])\s*$", line)
        if match:
            result.append(Distro(match[2].strip(), match[3].strip(), int(match[4]), bool(match[1])))
    return result


def run_wsl(argv, *, payload=None, timeout=30):
    try:
        result = subprocess.run(["wsl.exe", *argv], input=payload, capture_output=True,
                                timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise PistolError("WSL is not installed. Run 'wsl --install' in Windows, then install a Linux distro.") from None
    except subprocess.TimeoutExpired:
        raise PistolError("WSL did not respond in time. Check 'wsl --status' and start your distro manually.") from None
    if result.returncode:
        detail = decode_output(result.stderr or result.stdout).strip()[-1500:]
        raise PistolError(f"WSL command failed: {detail or 'the distro could not start'}. Check 'wsl --status'.")
    return result.stdout


def list_distros():
    items = parse_distros(run_wsl(["--list", "--verbose"]))
    if not items:
        raise PistolError("No WSL distributions found. Install one using 'wsl --install -d <name>'.")
    return items


def select_distro(items, requested=None, choose=None):
    if requested:
        found = next((item for item in items if item.name.casefold() == requested.casefold()), None)
        if not found:
            raise PistolError(f"WSL distribution '{requested}' does not exist. Run 'pistol wsl list'.")
        return found
    if len(items) == 1:
        return items[0]
    defaults = [item for item in items if item.default]
    if len(defaults) == 1:
        return defaults[0]
    if choose:
        return choose(items)
    raise PistolError("Choose a distribution: pistol wsl gui <distro>.")


def wsl_command(distro, *command):
    if not isinstance(distro, str) or not distro or any(ord(c) < 32 for c in distro):
        raise PistolError("Invalid WSL distribution name.")
    return ["--distribution", distro, "--exec", *command]


def candidate_ports():
    ports = [8765] if available(8765) else []
    while len(ports) < 8:
        ports.append(free_port(set(ports)))
    return ports


def bundle():
    root = Path(__file__).resolve().parents[1]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file() and (path.suffix == ".py" or "web" in path.relative_to(root).parts):
                archive.write(path, "pistol/" + path.relative_to(root).as_posix())
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def agent(distro, action, **values):
    script = Path(__file__).with_name("bootstrap.py").read_text(encoding="utf-8")
    # Keep argv short and independent of Windows command-line size limits.
    # The script, bundle and request travel over stdin, with no shell interpolation.
    loader = ("import json,os,sys\n"
              "packet=json.load(sys.stdin)\n"
              "namespace={'__name__':'pistol_wsl_bootstrap'}\n"
              "os.umask(0o077)\n"
              "try:\n"
              " exec(packet['script'],namespace)\n"
              " print(json.dumps(namespace['execute'](packet['request'])))\n"
              "except Exception as exc:\n"
              " print(json.dumps({'error':str(exc)}))\n")
    raw = run_wsl(wsl_command(distro, "python3", "-c", loader),
                  payload=json.dumps({"script": script, "request": {"action": action, "distro": distro, **values}}).encode(),
                  timeout=240 if action == "start" else 30)
    try:
        response = json.loads(raw)
    except (ValueError, UnicodeError):
        raise PistolError("The WSL agent returned invalid output. Check Python 3.11+ in the distro.") from None
    if not isinstance(response, dict):
        raise PistolError("The WSL agent returned an invalid response.")
    if response.get("error"):
        raise PistolError(response["error"])
    return response


def state_path():
    return data_dir() / "wsl-console.json"


def public_state(state):
    return {"distro": state["distro"], "status": state.get("status", "Running"),
            "pid": state["pid"], "port": state["port"], "url": f"http://localhost:{state['port']}"}


def validate_state(state):
    if (not isinstance(state, dict) or not all(isinstance(state.get(key), str) and state[key]
            for key in ("distro", "token", "instance", "start_ticks")) or
            type(state.get("pid")) is not int or state["pid"] <= 1 or
            type(state.get("port")) is not int or not 0 <= state["port"] <= 65535):
        raise PistolError("Invalid WSL console state. Inspect wsl-console.json; it has been left intact.")
    return state


def request_agent(state, route, body=None):
    request = urllib.request.Request(f"http://127.0.0.1:{state['port']}/api/{route}",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Authorization": "Bearer " + state["token"],
                                              "Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=8 if body is not None else 2) as response:
        return json.load(response)


def reachable(state):
    try:
        value = request_agent(state, "health")
        return value.get("instance") == state["instance"] and value.get("pid") == state["pid"]
    except (OSError, ValueError):
        return False


def _tracked():
    saved = read_json(state_path(), {})
    if not saved:
        return None
    validate_state(saved)
    if reachable(saved):
        return saved
    result = agent(saved["distro"], "status")
    state = result.get("state")
    if state:
        validate_state(state)
        atomic_json(state_path(), state)
        return state
    state_path().unlink(missing_ok=True)
    return None


def status():
    with state_lock(timeout=260):
        state = _tracked()
    if state and not reachable(state):
        state["status"] = "Running; Windows localhost forwarding unavailable"
    return state


def stop():
    with state_lock(timeout=260):
        state = _tracked()
        if not state:
            return False
        try:
            response = request_agent(state, "shutdown", {"instance": state["instance"]})
            if response.get("instance") != state["instance"]:
                raise ValueError("Agent identity changed.")
            for _ in range(30):
                if not reachable(state):
                    break
                time.sleep(0.2)
            else:
                raise OSError("Agent has not stopped.")
        except (OSError, ValueError):
            agent(state["distro"], "stop", instance=state["instance"])
        state_path().unlink(missing_ok=True)
        logging.getLogger("pistol.wsl").info("Stopped WSL console in %s", state["distro"])
        return True


def start(requested=None, *, choose=None, progress=lambda message: None):
    items = list_distros()
    progress("WSL detected")
    with state_lock(timeout=260):
        tracked = _tracked()
        selected = select_distro(items, requested or (tracked["distro"] if tracked else None), choose)
        progress(f"{selected.name} selected")
        if tracked and tracked["distro"] != selected.name:
            raise PistolError(f"A console is already running in {tracked['distro']}. Run 'pistol wsl stop' before switching.")
        if tracked:
            state, reused = tracked, True
        else:
            progress("Starting Pistol agent (first use installs dependencies in a private Linux venv)")
            # Reuse the chamber library on Windows; transfer only display metadata, never env secrets.
            from ..chamber import list_chambers
            from ..fire import is_alive
            chambers = [{"name": c.name, "path": c.project, "port": c.port, "entrypoint": c.entrypoint,
                         "status": "running on Windows" if is_alive(read_json(c.directory / "process.json", {})) else c.status}
                        for c in list_chambers()]
            result = agent(selected.name, "start", archive=bundle(), ports=candidate_ports(),
                           version=selected.version, chambers=chambers)
            state, reused = result["state"], result.get("reused", False)
            atomic_json(state_path(), state)
        for _ in range(12):
            if reachable(state):
                break
            time.sleep(0.4)
        else:
            raise PistolError("The console is running inside WSL, but Windows cannot reach its loopback port. "
                              "Check WSL localhostForwarding in %USERPROFILE%\\.wslconfig and firewall settings. "
                              "Use 'pistol wsl status' or 'pistol wsl stop'; Pistol will not expose it to the LAN.")
    progress("Terminal service ready")
    progress("Web console ready")
    logging.getLogger("pistol.wsl").info("WSL console ready in %s on port %s", state["distro"], state["port"])
    return state, reused
