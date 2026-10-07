"""Portable orchestration/security tests plus Linux-only real filesystem and PTY tests."""
import asyncio
import json
import os
from pathlib import Path
import subprocess

import pytest

from pistol.cli import build_parser, main
from pistol.config import PistolError, atomic_json
from pistol.wsl import files, manager

DISTROS = "  NAME                   STATE           VERSION\r\n* Ubuntu                 Running         2\r\n  Debian custom          Stopped         1\r\n"
linux = pytest.mark.skipif(os.name != "posix", reason="Requires Linux directory descriptors / PTY")


@pytest.mark.parametrize("value", [DISTROS, DISTROS.encode("utf-16-le"), DISTROS.encode("utf-16"), DISTROS.encode()])
def test_parse_distros(value):
    result = manager.parse_distros(value)
    assert result == [manager.Distro("Ubuntu", "Running", 2, True), manager.Distro("Debian custom", "Stopped", 1)]


def test_localized_state_and_header():
    assert manager.parse_distros("  NAME     STATUS    VERSION\n* Ubuntu   Angehalten   2")[0].state == "Angehalten"
    assert manager.parse_distros("No distributions installed") == []
    assert manager.parse_distros("* My  Ubuntu    Running    2")[0].name == "My  Ubuntu"


def test_bootstrap_script_travels_over_stdin(monkeypatch):
    def run(argv, *, payload, timeout):
        assert len(argv[-1]) < 1000
        assert "execute(request)" in json.loads(payload)["script"]
        assert json.loads(payload)["request"] == {"action": "status", "distro": "Ubuntu"}
        return b'{"state":null}'
    monkeypatch.setattr(manager, "run_wsl", run)
    assert manager.agent("Ubuntu", "status") == {"state": None}


def test_selection():
    items = manager.parse_distros(DISTROS)
    assert manager.select_distro(items).name == "Ubuntu"
    assert manager.select_distro(items, "debian CUSTOM").version == 1
    assert manager.select_distro(items[1:]).name == "Debian custom"
    with pytest.raises(PistolError, match="does not exist"):
        manager.select_distro(items, "Missing")
    items[0].default = False
    with pytest.raises(PistolError, match="Choose"):
        manager.select_distro(items)
    assert manager.select_distro(items, choose=lambda choices: choices[1]) == items[1]


def test_wsl_argv_no_shell(monkeypatch):
    captured = {}
    def run(argv, **kwargs):
        captured.update(argv=argv, **kwargs)
        return subprocess.CompletedProcess(argv, 0, b"ok", b"")
    monkeypatch.setattr(manager.subprocess, "run", run)
    name = "Distro space;$(echo nope)"
    manager.run_wsl(manager.wsl_command(name, "python3", "-c", "print('hi')"))
    assert captured["argv"] == ["wsl.exe", "--distribution", name, "--exec", "python3", "-c", "print('hi')"]
    assert not captured.get("shell")


def test_missing_wsl_and_timeout(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError()
    monkeypatch.setattr(manager.subprocess, "run", missing)
    with pytest.raises(PistolError, match="not installed"):
        manager.list_distros()
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("wsl", 30)
    monkeypatch.setattr(manager.subprocess, "run", timeout)
    with pytest.raises(PistolError, match="did not respond"):
        manager.list_distros()


def test_port_fallback(monkeypatch):
    monkeypatch.setattr(manager, "available", lambda port: False)
    monkeypatch.setattr(manager, "free_port", lambda used: 18000 + len(used))
    assert manager.candidate_ports() == list(range(18000, 18008))
    monkeypatch.setattr(manager, "available", lambda port: True)
    assert manager.candidate_ports()[0] == 8765


def state():
    return {"distro": "Ubuntu", "pid": 1234, "port": 8765, "instance": "unique", "token": "secret", "start_ticks": "42"}


def test_stale_state_removed(monkeypatch):
    atomic_json(manager.state_path(), state())
    monkeypatch.setattr(manager, "agent", lambda *args, **kwargs: {"state": None})
    monkeypatch.setattr(manager, "reachable", lambda saved: False)
    assert manager.status() is None
    assert not manager.state_path().exists()


def test_stop_checks_current_identity(monkeypatch):
    atomic_json(manager.state_path(), state())
    calls = []
    def agent(distro, action, **kwargs):
        calls.append((distro, action, kwargs))
        return {"state": state()}
    monkeypatch.setattr(manager, "agent", agent)
    monkeypatch.setattr(manager, "reachable", lambda saved: False)
    def unavailable(*args, **kwargs):
        raise OSError("Forwarding unavailable")
    monkeypatch.setattr(manager, "request_agent", unavailable)
    assert manager.stop()
    assert calls[-1] == ("Ubuntu", "stop", {"instance": "unique"})
    assert not manager.state_path().exists()


def test_reuse_does_not_launch(monkeypatch):
    atomic_json(manager.state_path(), state())
    monkeypatch.setattr(manager, "list_distros", lambda: manager.parse_distros(DISTROS))
    calls = []
    def agent(distro, action, **kwargs):
        calls.append(action)
        return {"state": state()}
    monkeypatch.setattr(manager, "agent", agent)
    monkeypatch.setattr(manager, "reachable", lambda saved: True)
    result, reused = manager.start()
    assert reused and result["pid"] == 1234 and calls == []
    with pytest.raises(PistolError, match="before switching"):
        manager.start("Debian custom")


def test_status_redacts_token_and_reports_forwarding(monkeypatch, capsys):
    atomic_json(manager.state_path(), state())
    monkeypatch.setattr(manager, "agent", lambda *args, **kwargs: {"state": state()})
    monkeypatch.setattr(manager, "reachable", lambda saved: False)
    assert main(["wsl", "status", "--json"]) == 0
    output = capsys.readouterr().out
    assert "secret" not in output and "forwarding unavailable" in output


@pytest.mark.parametrize("argv", [["wsl", "gui"], ["wsl", "gui", "Debian custom"], ["wsl", "list"], ["wsl", "status"], ["wsl", "stop"]])
def test_cli_commands(argv):
    assert build_parser().parse_args(argv).wsl_action == argv[1]


@pytest.mark.parametrize("value", ["relative", "/home/../etc", "\x00", "/tmp/a\x00b", "/tmp/a\\b", None, 5])
def test_invalid_paths(value):
    with pytest.raises(ValueError):
        files.validate_path(value)


@pytest.mark.parametrize("path", ["/", "//etc", "/etc", "/etc/passwd", "/usr/lib", "/proc", "/mnt/c", "/home",
                                  "/mnt/c/Windows/System32", "/mnt/c/Users", "/mnt/c/Users/Keller", "/mnt/d/Program Files"])
def test_protected_deletion(path):
    with pytest.raises(PermissionError):
        files.protect_delete(path)


@linux
def test_files_roundtrip_conflicts_and_symlinks(tmp_path, request):
    target = str(tmp_path / "file.txt")
    files.create(target, b"original")
    with pytest.raises(FileExistsError):
        files.create(target, b"overwrite")
    document = files.read_text(target)
    assert document["text"] == "original"
    with pytest.raises(ValueError, match="Confirm"):
        files.save_text(target, "new", document["revision"], False)
    files.save_text(target, "new", document["revision"], True)
    with pytest.raises(FileExistsError):
        files.save_text(target, "stale", document["revision"], True)
    link = tmp_path / "link"
    link.symlink_to(target)
    request.addfinalizer(lambda: link.unlink(missing_ok=True))
    with pytest.raises(OSError):
        files.read_text(str(link))
    directory_link = tmp_path / "directory-link"
    directory_link.symlink_to(tmp_path, target_is_directory=True)
    request.addfinalizer(lambda: directory_link.unlink(missing_ok=True))
    with pytest.raises(OSError):
        files.create(str(directory_link / "escape"))
    other = str(tmp_path / "other.txt")
    files.create(other, b"keep")
    with pytest.raises(FileExistsError):
        files.rename(target, other)
    files.rename(target, str(tmp_path / "renamed.txt"))
    with pytest.raises(ValueError):
        files.delete(other, False)
    files.delete(other, True)
    assert not Path(other).exists()
    assert files.listing(str(tmp_path))["entries"]


@linux
def test_recursive_delete_does_not_follow_link(tmp_path, request):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "link").symlink_to(outside, target_is_directory=True)
    request.addfinalizer(lambda: (folder / "link").unlink(missing_ok=True))
    files.delete(str(folder), True)
    assert (outside / "keep").read_text() == "keep"


def test_authentication_origin_and_host(monkeypatch):
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer
    from pistol.wsl import server
    monkeypatch.setattr(server.system, "snapshot", lambda config: {"home": "/home/test"})
    monkeypatch.setattr(server.processes.ProcessMonitor, "sample", lambda self: [])
    async def scenario():
        config = {"token": "secret", "instance": "test", "port": 0}
        app = server.create_app(config)
        async with TestClient(TestServer(app)) as client:
            config["port"] = client.server.port
            assert (await client.get("/api/system")).status == 401
            auth = {"Authorization": "Bearer secret"}
            assert (await client.get("/api/system", headers=auth)).status == 200
            assert (await client.get("/api/system", headers={**auth, "Origin": "http://evil.example"})).status == 403
            assert (await client.get("/api/system", headers={**auth, "Host": "evil.example"})).status == 403
            assert (await client.post("/api/files", json={"action":"delete", "path":"/etc"}, headers=auth)).status == 400
            assert (await client.get("/assets/../server.py", headers=auth)).status == 404
            assert (await client.get("/")).headers["X-Frame-Options"] == "DENY"
            assert (await client.post("/api/shutdown", json={"instance":"wrong"}, headers=auth)).status == 400
            assert not app[server.STOPPING].is_set()
            # The first WS message must authenticate before any PTY is created.
            if os.name == "posix":
                ws = await client.ws_connect("/ws", headers={"Origin": f"http://127.0.0.1:{config['port']}"})
                await ws.send_json({"token": "wrong"})
                await ws.receive()
                assert ws.close_code == 1008
            assert (await client.post("/api/shutdown", json={"instance":"test"}, headers=auth)).status == 200
            assert app[server.STOPPING].is_set()
    asyncio.run(scenario())


@linux
def test_real_pty_resize_ctrl_c_and_cleanup():
    from pistol.wsl.terminal import TerminalSession
    async def scenario():
        session = TerminalSession()
        try:
            session.resize(35, 111)
            await session.write(b"stty size; printf 'PTY_'; printf 'READY\\n'\n")
            output = b""
            async with asyncio.timeout(8):
                while b"35 111" not in output or b"PTY_READY" not in output:
                    output += await session.read()
            await session.write(b"sleep 30\n")
            await asyncio.sleep(0.2)
            await session.write(b"\x03printf 'AFTER_'; printf 'INT\\n'\n")
            output = b""
            async with asyncio.timeout(8):
                while b"AFTER_INT" not in output:
                    output += await session.read()
        finally:
            await session.close()
            await session.close()
        assert session.process.poll() is not None
        with pytest.raises(OSError):
            os.fstat(session.master)
    asyncio.run(scenario())


def test_pid_identity_rejects_reuse(monkeypatch):
    from pistol.wsl import bootstrap
    monkeypatch.setattr(bootstrap, "ticks", lambda pid: "different")
    assert not bootstrap.alive(state())
    monkeypatch.setattr(bootstrap, "ticks", lambda pid: "42")
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"unrelated\0unique\0")
    assert not bootstrap.alive(state())
    monkeypatch.setattr(Path, "read_bytes", lambda path: b"python\0-m\0pistol.wsl.server\0--instance\0unique\0")
    assert bootstrap.alive(state())


def test_windows_chamber_path_adapter():
    from pistol.wsl.chambers import windows_path
    assert windows_path("C:\\Projects\\My App").as_posix() == "/mnt/c/Projects/My App"
    assert windows_path("\\\\server\\share\\project") is None


def test_corrupt_tracking_state_preserved():
    atomic_json(manager.state_path(), {"distro": "Ubuntu", "pid": "invalid"})
    with pytest.raises(PistolError, match="left intact"):
        manager.status()
    assert manager.state_path().exists()


def test_authenticated_stop_without_wsl_exec(monkeypatch):
    atomic_json(manager.state_path(), state())
    running = [True]
    monkeypatch.setattr(manager, "reachable", lambda saved: running[0])
    def request(saved, route, body):
        assert route == "shutdown" and body == {"instance": "unique"}
        running[0] = False
        return body
    monkeypatch.setattr(manager, "request_agent", request)
    monkeypatch.setattr(manager, "agent", lambda *args, **kwargs: pytest.fail("No WSL exec needed"))
    assert manager.stop()
    assert not manager.state_path().exists()


def test_ports_parse_and_systemd_unavailable(monkeypatch):
    from pistol.wsl import ports, services
    async def ss(*args):
        return 'tcp LISTEN 0 4096 127.0.0.1:3000 0.0.0.0:* users:(("node",pid=1832,fd=3))\n'
    monkeypatch.setattr(ports, "command", ss)
    result = asyncio.run(ports.listing())
    assert result["ports"][0]["pid"] == 1832
    assert result["ports"][0]["port"] == 3000
    monkeypatch.setattr(Path, "exists", lambda path: False)
    assert "systemd is not running" in asyncio.run(services.listing())["message"]


def test_process_ownership_and_pid_reuse(monkeypatch):
    from types import SimpleNamespace
    from pistol.wsl import processes
    monkeypatch.setattr(processes.os, "getuid", lambda: 1000, raising=False)
    process = SimpleNamespace(uids=lambda: SimpleNamespace(real=0, effective=0), create_time=lambda: 10)
    monkeypatch.setattr(processes.psutil, "Process", lambda pid: process)
    with pytest.raises(PermissionError):
        processes.terminate(99999, 10)
    process.uids = lambda: SimpleNamespace(real=1000, effective=1000)
    with pytest.raises(ValueError, match="identity changed"):
        processes.terminate(99999, 9)


@linux
def test_two_websocket_sessions_are_independent_and_cleaned(monkeypatch):
    from aiohttp import WSMsgType
    from aiohttp.test_utils import TestClient, TestServer
    from pistol.wsl import server
    from pistol.wsl.terminal import TerminalSession
    sessions = []
    original = TerminalSession.__init__
    def track(self):
        original(self)
        sessions.append(self)
    monkeypatch.setattr(TerminalSession, "__init__", track)
    async def output_until(ws, marker):
        output = b""
        async with asyncio.timeout(8):
            while marker not in output:
                message = await ws.receive()
                assert message.type == WSMsgType.BINARY
                output += message.data
        return output
    async def scenario():
        config = {"token": "secret", "instance": "test", "port": 0, "distro": "test"}
        async with TestClient(TestServer(server.create_app(config))) as client:
            config["port"] = client.server.port
            origin = f"http://127.0.0.1:{config['port']}"
            one = await client.ws_connect("/ws", headers={"Origin": origin})
            two = await client.ws_connect("/ws", headers={"Origin": origin})
            for ws in (one, two):
                await ws.send_json({"token": "secret"})
            await one.send_json({"type": "input", "data": "export PISTOL_TEST=only_one; cd /tmp; printf 'FIRST_'; printf 'READY\\n'\n"})
            await output_until(one, b"FIRST_READY")
            await two.send_json({"type": "input", "data": "printf 'ISOLATED_'; printf '%s\\n' ${PISTOL_TEST:-yes}\n"})
            await output_until(two, b"ISOLATED_yes")
            await one.close()
            await two.send_json({"type": "input", "data": "\x04"})
            async with asyncio.timeout(8):
                while not two.closed:
                    await two.receive()
            async with asyncio.timeout(8):
                while not all(s.closed and s.process.poll() is not None for s in sessions):
                    await asyncio.sleep(0.05)
            # Graceful API shutdown must also clean a currently attached shell.
            three = await client.ws_connect("/ws", headers={"Origin": origin})
            await three.send_json({"token": "secret"})
            await three.send_json({"type": "input", "data": "printf 'THIRD_'; printf 'READY\\n'\n"})
            await output_until(three, b"THIRD_READY")
            response = await client.post("/api/shutdown", json={"instance": "test"},
                                         headers={"Authorization": "Bearer secret"})
            assert response.status == 200
            assert all(s.closed and s.process.poll() is not None for s in sessions)
        assert len(sessions) == 3
    asyncio.run(scenario())
