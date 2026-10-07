"""Detached lifecycle integration tests use isolated state and ephemeral ports."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import uuid

import psutil
import pytest

from pistol import certificates, chamber, lecap, lecap_runtime as runtime
from pistol.cli import build_parser, main
from pistol.config import PistolError, atomic_json, read_json
from pistol.lecap_proxy import request


@pytest.fixture
def route(project):
    class App(BaseHTTPRequestHandler):
        def do_GET(self):
            body = self.path.encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-App-Host", self.headers["Host"])
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    app = ThreadingHTTPServer(("127.0.0.1", 0), App)
    thread = threading.Thread(target=app.serve_forever, daemon=True)
    thread.start()
    item = chamber.create("api", project, create_venv=False, env={"SECRET_TOKEN": "never-in-runtime-state"})
    lecap.update(item, {"domain": "worker.test", "port": app.server_port})
    try:
        yield item
    finally:
        runtime.control("stop")
        app.shutdown()
        app.server_close()
        thread.join()


def test_toggle_starts_detached_ready_proxy_and_stops_without_orphan(route):
    started = runtime.control(http_port=0, https_port=0)
    assert started["action"] == "started" and started["active"]
    assert started["pid"] != os.getpid()
    assert runtime.status()["active"]
    status_code, headers = request("worker.test", "127.0.0.1", started["http_port"], path="/v1?a=1")
    assert status_code == 200 and headers["X-App-Host"] == "worker.test"
    assert "active (PID" in lecap.status(route)["proxy_status"]
    stopped = runtime.control()
    assert stopped["action"] == "stopped" and not stopped["active"]
    assert not psutil.pid_exists(started["pid"])
    assert not any(c.status == psutil.CONN_LISTEN and c.laddr.port == started["http_port"] for c in psutil.net_connections(kind="tcp"))


def test_explicit_start_and_stop_are_idempotent(route):
    first = runtime.control("start", http_port=0, https_port=0)
    second = runtime.control("start")
    assert second["action"] == "already_active" and second["pid"] == first["pid"]
    assert runtime.control("stop")["action"] == "stopped"
    assert runtime.control("stop")["action"] == "already_stopped"


def test_worker_survives_launcher_exit_and_separate_application_termination(route):
    script = "import json; from pistol.lecap_runtime import control; print(json.dumps(control('start', http_port=0, https_port=0)))"
    launcher = subprocess.run([sys.executable, "-I", "-c", script], capture_output=True, text=True, timeout=30)
    assert launcher.returncode == 0, launcher.stderr
    started = json.loads(launcher.stdout)
    assert runtime.status()["pid"] == started["pid"]
    from pistol import fire
    app = fire.launch(route.project, command=[sys.executable, "-u", "-c", "import time; time.sleep(60)"], echo=False)
    try:
        # On Windows fire.stop sends CTRL_BREAK to the application's process group.
        assert fire.stop(project=route.project)
        app.finish()
        assert runtime.status()["active"]
    finally:
        if app.process.poll() is None:
            fire.stop(project=route.project)
            app.finish()


def test_background_https_redirect_and_multiple_routes(route, project):
    lecap.update(route, {"https": True, "subdomain": ["one", "alias"]})
    second = chamber.create("second", project, create_venv=False)
    lecap.update(second, {"domain": "second.test", "port": lecap.effective(route).values["port"], "https": True})
    started = runtime.control("start", http_port=0, https_port=0)
    context = ssl.create_default_context(cafile=str(certificates.directory() / "ca.pem"))
    for name in ("one.worker.test", "alias.worker.test", "second.test"):
        status_code, headers = request(name, "127.0.0.1", started["http_port"], path="/v1?x=1")
        assert status_code == 308 and headers["Location"] == f"https://{name}/v1?x=1"
        status_code, headers = request(name, "127.0.0.1", started["https_port"], context=context, path="/v1")
        assert status_code == 200 and headers["X-App-Host"] == name
    state_text = (runtime.directory() / "state.json").read_text(encoding="utf-8")
    assert "PRIVATE KEY" not in state_text and "never-in-runtime-state" not in state_text
    assert "ca-key" not in state_text and '"env"' not in state_text
    runtime.control("stop")
    log = runtime.log_path().read_text(encoding="utf-8")
    assert "LECAP ready PID" in log and "LECAP stopped" in log


def test_stale_pid_is_removed_without_signaling_anyone(monkeypatch):
    instance = uuid.uuid4().hex
    record = runtime.identity(instance, 80, 443)
    record.update(pid=999999999, command=[sys.executable, *runtime.worker_arguments(instance, 80, 443)], status="ready")
    atomic_json(runtime.directory() / "state.json", record)
    assert runtime.control("stop")["action"] == "already_stopped"
    assert not (runtime.directory() / "state.json").exists()


def test_unrelated_reused_pid_is_never_stopped():
    instance = uuid.uuid4().hex
    record = runtime.identity(instance, 80, 443)
    # Real current PID and creation time, but not a Pistol worker command line.
    record.update(command=[sys.executable, *runtime.worker_arguments(instance, 80, 443)], status="ready")
    atomic_json(runtime.directory() / "state.json", record)
    result = runtime.control("stop")
    assert result["action"] == "already_stopped"
    assert psutil.Process(os.getpid()).is_running()
    assert "unrelated" in result["reason"]


def test_create_time_mismatch_rejects_pid_reuse(route):
    runtime.control("start", http_port=0, https_port=0)
    record = read_json(runtime.directory() / "state.json", {})
    record["create_time"] -= 10
    assert runtime.owned_process(record) is None
    runtime._stop(record)  # must not request shutdown for a mismatched identity
    assert runtime.status()["active"]


def test_stop_handles_windows_wait_access_denied(monkeypatch):
    class QueryableWorker:
        pid = 1234
        checks = iter([True, False])
        def wait(self, timeout):
            raise psutil.AccessDenied(self.pid)
        def is_running(self):
            return next(self.checks)
    monkeypatch.setattr(runtime, "owned_process", lambda record: QueryableWorker())
    runtime._stop({"instance": "test-instance"})
    assert runtime.read_record("stop.json") == {"instance": "test-instance"}


def test_stop_wait_access_denied_does_not_claim_a_live_worker_exited(monkeypatch):
    class QueryableWorker:
        pid = 1234
        def wait(self, timeout):
            raise psutil.AccessDenied(self.pid)
        def is_running(self):
            return True
    monkeypatch.setattr(runtime, "owned_process", lambda record: QueryableWorker())
    with pytest.raises(PistolError, match="remains tracked"):
        runtime._stop({"instance": "test-instance"}, timeout=0)


def test_missing_state_does_not_spawn_second_worker(route):
    started = runtime.control("start", http_port=0, https_port=0)
    original = read_json(runtime.directory() / "state.json", {})
    (runtime.directory() / "state.json").unlink()
    try:
        with pytest.raises(PistolError, match="already busy"):
            runtime.control("start", http_port=0, https_port=0)
        assert psutil.pid_exists(started["pid"])
    finally:
        atomic_json(runtime.directory() / "state.json", original)


def test_occupied_port_reports_owner_without_launching(route, monkeypatch):
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        port = occupied.getsockname()[1]
        monkeypatch.setattr(runtime, "_launch", lambda *a: pytest.fail("must not launch"))
        with pytest.raises(PistolError, match=f"Port {port} is already owned by PID {os.getpid()}"):
            runtime.control("start", http_port=port)
        assert occupied.fileno() != -1


def test_worker_startup_failure_is_reported_and_logged(route):
    lecap.update(route, {"lan": True})  # no explicitly assigned LAN address
    with pytest.raises(PistolError, match="lan-address"):
        runtime.control("start", http_port=0, https_port=0)
    assert not runtime.status()["running"]
    assert "lan-address" in runtime.log_path().read_text(encoding="utf-8")


def test_config_change_stops_worker_and_reports_restart_required(route):
    started = runtime.control("start", http_port=0, https_port=0)
    process = psutil.Process(started["pid"])
    lecap.update(route, {"enabled": False})
    process.wait(timeout=10)
    state = runtime.status()
    assert not state["active"] and state["needs_restart"]
    assert "Configuration changed" in state["reason"]


@pytest.mark.parametrize("argv,action", [(["lecap", "--serve"], "toggle"),
    (["lecap", "--serve", "start"], "start"), (["lecap", "--serve", "stop"], "stop")])
def test_serve_parser(argv, action):
    assert build_parser().parse_args(argv).serve == action
    assert "lecap_worker" not in build_parser().format_help()


def test_cli_json_contains_only_machine_readable_state(route, monkeypatch, capsys):
    from pistol import lecap_proxy
    real_serve = lambda action: runtime.control(action, http_port=0, https_port=0)
    monkeypatch.setattr(lecap_proxy, "serve", real_serve)
    assert main(["lecap", "--project", route.project, "--serve", "--json"]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state["active"] and state["pid"]
    assert state["http_port"] and state["https_port"] is None
    assert main(["lecap", "--project", route.project, "--serve", "stop", "--json"]) == 0
    assert not json.loads(capsys.readouterr().out)["active"]


def test_windows_windowless_flags_and_no_console_handles(monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows process flags")
    captured = {}
    monkeypatch.setattr(runtime.subprocess, "Popen", lambda command, **kwargs: captured.update(command=command, **kwargs))
    runtime._launch(uuid.uuid4().hex, 80, 443)
    assert captured["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert captured["stdin"] == captured["stdout"] == captured["stderr"] == subprocess.DEVNULL
    assert captured["close_fds"] is True


@pytest.mark.skipif(os.name != "nt", reason="Windows console APIs")
def test_real_worker_has_no_console_window(route):
    started = runtime.control("start", http_port=0, https_port=0)
    # Query the actual worker, not the venv redirector PID. A separate windowless
    # helper temporarily attaches to its console without changing pytest's own.
    script = '''
import ctypes, json, sys
from ctypes import wintypes
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.AttachConsole.argtypes = [wintypes.DWORD]
kernel.GetConsoleWindow.restype = wintypes.HWND
kernel.FreeConsole()
attached = kernel.AttachConsole(int(sys.argv[1]))
if not attached and ctypes.get_last_error() != 6:  # ERROR_INVALID_HANDLE: no console
    raise ctypes.WinError(ctypes.get_last_error())
try:
    print(json.dumps({"console_window": kernel.GetConsoleWindow()}))
finally:
    if attached:
        kernel.FreeConsole()
'''
    checked = subprocess.run([sys.executable, "-I", "-c", script, str(started["pid"])],
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             capture_output=True, text=True, timeout=15)
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["console_window"] is None
    assert runtime.status()["active"]


@pytest.mark.parametrize("port", [80, 443])
def test_standard_listener_conflicts_are_clear(route, monkeypatch, port):
    lecap.update(route, {"https": True})
    class BusySocket:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def setsockopt(self, *args): pass
        def bind(self, address):
            if address[1] == port:
                raise OSError("address already in use")
    monkeypatch.setattr(runtime.socket, "socket", BusySocket)
    monkeypatch.setattr(runtime, "describe_conflict", lambda address, number, error: f"Port {number} is already owned by PID 1234 (other.exe)")
    with pytest.raises(PistolError, match=f"Port {port}.*PID 1234"):
        runtime.check_ports(lecap.routes(), 80, 443)


def test_startup_cancellation_leaves_no_worker(route, monkeypatch):
    launched = []
    original = runtime._launch
    def capture(*args):
        child = original(*args)
        launched.append(child)
        return child
    monkeypatch.setattr(runtime, "_launch", capture)
    with pytest.raises(PistolError, match="did not become ready"):
        runtime.control("start", http_port=0, https_port=0, startup_timeout=0)
    assert launched[0].wait(timeout=10) is not None
    assert not runtime.status()["running"]


def test_concurrent_starts_share_one_instance(route):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(runtime.control, "start", http_port=0, https_port=0) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert {r["action"] for r in results} == {"started", "already_active"}
    assert len({r["pid"] for r in results}) == 1


def test_status_checks_listeners_and_route_not_just_pid(route):
    runtime.control("start", http_port=0, https_port=0)
    path = runtime.directory() / "state.json"
    original = read_json(path, {})
    changed = {**original, "http_port": lecap.effective(route).values["port"]}
    atomic_json(path, changed)
    try:
        status = runtime.status()
        assert status["running"] and not status["active"]
    finally:
        atomic_json(path, original)
