from pathlib import Path
import os
import socket
import sys

import pytest

from pistol import ports, vent
from pistol.config import PistolError
from pistol.explorer import parse_uri
from pistol.pwr import shell_parameters
from pistol.utils.files import safe_remove


def test_port_detection_with_real_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        assert not ports.available(port)
        report = ports.inspect(port)
        assert not report.available
        assert any(conn["pid"] == os.getpid() for conn in report.listeners)
    assert ports.available(port)
    assert 1 <= ports.free_port() <= 65535


@pytest.mark.parametrize("port", [0, -1, 65536, True])
def test_invalid_port(port):
    with pytest.raises(PistolError):
        ports.validate_port(port)


def test_safe_vent_preserves_dependencies_and_source(project):
    keep = ["node_modules", ".venv", "target", "build", "dist"]
    for name in keep + ["__pycache__", ".pytest_cache"]:
        folder = project / name
        folder.mkdir()
        (folder / "keep.dat").write_bytes(b"12345")
    node_cache = project / ".next" / "cache"
    node_cache.mkdir(parents=True)
    (node_cache / "cache.dat").write_bytes(b"12345")
    result = vent.run(project)
    assert result.bytes_freed == 15
    assert all((project / name / "keep.dat").exists() for name in keep)
    assert (project / "main.py").exists()
    assert not (project / "__pycache__").exists()
    assert not node_cache.exists()


def test_cleanup_outside_boundary_refused(tmp_path, project):
    outside = tmp_path / "important.txt"
    outside.write_text("keep", encoding="utf-8")
    with pytest.raises(PistolError):
        safe_remove(outside, project)
    with pytest.raises(PistolError):
        safe_remove(project, project)
    assert outside.read_text() == "keep"


def test_vent_skips_mocked_junction(project, monkeypatch):
    cache = project / "__pycache__"
    cache.mkdir()
    (cache / "outside.txt").write_text("keep", encoding="utf-8")
    monkeypatch.setattr(vent, "is_link", lambda path: path == cache)
    assert vent.plan(project) == []
    assert (cache / "outside.txt").exists()


def test_child_link_blocks_entire_cleanup(project, monkeypatch):
    from pistol.utils import files
    cache = project / "__pycache__"
    cache.mkdir()
    suspect = cache / "link"
    suspect.mkdir()
    monkeypatch.setattr(files, "is_link", lambda path: path == suspect)
    with pytest.raises(PistolError, match="containing a link"):
        safe_remove(cache, project)
    assert cache.exists()


@pytest.mark.parametrize("uri", [
    "pistol:fire", "pistol:dev?cmd=evil", "pistol://dev", "https:dev", "pistol:dev#fragment",
    "pistol:dev?path=relative", 'pistol:dev" --cmd evil', "pistol:dev?path=%00",
    "pistol:dev?path=%22", "pistol:dev?path=%ZZ", "pistol:dev?path=a&path=b",
    "pistol:dev?path=%5C%5Cserver%5Cshare", "pistol:dev\n",
])
def test_uri_rejects_command_injection(uri):
    with pytest.raises(PistolError):
        parse_uri(uri)


def test_uri_uses_explicit_default(project):
    assert parse_uri("pistol:doctor", project) == ("doctor", project.resolve())
    assert parse_uri("pistol:pwr", project) == ("pwr", project.resolve())


def test_powershell_path_is_literal_and_encoded(project):
    import base64
    parameters = shell_parameters(project / "a'b $(write-evil)")
    script = base64.b64decode(parameters.rsplit(" ", 1)[1]).decode("utf-16-le")
    assert "Set-Location -LiteralPath '" in script
    assert "a''b $(write-evil)'" in script
    assert "$raw.BackgroundColor = 'Black'" in script
    assert "$raw.ForegroundColor = 'Gray'" in script
    assert "Get-ItemProperty -LiteralPath 'HKCU:\\Console' -Name WindowSize" in script
    assert "$raw.WindowSize = [System.Management.Automation.Host.Size]::new($width, $height)" in script
    assert "-NoExit" in parameters


def test_pwr_uses_normal_uac(monkeypatch, project):
    from pistol import pwr
    from pistol.utils.commands import split_command
    calls = []
    class Execute:
        def __call__(self, *args):
            calls.append(args)
            return 42
    class Shell:
        ShellExecuteW = Execute()
    if os.name != "nt":
        pytest.skip("Windows API binding test")
    original_windll = pwr.ctypes.WinDLL
    monkeypatch.setattr(pwr.ctypes, "WinDLL", lambda *a, **kw: Shell())
    monkeypatch.setattr(pwr, "terminal_executable", lambda: str(project / "wt.exe"))
    pwr.run(project)
    monkeypatch.setattr(pwr.ctypes, "WinDLL", original_windll)
    assert calls[0][1] == "runas"
    assert calls[0][2].lower().endswith("wt.exe")
    assert split_command(calls[0][3]) == ["-w", "new", "-d", str(project.resolve())]
    assert calls[0][4] == str(project.resolve())


def test_pwr_falls_back_only_when_terminal_missing(monkeypatch, project):
    from pistol import pwr
    calls = []
    class Execute:
        def __call__(self, *args):
            calls.append(args)
            return 42
    class Shell:
        ShellExecuteW = Execute()
    if os.name != "nt":
        pytest.skip("Windows API binding test")
    monkeypatch.setattr(pwr.ctypes, "WinDLL", lambda *a, **kw: Shell())
    monkeypatch.setattr(pwr, "terminal_executable", lambda: None)
    pwr.run(project)
    assert calls[0][1] == "runas"
    assert calls[0][2].lower().endswith("powershell.exe")
    assert "-NoExit" in calls[0][3]


def test_terminal_launch_failure_does_not_fall_back(monkeypatch, project):
    from pistol import pwr
    calls = []
    class Execute:
        def __call__(self, *args):
            calls.append(args)
            return 5
    class Shell:
        ShellExecuteW = Execute()
    if os.name != "nt":
        pytest.skip("Windows API binding test")
    monkeypatch.setattr(pwr.ctypes, "WinDLL", lambda *a, **kw: Shell())
    monkeypatch.setattr(pwr, "terminal_executable", lambda: str(project / "wt.exe"))
    with pytest.raises(PistolError, match="Windows Terminal"):
        pwr.run(project)
    assert len(calls) == 1
    assert calls[0][2].lower().endswith("wt.exe")


def test_terminal_alias_resolution_when_path_is_missing(monkeypatch, tmp_path):
    from pistol import pwr
    alias = tmp_path / "Microsoft" / "WindowsApps" / "wt.exe"
    alias.parent.mkdir(parents=True)
    alias.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(pwr.shutil, "which", lambda name: None)
    assert pwr.terminal_executable() == str(alias)


def test_terminal_pwr_defaults_to_current_directory(monkeypatch, project):
    from pistol import pwr
    calls = []
    monkeypatch.chdir(project)
    monkeypatch.setattr(pwr, "open_shell", lambda path=None, **kwargs: calls.append((path, kwargs)))
    pwr.run()
    assert calls == [(None, {"elevated": True})]
