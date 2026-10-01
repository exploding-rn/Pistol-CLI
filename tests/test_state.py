from dataclasses import asdict
import json
from pathlib import Path

import pytest

from pistol import chamber, magazine
from pistol.config import PistolError, atomic_json, data_dir, read_json, valid_name


def test_atomic_roundtrip_and_no_temporary_junk(tmp_path):
    path = tmp_path / "config" / "state.json"
    atomic_json(path, {"unicode": "café 日本語", "nested": [1, 2]})
    assert read_json(path)["unicode"] == "café 日本語"
    assert list(path.parent.iterdir()) == [path]


def test_interrupted_replace_preserves_previous_file(tmp_path, monkeypatch):
    from pistol import config
    path = tmp_path / "state.json"
    atomic_json(path, {"version": 1})
    def fail(*args):
        raise OSError("simulated interrupted replacement")
    monkeypatch.setattr(config.os, "replace", fail)
    with pytest.raises(OSError):
        atomic_json(path, {"version": 2})
    assert read_json(path) == {"version": 1}
    assert list(tmp_path.glob(".write-*")) == []


def test_corrupt_state_not_overwritten(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(PistolError, match="left intact"):
        read_json(path)
    assert path.read_text() == "{bad"


def test_wrong_json_shape_has_actionable_error(tmp_path):
    path = tmp_path / "process.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(PistolError, match="Expected a JSON object"):
        read_json(path, {})


@pytest.mark.parametrize("name", ["..", "../escape", "C:\\escape", "CON", "nul", "a/b", " x", "x.", "a" * 65])
def test_unsafe_names(name):
    with pytest.raises(PistolError):
        valid_name(name)


def test_chamber_creation_real_venv(project):
    item = chamber.create("API", project)
    assert item.name == "api"
    assert item.python.is_file()
    assert item.entrypoint == ["main.py"]
    assert item.directory.is_relative_to(data_dir())
    assert asdict(chamber.get("API")) == asdict(item)
    assert not (project / ".pistol").exists()


def test_duplicate_chamber_and_port_rejected(project):
    first = chamber.create("api", project, create_venv=False)
    with pytest.raises(PistolError, match="already exists"):
        chamber.create("api", project, create_venv=False)
    with pytest.raises(PistolError, match="occupied or assigned"):
        chamber.create("other", project, port=first.port, create_venv=False)


def test_switch_and_delete_preserve_source(project):
    chamber.create("one", project, create_venv=False)
    chamber.create("two", project, create_venv=False)
    assert chamber.current(project) is None
    chamber.switch("two")
    assert chamber.current(project).name == "two"
    magazine.create("group")
    magazine.add("group", "two")
    chamber.delete("two")
    assert magazine.get("group").chambers == []
    assert (project / "main.py").is_file()
    assert chamber.current(project).name == "one"


def test_magazine_environment_and_collision(project):
    one = chamber.create("api-server", project, create_venv=False)
    chamber.create("api_server", project, create_venv=False)
    magazine.create("group")
    magazine.add("group", "api-server")
    values = magazine.environment(magazine.get("group"))
    assert values == {"PISTOL_API_SERVER_HOST": "127.0.0.1", "PISTOL_API_SERVER_PORT": str(one.port)}
    with pytest.raises(PistolError, match="collide"):
        magazine.add("group", "api_server")
    assert len(magazine.list_magazines()) == 1


@pytest.mark.parametrize("suffix, expected", [
    (".py", "python"), (".pyw", "python"),
    (".js", "node"), (".mjs", "node"), (".cjs", "node"), (".ts", "node"),
    (".rs", "rust"),
])
def test_explicit_entrypoint_extension_selects_runtime(project, suffix, expected):
    (project / "package.json").write_text('{"scripts":{"dev":"node app.js"}}', encoding="utf-8")
    entrypoint = project / f"custom{suffix}"
    entrypoint.write_text("", encoding="utf-8")
    item = chamber.create("specific", project, entrypoint=[entrypoint.name], create_venv=False)
    assert item.runtime == expected
    assert item.entrypoint == [entrypoint.name]


def test_missing_explicit_entrypoint_leaves_no_chamber(project):
    expected = project / "pistolata.py"
    with pytest.raises(PistolError, match="^Entrypoint not found: ") as exc:
        chamber.create("missing", project, entrypoint=["pistolata.py"])
    assert str(expected) in str(exc.value)
    assert not (data_dir() / "chambers" / "missing").exists()


def test_reset_rejects_missing_entrypoint_before_runtime_cleanup(project):
    script = project / "worker.js"
    script.write_text("console.log('ok')", encoding="utf-8")
    item = chamber.create("worker", project, entrypoint=[script.name])
    marker = item.directory / "temp" / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    script.unlink()
    with pytest.raises(PistolError, match="Entrypoint not found"):
        chamber.reset("worker")
    assert marker.read_text(encoding="utf-8") == "keep"


def test_home_package_marker_does_not_override_nested_python(monkeypatch, tmp_path):
    home = tmp_path / "fake-home"
    project = home / "work" / "small-project"
    project.mkdir(parents=True)
    (home / "package.json").write_text('{"name":"unrelated"}', encoding="utf-8")
    (project / "pistolata.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.chdir(project)
    item = chamber.create("nested", entrypoint=["pistolata.py"], create_venv=False)
    assert Path(item.project) == project
    assert item.runtime == "python"


def test_explicit_runtime_flag_overrides_entrypoint_inference(project):
    (project / "runner.py").write_text("print('run')", encoding="utf-8")
    item = chamber.create("custom", project, runtime="command", entrypoint=["runner.py"])
    assert item.runtime == "command"
