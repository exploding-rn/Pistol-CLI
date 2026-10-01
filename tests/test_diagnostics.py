from pathlib import Path
import sys

from pistol import doctor, medic, wtf
from pistol.config import atomic_json, project_state, read_json


def test_doctor_checks_syntax_without_executing_source(project):
    (project / "main.py").write_text("raise RuntimeError('must never run')\n", encoding="utf-8")
    report = doctor.run(project)
    assert report.ok
    (project / "main.py").write_text("def broken(:\n", encoding="utf-8")
    report = doctor.run(project)
    assert report.exit_code == 1
    assert any(check.level == "error" and "syntax" in check.message.lower() for check in report.checks)


def test_doctor_missing_dependency_and_invalid_toml(project):
    (project / "requirements.txt").write_text("pistol-test-package-that-does-not-exist-123456>=1\n", encoding="utf-8")
    report = doctor.run(project)
    assert not report.ok
    assert any("Missing dependencies" in check.message for check in report.checks)
    (project / "pyproject.toml").write_text("[invalid\n", encoding="utf-8")
    report = doctor.run(project)
    assert any("Invalid pyproject" in check.message for check in report.checks)


def test_doctor_invalid_metadata_shape_is_a_report(project):
    (project / "pyproject.toml").write_text("project = 123\n", encoding="utf-8")
    report = doctor.run(project)
    assert not report.ok
    assert any("Invalid pyproject" in check.message for check in report.checks)


def test_node_medic_does_not_create_python_venv(project):
    (project / "main.py").unlink()
    (project / "package.json").write_text('{"scripts":{"dev":"node index.js"}}', encoding="utf-8")
    assert medic.plan(project) == []


def test_medic_does_not_mark_incomplete_chamber_ready_without_entrypoint(project):
    from pistol import chamber
    script = project / "missing-later.py"
    script.write_text("print('later')", encoding="utf-8")
    item = chamber.create("api", project, entrypoint=[script.name], create_venv=False)
    item.status = "incomplete"
    chamber.save(item)
    script.unlink()
    assert not any(repair.kind in {"venv", "ready"} for repair in medic.plan(project, chamber_name="api"))


def test_medic_never_overwrites_dotenv(project, monkeypatch):
    (project / ".env").write_text("TOKEN=keep", encoding="utf-8")
    (project / ".env.example").write_text("TOKEN=example", encoding="utf-8")
    monkeypatch.setattr(medic.venv.EnvBuilder, "create", lambda *a: None)
    medic.run(project)
    assert (project / ".env").read_text() == "TOKEN=keep"
    (project / ".env").unlink()
    assert "dotenv" in [r.kind for r in medic.plan(project)]
    medic.run(project)
    assert (project / ".env").read_text() == "TOKEN=example"


def test_stale_pid_archived_and_failure_evidence_retained(project, monkeypatch):
    path = project_state(project) / "process.json"
    record = {"status": "running", "pid": 99999999, "create_time": 1, "log_file": "missing.log"}
    atomic_json(path, record)
    monkeypatch.setattr(medic.venv.EnvBuilder, "create", lambda *a: None)
    medic.run(project)
    assert not path.exists()
    assert read_json(path.parent / "last-process.json") == record


def test_wtf_evidence_missing_module(project):
    state = project_state(project)
    log = state / "logs" / "failure.log"
    log.parent.mkdir(parents=True)
    log.write_text("Traceback (most recent call last):\nModuleNotFoundError: No module named 'fastapi'\n", encoding="utf-8")
    atomic_json(state / "process.json", {"status": "exited", "exit_code": 1, "log_file": str(log), "command": [sys.executable, "main.py"]})
    result = wtf.run(project)
    assert result.exit_code == 1
    assert result.findings[0].issue == "Missing Python module"
    assert "fastapi" in result.findings[0].evidence


def test_wtf_redacts_known_secrets():
    assert "very-secret" not in wtf.redact("failed with very-secret token=another-secret", {"API_TOKEN": "very-secret"})
