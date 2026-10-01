import os
from pathlib import Path
import shutil
import sys
import time

import pytest

from pistol import chamber, fire, magazine, watch
from pistol.cli import main as cli_main
from pistol.config import PistolError, read_json


def test_foreground_launch_captures_failure_in_space_path(project):
    (project / "main.py").write_text("import sys\nprint('hello ü')\nprint('failure evidence', file=sys.stderr)\nsys.exit(7)\n", encoding="utf-8")
    running = fire.launch(project, echo=False)
    assert fire.supervise([running]) == 7
    record = read_json(running.state_file)
    assert record["status"] == "exited"
    assert record["exit_code"] == 7
    assert record["cwd"] == str(project)
    log = Path(record["log_file"]).read_text(encoding="utf-8")
    assert "hello ü" in log and "failure evidence" in log
    assert not fire.is_alive(record)


def test_explicit_stop_and_duplicate_launch_guard(project):
    (project / "main.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    running = fire.launch(project, echo=False)
    try:
        assert fire.is_alive(running.record)
        with pytest.raises(PistolError, match="already has"):
            fire.launch(project, echo=False)
        assert fire.stop(project=project)
    finally:
        if running.process.poll() is None:
            running.process.kill()
        running.finish()
    assert running.process.poll() is not None


def test_reused_pid_not_alive():
    import psutil
    process = psutil.Process(os.getpid())
    record = {"pid": process.pid, "create_time": process.create_time() - 10, "status": "running"}
    assert not fire.is_alive(record)
    assert not fire.stop_record(record)


def test_magazine_environment_and_failure_exit(project):
    script = project / "main.py"
    script.write_text("import os,sys\nprint(os.environ['PISTOL_API_PORT'])\nprint(os.environ['PISTOL_WORKER_HOST'])\nsys.exit(3)\n", encoding="utf-8")
    api = chamber.create("api", project, runtime="command", entrypoint=[sys.executable, "main.py"])
    chamber.create("worker", project, runtime="command", entrypoint=[sys.executable, "-c", "import time; time.sleep(30)"])
    magazine.create("group")
    magazine.add("group", "api")
    magazine.add("group", "worker")
    assert fire.run(magazine="group") == 3
    record = read_json(api.directory / "process.json")
    assert str(api.port) in Path(record["log_file"]).read_text(encoding="utf-8")
    assert not fire.is_alive(read_json(chamber.get("worker").directory / "process.json"))


def test_launch_rollback_when_magazine_member_is_invalid(project):
    (project / "main.py").write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    api = chamber.create("api", project, runtime="command", entrypoint=[sys.executable, "main.py"])
    chamber.create("broken", project, runtime="command", entrypoint=["pistol-nonexistent-executable-1234"])
    magazine.create("group")
    magazine.add("group", "api")
    magazine.add("group", "broken")
    with pytest.raises(PistolError, match="Runtime executable not found"):
        fire.run(magazine="group")
    assert not fire.is_alive(read_json(api.directory / "process.json"))


def test_environment_precedence_and_isolated_temp(project, monkeypatch):
    (project / ".env").write_text("VALUE=dotenv\nDOTENV_ONLY=literal\n", encoding="utf-8")
    monkeypatch.setenv("VALUE", "process")
    item = chamber.create("api", project, env={"VALUE": "chamber"}, create_venv=False)
    result = fire.environment(project, item, {"VALUE": "magazine"})
    assert result["VALUE"] == "magazine"
    assert result["DOTENV_ONLY"] == "literal"
    assert result["PORT"] == str(item.port)
    assert Path(result["TMP"]).is_relative_to(item.directory)


def test_windows_npm_uses_node_directly(project, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows executable resolution")
    installation = project / "Node with spaces"
    script = installation / "node_modules" / "npm" / "bin" / "npm-cli.js"
    script.parent.mkdir(parents=True)
    script.write_text("// fake npm launcher", encoding="utf-8")
    node = str(installation / "node.exe")
    (installation / "node.exe").write_bytes(b"")
    (installation / "npm.CMD").write_bytes(b"")
    monkeypatch.setattr(fire.shutil, "which", lambda name, **kw: str(installation / "npm.CMD") if name == "npm" else node)
    assert fire.resolve_executable(["npm", "run", "dev"], project, {}) == [node, str(script), "run", "dev"]


def test_fire_missing_entrypoint_is_actionable(project):
    script = project / "custom.py"
    script.write_text("print('ok')", encoding="utf-8")
    item = chamber.create("api", project, runtime="command", entrypoint=[sys.executable, script.name])
    script.unlink()
    with pytest.raises(PistolError, match="^Entrypoint not found: ") as exc:
        fire.launch(selected=item, echo=False)
    assert str(script) in str(exc.value)
    assert not (item.directory / "process.json").exists()


def test_fire_missing_python_executable_has_clear_cli_error(project, capsys):
    chamber.create("api", project, create_venv=False)
    assert cli_main(["fire", "api"]) == 2
    output = capsys.readouterr().err
    assert "Python executable not found" in output
    assert "WinError 2" not in output


def test_fire_missing_node_executable_has_clear_cli_error(project, monkeypatch, capsys):
    script = project / "web.js"
    script.write_text("console.log('ok')", encoding="utf-8")
    chamber.create("web", project, entrypoint=[script.name])
    monkeypatch.setattr(fire.shutil, "which", lambda *args, **kwargs: None)
    assert cli_main(["fire", "web"]) == 2
    output = capsys.readouterr().err
    assert "Node.js executable not found" in output
    assert "WinError 2" not in output


def test_fire_spawn_race_reports_executable_instead_of_raw_os_error(project, monkeypatch):
    item = chamber.create("api", project, runtime="command", entrypoint=[sys.executable, "main.py"])
    def missing(*args, **kwargs):
        raise FileNotFoundError(2, "The system cannot find the file specified")
    monkeypatch.setattr(fire.subprocess, "Popen", missing)
    with pytest.raises(PistolError, match="Runtime executable not found"):
        fire.launch(selected=item, echo=False)
    assert not (item.directory / "process.json").exists()


def test_node_script_entrypoint_runs_directly(project):
    if not shutil.which("node"):
        pytest.skip("Node is not installed")
    script = project / "web.js"
    script.write_text("console.log('direct-node-smoke')\n", encoding="utf-8")
    item = chamber.create("web", project, entrypoint=[script.name])
    assert item.runtime == "node"
    assert fire.run(name="web") == 0
    record = read_json(item.directory / "process.json")
    assert "direct-node-smoke" in Path(record["log_file"]).read_text(encoding="utf-8")


def test_rust_script_entrypoint_compiles_and_runs(project):
    if not shutil.which("rustc"):
        pytest.skip("Rust is not installed")
    script = project / "demo.rs"
    script.write_text('fn main() { println!("direct-rust-smoke"); }\n', encoding="utf-8")
    item = chamber.create("rusty", project, entrypoint=[script.name])
    assert item.runtime == "rust"
    assert fire.run(name="rusty") == 0
    record = read_json(item.directory / "process.json")
    assert "direct-rust-smoke" in Path(record["log_file"]).read_text(encoding="utf-8")


def test_batch_commands_require_explicit_shell(project, monkeypatch):
    if os.name != "nt":
        pytest.skip("Windows batch wrapper validation")
    wrapper = project / "unsafe.cmd"
    wrapper.write_bytes(b"")
    with pytest.raises(PistolError, match="explicit cmd.exe"):
        fire.resolve_executable([str(wrapper), "hello&world"], project, {})


def test_watcher_restart_and_cleanup(project, monkeypatch):
    snapshots = iter([{"a": (1, 1)}, {"a": (2, 1)}, {"a": (2, 1)}])
    monkeypatch.setattr(watch, "snapshot", lambda root: next(snapshots))
    launched, stopped = [], []
    class Process:
        def poll(self):
            return None
    class Running:
        process = Process()
        def __init__(self):
            self.record = {"id": len(launched)}
            self.finished = False
        def finish(self):
            self.finished = True
    def launch(*args, **kwargs):
        child = Running()
        launched.append(child)
        return child
    ticks = 0
    def sleep(seconds):
        nonlocal ticks
        ticks += 1
        if ticks == 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(watch, "launch", launch)
    monkeypatch.setattr(watch, "stop_record", stopped.append)
    monkeypatch.setattr(watch.time, "sleep", sleep)
    assert watch.run(project, debounce=0) == 130
    assert len(launched) == 2 and len(stopped) == 2
    assert all(child.finished for child in launched)
