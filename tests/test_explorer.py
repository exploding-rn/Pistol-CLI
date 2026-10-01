"""Registry lifecycle is fully mocked; tests never register a real protocol."""
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote

import pytest

from pistol import explorer, uri_dispatcher, windows_explorer
from pistol.config import PistolError, data_dir


class FakeRegistry:
    HKEY_CURRENT_USER = 1
    KEY_WRITE = 2
    REG_SZ = 3

    def __init__(self):
        self.values = {}
        self.deleted = []

    class Key:
        def __init__(self, path): self.path = path
        def __enter__(self): return self
        def __exit__(self, *args): pass

    def OpenKey(self, hive, path):
        if path not in self.values:
            raise FileNotFoundError(path)
        return self.Key(path)

    def CreateKeyEx(self, hive, path, *args):
        self.values.setdefault(path, {})
        return self.Key(path)

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[key.path][name] = value

    def QueryValueEx(self, key, name):
        if name not in self.values[key.path]:
            raise FileNotFoundError(name)
        return self.values[key.path][name], self.REG_SZ

    def DeleteKey(self, hive, path):
        self.deleted.append(path)
        self.values.pop(path, None)


def test_install_uninstall_and_quoted_python(monkeypatch, project):
    registry = FakeRegistry()
    monkeypatch.setattr(explorer, "_registry", lambda: registry)
    (project / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(explorer.sys, "executable", str(project / "python.exe"))
    command = explorer.install()
    assert command.startswith('"') and command.endswith(' "%1"')
    assert "pythonw.exe" in command
    assert " -I -m pistol.uri_dispatcher " in command
    assert "python.exe" not in command
    assert registry.values[explorer.REGISTRY_PATH]["URL Protocol"] == ""
    assert explorer.uninstall()
    assert len(registry.deleted) == 4


def test_registration_does_not_replace_another_owner(monkeypatch, project):
    registry = FakeRegistry()
    registry.values[explorer.REGISTRY_PATH] = {"PistolOwner": "another app"}
    monkeypatch.setattr(explorer, "_registry", lambda: registry)
    (project / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(explorer.sys, "executable", str(project / "python.exe"))
    with pytest.raises(PistolError, match="another application"):
        explorer.install()
    with pytest.raises(PistolError, match="another application"):
        explorer.uninstall()
    assert registry.values[explorer.REGISTRY_PATH]["PistolOwner"] == "another app"


def test_registration_requires_matching_windowless_python(monkeypatch, project):
    monkeypatch.setattr(explorer, "_registry", lambda: FakeRegistry())
    monkeypatch.setattr(explorer.sys, "executable", str(project / "python.exe"))
    with pytest.raises(PistolError, match="Windowless Python launcher is missing"):
        explorer.install()


def test_pwr_uri_uses_home_when_no_path(monkeypatch, project):
    calls = []
    monkeypatch.setattr(explorer.Path, "home", lambda: project)
    monkeypatch.setattr(explorer.windows_explorer, "active_folder", lambda: None)
    monkeypatch.setattr(explorer.pwr, "run", calls.append)
    explorer.handle("pistol:pwr")
    assert calls == [project.resolve()]


def test_pwr_uri_uses_invoking_explorer_folder(monkeypatch, project, tmp_path):
    active = tmp_path / "Active Explorer folder"
    active.mkdir()
    calls = []
    monkeypatch.setattr(explorer.Path, "home", lambda: project)
    monkeypatch.setattr(explorer.windows_explorer, "active_folder", lambda: active)
    monkeypatch.setattr(explorer.pwr, "run", calls.append)
    explorer.handle("pistol:pwr")
    assert calls == [active]


def test_pwr_uri_uses_explicit_encoded_path(monkeypatch, project):
    calls = []
    def unexpected_lookup():
        raise AssertionError("An explicit path must bypass Explorer folder lookup")
    monkeypatch.setattr(explorer.windows_explorer, "active_folder", unexpected_lookup)
    monkeypatch.setattr(explorer.pwr, "run", calls.append)
    explorer.handle("pistol:pwr?path=" + quote(str(project), safe=""))
    assert calls == [project.resolve()]


def test_dispatcher_invokes_same_pwr_function(monkeypatch, project):
    calls = []
    monkeypatch.setattr(explorer.Path, "home", lambda: project)
    monkeypatch.setattr(explorer.windows_explorer, "active_folder", lambda: None)
    monkeypatch.setattr(explorer.pwr, "run", calls.append)
    assert uri_dispatcher.main(["pistol:pwr"]) == 0
    assert calls == [project.resolve()]


def test_pwr_uri_logs_terminal_executable(monkeypatch, project):
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
    monkeypatch.setattr(explorer.Path, "home", lambda: project)
    monkeypatch.setattr(explorer.windows_explorer, "active_folder", lambda: None)
    monkeypatch.setattr(pwr, "terminal_executable", lambda: str(project / "wt.exe"))
    monkeypatch.setattr(pwr.ctypes, "WinDLL", lambda *a, **kw: Shell())
    assert uri_dispatcher.main(["pistol:pwr"]) == 0
    assert calls[0][1] == "runas"
    assert Path(calls[0][2]).name.lower() == "wt.exe"
    assert "Launching Windows Terminal: wt.exe" in (data_dir() / "logs" / "uri.log").read_text(encoding="utf-8")


def test_explorer_folder_lookup_accepts_local_directory(monkeypatch, project):
    if os.name != "nt":
        pytest.skip("Windows Explorer lookup")
    monkeypatch.setattr(windows_explorer, "_foreground_hwnd", lambda: 123)
    monkeypatch.setattr(windows_explorer, "_top_level_hwnds", lambda: [123, 456])
    monkeypatch.setattr(windows_explorer, "_shell_window_folder", lambda hwnd, order: str(project) if hwnd == 123 and order == [123, 456] else None)
    assert windows_explorer.active_folder() == project.resolve()


def test_explorer_folder_lookup_rejects_missing_directory(monkeypatch, tmp_path):
    if os.name != "nt":
        pytest.skip("Windows Explorer lookup")
    monkeypatch.setattr(windows_explorer, "_foreground_hwnd", lambda: 123)
    monkeypatch.setattr(windows_explorer, "_top_level_hwnds", lambda: [123])
    monkeypatch.setattr(windows_explorer, "_shell_window_folder", lambda hwnd, order: str(tmp_path / "missing"))
    assert windows_explorer.active_folder() is None


def test_dispatcher_logs_invalid_uri_without_console():
    assert uri_dispatcher.main(["pistol:fire"]) == 2
    log = data_dir() / "logs" / "uri.log"
    assert log.is_file()
    assert "Only pistol:doctor, pistol:dev and pistol:pwr" in log.read_text(encoding="utf-8")
    assert uri_dispatcher.main([]) == 2
    assert "Expected exactly one Pistol URI" in log.read_text(encoding="utf-8")


def test_real_pythonw_dispatcher_logs_failure_from_other_directory(tmp_path):
    if os.name != "nt":
        pytest.skip("Windows pythonw smoke test")
    executable = Path(sys.executable).with_name("pythonw.exe")
    if not executable.is_file():
        pytest.skip("This Python environment has no pythonw.exe")
    result = subprocess.run([str(executable), "-I", "-m", "pistol.uri_dispatcher", "pistol:fire"], cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    assert result.returncode == 2
    assert "Only pistol:doctor, pistol:dev and pistol:pwr" in (data_dir() / "logs" / "uri.log").read_text(encoding="utf-8")
