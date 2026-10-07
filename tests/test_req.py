"""Requirements scanning should stay scoped, read-only and single-pass."""

from pathlib import Path

import pytest

from pistol import req
from pistol.cli import build_parser, main


def test_package_json_marks_project_before_home_requirements(tmp_path, monkeypatch):
    home = tmp_path / "home"
    project = home / "Desktop" / "CODE" / "api"
    project.mkdir(parents=True)
    (home / "requirements.txt").write_text("unrelated-package\n", encoding="utf-8")
    (project / "package.json").write_text('{"name":"api"}', encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    assert req._resolve_project(project) == project
    (project / "package.json").unlink()
    assert req._resolve_project(project) == project


@pytest.mark.parametrize("ignored", [
    "node_modules", ".venv", "venv", "__pycache__", ".git", ".hg", ".svn",
    "dist", "build", "custom.egg-info", "Node_Modules",
])
def test_generated_directories_are_never_source_scanned(tmp_path, ignored):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "main.py").write_text("import flask\n", encoding="utf-8")
    (tmp_path / ignored).mkdir()
    (tmp_path / ignored / "generated.py").write_text("import should_never_be_seen\n", encoding="utf-8")
    paths = list(req._python_files(tmp_path))
    assert [p.name for p in paths] == ["main.py"]
    assert "should_never_be_seen" not in req._scan_import_files(tmp_path)


def test_plain_scan_reads_each_source_once_and_never_installs(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "app.py").write_text("import flask\nfrom flask_cors import CORS\nvalue = (n := 1)\n", encoding="utf-8")
    (tmp_path / "more.py").write_text("from dotenv import load_dotenv\n", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "noise.py").write_text("import enormous_generated_tree\n", encoding="utf-8")

    visited = []
    original_analyze = req._analyze_python_file
    monkeypatch.setattr(req, "_analyze_python_file", lambda path: (visited.append(path), original_analyze(path))[1])
    monkeypatch.setattr(req, "_chamber_python", lambda project: None)
    monkeypatch.setattr(req, "_distribution_version", lambda package: None)
    monkeypatch.setattr(req.metadata, "packages_distributions", lambda: pytest.fail("known imports should not enumerate distribution metadata"))
    monkeypatch.setattr(req.subprocess, "run", lambda *args, **kwargs: pytest.fail("plain req should not invoke pip or Python subprocesses"))

    report = req.run(tmp_path)
    assert len(visited) == 2
    assert set(visited) == {tmp_path / "app.py", tmp_path / "more.py"}
    assert report.python.static_minimum == ">=3.8"
    assert {item.package_name for item in report.third_party} == {"Flask", "flask-cors", "python-dotenv"}
    assert not (tmp_path / "requirements.txt").exists()


def test_unknown_import_metadata_discovered_once_per_scan(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "app.py").write_text("import first_private\nimport second_private\nimport mysterious_name\n", encoding="utf-8")
    calls = []

    def metadata_map():
        calls.append(True)
        return {"first_private": ["dist-first"], "second_private": ["dist-second"]}

    monkeypatch.setattr(req.metadata, "packages_distributions", metadata_map)
    monkeypatch.setattr(req, "_chamber_python", lambda project: None)
    monkeypatch.setattr(req, "_distribution_version", lambda package: None)
    report = req.run(tmp_path)
    assert len(calls) == 1
    assert {item.package_name for item in report.third_party} == {"dist-first", "dist-second"}
    assert report.unresolved == ["mysterious_name"]


def test_known_import_mapping_is_stable_without_metadata(tmp_path, monkeypatch):
    known = {"flask": "Flask", "flask_cors": "flask-cors", "dotenv": "python-dotenv",
             "discord": "discord.py", "yaml": "PyYAML", "PIL": "Pillow",
             "cv2": "opencv-python", "bs4": "beautifulsoup4", "sklearn": "scikit-learn"}
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "app.py").write_text("\n".join(f"import {name}" for name in known) + "\n", encoding="utf-8")
    monkeypatch.setattr(req, "_chamber_python", lambda project: None)
    monkeypatch.setattr(req, "_distribution_version", lambda package: None)
    monkeypatch.setattr(req.metadata, "packages_distributions", lambda: pytest.fail("known imports should not enumerate metadata"))
    report = req.run(tmp_path)
    assert {item.import_name: item.package_name for item in report.third_party} == known


def test_duplicate_distribution_only_checks_version_once(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "app.py").write_text("import flask\nimport flask_cors\n", encoding="utf-8")
    monkeypatch.setattr(req, "_chamber_python", lambda project: None)
    monkeypatch.setitem(req.PACKAGE_MAP, "flask_cors", "Flask")
    versions = []
    monkeypatch.setattr(req, "_distribution_version", lambda package: (versions.append(package), "1.0")[1])
    report = req.run(tmp_path)
    assert versions == ["Flask"]
    assert all(item.installed for item in report.third_party)


@pytest.mark.parametrize("mode", ["--check", "--write", "--install", "--sync", "--python", "--json"])
def test_req_modes_still_parse(mode):
    assert build_parser().parse_args(["req", mode]).command == "req"


def test_req_cli_modes_preserve_dispatch(tmp_path, monkeypatch, capsys):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "app.py").write_text("import flask\n", encoding="utf-8")
    monkeypatch.setattr(req, "_chamber_python", lambda project: None)
    monkeypatch.setattr(req, "_distribution_version", lambda package: None)

    assert main(["req", "--project", str(tmp_path), "--check"]) == 1
    assert "Flask" in capsys.readouterr().out
    assert main(["req", "--project", str(tmp_path), "--python"]) == 0
    assert "Python" in capsys.readouterr().out
    assert main(["req", "--project", str(tmp_path), "--json"]) == 0
    assert '"package_name": "Flask"' in capsys.readouterr().out
    assert main(["req", "--project", str(tmp_path), "--write"]) == 0
    capsys.readouterr()
    assert (tmp_path / "requirements.txt").read_text(encoding="utf-8") == "Flask\n"
    monkeypatch.setattr(req, "install", lambda project: ["Flask"])
    assert main(["req", "--project", str(tmp_path), "--install"]) == 0
    assert "Flask" in capsys.readouterr().out
    monkeypatch.setattr(req, "sync", lambda project: (tmp_path / "requirements.txt", [], req.run(tmp_path)))
    assert main(["req", "--project", str(tmp_path), "--sync"]) == 0
    assert "Flask" in capsys.readouterr().out
