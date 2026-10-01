import json
import pytest

from pistol.cli import build_parser, main
from pistol.utils.commands import split_command


@pytest.mark.parametrize("argv,command", [
    (["doctor"], "doctor"), (["chamber", "--list"], "chamber"),
    (["mag", "group", "--add", "api"], "mag"),
    (["fire", "--mag", "group"], "fire"),
    (["detective", "--port", "8775"], "detective"),
    (["shrimp", "--calls", "--file", "main.py"], "shrimp"),
    (["explorer", "--install"], "explorer"),
    (["watch", "--cmd", "python main.py"], "watch"),
])
def test_parsing(argv, command):
    assert build_parser().parse_args(argv).command == command


@pytest.mark.parametrize("argv", [["chamber"], ["detective"], ["explorer"], ["_uri", "pistol:dev", "--cmd", "evil"], ["doctor", "--proj", "."]])
def test_invalid_arguments(argv):
    with pytest.raises(SystemExit) as exc:
        build_parser().parse_args(argv)
    assert exc.value.code == 2


def test_json_before_and_after_command(capsys):
    for argv in (["--json", "chamber", "--list"], ["chamber", "--list", "--json"]):
        assert main(argv) == 0
        assert json.loads(capsys.readouterr().out) == []


def test_library_errors_are_readable(capsys):
    assert main(["fire", "does-not-exist"]) == 2
    assert "does not exist" in capsys.readouterr().err


def test_command_paths_with_spaces():
    assert split_command('python "a folder/main.py" --title "hello world"') == ["python", "a folder/main.py", "--title", "hello world"]


def test_chamber_environment_hidden_in_json(project, capsys):
    from pistol.chamber import create
    create("api", project, env={"SECRET_TOKEN": "very-secret"}, create_venv=False)
    assert main(["chamber", "--list", "--json"]) == 0
    output = capsys.readouterr().out
    assert "SECRET_TOKEN" in output
    assert "very-secret" not in output


def test_cli_explicit_python_entrypoint_overrides_package_json(project, monkeypatch, capsys):
    from pistol import chamber
    (project / "package.json").write_text('{"scripts":{"dev":"node web.js"}}', encoding="utf-8")
    (project / "pistolata.py").write_text("print('ready')", encoding="utf-8")
    monkeypatch.setattr(chamber.venv.EnvBuilder, "create", lambda *args, **kwargs: None)
    assert main(["chamber", "--name", "pistol-test", "--port", "2347", "--entrypoint", "pistolata.py", "--project", str(project)]) == 0
    capsys.readouterr()
    item = chamber.get("pistol-test")
    assert item.runtime == "python"
    assert item.entrypoint == ["pistolata.py"]


def test_cli_missing_explicit_entrypoint_is_clear_and_atomic(project, capsys):
    from pistol.config import data_dir
    assert main(["chamber", "--name", "missing", "--entrypoint", "pistolata.py", "--project", str(project)]) == 2
    assert f"Entrypoint not found: {project / 'pistolata.py'}" in capsys.readouterr().err
    assert not (data_dir() / "chambers" / "missing").exists()
